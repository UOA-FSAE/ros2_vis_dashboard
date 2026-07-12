# ROS 2 Visualisation Dashboard — Implementation Plan

Real-time telemetry dashboard for the UOA-FSAE autonomous stack (Jetson Orin Nano,
ROS 2), replacing one-topic-at-a-time `ros2 topic echo` debugging with a single
configurable multi-panel UI.

Analysis based on `UOA-FSAE/autonomous`, branch `codebase_refactor`
(HEAD 2026-05-10, "cam seems to work, might stop and move to lidar").

---

## 1. Topic inventory (what the stack publishes)

The main pipeline (`gocart_autonomous.launch.py`) runs everything under the
**`/moa` namespace**: ZED perception → cone landmark mapper → fast-tube planner →
Stanley controller → CAN adapter.

### 1.1 Perception (ZED, custom SDK node — `zed_perception/zed_launch`)

The ZED node is a custom C++ node using the ZED SDK directly (node name
`cone_detection_node`, launched as `perception`), *not* the standard
`zed-ros2-wrapper`.

| Topic (resolved) | Type | Source | Dashboard panel |
|---|---|---|---|
| `/moa/zed/cone_detection` | `fsae_interfaces/Detections` (car pose + yellow/blue/orange `Point[]`) | zed_launch.hpp:61 | 2D track view (raw detections layer) |
| `/moa/zed/car_position` | `geometry_msgs/Pose` | zed_launch.hpp:62 | 2D track view (car marker) + XY trace |
| `/moa/zed/car_velocity` | `geometry_msgs/Vector3` | zed_launch.hpp:63 | Time-series plot |
| `/moa/zed/image` | `sensor_msgs/Image` (raw!) | zed_launch.hpp:64 | Camera panel (throttled/compressed) |

> ⚠️ **Topic-name mismatch on this branch.** The ZED node publishes with a
> `zed/` prefix, but every downstream subscriber listens on the *unprefixed*
> names (`cone_detection`, `car_position`, `image` — e.g.
> `cone_landmark_mapper.cpp:32`, `fasttube_planner.py:27`,
> `ImageThrollerNode.py`), and `gocart_autonomous.launch.py` contains **no
> remappings**. Either a remap is applied somewhere not committed, or the
> pipeline is currently broken on this branch. The dashboard should not
> hard-code either name — topic selection must be discovery-driven (see §4.4),
> which also makes this bug immediately visible in the UI.

### 1.2 SLAM / mapping (`fsae_slam`)

| Topic | Type | Source | Panel |
|---|---|---|---|
| `/moa/left_track` | `fsae_interfaces/Track` (`Point[]`) | cone_landmark_mapper.cpp:38 (C++), reference_cone_landmark_mapper.py:72 (Python ref) | 2D track view (blue boundary) |
| `/moa/right_track` | `fsae_interfaces/Track` | cone_landmark_mapper.cpp:39 / .py:75 | 2D track view (yellow boundary) |
| `/moa/times_modified` | `std_msgs/Float32MultiArray` | reference_cone_landmark_mapper.py:78 | Heatmap / bar (Kalman update counts) |
| `/moa/cone_map` | `fsae_interfaces/ConeMap` | subscribed by planners (publisher lives in older/Python mapper) | 2D track view (fused map layer) |

### 1.3 Planning (`fsae_planning`)

| Topic | Type | Source | Panel |
|---|---|---|---|
| `/moa/selected_trajectory` | `geometry_msgs/PoseArray` | fasttube_planner.py:30, fasttube_without_kalman.py:30, centerline_planner.py:29, simple_centerline_planner.py:23 | 2D track view (planned path) |
| `/moa/trajectories`, `/moa/inbound_trajectories` | `fsae_interfaces/AllTrajectories` (`PoseArray[]`) | candidate sets consumed by visualisers | 2D track view (candidate fan, optional) |
| `/moa/best_trajectory_index`, `/moa/out_of_bounds` | `std_msgs/Int16`, `Int32MultiArray` | planner debug | Stat tiles |

### 1.4 Control (`fsae_control` — Stanley)

| Topic | Type | Source | Panel |
|---|---|---|---|
| `/moa/cmd_vel` | `ackermann_msgs/AckermannDriveStamped` | stanley_controller.py:101 (also teleop, mock_stimulus, trajectory_follower) | **Primary time-series: steering angle + speed vs t** |
| `/moa/drive` | `ackermann_msgs/AckermannDrive` | stanley_controller.py:99 | Time-series (redundant with cmd_vel) |
| `/moa/drive_vis` | `ackermann_msgs/AckermannDrive` | stanley_controller.py:100 | Steering-arc overlay on track view |
| `/moa/track_point` | `geometry_msgs/Pose` | stanley_controller.py:102 (declared, publish currently unused) | Target-point marker |

### 1.5 Go-kart hardware / CAN (`gocart_control`, `gocart_driver`)

| Topic | Type | Source | Panel |
|---|---|---|---|
| `/moa/pub_raw_can` | `fsae_interfaces/CANStamped` | ack_to_can.py:68 | Raw CAN console (hex table, filterable) |
| `/battery_state` | `sensor_msgs/BatteryState` | can_decoder_jnano.py:26 | Stat tile + voltage plot |
| `/glv_state` | `sensor_msgs/BatteryState` | can_decoder_jnano.py:29 | Stat tile |
| `/drive_status` | `AckermannStamped` (feedback from kart) | can_decoder_jnano.py:27 | Overlay commanded-vs-actual on the cmd_vel plot |
| `/moa/as_status` | `std_msgs/UInt8` | sys_status.py:20 | AS state indicator (large colour tile) |
| `mission_finished` (`Pulse`), `start/end_description` (`String`) | event controller template | Event log panel |

### 1.6 Existing visualisation topics (Foxglove-oriented — reuse!)

The team already publishes render-ready topics (`foxglove_msgs/SceneUpdate`,
`visualization_msgs/MarkerArray`) and vendors **foxglove_bridge** (WebSocket,
port 8765) in `third_party/`:

| Topic | Type | Source |
|---|---|---|
| `/moa/image_throttled` | `sensor_msgs/Image` (1-in-20 frames) | ImageThrollerNode.py |
| `visualization_marker_cones`, `visualization_marker_car`, `visualization_marker_detections` | `MarkerArray` | visualise_cone_map.py |
| `visualization_trajectories` | `foxglove_msgs/SceneUpdate` | visualise_trajectories*.py |
| `visualization_centerline` | `MarkerArray` | visualise_trajectories.py:30 |
| `control_visualization` | `foxglove_msgs/SceneUpdate` | visualise_pure_pursuit.py |
| `base_tf` | `TransformStamped` | base_tf.py |

### 1.7 Gaps to flag

- **No IMU topic exists anywhere in the stack.** The ZED 2i has a built-in
  IMU/baro/mag, but the custom SDK node never calls `getSensorsData()`. The
  "IMU panel at the bottom" requires a small addition to `zed_launch.cpp`
  publishing `sensor_msgs/Imu` at 100–200 Hz (or running `zed-ros2-wrapper`
  alongside, which publishes `~/imu/data`, `~/odom`, `~/pose`,
  `~/left/image_rect_color`, `~/point_cloud/cloud_registered`, etc.).
- **Raw `Image` only** — no `CompressedImage`/`image_transport` republisher.
  Raw 720p@30fps ≈ 80 MB/s; unusable over Wi-Fi. Plan: throttle + JPEG-compress
  on the Jetson (foxglove_bridge does this automatically for its clients).
- `/drive_status` gives commanded-vs-actual comparison — high debugging value,
  wire it into the same plot as `cmd_vel`.

---

## 2. Framework evaluation

### 2.1 Is CustomTkinter + Matplotlib sufficient? — **No.**

| Requirement | CustomTkinter + Matplotlib |
|---|---|
| Smooth real-time plots (10+ streams, 50–100 Hz) | ❌ Matplotlib manages ~5–20 redraws/s even with blitting; canvas redraw is CPU-bound and stutters as panels multiply |
| 30 fps camera stream | ❌ Tk `PhotoImage` round-trips through the Python object layer; expect <15 fps at 720p with high CPU |
| Dockable/tearable/resizable panels | ❌ No docking framework exists for Tk; you would write one from scratch |
| Threaded data ingestion | ⚠️ Tk is single-threaded; all updates must be marshalled through `after()` |
| Cross-platform | ✅ | 

CustomTkinter is fine for the *connection dialog* level of UI, but the plotting
and docking requirements rule it out.

### 2.2 Options compared

| Option | Real-time perf | Docking | Camera/3D | SSH manager | Effort |
|---|---|---|---|---|---|
| **A. Foxglove (or Lichtblick, the open-source fork)** + existing `foxglove_bridge` | ✅ GPU-accelerated, built for exactly this | ✅ full layout system, shareable layout JSON | ✅ image, 3D, plots, raw msg, diagnostics panels | ❌ (pair with a tiny launcher script) | **Days** |
| **B. PySide6 + pyqtgraph + Qt Advanced Docking System** (custom app) | ✅ pyqtgraph sustains 60 fps line plots with 100k+ points; QPixmap image panel does 30 fps easily | ✅ PySide6-QtAds: VS-Code-grade dock/tear/save-restore | ✅ 2D GPU plots; 3D via `pyqtgraph.opengl` if needed | ✅ full control (asyncssh) | **Weeks** |
| C. PlotJuggler | ✅ excellent time series | ⚠️ plots only | ❌ no camera/track view | ❌ | Hours (as a side tool) |
| D. Web app (React + foxglove ws-protocol) | ✅ | ✅ (golden-layout etc.) | ✅ | ⚠️ needs backend | Weeks+, more moving parts |
| E. rqt / RViz2 | ⚠️ | ⚠️ rqt docking is clunky | RViz for 3D only | ❌ | Linux-only in practice; RViz cannot be embedded cross-platform |

### 2.3 Recommendation: **B as the project, A as the safety net**

Build the custom **PySide6 dashboard (Option B)** — it is the only option that
satisfies every stated requirement (SSH config, topic selection, docking,
cross-platform). But because the repo already vendors `foxglove_bridge` and
publishes `SceneUpdate`/`MarkerArray` topics, set up a **Foxglove/Lichtblick
layout in week 1** as the interim tool and reference implementation. If the
custom app schedule slips before competition, the team still has a working
dashboard.

Core stack for Option B (all pip-installable, all cross-platform):

- **PySide6** — UI framework (LGPL, official Qt bindings)
- **PySide6-QtAds** — Qt Advanced Docking System bindings (dock, tear-off, tabify, perspectives)
- **pyqtgraph** — real-time plotting (line plots, scatter, images) on Qt's Graphics View; optional OpenGL
- **asyncssh** — SSH connections, remote launch, port forwarding (pure Python, no OpenSSH dependency on Windows)
- **roslibpy** *(or a foxglove ws-protocol client)* — WebSocket data plane, see §3.1
- **numpy** — ring buffers / decode
- **PyYAML + platformdirs** — config persistence

RViz itself cannot be embedded (C++/Ogre, Linux-centric). The "RViz stream on
the left" requirement is met by a **custom top-down 2D track panel** (cones,
boundaries, trajectory, car pose — this is what FS debugging actually needs and
pyqtgraph renders it trivially at 60 fps), plus the camera image panel. A full
3D view remains one click away in Foxglove/Lichtblick.

---

## 3. Architecture

```
┌────────────────────────── Laptop (Ubuntu 24 / Win / macOS) ──────────────────────────┐
│  Dashboard app (PySide6)                                                             │
│                                                                                      │
│  ┌ Connection manager ┐   ┌ Transport layer ┐    ┌ Data hub ┐     ┌ UI (Qt ADS) ┐   │
│  │ asyncssh:          │   │ RosbridgeTransport│   │ per-topic │    │ TrackView    │   │
│  │  - connect/auth    │   │ (roslibpy/CBOR)  │──▶│ ring      │──▶ │ TimeSeries   │   │
│  │  - remote launch   │   │ RclpyTransport   │   │ buffers,  │    │ CameraPanel  │   │
│  │  - port-forward ───┼──▶│ (native, Ubuntu) │   │ 30 Hz UI  │    │ StatTiles    │   │
│  │    8765/9090→local │   └──────────────────┘   │ tick      │    │ TopicBrowser │   │
│  └────────────────────┘                          └───────────┘    │ CanConsole   │   │
│                                                                   │ EventLog     │   │
└───────────────────────────────────┬──────────────────────────────────────────────────┘
                                    │ SSH tunnel (Wi-Fi)
┌───────────────────────────────────▼───────────────── Jetson Orin Nano ───────────────┐
│  gocart_autonomous.launch.py  +  rosbridge_server (9090) / foxglove_bridge (8765)    │
└───────────────────────────────────────────────────────────────────────────────────────┘
```

### 3.1 Data plane: WebSocket bridge, not native DDS

**Primary transport: `rosbridge_server` (or foxglove_bridge) on the Jetson,
tunnelled through SSH.** Rationale:

1. **Cross-platform for free** — the laptop needs *no ROS installation* on
   Windows/macOS (`roslibpy` is pure Python). This is the single biggest
   simplifier for the "ideally Windows and macOS" requirement.
2. **ROS-version agnostic** — the README notes the Jetson still runs Humble
   while the PCs run Jazzy. Native DDS across mixed distros + Wi-Fi multicast
   discovery is exactly the flaky mess the team is debugging around today. A
   WebSocket over an SSH tunnel is one TCP connection: it either works or it
   tells you why.
3. **Server-side throttling** — rosbridge subscriptions accept
   `throttle_rate`/`queue_length` per topic; foxglove_bridge negotiates
   compression. Don't ship 80 MB/s of raw image over Wi-Fi.
4. Use **CBOR encoding** in roslibpy (`compression='cbor-raw'`) — JSON-encoding
   `Image` or big arrays is the classic rosbridge performance trap.

Wrap it in a `Transport` interface (`subscribe(topic, type, cb, rate)`,
`list_topics()`, `unsubscribe`, `call_service`) with two implementations:

- `RosbridgeTransport` (roslibpy) — default everywhere.
- `RclpyTransport` (native) — optional, Ubuntu-only, zero-copy for high-rate
  work on the same LAN when a native ROS install exists. Loaded lazily so its
  absence never breaks the app.

This isolates the "which bridge" decision to one module; if the team later
moves to `rmw_zenoh`/zenoh-bridge, it's a third adapter.

### 3.2 Control plane: SSH manager

`asyncssh` running on a dedicated asyncio thread:

- **Connection profiles** (name, host/IP, port, user, password *or* key file,
  saved to config; passwords via OS keychain using `keyring`, never plaintext).
- **Port forwarding**: local 9090→jetson:9090 (rosbridge), 8765→8765
  (foxglove_bridge). Data plane always connects to `localhost:<forwarded>` —
  no firewall/multicast issues, encrypted by SSH.
- **Remote process management**: launch/stop the stack or just the bridge
  (`ros2 launch gocart_bringup gocart_autonomous.launch.py`,
  `ros2 launch rosbridge_server rosbridge_websocket_launch.xml`) in a remote
  PTY; stream stdout into a "Jetson console" panel; detect exit codes.
  Configurable pre-command (e.g. `source ~/ros2_ws/install/setup.bash`).
- **Health checks**: periodic `nvidia-smi`/`tegrastats` scrape → Jetson
  CPU/GPU/temp tile (huge value on an Orin Nano running TensorRT).

### 3.3 Threading model (the part that makes it "smooth")

- **Network thread(s)**: transport callbacks deserialize into numpy and append
  to lock-light per-topic **ring buffers** (fixed N seconds of history). No Qt
  calls here.
- **UI timer at 30 Hz** (one `QTimer`): each visible panel pulls the latest
  buffer slice and calls `setData()` / `setImage()`. Decouples message rate
  (CAN at 100 Hz+) from paint rate; pyqtgraph `setData` on preallocated arrays
  is O(visible points).
- Hidden/closed panels auto-unsubscribe (or drop to 1 Hz) — bandwidth follows
  the layout.

### 3.4 Message types without ROS installed

`fsae_interfaces` are custom types. rosbridge/foxglove_bridge send full type
info (CBOR includes field layout via the bridge), and roslibpy delivers parsed
dicts — so **no client-side .msg compilation is needed**. Keep a small schema
registry in the app for the ~10 custom types to map dict → typed dataclass →
panel-friendly numpy.

---

## 4. UI design

### 4.1 Dock layout (Qt Advanced Docking System)

Default "Autonomous" perspective:

```
┌───────────────────────────┬──────────────────────────────┐
│                           │  Trajectory / Track View     │
│   Camera (ZED image,      │  (cones, boundaries, path,   │
│   throttled)              │   car pose, steering arc)    │
│                           ├──────────────────────────────┤
│                           │  AS status │ Battery │ Jetson│
├───────────────┬───────────┴──────────────────────────────┤
│ Steering/Speed│  IMU (accel/gyro)   │  Topic browser /   │
│ cmd vs actual │  (once published)   │  Jetson console    │
└───────────────┴─────────────────────┴────────────────────┘
```

- Every panel: dock, float (tear-off to second monitor), tabify, close.
- **Perspectives** saved/restored as JSON (`ADS` has built-in
  `saveState`/`restoreState`); ship presets: *Autonomous*, *Perception debug*,
  *CAN/hardware*, *Planning debug*.

### 4.2 Panel types (initial set)

| Panel | Renders | Backing widget |
|---|---|---|
| **TrackView2D** | layered: raw detections, fused cone map, left/right track, candidate trajectories, selected trajectory, car pose trail, Stanley target point; follow-car / free camera; per-layer toggles | `pyqtgraph.PlotWidget` + ScatterPlotItem/PlotCurveItem |
| **TimeSeries** | any numeric field(s) selected from any topic (drag from topic browser); shared time axis, pause/zoom-while-recording, legend | `pyqtgraph` multi-curve |
| **Camera** | `Image`/`CompressedImage`, fps + latency overlay | `QLabel`/`pg.ImageItem` |
| **StatTile grid** | AS state (colour-coded enum), battery V/%, GLV, mission state, msg rates | custom widgets |
| **CanConsole** | scrolling hex table of `CANStamped`, filter by ID, decode via user DBC-like map | `QTableView` + model |
| **EventLog** | `Pulse`/`String` events, node lifecycle, bridge connect/disconnect | `QListView` |
| **TopicBrowser** | live topic list + types + Hz + bandwidth; checkbox = subscribe; drag field → plot | `QTreeView` |
| **JetsonConsole** | remote launch stdout, tegrastats | `QPlainTextEdit` |
| **RawInspector** | pretty-printed latest message of any topic (the `ros2 topic echo` replacement) | tree view |

### 4.3 Connection & topic configuration UX

- **Connection dialog**: profile dropdown, host, user, auth (password/key),
  bridge choice + port, "launch bridge if not running" checkbox, test button.
- **Per-topic subscription config**: throttle rate, buffer length, on/off —
  persisted per profile in `~/.config/fsae-dashboard/config.yaml`
  (`platformdirs`), so "Tommy's Jetson at the track" is one click.
- **Discovery-driven**: never hard-code topic names (see the `zed/` prefix bug,
  §1.1). The topic browser shows what actually exists; default layout binds by
  suffix match (`*/selected_trajectory`) with a visible "unmatched" warning.

---

## 5. Jetson-side additions (small PRs to `autonomous`)

1. **IMU publisher** in `zed_launch.cpp` (`getSensorsData` → `sensor_msgs/Imu`,
   ~100 Hz) — prerequisite for the IMU panel.
2. **`dashboard.launch.py`** in `gocart_bringup`: rosbridge_server (and/or the
   existing foxglove_bridge) + `image_throttler` + optional
   `compressed_image` republisher, with a topic whitelist parameter.
3. Fix / document the **`zed/` topic-prefix mismatch** (remap in launch or
   rename in code).
4. Optional: `tegrastats`→topic node for system health (or scrape via SSH, §3.2).

---

## 6. Milestones

| Phase | Deliverable | Est. |
|---|---|---|
| **0. Interim tooling** | Foxglove/Lichtblick layout JSON committed to repo using existing `foxglove_bridge`; team debugging improves immediately | 1–2 days |
| **1. Skeleton** | PySide6 app + QtAds docking + config persistence + connection dialog; connects to rosbridge over SSH tunnel; TopicBrowser with live Hz | 1 wk |
| **2. Core panels** | TimeSeries + StatTiles + RawInspector; ring-buffer data hub; 30 Hz render loop; verified against `mock_stimulus`/rosbag on desktop | 1–2 wk |
| **3. Track view + camera** | TrackView2D with all layers; Camera panel with throttled stream; layout perspectives | 1–2 wk |
| **4. SSH ops** | Remote launch/stop, Jetson console, tegrastats tile, keyring auth | 1 wk |
| **5. Hardening** | Reconnect logic, bandwidth guardrails, Windows/macOS smoke tests, PyInstaller one-file builds, docs | 1 wk |

Test rig without the kart: `ros2 bag play` of a recorded run + the repo's
`mock_stimulus` node; add a `fake_publishers.py` script emitting every topic in
§1 so panels are testable on any laptop.

## 7. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Wi-Fi bandwidth (raw images) | Server-side throttle + JPEG; camera panel defaults to `image_throttled`; bandwidth meter in TopicBrowser |
| Jetson Humble vs laptop Jazzy | Bridge transport is distro-agnostic (§3.1); no DDS discovery across versions |
| `codebase_refactor` in flux (possible LiDAR move) | Discovery-driven topics + schema registry; new topics appear in browser automatically; adding a PointCloud panel later is contained to one panel class |
| roslibpy CBOR gaps for a specific type | foxglove ws-protocol client as fallback adapter; transport interface isolates the swap |
| Custom app slips before comp | Phase 0 Foxglove layout is the fallback dashboard |

---

*Sources: publisher/subscriber extraction from `src/**` (grep of
`create_publisher`/`create_subscription`), `gocart_autonomous.launch.py`,
package READMEs, `fsae_interfaces/msg/*.msg`.*
