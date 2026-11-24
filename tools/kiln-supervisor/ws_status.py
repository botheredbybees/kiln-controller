import json, sys, time
import websocket   # websocket-client, already in the project's venv
ws = websocket.create_connection("ws://%s/status" % __import__("os").environ.get("KILN_HOST", "kiln-rpi.local:8081"), timeout=15)
print("connected to the controller's /status feed (read-only)")
seen = 0
t0 = time.time()
while seen < 3 and time.time() - t0 < 25:
    msg = json.loads(ws.recv())
    seen += 1
    if msg.get("type") == "backlog":
        print("backlog: profile=%s, logged states=%d" % ((msg.get("profile") or {}).get("name"), len(msg.get("log", []))))
        continue
    keep = ("state", "temperature", "target", "heat", "cool", "air", "runtime", "totaltime", "door", "pidstats")
    print("state message:", {k: (round(v, 2) if isinstance(v, float) else v) for k, v in msg.items() if k in keep and k != "pidstats"})
ws.close()
