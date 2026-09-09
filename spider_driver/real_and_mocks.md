# Real and Mocks

How the pieces of this project fit together, and how to run the whole chain on
one machine with no robot and no steering wheel.

For the operator-facing description — controls, safety rules, deployment — see
[README.md](README.md). This document is about the *shape* of the system and
its test doubles.

---

## First, the word "wheel"

This is the single most confusing thing in the repo, so it is worth stating
plainly up front:

> **"Wheel" always means the Logitech G29 racing wheel on the operator's desk.**
> It never means a wheel on the robot. The robot does not have wheels.

Everything named `wheel` — `g29_config.json`, `WheelView`, `wheel_chip`, the
`WHEEL ONLINE` indicator, the "All wheel buttons" tab — refers to the **input
device**, not the vehicle.

The robot is **tracked**. It moves on two rubber treads, plus four articulated
flippers. The telemetry decoder confirms this: message `0x12` unpacks as
`left velocity, right velocity, left position, right position` for the
**tracks** ([teensy_protocol.py:100](teensy_protocol.py#L100)).

| Term | What it actually is |
|---|---|
| wheel, G29 | The operator's racing wheel — a USB input device |
| tracks | The robot's two treads, what makes it drive |
| flippers | Four articulated arms (FL, FR, RR, RL) at the corners |
| platform | Which Spider model is attached (10, 20, or 30) — changes packet layouts |

Flippers are the interesting part. They are arms that let a tracked robot climb
stairs and obstacles by lifting and bracing itself. That is why they get a
safety interlock that plain driving does not.

---

## The four pieces

```text
 ┌─ home computer ─────────────┐       ┌─ field computer ──┐      ┌─ robot ──────┐
 │  G29 wheel                  │       │                   │      │              │
 │    ↓                        │  UDP  │  validates        │ UDP  │  Teensy MCU  │
 │  PySide6 dashboard          ├──9999─┤  owns session     ├─8888─┤  2 tracks    │
 │  encodes Teensy commands    │       │  enforces deadman │      │  4 flippers  │
 │  20 Hz                      │       │  relays           │      │              │
 └─────────────────────────────┘       └───────────────────┘      └──────────────┘
              ▲                                  │
              └────── telemetry, byte-identical ─┘
```

**Home never talks to the robot directly.** That is the whole point of the
split. The field computer sits near the robot; the home computer can be
anywhere.

| | Home | Field |
|---|---|---|
| Entry point | `main.py home` | `main.py field` |
| Python | 3.10+, Linux only (reads `/dev/input/js*`) | 3.7+, standard library only |
| Needs PySide6 | yes | no |
| Owns | wheel polling, UI, command encoding | robot session, validation, failsafe |

### Who owns what on the wire

- **Home → field:** only `TYPED` command datagrams (`0x40`–`0x46`). No session
  messages, heartbeats, or text commands ever cross this link.
- **Field ↔ robot:** the full session — `SYN`/`SYN_ACK`/`ACK`, `PING`/`PONG`,
  `TERMINATE` — plus commands and telemetry.
- **Field → home:** robot telemetry, forwarded unchanged.

`teensy_protocol.py` is the shared encoder and validator, and it is the trust
boundary: every packet entering the relay passes through
`validate_command_packet` ([field_process.py:108](field_process.py#L108)), and
field then forwards the original bytes rather than re-encoding them, so the two
sides cannot drift.

### Why the safety logic lives on field

If the link between home and field drops, the robot must stop. Field cannot
rely on home to tell it that, so it runs its own deadman: no valid command for
500 ms (`--safety-timeout`) and it emits five zero-`TWIST` and zero-`FLIP_VEL`
bursts on its own ([field_process.py:120-137](field_process.py#L120-L137)).

There is a second, quieter guard: commands sit in a queue with a timestamp, and
anything older than 500 ms is dropped instead of sent
([field_process.py:58](field_process.py#L58)). A robot that reconnects after an
outage therefore never replays stale motion.

---

## What actually moves

| Command | Payload | Drives | Triggered by |
|---|---|---|---|
| `0x40 TWIST` | `<2f` linear, angular | Tracks — the robot mixes these into left/right tread speeds | Accelerator, brake, steering — every tick, 20 Hz |
| `0x41 FLIP_POS` | `<4f` FL, FR, RR, RL | Absolute flipper angles | Nothing — valid on the wire, unused by the UI |
| `0x42 FLIP_VEL` | `<4f` FL, FR, RR, RL | Flipper rotation speed | Buttons 19 / 20, only while the clutch is held |
| `0x44 PID` | `<2f` (Spider 10/20) or `<6f` (Spider 30) | Control-loop gains, used as an authority gate | The clutch, automatically |
| `0x45 LIGHTS` | `<4B` VIS0, VIS1, NIR0, NIR1 | Lamps | Nothing — deliberately absent from the operator UI |
| `0x46 CALIBRATE` | one byte, `0x01` | Encoder zeroing | Nothing |

Note that home sends **intent, not per-motor commands**: "drive forward at X,
turn at Y". The robot does its own mixing into track speeds.

### The clutch is an interlock, not a pedal

This trips people up. The clutch does **not** affect driving at all. It gates
the flippers, and it does so in two independent ways at once:

1. It gates `FLIP_VEL` locally — buttons 19/20 do nothing unless
   `clutch > 0.5` ([home_process.py:318](home_process.py#L318)).
2. It sends `PID` gains as an **authority gate**: real gains when engaged, all
   zeros when released ([teensy_protocol.py:57-67](teensy_protocol.py#L57-L67)).
   Zero gains mean the flipper control loop has no authority even if a stray
   command arrives.

Releasing the clutch sends five zero-velocity packets and then denies PID.
Driving is untouched throughout.

### Repeats instead of acknowledgements

Nothing in this protocol is acknowledged, so state changes are simply repeated:

| Event | Repeat |
|---|---|
| Flippers stop | 5 zero `FLIP_VEL` packets |
| Clutch state changes | 5 `PID` packets, 250 ms apart |
| Field deadman fires | 5 zero `TWIST` + `FLIP_VEL`, 50 ms apart |
| Home window closes | 5 of each, plus PID deny |

---

## Telemetry coming back

Any `TYPED` packet with a message type in `0x10`–`0x1F` is treated as telemetry
and forwarded to home untouched.

| Type | Contents |
|---|---|
| `0x10` | Platform — which Spider model. Selects the `PID` payload layout. |
| `0x11` | Per-motor current, velocity, position, status |
| `0x12` | Track velocities and positions |
| `0x13` | Flipper positions (FL, FR, RR, RL) |
| `0x14` | Motor peripherals (temperatures, voltages) |
| `0x15` | Attitude — roll, pitch, yaw |

`0x10` matters more than it looks: **Spider 30 uses different binary layouts**
for `PID`, motor data, and peripherals than Spider 10/20. Until a platform
telemetry packet arrives, `pid_packet` refuses to build anything at all
([teensy_protocol.py:58-59](teensy_protocol.py#L58-L59)). Anything that cannot
be decoded is displayed as hex rather than dropped.

---

## The mocks

Three test doubles let the whole chain run on one machine.

### Mock robot — `code_examples/spider_mock_server.py`

Speaks the robot half of the protocol: completes the handshake, decodes and
logs every command, runs toy physics, and streams all six telemetry types at
20 Hz. It models 8 motors — 0–1 tracks, 2–5 flippers, 6–7 spare
([spider_mock_server.py:106-118](code_examples/spider_mock_server.py#L106-L118)).

```bash
./run_mock_local.sh                                  # port 8888, SPIDER_30
python3 code_examples/spider_mock_server.py --platform SPIDER_10
python3 code_examples/spider_mock_server.py --quiet-telemetry   # errors only
```

Use `--platform` to exercise the layout switching described above.

### Gazebo robot — `code_examples/spider_gz_bridge.py`

The Gazebo simulation can replace the mock server while keeping the same home,
field, and native Teensy UDP path. Build and launch `spider_simulation` first,
then run the bridge as the robot endpoint. Run each command in a separate
terminal:

Build the simulation workspace:

```bash
cd ../spider_simulation
source /opt/ros/jazzy/setup.bash
colcon build --symlink-install
source install/setup.bash
```

Then start the full chain:

```bash
# Terminal 1
cd ../spider_simulation
./run_simulation.sh

# Terminal 2, from spider_driver: native Teensy UDP <-> ROS/Gazebo topics
./run_gz_bridge.sh

# Terminal 3: field connects to the bridge on localhost:8888
./run_field_local.sh

# Terminal 4: dashboard + virtual G29
./run_mock_joystick.sh
```

The bridge targets the simulation's `/cmd_vel`, `/flipper/{fl,fr,rl,rr}`,
`/joint_states`, and `/odom` topics. `run_gz_bridge.sh` deliberately uses the
system Python supplied with ROS, even if the driver virtualenv is active.

`run_mock_joystick.sh` opens both the virtual G29 and the operator dashboard.
Move the **Accelerator** slider above zero to drive, use **Steering** to turn,
and raise **Brake** to reduce the commanded speed. The clutch is not required
for driving. To operate the simulated flippers, move **Clutch** above 50% and
use the mapped **Up (19)** or **Down (20)** button. The **Direction (23)**
button toggles forward and reverse.

### Virtual G29 — `code_examples/virtual_g29.py`

A stand-in for the physical wheel, for testing without hardware. Sliders for
steering / accelerator / brake / clutch, and a latching button for every entry
in `g29_config.json`.

It works because `G29Input` only opens a path and reads 8-byte `IhBB` structs —
it never issues a joystick ioctl ([g29_input.py:50](g29_input.py#L50)). So a
**named pipe is indistinguishable from a real wheel**, and no change to the
home process is needed: it is simply pointed at a generated config via
`--wheel-config`.

Three implementation details worth knowing:

- It holds the FIFO open `O_RDWR`. A pipe with no writer reports EOF, which
  `G29Input` treats as a disconnect ([g29_input.py:57-60](g29_input.py#L57-L60)).
- It resends full state at 2 Hz. A real joystick only reports changes, so a
  dashboard started second would otherwise sit at defaults.
- Axis numbers, pedal endpoints, and the button list are read from the real
  `g29_config.json`, so remapping the wheel remaps the mock too.

The **"Simulate wheel unplugged"** checkbox stops emission, which is the way to
exercise the disconnect-safety path: the dashboard should flip to
`WHEEL OFFLINE` and zero its output.

```bash
./code_examples/virtual_g29.py     # writes /tmp/virtual_g29_js + /tmp/virtual_g29.json
```

Flags: `--device`, `--config-out`, `--wheel-config`.

---

## Running the whole thing locally

Home needs PySide6; field and the mock robot need only the standard library.

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

With a real G29, three terminals:

```bash
./run_mock_local.sh      # mock robot on 127.0.0.1:8888
./run_field_local.sh     # relay on 127.0.0.1:9999
./run_home_local.sh      # dashboard
```

Without a wheel, swap the last one — `run_mock_joystick.sh` starts the virtual
G29, waits for its FIFO, launches the dashboard against the generated config,
and stops the joystick again on exit:

```bash
./run_mock_local.sh
./run_field_local.sh
./run_mock_joystick.sh   # dashboard + virtual wheel
```

It takes an optional field IP (default `127.0.0.1`), honours `PYTHON`,
`VIRTUAL_G29_DEVICE`, and `VIRTUAL_G29_CONFIG`, and prefers `.venv/bin/python`
when one exists.

Tests:

```bash
python3 -m unittest discover -v
```

---

## Things that will confuse you while testing

**The dashboard has no interactive controls.** No buttons, no sliders, no mouse
or keyboard handlers exist anywhere in `home_process.py` — only labels, a
read-only text pane, and two custom-painted wheel widgets. The manual API panel
was deliberately removed ([README.md:110](README.md#L110)). **The tab bar is the
only clickable thing in the entire application.** Everything else is driven by
the G29 and by telemetry. With neither present, the UI is correctly, completely
static — it is not frozen.

**Telemetry is hidden on the default tab.** The dashboard opens on "Functional
controls", and the telemetry panel is only visible on tab index 0
([home_process.py:265-266](home_process.py#L265-L266)). Switch to **"All wheel
buttons"** to see any data, and to see the raw axis/button strip that tells you
which indices your hardware actually reports.

**Field can get stuck believing it is still connected.** The forward loop runs
while `connected_to_server` is set ([field_process.py:48](field_process.py#L48)),
and that flag is cleared *only* by an explicit `TERMINATE` from the robot
([liveness_loop.py:77-79](liveness_loop.py#L77-L79)). There is no robot-liveness
timeout — field sees the robot's `PING`s but never notices when they stop. If
the robot dies without a clean shutdown, field never re-`SYN`s.

This bites constantly in local testing, because `SIGTERM` terminates Python
*without* running `finally` blocks, so a killed mock never sends its
`TERMINATE`. Symptom: you restart the mock and it receives nothing at all. Fix:
restart the relay. Note the asymmetry — the mock protects itself, dropping its
client after 3 s without a `PONG`
([spider_mock_server.py:327-329](code_examples/spider_mock_server.py#L327-L329));
field has no equivalent.

**Two mock robots can bind port 8888 at once.** The mock sets `SO_REUSEADDR`
([spider_mock_server.py:130](code_examples/spider_mock_server.py#L130)), so a
forgotten instance will silently split telemetry with a new one. Check with
`ss -ulpn | grep 8888` before assuming the relay is at fault.

**PID gains are defined twice.** `teensy_protocol.pid_packet`
([teensy_protocol.py:60-66](teensy_protocol.py#L60-L66)) and
`spider_logic.Spider` ([spider_logic.py:13-14](spider_logic.py#L13-L14)) each
carry their own copy of the Spider 10/20 and 30 tuples, with nothing keeping
them in sync. The `spider_logic` copy is currently dead code — only
`pid_packet` is reachable — but it is a trap for anyone tuning gains.
