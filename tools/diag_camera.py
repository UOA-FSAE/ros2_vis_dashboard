"""Standalone camera-feed diagnostic for the FSAE dashboard.

Connects to a rosbridge server the same way the dashboard does and reports,
layer by layer, WHERE the camera feed breaks — so you can tell whether the
problem is on the Jetson (ROS/bridge) side or on the dashboard side.

It intentionally does NOT import the dashboard code: it reproduces the exact
subscription the app makes (compression="none", 3-part type, image throttle)
using only roslibpy, so a failure here is a bridge/ROS problem, and success
here points the finger at the app.

Usage (from the repo root):

    # direct connection
    python tools/diag_camera.py --host 192.168.1.10 --port 9090

    # over an SSH tunnel you already opened (ssh -L 9090:localhost:9090 jetson@ip)
    python tools/diag_camera.py --host 127.0.0.1 --port 9090

    # inspect a specific topic instead of auto-picking the first image topic
    python tools/diag_camera.py --host 192.168.1.10 --topic /camera/image_raw

Exit code is 0 only if at least one image frame was received and decoded.
"""
from __future__ import annotations

import argparse
import base64
import sys
import time

try:
    import roslibpy
except ImportError:
    print("FAIL: roslibpy is not installed in this environment. Run: pip install roslibpy")
    sys.exit(2)


def _normalise_type(msg_type: str) -> str:
    if not msg_type or "/" not in msg_type:
        return msg_type
    parts = msg_type.split("/")
    return f"{parts[0]}/msg/{parts[1]}" if len(parts) == 2 else msg_type


def _as_bytes(data) -> bytes:
    if isinstance(data, (bytes, bytearray)):
        return bytes(data)
    if isinstance(data, str):
        try:
            return base64.b64decode(data)
        except Exception:
            return b""
    if isinstance(data, list):
        return bytes(b & 0xFF for b in data)
    return b""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="127.0.0.1", help="rosbridge host (default 127.0.0.1)")
    ap.add_argument("--port", type=int, default=9090, help="rosbridge port (default 9090)")
    ap.add_argument("--topic", default="", help="image topic; default = auto-pick first Image topic")
    ap.add_argument("--seconds", type=float, default=8.0, help="how long to wait for frames")
    ap.add_argument("--throttle", type=int, default=500, help="throttle_rate ms, matches the app (500)")
    args = ap.parse_args()

    print(f"[1/5] Connecting to rosbridge at ws://{args.host}:{args.port} ...")
    client = roslibpy.Ros(host=args.host, port=args.port)
    try:
        client.run(timeout=10)
    except Exception as exc:
        print(f"  FAIL: could not connect: {exc}")
        print("  -> Bridge not reachable. Check: bridge running on the Jetson (port 9090),")
        print("     network/firewall, or that your SSH tunnel is up. This is JETSON/NETWORK side.")
        return 2
    if not client.is_connected:
        print("  FAIL: client.run() returned but is_connected is False.")
        return 2
    print("  OK: connected.")

    print("[2/5] Listing topics via /rosapi/topics ...")
    svc = roslibpy.Service(client, "/rosapi/topics", "rosapi/Topics")
    try:
        result = svc.call(roslibpy.ServiceRequest(), timeout=5)
    except Exception as exc:
        print(f"  FAIL: rosapi topics service call failed: {exc}")
        print("  -> rosapi isn't running. Launch rosbridge via rosbridge_websocket_launch.xml")
        print("     (it starts rosapi too). This is JETSON side.")
        client.terminate()
        return 2
    names = result.get("topics", [])
    types = result.get("types", [])
    tmap = dict(zip(names, types)) if len(types) == len(names) else {n: "" for n in names}
    image_topics = [(n, t) for n, t in tmap.items() if "Image" in t]
    print(f"  OK: {len(names)} topics total; {len(image_topics)} Image topic(s):")
    for n, t in image_topics:
        print(f"       {n}   [{t}]")
    if not image_topics and not args.topic:
        print("  FAIL: no sensor_msgs/Image or CompressedImage topics are being published.")
        print("  -> The camera node isn't publishing (or the bridge shell wasn't sourced with")
        print("     the camera's workspace). This is JETSON side. `ros2 topic list` on the")
        print("     Jetson to confirm the camera topic exists.")
        client.terminate()
        return 2

    topic = args.topic or image_topics[0][0]
    msg_type = _normalise_type(tmap.get(topic, "sensor_msgs/Image"))
    print(f"[3/5] Subscribing to {topic}  (type {msg_type}, compression=none, throttle={args.throttle}ms)")
    print(f"      Reproducing the app's exact subscription. Waiting {args.seconds:.0f}s for frames ...")

    state = {"count": 0, "first": None, "last_msg": None, "bytes": 0}

    def on_msg(msg):
        state["count"] += 1
        if state["first"] is None:
            state["first"] = time.time()
        state["last_msg"] = msg

    sub = roslibpy.Topic(
        client, topic, msg_type,
        throttle_rate=args.throttle, queue_length=1, compression="none",
    )
    sub.subscribe(on_msg)

    t_end = time.time() + args.seconds
    while time.time() < t_end:
        time.sleep(0.2)
    sub.unsubscribe()

    n = state["count"]
    print(f"  frames received in {args.seconds:.0f}s: {n}")
    if n == 0:
        print("  FAIL: subscription registered but ZERO frames arrived.")
        print("  Likely causes (in order):")
        print("   a) Topic publishes but the bridge shell lacks the message type -> serialisation")
        print("      fails silently. Re-launch rosbridge from a shell with the workspace sourced.")
        print("   b) QoS mismatch on a BEST_EFFORT/high-rate topic (rare for cameras).")
        print("   c) Raw Image is so large the bridge can't JSON-serialise it in time -> use the")
        print("      /compressed topic instead (see [5/5]).")
        print("  This is JETSON/BRIDGE side (the app would also see nothing).")
        client.terminate()
        return 1

    msg = state["last_msg"]
    keys = sorted(msg.keys())
    print(f"[4/5] Inspecting a frame. Message keys: {keys}")
    raw = msg.get("data")
    data = _as_bytes(raw)
    print(f"      data field python type: {type(raw).__name__}; decoded bytes: {len(data)}")

    is_compressed = "format" in msg
    if is_compressed:
        print(f"      -> CompressedImage, format={msg.get('format')!r} (this decodes efficiently)")
        ok = len(data) > 0
    else:
        w = int(msg.get("width", 0)); h = int(msg.get("height", 0))
        enc = msg.get("encoding", "?"); step = int(msg.get("step", 0))
        print(f"      -> raw Image {w}x{h} encoding={enc!r} step={step} "
              f"expected_bytes={w*h*(3 if enc in ('rgb8','bgr8') else 1)}")
        ok = w > 0 and h > 0 and len(data) >= w * h
        approx_json = int(len(data) * 4 / 3)
        print(f"      NOTE: over rosbridge JSON this frame is ~{approx_json/1e6:.1f} MB of base64 text")
        if approx_json > 1_000_000:
            print("      WARNING: that is huge for a WebSocket at 2 fps. Prefer a CompressedImage topic.")

    if not ok:
        print("  FAIL: frame arrived but payload looks malformed (empty/short data or bad dims).")
        print("  This points at the encoding/serialisation. Check the encoding is one the app")
        print("  handles: rgb8, bgr8, mono8/8UC1 (raw) or jpeg/png (compressed).")
        client.terminate()
        return 1

    dt = time.time() - state["first"] if state["first"] else args.seconds
    fps = (n - 1) / dt if dt > 0 and n > 1 else n / args.seconds
    print(f"[5/5] SUCCESS: bridge delivers decodable frames (~{fps:.1f} fps at the app's throttle).")
    print("      => The Jetson/bridge side is healthy. If the dashboard shows nothing, the")
    print("         problem is on the APP side (topic not selected in the panel, panel not")
    print("         subscribed, or a decode/display bug). See notes printed above.")
    if not is_compressed:
        print("      TIP: even though it works, raw Image is heavy. On the Jetson run e.g.")
        print("         ros2 run image_transport republish raw compressed \\")
        print(f"            --ros-args -r in:={topic} -r out/compressed:={topic}/compressed")
        print("      then pick the /compressed topic in the Camera panel for a smooth feed.")
    client.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
