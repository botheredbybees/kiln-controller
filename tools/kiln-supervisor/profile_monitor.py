"""Supervise and record a PROFILE run on the kiln (the controller heats the kiln, this watches it).

  python profile_monitor.py --selftest                       # test the safety rules on fake data; touches nothing
  python profile_monitor.py --profile NAME --profile-file F  # DRY RUN: connects read-only, shows the plan, starts NOTHING
  python profile_monitor.py --profile NAME --peak 150 --total-min 88 --go          # really starts the run
  python profile_monitor.py --profile NAME --profile-file F --extra-min 240 --go   # peak and length read from the file
  python profile_monitor.py --profile NAME --profile-file F --attach               # supervise a run that is ALREADY running (or about to be, within 3 min); starts nothing

--peak is the profile's highest temperature (C); --total-min its length in minutes (or give --profile-file).
--extra-min: minutes allowed beyond the profile's own length (the controller pauses the schedule while the kiln catches up).
--ceiling C: absolute ceiling for the whole kiln (default 1050; use the controller's emergency_shutoff_temp plus a margin).
--feed-lost-min: minutes of reconnect attempts after the status feed goes quiet before the run is stopped (default 5).
Every reading goes to a CSV on the laptop. Safety rules send the MAIN stop (POST /api stop).
Nothing here uses ssh. Without --go nothing is ever started. Exit code 0 only if the profile finished normally.
A STOP cannot open a relay that has welded shut: rules that suspect that say CUT THE POWER AT THE WALL.
"""
import csv
import json
import os
import sys
import time
import urllib.request

import websocket

HOST = os.environ.get("KILN_HOST", "192.168.1.125:8081")             # the controller, host:port
OUTDIR = os.path.expanduser(os.environ.get("KILN_LOG_DIR", "~/backups/kiln-profiling"))
HEAT_CYCLE_S = 2.0                 # config.sensor_time_wait: the status "heat" value is seconds ON per cycle (0 to 2), not a fraction

# --- the rules. Each is a deliberate, simple limit; the controller has its own emergency checks as well.
ABS_CEILING = 1050.0               # never above this, whatever the profile (two readings in a row)
OVER_PEAK_MARGIN = 30.0            # more than this above the profile's highest point (two readings in a row)
OVER_TARGET_HEATER_ON = 10.0       # heater on this far above the current target ...
OVER_TARGET_HEATER_ON_S = 20.0     # ... for this long
OVER_TARGET_ANY = 25.0             # this far above target, heater or not ...
OVER_TARGET_ANY_S = 120.0          # ... for this long
LAG_BELOW_TARGET = 40.0            # more than this below target while running ...
LAG_BELOW_TARGET_S = 600.0         # ... for this long: not following the profile
MAX_RISE_PER_MIN = 30.0            # while heating, over a 60 s window (measured 17-23 C/min at full power from cold)
NO_RISE_AFTER_HEATING_S = 600.0    # 10 minutes of heater-on time while the target is RISING ...
NO_RISE_MIN_C = 3.0                # ... must have gained at least this much
STALL_WINDOW_S = 1200.0            # the kiln cannot reach the target: this long at (near) full power ...
STALL_MIN_HEAT = 0.9               # ... mean output at least this ...
STALL_MIN_GAIN_C = 5.0             # ... and gaining less than this ...
STALL_MIN_BELOW_TARGET = 4.0       # ... while at least this far below the target
STUCK_ZERO_HEAT_S = 720.0          # heater output reported 0 this long ...
STUCK_RISE_WINDOW_S = 360.0        # ... and the temperature still rose this much in the last window:
STUCK_RISE_C = 6.0                 # the heater may be stuck on (a coast after switch-off ends within minutes)
FEED_SILENT_S = 30.0               # no status message for this long: try to reconnect
CONSECUTIVE = 2                    # readings in a row for the absolute limits (one bad reading must not kill a long firing)


class Rules:
    def __init__(self, peak, total_s, extra_s=1800.0):
        self.peak = peak
        self.total_s = total_s
        self.extra_s = extra_s
        self.start_temp = None
        self.heat_time = 0.0
        self.last_t = None
        self.hist = []              # (time, temp) while heating, last 60 s
        self.tgt_hist = []          # (time, target), last 60 s
        self.samples = []           # (time, temp, heat), last STALL_WINDOW_S
        self.over_heat_since = None
        self.over_any_since = None
        self.lag_since = None
        self.zero_heat_since = None
        self.ceiling_n = 0
        self.peak_n = 0

    def target_rising(self, now, target):
        self.tgt_hist.append((now, target))
        self.tgt_hist = [(t, x) for t, x in self.tgt_hist if now - t <= 60]
        return len(self.tgt_hist) > 1 and target - self.tgt_hist[0][1] > 0.1

    def check(self, now, m, elapsed):
        """returns a reason string if the run must be stopped now, else None"""
        temp, state = m["temperature"], m["state"]
        heat = min(max((m.get("heat") or 0) / HEAT_CYCLE_S, 0), 1)   # seconds-on per cycle -> fraction; the first sample of a run reads 2.0 (full)
        target = m.get("target") or 0
        if self.start_temp is None:
            self.start_temp = temp
        dt = 0 if self.last_t is None else now - self.last_t
        self.last_t = now
        heating = heat > 0
        running = state == "RUNNING"
        rising = self.target_rising(now, target)
        if heating and rising:
            self.heat_time += dt
        if heating:
            self.hist.append((now, temp))
            self.hist = [(t, c) for t, c in self.hist if now - t <= 60]
        else:
            self.hist = []
        self.samples.append((now, temp, heat))
        self.samples = [s for s in self.samples if now - s[0] <= STALL_WINDOW_S + 30]

        self.ceiling_n = self.ceiling_n + 1 if temp >= ABS_CEILING else 0
        if self.ceiling_n >= CONSECUTIVE:
            return "absolute ceiling %.0f C reached" % ABS_CEILING
        self.peak_n = self.peak_n + 1 if temp > self.peak + OVER_PEAK_MARGIN else 0
        if self.peak_n >= CONSECUTIVE:
            return "%.1f C is more than %.0f C above the profile's highest point (%.0f C)" % (temp, OVER_PEAK_MARGIN, self.peak)
        if running and heating and temp > target + OVER_TARGET_HEATER_ON:
            self.over_heat_since = now if self.over_heat_since is None else self.over_heat_since
            if now - self.over_heat_since > OVER_TARGET_HEATER_ON_S:
                return "heater on at %.1f C, more than %.0f C above the %.1f C target, for %.0f s" % (temp, OVER_TARGET_HEATER_ON, target, now - self.over_heat_since)
        else:
            self.over_heat_since = None
        if running and temp > target + OVER_TARGET_ANY:
            self.over_any_since = now if self.over_any_since is None else self.over_any_since
            if now - self.over_any_since > OVER_TARGET_ANY_S:
                return "%.1f C is more than %.0f C above the %.1f C target for %.0f s" % (temp, OVER_TARGET_ANY, target, now - self.over_any_since)
        else:
            self.over_any_since = None
        if running and temp < target - LAG_BELOW_TARGET:
            self.lag_since = now if self.lag_since is None else self.lag_since
            if now - self.lag_since > LAG_BELOW_TARGET_S:
                return "%.1f C is more than %.0f C below the %.1f C target for %.0f s: not following the profile" % (temp, LAG_BELOW_TARGET, target, now - self.lag_since)
        else:
            self.lag_since = None
        if heating and len(self.hist) >= 6 and self.hist[-1][0] - self.hist[0][0] >= 30:
            rate = (self.hist[-1][1] - self.hist[0][1]) / ((self.hist[-1][0] - self.hist[0][0]) / 60)
            if rate > MAX_RISE_PER_MIN:
                return "rising too fast while heating: %.1f C/min" % rate
        if self.heat_time > NO_RISE_AFTER_HEATING_S and temp - self.start_temp < NO_RISE_MIN_C:
            return "no temperature rise after %.0f s of heating (%.2f C)" % (self.heat_time, temp - self.start_temp)
        # the kiln cannot reach the target: long stretch at near full power with no gain, still below target
        if running and self.samples and now - self.samples[0][0] >= STALL_WINDOW_S * 0.95 and temp < target - STALL_MIN_BELOW_TARGET:
            mean_heat = sum(s[2] for s in self.samples) / len(self.samples)
            if mean_heat >= STALL_MIN_HEAT and temp - self.samples[0][1] < STALL_MIN_GAIN_C:
                return "the kiln cannot reach the target: %.1f C vs %.1f C after %.0f min at %.0f%% output with a gain of %.1f C" % (temp, target, (now - self.samples[0][0]) / 60, mean_heat * 100, temp - self.samples[0][1])
        # unexplained heating: controller says the heater is off, the kiln keeps getting hotter
        if heat <= 0.001:
            self.zero_heat_since = now if self.zero_heat_since is None else self.zero_heat_since
            if now - self.zero_heat_since >= STUCK_ZERO_HEAT_S:
                past = [s for s in self.samples if now - s[0] >= STUCK_RISE_WINDOW_S]
                if past and temp - past[-1][1] >= STUCK_RISE_C:
                    return "heater may be stuck on: output 0 for %.0f min yet the temperature rose %.1f C in the last %.0f min (now %.1f C)" % ((now - self.zero_heat_since) / 60, temp - past[-1][1], STUCK_RISE_WINDOW_S / 60, temp)
        else:
            self.zero_heat_since = None
        if elapsed > self.total_s + self.extra_s:
            return "run has lasted %.0f min, %.0f min past the profile's length" % (elapsed / 60, self.extra_s / 60)
        return None


def reconnect(connect, recv, giveup_s, sleep=time.sleep, clock=time.time):
    """try to get the status feed back; returns (ws, first_message) or (None, None) after giveup_s"""
    t0 = clock()
    while clock() - t0 < giveup_s:
        try:
            ws = connect()
            m = recv(ws)
            if m:
                return ws, m
        except Exception:
            pass
        sleep(5)
    return None, None


def selftest():
    def run(peak, total_s, steps, extra_s=1800.0):
        r = Rules(peak, total_s, extra_s)
        for now, temp, target, heat, state in steps:
            why = r.check(now, {"temperature": temp, "target": target, "heat": heat * HEAT_CYCLE_S, "state": state}, now)
            if why:
                return why
        return None

    def ramp(n, t0=0, c0=22.0, per_step=0.17, lag=0.0, heat=1):     # about 5 C/min at 2 s steps would be 0.17 C per step
        return [(t0 + i * 2, c0 + i * per_step, c0 + i * per_step + lag, heat, "RUNNING") for i in range(n)]

    quiet, trip = 0, 0

    def must_be_quiet(why, what):
        nonlocal quiet
        assert why is None, "%s must not trip, but: %s" % (what, why)
        quiet += 1

    def must_trip(why, needle, what):
        nonlocal trip
        assert why and needle in why, "%s must trip with %r, got %r" % (what, needle, why)
        trip += 1

    must_be_quiet(run(150, 5280, ramp(400)), "a healthy ramp")
    hold = [(i * 2, 150.0, 150.0, 1 if i % 3 == 0 else 0, "RUNNING") for i in range(300)]
    must_be_quiet(run(150, 5280, hold), "a healthy hold")
    swing = [(i * 2, 150.0 + (4 if i % 20 < 10 else -4), 150.0, 0, "RUNNING") for i in range(300)]
    must_be_quiet(run(150, 5280, swing), "small swings around the target")
    over_heat = [(i * 2, 165.0, 150.0, 1, "RUNNING") for i in range(20)]
    must_trip(run(150, 5280, over_heat), "heater on", "heater on well above target")
    over_any = [(i * 10, 178.0, 150.0, 0, "RUNNING") for i in range(20)]
    must_trip(run(200, 5280, over_any), "above the", "sustained overshoot")
    lag = [(i * 30, 60.0, 150.0, 1, "RUNNING") for i in range(30)]
    must_trip(run(150, 5280, lag), "not following", "sustained lag")
    fast = [(i * 2, 22 + i * 1.2, 150.0, 1, "RUNNING") for i in range(60)]
    must_trip(run(300, 5280, fast), "too fast", "runaway rate")
    stuck = [(i * 2, 22.0, 22.0 + i * 0.05, 1, "RUNNING") for i in range(400)]   # target rising, reading dead flat
    must_trip(run(150, 5280, stuck), "no temperature rise", "no rise while the target rises")
    stuck_far = [(i * 2, 22.0, 150.0, 1, "RUNNING") for i in range(400)]        # far below target: the lag rule catches it first
    must_trip(run(150, 5280, stuck_far), "not following", "a stuck reading far below target")
    peak_over = [(0, 190.0, 150.0, 0, "RUNNING"), (2, 191.0, 150.0, 0, "RUNNING")]
    must_trip(run(150, 5280, peak_over), "highest point", "above the profile's peak plus margin, twice")
    must_be_quiet(run(150, 5280, [(0, 190.0, 150.0, 0, "RUNNING"), (2, 150.0, 150.0, 0, "RUNNING")]), "one bad high reading")
    must_trip(run(1000, 5280, [(0, 1051.0, 900.0, 0, "IDLE"), (2, 1052.0, 900.0, 0, "IDLE")]), "ceiling", "absolute ceiling twice")
    must_be_quiet(run(1000, 5280, [(0, 1051.0, 900.0, 0, "IDLE"), (2, 900.0, 900.0, 0, "IDLE")]), "one reading over the ceiling")
    too_long = [(0, 22.0, 22.0, 0, "RUNNING"), (5280 + 1900, 22.0, 22.0, 0, "RUNNING")]
    must_trip(run(150, 5280, too_long), "lasted", "overrunning the profile length")
    must_be_quiet(run(150, 5280, too_long, extra_s=7200.0), "overrunning with a long allowance")
    cooling = [(i * 10, 150.0 - i * 0.5, 0, 0, "IDLE") for i in range(100)]
    must_be_quiet(run(150, 5280, cooling), "a normal cool-down after the run")
    # a controlled cool-down: target falling, heater cycling to slow it, reading falling 1 C/min for 3 hours
    cool_run = [(i * 10, 900.0 - i * (10 / 60), 900.0 - i * (10 / 60) + 1.0, 0.3 if i % 2 else 0.0, "RUNNING") for i in range(1080)]
    must_be_quiet(run(1000, 70000, cool_run, extra_s=7200.0), "a controlled cool-down with the heater on while the temperature falls")
    # at the top of the ramp: heater off for 4 minutes while the kiln coasts up 20 C and then falls
    coast = [(i * 2, 1000.0 + (min(i, 60) * 0.33 if i <= 60 else 20.0 - (i - 60) * 0.1), 1000.0, 0 if i > 5 else 0.5, "RUNNING") for i in range(240)]
    must_be_quiet(run(1000, 70000, coast), "a short coast after the heater goes off")
    stall = [(i * 10, 700.0 + i * 0.001, 720.0, 1.0, "RUNNING") for i in range(150)]   # 25 min at full power, flat, 20 C below
    must_trip(run(1000, 70000, stall, extra_s=7200.0), "cannot reach", "full power with no gain, below target")
    slow_climb = [(i * 10, 700.0 + i * 0.2, 706.0 + i * 0.2, 1.0, "RUNNING") for i in range(150)]   # gaining 1.2 C/min at full power, 6 C behind (the controller holds the schedule near the kiln): slow but fine
    must_be_quiet(run(1000, 70000, slow_climb, extra_s=7200.0), "a slow climb at full power")
    stuck_on = [(i * 5, 300.0 + i * (1 / 12), 290.0, 0.0, "RUNNING") for i in range(400)]   # heater reported off, +1 C/min for 33 min
    must_trip(run(1000, 70000, stuck_on, extra_s=7200.0), "stuck on", "temperature rising with the heater reported off")
    stuck_idle = [(i * 5, 300.0 + i * (1 / 12), 0, 0.0, "IDLE") for i in range(400)]
    must_trip(run(1000, 70000, stuck_idle, extra_s=7200.0), "stuck on", "temperature rising while the controller is idle")
    # feed recovery logic with a fake clock
    clock = {"t": 0.0}
    calls = {"n": 0}

    def connect():
        calls["n"] += 1
        if calls["n"] < 3:
            raise OSError("down")
        return "ws"

    ws, m = reconnect(connect, lambda w: {"temperature": 1}, 300, sleep=lambda s: clock.__setitem__("t", clock["t"] + s), clock=lambda: clock["t"])
    assert ws == "ws" and m, "reconnect must succeed once the feed is back"
    quiet += 1
    clock["t"] = 0.0
    ws, m = reconnect(lambda: (_ for _ in ()).throw(OSError("down")), lambda w: None, 60, sleep=lambda s: clock.__setitem__("t", clock["t"] + s), clock=lambda: clock["t"])
    assert ws is None and m is None and clock["t"] >= 60, "reconnect must give up after the limit"
    trip += 1
    print("SELFTEST PASSED: %d cases (%d must stay quiet or recover, %d must trip or give up)" % (quiet + trip, quiet, trip))


def api(path, body, timeout=15):
    r = urllib.request.Request("http://%s%s" % (HOST, path), json.dumps(body).encode(), {"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r, timeout=timeout).read().decode())


def recv_state(ws, timeout):
    end = time.time() + timeout
    while time.time() < end:
        ws.settimeout(max(1, end - time.time()))
        try:
            m = json.loads(ws.recv())
        except websocket.WebSocketTimeoutException:
            return None
        if "temperature" in m and "state" in m:
            return m
    return None


def send_stop(why):
    print(">>> STOP: %s" % why, flush=True)
    for i in range(1, 6):
        try:
            print("    stop reply:", api("/api", {"cmd": "stop"}), flush=True)
            return True
        except Exception as e:
            print("    stop attempt %d failed: %s" % (i, e), flush=True)
            time.sleep(2)
    print("    !!! COULD NOT SEND STOP. CUT THE POWER AT THE WALL !!!", flush=True)
    return False


def arg(a, name, default=None):
    return a[a.index(name) + 1] if name in a else default


def main():
    a = sys.argv[1:]
    if "--selftest" in a:
        return selftest()
    name = arg(a, "--profile")
    peak, total_s = arg(a, "--peak"), None
    pfile = arg(a, "--profile-file")
    if pfile:
        data = json.load(open(pfile))["data"]
        peak = max(p[1] for p in data)
        total_s = data[-1][0]
        print("profile file: peak %.0f C, length %.1f h (%d points)" % (peak, total_s / 3600, len(data)), flush=True)
    if not name or peak is None:
        print(__doc__); return 2
    peak = float(peak)
    if total_s is None:
        total_s = float(arg(a, "--total-min", "90")) * 60
    extra_s = float(arg(a, "--extra-min", "30")) * 60
    feed_lost_s = float(arg(a, "--feed-lost-min", "5")) * 60
    max_start = float(arg(a, "--max-start", "45"))
    after_s = float(arg(a, "--after-min", "10")) * 60
    go = "--go" in a
    if "--ceiling" in a:
        globals()["ABS_CEILING"] = float(arg(a, "--ceiling"))
    os.makedirs(OUTDIR, exist_ok=True)
    path = "%s/profile-%s-%s.csv" % (OUTDIR, name, time.strftime("%Y%m%d-%H%M%S"))
    f = open(path, "w", newline=""); w = csv.writer(f)
    w.writerow(["wall_time", "elapsed_s", "oven_state", "temperature_c", "target_c", "heat", "runtime_s", "totaltime_s", "temp_errors", "note"]); f.flush()
    print("logging to", path, flush=True)

    def connect():
        return websocket.create_connection("ws://%s/status" % HOST, timeout=15)

    ws = connect()
    first = recv_state(ws, 20)
    if not first:
        print("no status from the controller"); return 2
    print("before: state=%s temp=%.2f heat=%s profile=%s" % (first["state"], first["temperature"], first.get("heat"), first.get("profile")), flush=True)
    clean = first["state"] == "IDLE" and not (first.get("heat") or 0) and 5 <= first["temperature"] <= max_start
    attach = "--attach" in a     # watch a run somebody else starts (within 3 min); never starts or needs a cold kiln
    if attach:
        print("ATTACH mode: this monitor starts nothing; it supervises a run started separately within 3 min.", flush=True)
    if not go and not attach:
        print("DRY RUN (no --go): would start profile %r (peak %.0f C, %.0f min, +%.0f min allowed) if the kiln is idle, heat 0, 5-%g C." % (name, peak, total_s / 60, extra_s / 60, max_start))
        print("clean start condition met now: %s. NOTHING WAS STARTED." % clean)
        return 0
    if not attach and not clean:
        print("not a clean idle start (need IDLE, heat 0, 5-%g C): NOT starting" % max_start); return 2

    rules = Rules(peak, total_s, extra_s)
    t0 = time.time()
    if not attach:
        print("starting profile %r ..." % name, flush=True)
        reply = api("/api", {"cmd": "run", "profile": name})
        print("start reply:", reply, flush=True)
        if not reply.get("success"):
            return 3
    outcome, stopped, last_print, was_running, idle_since = "unknown", False, 0, False, None
    try:
        while True:
            m = recv_state(ws, FEED_SILENT_S)
            note = ""
            if m is None:
                print("!!! status feed silent for %.0f s: trying to reconnect for up to %.0f min" % (FEED_SILENT_S, feed_lost_s / 60), flush=True)
                try:
                    ws.close()
                except Exception:
                    pass
                ws, m = reconnect(connect, lambda x: recv_state(x, 15), feed_lost_s)
                if m is None:
                    outcome = "status feed lost for %.0f min: supervision lost" % (feed_lost_s / 60); break
                note = "feed recovered"
                print("feed recovered", flush=True)
            now = time.time()
            w.writerow([time.strftime("%H:%M:%S"), round(now - t0, 1), m["state"], m["temperature"], m.get("target"), m.get("heat"), m.get("runtime"), m.get("totaltime"), m.get("temp_errors"), note]); f.flush()
            if now - last_print >= 30:
                last_print = now
                print("%6.0fs  %-8s temp=%7.2f target=%7.2f heat=%s runtime=%s" % (now - t0, m["state"], m["temperature"], m.get("target") or 0, m.get("heat"), m.get("runtime")), flush=True)
            if m["state"] == "RUNNING":
                was_running = True
            why = rules.check(now, m, now - t0)
            if why:
                outcome = "RULE TRIPPED: " + why; send_stop(why); stopped = True
                if "stuck on" in why:
                    print("    !!! A STOP CANNOT OPEN A WELDED RELAY. CUT THE POWER AT THE WALL !!!", flush=True)
                break
            if was_running and m["state"] == "IDLE" and not (m.get("heat") or 0):
                idle_since = idle_since or now
                if now - idle_since > after_s:
                    outcome = "profile finished; logged %.0f min of cool-down" % (after_s / 60); break
            elif not was_running and now - t0 > (180 if attach else 60):
                outcome = "the run never entered RUNNING within 60 s"; break
    except KeyboardInterrupt:
        outcome = "interrupted by the operator"
    finally:
        if outcome.startswith(("status feed lost", "interrupted", "the run never")) and not stopped:
            send_stop(outcome); stopped = True
        end = time.time() + 40
        while stopped and time.time() < end:
            try:
                m = recv_state(ws, 10)
            except Exception:
                m = None
            if m:
                print("after: state=%s temp=%.2f heat=%s" % (m["state"], m["temperature"], m.get("heat")), flush=True)
                w.writerow([time.strftime("%H:%M:%S"), round(time.time() - t0, 1), m["state"], m["temperature"], m.get("target"), m.get("heat"), m.get("runtime"), m.get("totaltime"), m.get("temp_errors"), "after stop"]); f.flush()
                if m["state"] == "IDLE" and not (m.get("heat") or 0):
                    break
        f.close()
        print("RESULT:", outcome, "| log:", path, flush=True)
    return 0 if outcome.startswith("profile finished") else 1


if __name__ == "__main__":
    sys.exit(main())
