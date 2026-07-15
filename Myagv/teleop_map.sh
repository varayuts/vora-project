#!/usr/bin/env bash
set -e
source /opt/ros/galactic/setup.bash
source ~/myagv_ros2/install/setup.bash

# บังคับ publish ไป /cmd_vel ให้แน่นอน
ros2 run teleop_twist_keyboard teleop_twist_keyboard --ros-args \
  -r cmd_vel:=/cmd_vel \
  -p speed:=0.06 \
  -p turn:=0.20

