#!/usr/bin/env bash
# Runs the Teensy <-> Gazebo bridge with ROS sourced and the system Python.
#
# The bridge must NOT run inside the project venv: rclpy is picked up from
# PYTHONPATH, but its dependencies (yaml, numpy, lark) live in system
# site-packages, which a venv created without --system-site-packages hides.
# This script therefore ignores any active venv on purpose.
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ros_setup="${ROS_SETUP:-/opt/ros/jazzy/setup.bash}"

if [[ -z "${ROS_DISTRO:-}" ]]; then
  if [[ ! -r "$ros_setup" ]]; then
    echo "Cannot find a ROS environment at $ros_setup." >&2
    echo "Source it yourself, or set ROS_SETUP=/opt/ros/<distro>/setup.bash" >&2
    exit 1
  fi
  # ROS setup scripts reference unset variables, so relax -u across the source.
  set +u
  # shellcheck disable=SC1090
  source "$ros_setup"
  set -u
fi

python_bin="${PYTHON:-/usr/bin/python3}"

if ! "$python_bin" -c "import yaml, rclpy" >/dev/null 2>&1; then
  echo "$python_bin cannot import rclpy and its dependencies." >&2
  echo "Check that $ros_setup matches this machine's ROS install." >&2
  exit 1
fi

exec "$python_bin" -u "$script_dir/code_examples/spider_gz_bridge.py" "$@"
