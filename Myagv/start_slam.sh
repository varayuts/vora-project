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
# ลอง 2 ชื่อ launch ที่พบบ่อย: ydlidar_launch.py หรือ ydlidar_launch_view.py
# ถ้าอันแรกไม่พบ ให้ Ctrl+C แล้วแก้เป็นอีกอัน
ros2 launch ydlidar_ros2_driver ydlidar_launch.py &
LIDAR_PID=$!

sleep 2

echo "[3/3] Start SLAM (gmapping)..."
# gmapping ชอบใช้ /scan และ odom->base_link TF
ros2 run slam_gmapping slam_gmapping \
  --ros-args \
  -p base_frame:=base_link \
  -p odom_frame:=odom \
  -p map_frame:=map \
  -r scan:=/scan

echo "SLAM exited. Cleaning up..."
kill $LIDAR_PID $BASE_PID 2>/dev/null || true
