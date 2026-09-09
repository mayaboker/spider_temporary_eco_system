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

## ROS interface

| Topic | Type | Direction |
|---|---|---|
| `/cmd_vel` | `geometry_msgs/msg/Twist` | command to Gazebo |
| `/flipper/fl`, `/fr`, `/rl`, `/rr` | `std_msgs/msg/Float64` | command to Gazebo |
| `/joint_states` | `sensor_msgs/msg/JointState` | telemetry from Gazebo |
| `/odom` | `nav_msgs/msg/Odometry` | telemetry from Gazebo |
