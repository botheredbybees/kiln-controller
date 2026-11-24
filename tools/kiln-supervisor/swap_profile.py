"""Replace the running profile with another one:  python swap_profile.py PROFILE_NAME

The controller only starts a profile from IDLE, so this sends Stop, waits for IDLE, then runs PROFILE_NAME
(which must already be in the Pi's storage/profiles). The heater is off for about two seconds and the PID's
integral term is reset, so the kiln dips a few degrees before it recovers. Use it only with someone at the kiln.
Run profile_monitor.py in --attach mode (or keep an existing monitor running) so something supervises the run.
"""
import json, os, sys, time, urllib.request
import websocket

HOST = os.environ.get("KILN_HOST", "192.168.1.125:8081")


def api(cmd, **kw):
    req = urllib.request.Request("http://%s/api" % HOST, data=json.dumps(dict(cmd=cmd, **kw)).encode(), headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=10))


def status(tries=12):
    ws = websocket.create_connection("ws://%s/status" % HOST, timeout=10)
    try:
        for _ in range(tries):
            m = json.loads(ws.recv())
            if "state" in m and "temperature" in m:
                return m
    finally:
        ws.close()


def main():
    if len(sys.argv) != 2:
        print(__doc__); return 2
    name = sys.argv[1]
    m = status()
    print("before: %s temp=%.1f target=%.1f heat=%s" % (m["state"], m["temperature"], m.get("target") or 0, m.get("heat")), flush=True)
    if m["state"] != "RUNNING":
        print("not running: use start_profile.py instead"); return 2
    print("stop:", api("stop"), flush=True)
    for i in range(20):
        time.sleep(1)
        m = status()
        if m["state"] == "IDLE":
            break
    print("after stop: %s temp=%.1f heat=%s" % (m["state"], m["temperature"], m.get("heat")), flush=True)
    if m["state"] != "IDLE":
        print("the controller did not go IDLE: NOT starting"); return 1
    for _ in range(3):
        r = api("run", profile=name); print("run:", r, flush=True)
        if r.get("success"):
            break
        time.sleep(2)
    time.sleep(4)
    m = status()
    print("now: %s temp=%.1f target=%.1f heat=%s runtime=%s total=%s" % (m["state"], m["temperature"], m.get("target") or 0, m.get("heat"), m.get("runtime"), m.get("totaltime")))
    return 0 if m["state"] == "RUNNING" else 1


if __name__ == "__main__":
    sys.exit(main())
