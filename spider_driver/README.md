# Spider Driver

Two-computer control for a tracked Spider robot. The **home** computer reads a
Logitech G29, displays its live controls in PySide6, and emits native Teensy API
datagrams. The **field** computer validates and relays those datagrams to the
robot or included mock server, and relays native robot telemetry back.

## Supported Teensy API

No text commands, timed intents, presets, heartbeat messages, or other custom
application commands cross the home/field link. It accepts these Teensy
`TYPED` commands only:

| Type | Command | Payload |
|---|---|---|
| `0x40` | `TWIST` | `<2f`: linear, angular |
| `0x41` | `FLIP_POS` | `<4f`: FL, FR, RR, RL |
| `0x42` | `FLIP_VEL` | `<4f`: FL, FR, RR, RL |
| `0x44` | `PID` | `<2f` for Spider 10/20, `<6f` for Spider 30 |
| `0x45` | `LIGHTS` | `<4B`: VIS0, VIS1, NIR0, NIR1 |
| `0x46` | `CALIBRATE` | one byte, `0x01` |

Field owns the Teensy `SYN`/`ACK`, `PING`/`PONG`, and `TERMINATE` session.
Robot `TYPED` telemetry is returned unchanged to home; platform telemetry
selects the correct PID layout. The home UI decodes and displays platform,
track, flipper, attitude, motor, and motor-peripheral telemetry even when no
wheel is connected.

## Install

Field supports Python 3.7 or newer and uses only the standard library. Home
requires Python 3.10 or newer and is Linux-only because it reads
`/dev/input/js*`; it also requires PySide6:

```bash
python3 -m pip install -r requirements.txt
```

## Run on two computers

Field computer:

```bash
python3 main.py field \
  --robot-host 10.42.17.3 \
  --robot-port 8888 \
  --allowed-home-ip HOME_COMPUTER_IP
```

Home computer:

```bash
python3 main.py home --field-host FIELD_COMPUTER_IP
```

Equivalent deployment scripts accept the dynamic peer IP as their first
argument and retain the standard robot/port defaults:

```bash
# Field: required HOME_IP; optional ROBOT_IP, RELAY_PORT, ROBOT_PORT
./deploy_run_field.sh HOME_COMPUTER_IP

# Home: required FIELD_IP; optional RELAY_PORT, WHEEL_CONFIG
./deploy_run_home.sh FIELD_COMPUTER_IP
```

Both sides use UDP port `9999` by default. Use `--listen-port` on field and
`--field-port` on home to change it. Field keeps listening while the robot is
offline and retries the robot connection.

For local testing, run these in three terminals:

```bash
./run_mock_local.sh
./run_field_local.sh
./run_home_local.sh
```

## G29 controls and safety

The default mapping in `g29_config.json` is:

| Input | Axis | Action |
|---|---:|---|
| Steering | 0 | angular velocity; always active while connected |
| Clutch | 1 | allow flipper control while held; does not affect driving |
| Accelerator | 2 | velocity in the selected direction |
| Brake | 3 | proportionally reduces accelerator velocity |
| Up | button 19 | positive velocity on all four flippers while held |
| Down | button 20 | negative velocity on all four flippers while held |
| Direction | button 23 | toggle accelerator between forward and reverse |

Pedals default to `+1` released and `-1` pressed. The dashboard shows every
raw axis and button, so correct the indices, endpoints, inversion, or steering
deadzone in the JSON if the Linux driver exposes a different mapping. Wheel
buttons are visualized, with functional mappings highlighted in the second tab.
Button labels, Linux indices, actions, and normalized positions around the wheel
are configured in `g29_config.json`; adjust them using the live raw-input strip.

Home sends `TWIST` at 20 Hz whenever the UI is running. Accelerator direction
starts forward and button 23 toggles it on the button-down edge. The clutch
automatically sends the platform-specific PID allow/deny state and gates only
flipper commands. Buttons 19 and 20 send `FLIP_VEL` for all four flippers at the
configured velocity while held; release or clutch disengagement sends five zero
velocity packets.

The dashboard has two wheel tabs. **All wheel buttons** shows every configured
G29 control in its physical position and highlights presses. **Functional
controls** is the default tab and shows only mapped buttons 19, 20, and 23 around the wheel. The manual
API panel and its PID, position, and calibration buttons have been removed.
Lights remain valid at the relay/protocol layer but are intentionally absent
from the operator UI. Telemetry is visible in the all-buttons tab and hidden in
functional controls.

The wheel artwork and blue center marker rotate through ±450 degrees with the
steering axis, while button overlays remain static. Its hub contains a small
original meerkat emblem drawn directly by Qt, keeping the complete UI offline.
The direction button reads **Forward** in green or **Reverse** in orange, and
changes immediately after button 23 is pressed. Wheel and robot status dots are
green online and red offline.

Disconnecting the wheel or closing home emits zero motion. Releasing the clutch
stops and disables only the flippers. If field receives no valid home commands
for 500 ms, it sends five
zero `TWIST` and `FLIP_VEL` bursts. Set another positive timeout with
`--safety-timeout`.

## Architecture

```text
G29 -> Home PySide6 UI -> native Teensy command UDP -> Field relay -> Spider/mock
          ^                                                    |
          +------------ native Teensy telemetry UDP <----------+
```

`teensy_protocol.py` is the shared encoder/validator. `home_process.py` owns
wheel polling, visualization, and 20 Hz command output. `field_process.py` owns
source validation, the fail-safe, robot reconnection, and transparent relay.

## Tests

```bash
python3 -m unittest discover -v
```
