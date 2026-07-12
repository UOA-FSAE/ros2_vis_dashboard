# FSAE Telemetry Dashboard

A configurable, dockable real-time visualisation app for the UOA-FSAE autonomous
ROS 2 stack. Replaces one-topic-at-a-time `ros2 topic echo` with a single
dashboard of live plots and visualisation panels. You compose your own windows
(a plot here, the track view there, CAN console at the bottom), then save the
whole layout as a YAML config file and reopen it later.

Connects to the car over a **rosbridge / foxglove WebSocket bridge**, optionally
through an **SSH tunnel** — so it runs on Ubuntu, Windows and macOS with **no
local ROS install required**. A built-in **simulator** lets you run the entire UI
with zero infrastructure.

See [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) for the design rationale and
the full topic inventory it was built from.

---

## Quick start

```bash
# from this directory
python -m venv .venv
# Windows:  .venv\Scripts\activate      Linux/macOS:  source .venv/bin/activate
pip install -r requirements.txt

# run against the built-in simulator (no car needed)
python -m fsae_dashboard --mock
```

You should see a track view, a live speed/steering plot, stat tiles and a topic
browser, all updating. Use **Add Panel** to add more, drag panels around to dock
/ tab / tear them off, then **Layout → Save** to persist.

## Connecting to the car

On the **Jetson**, run a rosbridge-compatible bridge (either works):

```bash
# rosbridge
ros2 launch rosbridge_server rosbridge_websocket_launch.xml       # port 9090
# or the foxglove bridge already vendored in the autonomous repo
ros2 run foxglove_bridge foxglove_bridge                          # port 8765
```

Then in the app: **Connection → Connect…**

- **Data source:** `rosbridge`
- **Bridge host/port:** the Jetson's IP and bridge port (e.g. `9090`)
- **SSH tunnel** (recommended over Wi-Fi): tick it, enter the Jetson's SSH
  host/user/password-or-key. The data plane then connects to a forwarded
  `localhost` port — encrypted, and immune to DDS discovery / multicast issues.
  Tick *Launch bridge on connect* to start rosbridge remotely over SSH.

Topic names are **discovered live** (Topic Browser → *Refresh topics*) rather
than hard-coded, so the current `zed/` topic-prefix bug in the stack is visible
immediately instead of silently breaking a panel.

## Panels

| Panel | What it shows |
|-------|---------------|
| **Time Series Plot** | Any numeric field(s) from any topic vs. time. `+ Field` to pick a topic + field. |
| **Track View (2D)** | Cones, left/right boundaries, planned trajectory, car pose + trail. The RViz replacement. |
| **Camera** | `sensor_msgs/Image` or `CompressedImage`, with fps. |
| **Stat Tiles** | Big at-a-glance values (AS state, battery voltage, …). |
| **Raw Message Inspector** | Pretty-printed latest message of any topic — the `topic echo` replacement. |
| **Topic Browser** | Live topic list with type + Hz; tick to subscribe. |
| **CAN Console** | Decoded `CANStamped` frames, filterable by ID. |

## Saving / loading layouts

**Layout → Save As…** writes a YAML file describing every panel (type + its
config) plus the exact dock geometry. **Layout → Open…** restores it. Load one at
startup with `--config path/to/layout.yaml`. The files are plain YAML — diffable
and hand-editable — so you can keep e.g. `perception_debug.yaml` and
`race_day.yaml` in version control.

## Architecture

```
transport/   data source behind one interface: mock | rosbridge (roslibpy)
data/        thread-safe ring-buffer hub + ref-counted subscription manager
ssh/         asyncssh tunnel + remote bridge launch (optional)
ui/          QMainWindow docking, dialogs, and the panels/ registry
```

- Transport callbacks (background thread) write to the **DataHub**; panels read
  snapshots on a single **30 Hz QTimer**. Message rate is decoupled from paint
  rate, which is what keeps it smooth with CAN at 100 Hz.
- Messages arrive as plain dicts (CBOR over the bridge) — no `.msg` compilation,
  so custom `fsae_interfaces` types just work on any OS.

### Adding a new panel type

Subclass `Panel`, implement `build_ui` / `get_config` / `apply_config` /
`on_tick`, decorate with `@register_panel`, and import it in
`ui/panels/__init__.py`. It appears in the **Add Panel** menu automatically.

## Dependencies

PySide6, pyqtgraph, numpy, roslibpy (data plane), asyncssh (tunnelling),
PyYAML + platformdirs (config). See `requirements.txt`.
