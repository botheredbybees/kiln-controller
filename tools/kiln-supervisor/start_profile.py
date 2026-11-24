"""Start a profile on an IDLE controller:  python start_profile.py PROFILE_NAME

Refuses unless the controller is IDLE. Start profile_monitor.py --attach first so the run is supervised.
With seek_start on, a run begins at the point on the ramp that matches the kiln's current temperature
(only on rising segments: a profile that only descends starts at its first point).
"""
import json, os, sys, time, urllib.request
import websocket

HOST = os.environ.get("KILN_HOST", "192.168.1.125:8081")


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
    m = status()
    print("before: %s temp=%.1f heat=%s" % (m["state"], m["temperature"], m.get("heat")), flush=True)
    if m["state"] != "IDLE":
        print("not IDLE: NOT starting"); return 2
    req = urllib.request.Request("http://%s/api" % HOST, data=json.dumps({"cmd": "run", "profile": sys.argv[1]}).encode(), headers={"Content-Type": "application/json"})
    r = json.load(urllib.request.urlopen(req, timeout=10)); print("run:", r, flush=True)
    time.sleep(4)
    m = status()
    print("now: %s temp=%.1f target=%.1f heat=%s runtime=%s" % (m["state"], m["temperature"], m.get("target") or 0, m.get("heat"), m.get("runtime")))
    return 0 if r.get("success") and m["state"] == "RUNNING" else 1


if __name__ == "__main__":
    sys.exit(main())
