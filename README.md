# Spider

Teleoperation for a tracked robot that drives on four articulated flippers,
plus a Gazebo simulation to exercise it without hardware.

Control is split across two computers. **Home** reads a Logitech G29 racing
wheel, shows a dashboard, and emits native Teensy command datagrams. **Field**
sits near the robot, validates those datagrams, owns the robot session, and
enforces a deadman stop. Home never talks to the robot directly.

```text
G29 -> home ---- UDP 9999 ----> field ---- UDP 8888 ----> robot
                                  ^                          |
                                  +---- telemetry, unchanged -+
```

| Directory | What it is |
|---|---|
| [`spider_driver/`](spider_driver/) | The home and field processes, the Teensy protocol, and the test doubles |
| [`spider_simulation/`](spider_simulation/) | ROS 2 + Gazebo model that stands in for the robot |

---

## 1. Quick install

Tested on **Ubuntu 24.04**, **ROS 2 Jazzy**, **Gazebo Harmonic** (gz-sim 8),
**Python 3.12**.

### System packages

Install ROS 2 Jazzy first, following
<https://docs.ros.org/en/jazzy/Installation.html>. Then:

```bash
sudo apt install \
  ros-jazzy-desktop \
  ros-jazzy-ros-gz \
  ros-jazzy-joy \
  python3-colcon-common-extensions \
  python3-venv
```

| Package | Needed for |
|---|---|
| `ros-jazzy-desktop` | ROS 2, RViz, `robot_state_publisher`, `xacro` |
| `ros-jazzy-ros-gz` | Gazebo Harmonic plus `ros_gz_sim` / `ros_gz_bridge` |
| `ros-jazzy-joy` | Only the optional gamepad teleop (`velocity_pub`) |
| `python3-colcon-common-extensions` | Building the simulation workspace |
| `python3-venv` | The dashboard's virtualenv |

Field computer needs **none** of this: it is Python 3.7+ standard library only.

### Python environment (home dashboard only)

```bash
cd spider_driver
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

That installs **PySide6-Essentials** (~80 MB). Do not install plain `PySide6`:
it pulls in `PySide6-Addons`, another 175 MB of QtWebEngine, Qt3D, Charts and
Multimedia that nothing here imports. On a slow link, add
`--timeout 120 --retries 10`.

If PySide6 is missing, the dashboard falls back to the **PySide2** that ROS
installs system-wide. It works and looks identical, but it is Qt5 — so if
something behaves oddly, check which binding loaded.

### Build the simulation

```bash
cd spider_simulation
source /opt/ros/jazzy/setup.bash
colcon build --symlink-install
```

`run_simulation.sh` does this for you on first run. For a non-Jazzy install,
set `ROS_SETUP=/opt/ros/<distro>/setup.bash`.

### Check it worked

```bash
cd spider_driver && python3 -m unittest discover   # 14 tests, no ROS needed
```

---

## 2. Quick start

Four terminals. Order matters: each stage waits for the one below it.

```text
simulation  <--  bridge  <--  field  <--  control board
  Gazebo       UDP 8888     UDP 9999      G29 + dashboard
```

**Terminal 1 — simulation.** Gazebo, the robot model, and the ROS/gz topic
bridges.

```bash
cd spider_simulation
./run_simulation.sh
```

**Terminal 2 — Teensy bridge.** Stands in for the robot: speaks the Teensy UDP
protocol on port 8888 and drives Gazebo through ROS topics. It deliberately
uses the system Python, since `rclpy` is not in the venv.

```bash
cd spider_driver
./run_gz_bridge.sh
```

**Terminal 3 — field computer.** Validates commands, relays telemetry, runs the
500 ms deadman. Listens on 9999, connects to the bridge on 8888.

```bash
cd spider_driver
./run_field_local.sh
```

**Terminal 4 — control board.** The virtual G29 panel and the operator
dashboard together. Use this when no physical wheel is connected.

```bash
cd spider_driver
./run_mock_joystick.sh
```

### Driving it

On the **Virtual G29** panel:

| Control | Effect |
|---|---|
| **Accelerator** slider | Drive. Steering turns, Brake slows. |
| **Steering** slider | Angular velocity. Always live. |
| **Clutch** slider above 50% | Arms the flippers. Does not affect driving. |
| **Up (19)** / **Down (20)** | All four flippers up / down. Clutch must be held. |
| **DIR (23)** | Toggle forward / reverse. |
| **Simulate wheel unplugged** | Exercises the disconnect-safety path. |

The dashboard opens on **Functional controls**. Switch to **All wheel buttons**
to see telemetry and the raw axis/button strip — telemetry is hidden on the
default tab.

### Stopping

Ctrl-C each terminal, or from anywhere:

```bash
./kill_all.sh
```

---

## Variants

**With a real G29**, replace terminal 4. Activate the venv first — this script
calls bare `python3`, so without it you get the Qt5 fallback:

```bash
cd spider_driver
source .venv/bin/activate
./run_home_local.sh
```

**Without Gazebo**, replace terminal 1 and 2 with the mock robot. It speaks the
same protocol, runs toy physics, and needs no ROS:

```bash
cd spider_driver
./run_mock_local.sh
```

**Gamepad instead of the wheel**, driving the simulation directly and bypassing
the Teensy chain entirely (needs `ros-jazzy-joy` and a pad):

```bash
ros2 launch velocity_pub four_ws_control.launch.py
```

**On two real computers**, pass each side the other's IP:

```bash
./deploy_run_field.sh HOME_IP     # field, near the robot
./deploy_run_home.sh  FIELD_IP    # home, with the G29
```

---

## If it does not work

**The relay receives nothing after you restart the bridge or mock.** Field has
no robot-liveness timeout and only drops the session on an explicit
`TERMINATE`, which `SIGTERM` skips. Restart terminal 3.

**Two robots on port 8888.** The mock sets `SO_REUSEADDR`, so a forgotten
instance silently splits telemetry with the new one. Check with
`ss -ulpn | grep 8888`.

**The dashboard looks frozen.** It has no interactive controls by design — the
tab bar is the only clickable thing. With no wheel and no telemetry it is
correctly static.

---

## Further reading

| Document | Covers |
|---|---|
| [`spider_driver/README.md`](spider_driver/README.md) | Teensy command table, G29 mapping, safety rules, deployment |
| [`spider_driver/real_and_mocks.md`](spider_driver/real_and_mocks.md) | How the pieces fit, the three test doubles, and the traps |
| [`spider_simulation/README.md`](spider_simulation/README.md) | The robot model, flipper mirroring, ROS topic list |
