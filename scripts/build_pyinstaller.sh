#!/usr/bin/env bash
set -euo pipefail
component_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$component_root"
python_bin="${BUILD_PYTHON:-python3}"
"$python_bin" -c 'import sys; assert sys.version_info[:2] == (3, 12), "Python 3.12 required"'
"$python_bin" -m PyInstaller --noconfirm --clean --onedir \
  --name localization --paths src --collect-all forge_msgs \
  --runtime-hook scripts/ros_runtime_hook.py \
  --exclude-module rclpy --exclude-module rcl_interfaces --exclude-module builtin_interfaces --exclude-module rosidl_parser --exclude-module rosidl_runtime_py --exclude-module ament_index_python --exclude-module action_msgs --exclude-module geometry_msgs --exclude-module lifecycle_msgs --exclude-module nav2_msgs --exclude-module nav_msgs --exclude-module sensor_msgs --exclude-module std_msgs --exclude-module std_srvs --exclude-module slam_toolbox --exclude-module livox_ros_driver2 --exclude-module tf2_ros --exclude-module tf2_py --exclude-module tf2_msgs --exclude-module unique_identifier_msgs --exclude-module rosgraph_msgs --exclude-module composition_interfaces --exclude-module launch --exclude-module launch_ros \
  scripts/pyinstaller_entry.py
./dist/localization/localization --version
./dist/localization/localization --check-runtime
cp LICENSE THIRD_PARTY_NOTICES.md dist/localization/
version=$("$python_bin" -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')
archive="dist/localization-${version}-linux-x86_64-ros2-jazzy.tar.gz"
tar --sort=name --mtime='UTC 1970-01-01' --owner=0 --group=0 --numeric-owner -C dist -czf "$archive" localization
sha256sum "$archive" > "$archive.sha256"
