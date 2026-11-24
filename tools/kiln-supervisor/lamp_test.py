#!/usr/bin/env python3
"""Lamp test: run a profile for a fixed time against a LAMP load, then always Stop.
Real hardware, but the load is a lamp. Needs --go to start anything."""
import json, sys, time, urllib.request
import websocket

HOST = __import__("os").environ.get("KILN_HOST", "192.168.1.125:8081")
PROFILE = "test-150c"
DURATION = 120


def api(cmd, **kw):
    body = json.dumps(dict(cmd=cmd, **kw)).encode()
    req = urllib.request.Request(f"http://{HOST}/api", body, {"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=8).read())


def state(ws, tries=6):
    for _ in range(tries):
        m = json.loads(ws.recv())
        if "state" in m:
            return m
    return None


def stop_hard():
    for i in range(5):
        try:
            print("  stop ->", api("stop"), flush=True)
            return True
        except Exception as e:
            print(f"  stop attempt {i+1} failed: {e}", flush=True)
            time.sleep(1)
    print("!!! COULD NOT SEND STOP. UNPLUG THE LAMP'S MAINS NOW !!!", flush=True)
    return False


def main():
    go = "--go" in sys.argv
    ws = websocket.create_connection(f"ws://{HOST}/status", timeout=10)
    s = state(ws)
    print("before:", {k: s.get(k) for k in ("state", "temperature", "heat")}, flush=True)
    if s["state"] != "IDLE" or s["heat"] != 0:
        print("not idle with heat 0 - refusing to start"); return 2
    if not go:
        print("dry run only (no --go): nothing started"); return 0
    started = False
    try:
        print("starting", PROFILE, "->", api("run", profile=PROFILE), flush=True)
        started = True
        t0 = time.time(); last = 0
        heats = []
        while time.time() - t0 < DURATION:
            m = state(ws)
            if m is None:
                print("no status for a while - stopping"); break
            heats.append(m["heat"])
            if time.time() - last >= 6:
                last = time.time()
                print(f"  {time.time()-t0:5.0f}s state={m['state']} temp={m['temperature']:.2f} "
                      f"target={m['target']:.2f} heat={m['heat']:.3f} errs={m.get('temp_errors')}", flush=True)
            if m["state"] != "RUNNING":
                print("state is", m["state"], "- ending early"); break
    except KeyboardInterrupt:
        print("interrupted")
    finally:
        if started:
            stop_hard()
            time.sleep(4)
            try:
                s = state(websocket.create_connection(f"ws://{HOST}/status", timeout=10))
                print("after:", {k: s.get(k) for k in ("state", "temperature", "heat")}, flush=True)
            except Exception as e:
                print("could not read final state:", e)
    if heats:
        print(f"heat samples: n={len(heats)} min={min(heats):.3f} max={max(heats):.3f} mean={sum(heats)/len(heats):.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
