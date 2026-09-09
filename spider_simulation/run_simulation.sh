#!/usr/bin/env bash
# Build (when needed) and launch the Spider Gazebo simulation.
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ros_setup="${ROS_SETUP:-/opt/ros/jazzy/setup.bash}"

if [[ ! -r "$ros_setup" ]]; then
  echo "Cannot find a ROS environment at $ros_setup." >&2
  echo "Set ROS_SETUP=/opt/ros/<distro>/setup.bash for this machine." >&2
  exit 1
fi

set +u
# shellcheck disable=SC1090
source "$ros_setup"
set -u

if [[ ! -r "$script_dir/install/spider_robot_sim/share/spider_robot_sim/package.bash" ]]; then
  echo "Building spider_simulation workspace..."
  (cd "$script_dir" && colcon build --symlink-install)
fi

set +u
# shellcheck disable=SC1091
source "$script_dir/install/setup.bash"
set -u

exec ros2 launch spider_robot_sim spider_sim.launch.py "$@"
