# Spider simulation

Gazebo Sim model and ROS 2 topics for exercising `spider_driver` without a
physical robot. The simulation uses Gazebo's DiffDrive and joint-position
systems and exposes the command and telemetry topics expected by the driver's
Teensy-to-Gazebo bridge.

## Start it

On ROS 2 Jazzy:

```bash
./run_simulation.sh
```

The script sources `/opt/ros/jazzy/setup.bash`, builds this colcon workspace on
its first run, sources the overlay, and launches Gazebo. For another ROS
installation, set `ROS_SETUP`, for example:

```bash
ROS_SETUP=/opt/ros/<distro>/setup.bash ./run_simulation.sh
```

Then follow the **Gazebo robot** section in
`../spider_driver/real_and_mocks.md`. Commands flow through the same home and
field UDP processes used by the real robot; `spider_gz_bridge.py` occupies the
robot's usual UDP endpoint on port 8888. Use `run_mock_joystick.sh` in the final
terminal when no physical G29 is connected.

## Robot model

The model is the **Spider** chassis. It has no wheels in the ordinary sense:
it drives on **four flippers**, each a tracked arm that pivots at its own
corner of the body. The two round links inside each flipper are the wheels the
arm's track runs around, not independent road wheels, and the whole arm swings
as one. Raising a flipper lifts the far end of its track, which is what lets
the vehicle climb.

| | |
|---|---|
| Chassis | 0.293 x 0.190 x 0.073 m, 0.843 kg |
| Flippers | 4, revolute, +/-1.46 rad (83.6 deg) |
| Track wheels | 8, radius 0.1 m, two per flipper at x = 0 and x = 0.24 |
| Track separation | 0.271 m |
| Overall | about 0.91 m long, 9.6 kg |

### The flippers are mirrored, and it matters

The flippers rotate about the centre of the vehicle, so the front and rear
pairs are **180 degrees opposed, not parallel**. Every flipper joint axis
resolves to body +y, and because the rear arms extend along -x, one shared
joint angle swings the front pair down while the rear pair goes up.

Operator intent is the opposite of that: dashboard buttons 19 and 20 mean "all
four up" and "all four down". The Teensy bridge therefore carries a sign map
(`FLIPPER_SIGN`) next to the ordering map it already had, negating the front
pair so a uniform command lifts or lowers the whole vehicle, and un-negating
flipper telemetry on the way back. `robot_control.py` applies the same
mirroring through `ARM_JOINT_SIGN`.

Send the same angle to all four joints without that mapping and the vehicle
see-saws instead of rising.

### Naming

Joint names stay on the Spider convention the driver expects. Each flipper is
`<fl|fr|rl|rr>_flipper_joint`. Its inner track wheel, on the flipper's own
pivot axis, is `<prefix>_track_wheel_joint`, and the outer one `<prefix>_track_wheel_2_joint`;
the Teensy bridge reads the four inner ones for track and motor telemetry.

The rear flippers are yawed by pi so they extend rearwards, and their track
wheel joints carry a second yaw of pi that cancels it, keeping every track
wheel spinning about body +y. Get that wrong and the rear tracks drive
backwards.

Geometry, masses and inertias come from the source description. Its ROS 1
Gazebo Classic plugins do not, having been replaced by the gz-sim equivalents
below; its sensor pod (cameras, rangefinder, Ouster OS1-64) is not carried over.

## ROS interface

| Topic | Type | Direction |
|---|---|---|
| `/cmd_vel` | `geometry_msgs/msg/Twist` | command to Gazebo |
| `/flipper/fl`, `/fr`, `/rl`, `/rr` | `std_msgs/msg/Float64` | command to Gazebo |
| `/joint_states` | `sensor_msgs/msg/JointState` | telemetry from Gazebo |
| `/odom` | `nav_msgs/msg/Odometry` | telemetry from Gazebo |
