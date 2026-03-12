#!/usr/bin/env bash
set -e

# ROS env
source /opt/ros/galactic/setup.bash
source ~/myagv_ros2/install/setup.bash

echo "[1/3] Launch myAGV base (odometry/driver)..."
ros2 launch myagv_odometry myagv_active.launch.py &
BASE_PID=$!

sleep 2

echo "[2/3] Launch LiDAR (ydlidar)..."
ros2 launch ydlidar_ros2_driver ydlidar_launch.py &
LIDAR_PID=$!

sleep 2

echo "[3/3] Start SLAM (gmapping)..."
# Run gmapping in foreground (Ctrl+C to stop)
ros2 run slam_gmapping slam_gmapping \
  --ros-args \
  -p base_frame:=base_link \
  -p odom_frame:=odom \
  -p map_frame:=map \
  -r scan:=/scan

echo "SLAM exited. Cleaning up..."
kill $LIDAR_PID $BASE_PID 2>/dev/null || true
