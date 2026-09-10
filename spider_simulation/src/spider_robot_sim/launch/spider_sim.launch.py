"""Spider simulation: Gazebo + ros_gz bridges, no ros2_control.

Replaces spider_robot_spawn.launch.py. The controller_manager path is not used
because the installed gz_ros2_control and controller_manager disagree on how
the parameters file reaches controller nodes; the model is driven by core
gz-sim systems (DiffDrive, JointPositionController) instead.

Topics this exposes to the Teensy bridge (code_examples/spider_gz_bridge.py):

    /cmd_vel        geometry_msgs/Twist    ->  tracks
    /flipper/{fl,fr,rl,rr}  std_msgs/Float64  ->  flipper angle setpoints, rad
    /joint_states   sensor_msgs/JointState <-  telemetry
    /odom           nav_msgs/Odometry      <-  attitude
"""
import os
from pathlib import Path

import xacro
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription, SetEnvironmentVariable
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

WORLD_NAME = "empty"      # the <world name=...> inside spider_robot_world.sdf
MODEL_NAME = "spider_robot"
FLIPPERS = ("fl", "fr", "rl", "rr")


def generate_launch_description():
    use_sim_time = LaunchConfiguration("use_sim_time", default=True)
    description_path = get_package_share_directory("spider_robot_description")
    sim_path = get_package_share_directory("spider_robot_sim")
    gui_config = os.path.join(sim_path, "gui", "spider_gui.config")

    resource_path = SetEnvironmentVariable(
        name="GZ_SIM_RESOURCE_PATH",
        value=[os.path.join(sim_path, "worlds"), ":" + str(Path(description_path).parent.resolve())],
    )

    gazebo = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            [os.path.join(get_package_share_directory("ros_gz_sim"), "launch"), "/gz_sim.launch.py"]
        ),
        launch_arguments=[(
            "gz_args",
            [LaunchConfiguration("world"), ".sdf", " -v 4", " -r",
             " --gui-config ", gui_config],
        )],
    )

    robot_desc = xacro.process_file(
        os.path.join(description_path, "robots", "spider_robot.urdf.xacro"),
        mappings={"use_sim": "true"},
    ).toxml()

    robot_state_publisher = Node(
        package="robot_state_publisher",
        executable="robot_state_publisher",
        output="screen",
        parameters=[{"robot_description": robot_desc, "use_sim_time": use_sim_time}],
    )

    spawn = Node(
        package="ros_gz_sim",
        executable="create",
        output="screen",
        arguments=["-string", robot_desc,
                   # Spider rests with its track wheels 0.0296 m above
                   # base_footprint, so 0.07 dropped a 9.6 kg robot 10 cm.
                   "-x", "0.0", "-y", "0.0", "-z", "0.0",
                   "-R", "0.0", "-P", "0.0", "-Y", "0.0",
                   "-name", MODEL_NAME, "-allow_renaming", "false"],
    )

    joint_state_topic = f"/world/{WORLD_NAME}/model/{MODEL_NAME}/joint_state"
    bridge_args = [
        # commands, ROS -> gz
        "/cmd_vel@geometry_msgs/msg/Twist]gz.msgs.Twist",
        *[f"/flipper/{name}@std_msgs/msg/Float64]gz.msgs.Double" for name in FLIPPERS],
        # telemetry, gz -> ROS
        f"{joint_state_topic}@sensor_msgs/msg/JointState[gz.msgs.Model",
        "/odom@nav_msgs/msg/Odometry[gz.msgs.Odometry",
        "/scan@sensor_msgs/msg/LaserScan[gz.msgs.LaserScan",
        "/clock@rosgraph_msgs/msg/Clock[gz.msgs.Clock",
    ]

    bridge = Node(
        package="ros_gz_bridge",
        executable="parameter_bridge",
        output="screen",
        arguments=bridge_args,
        remappings=[(joint_state_topic, "/joint_states")],
        parameters=[{"use_sim_time": use_sim_time}],
    )

    return LaunchDescription([
        DeclareLaunchArgument("world", default_value="spider_robot_world"),
        DeclareLaunchArgument("use_sim_time", default_value="true"),
        resource_path,
        gazebo,
        robot_state_publisher,
        spawn,
        bridge,
    ])
