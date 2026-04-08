VORA MyAGV Robot-Side Reliability Audit
========================================
Date: 2026-04-08
Scope: new5/ (robot deploy directory)
Target: ROS2 Galactic on Jetson Nano 4GB


FILES AUDITED (9 files)
========================
1. start_myagv.sh        — Main startup (7 gnome-terminal windows)
2. start_nav2.sh         — Nav2 stack launcher
3. odom_tf_broadcaster.py — TF + dead reckoning
4. ros_camera_pub.py     — Camera to ROS2 topics
5. command_executor.py   — /vora/command dispatcher
6. nav2_params.yaml      — Nav2 parameter tuning
7. slam_toolbox_params.yaml — SLAM parameters
8. send_audio_to_gateway.py — Audio streaming
9. maps/lab_room.yaml    — Map configuration


ISSUES FOUND (ranked by criticality)
======================================

1. CRITICAL — ros2 service call hangs forever on Galactic
   File: start_nav2.sh:218-219, start_nav2.sh:257-258

   Root cause: ros2 service call in Galactic has NO --timeout flag.
   If lifecycle_manager_navigation service doesn't exist yet (Nav2 still
   starting), the command prints "waiting for service..." and HANGS
   INDEFINITELY. The || true catches exit codes but NOT hangs. Each
   iteration of the retry loop blocks forever instead of retrying.

   Impact: start_nav2.sh appears frozen after "Waiting for Nav2
   lifecycle_manager_navigation to become active..." — user has to Ctrl+C.

   Fix: Added "timeout 5" shell command wrapper.


2. MEDIUM — ros2 node list can hang on DDS congestion
   File: start_nav2.sh:137

   Root cause: EKF detection runs "ros2 node list" without a timeout.
   On Jetson Nano cold boot with DDS congestion, this can stall.

   Fix: Added "timeout 5" shell command wrapper.


3. MEDIUM — Map YAML uses absolute path outside deploy directory
   File: maps/lab_room.yaml:1

   Root cause: save_map.sh writes absolute paths into YAML files.
   The YAML referenced /home/er/Desktop/VORA_myAGV_only_ros2_package/maps/lab_room_v2.pgm
   (parent directory) while new5/maps/lab_room.pgm exists and is newer.
   On a fresh robot deploy, the parent directory might not exist.

   Fix: Changed to relative path "./lab_room.pgm" which resolves to
   new5/maps/lab_room.pgm (same directory as the YAML).


NO CHANGES NEEDED (and why)
=============================

start_myagv.sh:
  Fixed sleeps (3s, 2s) between terminals are compensated by
  start_nav2.sh retry loops (30s for /scan, 20s for /odom).
  EKF terminal has internal 5s wait before starting.

odom_tf_broadcaster.py:
  Dead reckoning: dt capping (skip if dt > 0.5s) prevents position jumps.
  Init timer publishes identity TF at 10Hz until first /odom arrives.
  Watchdog: 1.0s timeout with 0.10 speed threshold correctly filters
  DWB trailing deceleration commands (~0.06 m/s).
  EKF crash recovery: not implemented but acceptable for demo.

ros_camera_pub.py:
  USB disconnect recovery: USB reset (unbind/rebind), escalating wait
  (2s + n*2, max 15s), 4 open strategies. Triggers after 30 consecutive
  read failures (~2s at 15fps).

nav2_params.yaml:
  odom_topic: /odom_fused (correct — /odom has x=y=0).
  base_footprint: consistent in AMCL, bt_navigator, costmaps.
  Footprint: [[0.13,0.105],...] = 26cm x 21cm matches hardware.
  bond_timeout: 30.0 (handles slow Jetson DDS discovery).
  robot_model_type: "differential" (avoids Galactic AMCL SIGSEGV).
  DWB: vx_samples=6, vy_samples=3, vtheta_samples=12 (216 trajectories,
  appropriate for Jetson Nano at 10Hz controller rate).
  transform_tolerance: 0.5 in all costmap observation sources.

command_executor.py:
  JSON error handling present. Blocking execute_motion acceptable for
  lab demo (2s default, 10Hz publish rate). Watchdog timer at 0.5s.

slam_toolbox_params.yaml:
  base_frame: base_footprint (consistent with Nav2).
  resolution: 0.05 matches Nav2 costmaps.

start_slam.sh:
  base_frame:=base_footprint (patched in previous session).


EXACT DIFFS
============

--- start_nav2.sh (3 changes) ---

Change 1 (line 137): EKF node check — add timeout
  BEFORE: if ros2 node list 2>/dev/null | grep -q "ekf_node"; then
  AFTER:  if timeout 5 ros2 node list 2>/dev/null | grep -q "ekf_node"; then

Change 2 (line 218-219): Nav mode lifecycle poll — add timeout
  BEFORE: STATE=$(ros2 service call /lifecycle_manager_navigation/get_state \
              lifecycle_msgs/srv/GetState 2>/dev/null || true)
  AFTER:  STATE=$(timeout 5 ros2 service call /lifecycle_manager_navigation/get_state \
              lifecycle_msgs/srv/GetState 2>/dev/null || true)

Change 3 (line 257-258): SLAM mode lifecycle poll — add timeout
  BEFORE: STATE=$(ros2 service call /lifecycle_manager_navigation/get_state \
              lifecycle_msgs/srv/GetState 2>/dev/null || true)
  AFTER:  STATE=$(timeout 5 ros2 service call /lifecycle_manager_navigation/get_state \
              lifecycle_msgs/srv/GetState 2>/dev/null || true)

--- maps/lab_room.yaml (1 change) ---

Change 4 (line 1): Map image path — absolute to relative
  BEFORE: image: /home/er/Desktop/VORA_myAGV_only_ros2_package/maps/lab_room_v2.pgm
  AFTER:  image: ./lab_room.pgm


WHY EACH FIX IS CORRECT
=========================

Change 1-3 (timeout 5):
  "timeout 5 command" kills the command after 5s and returns exit code 124.
  Combined with || true, this sets STATE="" on timeout, grep doesn't match
  "active", loop sleeps 2s, retries. No hang.
  Worst case (service never appears): 20 iterations x (5s timeout + 2s sleep) = 140s.
  Practical case (service appears after 15s): 3 iterations x 7s = 21s.
  "timeout" is GNU coreutils — standard on Ubuntu/Jetson.
  Galactic compatible: uses shell timeout, not any ROS2 CLI flag.

Change 4 (relative path):
  nav2_map_server resolves image paths relative to the YAML file location.
  "./lab_room.pgm" resolves to new5/maps/lab_room.pgm (147517 bytes, Apr 7).
  This is the latest map and lives inside the deploy directory.
  Portable: works regardless of absolute path on robot.


VERIFICATION COMMANDS (Galactic-safe)
=======================================

After deploying to robot:

  # Check /scan is publishing
  ros2 topic hz /scan 2>/dev/null | head -5

  # Check /odom is publishing
  ros2 topic hz /odom 2>/dev/null | head -5

  # Check TF chain
  timeout 5 ros2 run tf2_ros tf2_echo odom base_footprint

  # Check map_server loads the map
  ros2 topic echo /map_metadata 2>/dev/null | head -10

  # Check AMCL particles (after Set Pose)
  ros2 topic echo /particle_cloud 2>/dev/null | head -5

  # Check lifecycle state
  timeout 5 ros2 service call /lifecycle_manager_navigation/get_state \
      lifecycle_msgs/srv/GetState

  # Check camera publishing
  ros2 topic hz /camera/compressed 2>/dev/null | head -5


PRE-DEMO ROBOT STARTUP CHECKLIST
===================================

Before start_myagv.sh:
  [ ] Robot powered on, Jetson Nano booted (wait for desktop)
  [ ] USB camera plugged in (/dev/video0 exists)
  [ ] YDLidar USB connected
  [ ] ReSpeaker USB connected
  [ ] Ethernet/WiFi connected (ping 192.168.0.1 works)
  [ ] Gateway server running (curl http://GATEWAY_IP:9001/health)

After start_myagv.sh:
  [ ] All 7 terminal windows open with no red errors
  [ ] ros2 topic list shows: /scan, /odom, /imu, /camera/compressed
  [ ] ROSBridge terminal shows "started server on port 9090"
  [ ] Wait 15s for DDS discovery to settle

Before start_nav2.sh:
  [ ] new5/maps/lab_room.pgm exists (map file)
  [ ] new5/maps/lab_room.yaml image path is ./lab_room.pgm (just fixed)

After start_nav2.sh:
  [ ] Script prints "Nav2 lifecycle_manager_navigation: ACTIVE"
  [ ] Set initial pose from webapp
  [ ] ros2 topic echo /amcl_pose 2>/dev/null | head -30 shows pose data
  [ ] Send turn left/right from webapp — no [exit code -11] in nav2 terminal
  [ ] Send navigation goal — robot moves, no progress checker abort

AMCL crash test (critical — validates SIGSEGV fix):
  [ ] Set Pose from webapp
  [ ] Send 5x turn_left commands
  [ ] Send 5x turn_right commands
  [ ] No "process has died [exit code -11]" in nav2 terminal
  [ ] AMCL still publishing: ros2 topic echo /amcl_pose 2>/dev/null | head -10
