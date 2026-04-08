#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════
# VORA MyAGV — Nav2 Navigation Stack Launcher
# ══════════════════════════════════════════════════════════
# Usage:
#   ./start_nav2.sh                   # Default: localization + navigation
#   ./start_nav2.sh --slam            # SLAM mode (no static map)
#   ./start_nav2.sh --explore         # SLAM + explore_lite (auto-explore)
#
# Prerequisites: start_myagv.sh must be running (base driver + LiDAR + ROSBridge)
# ══════════════════════════════════════════════════════════
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Colors
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
info()  { echo -e "${BLUE}ℹ️  $1${NC}"; }
ok()    { echo -e "${GREEN}✅ $1${NC}"; }
warn()  { echo -e "${YELLOW}⚠️  $1${NC}"; }
err()   { echo -e "${RED}❌ $1${NC}"; }

# ── Parse args ──
MODE="nav"   # nav | slam | explore
MAP_FILE="${SCRIPT_DIR}/maps/lab_room.yaml"

# Default bootstrap pose — robot's approximate starting position in map frame.
# AMCL needs this to establish map→odom TF. Correct via webapp Set Pose after startup.
INIT_X="${INIT_X:-0.0}"
INIT_Y="${INIT_Y:-0.0}"
INIT_YAW="${INIT_YAW:-0.0}"   # radians

while [[ $# -gt 0 ]]; do
    case $1 in
        --slam)    MODE="slam"; shift ;;
        --explore) MODE="explore"; shift ;;
        --map)     MAP_FILE="$2"; shift 2 ;;
        *)         echo "Unknown option: $1"; exit 1 ;;
    esac
done

# ── Source ROS2 ──
info "Sourcing ROS2 environment..."
source /opt/ros/galactic/setup.bash
source ~/myagv_ros2/install/setup.bash
source ~/colcon_ws/install/setup.bash 2>/dev/null || true
ok "ROS2 sourced"

# ── Kill any leftover Nav2 navigation nodes from previous runs ──
# Prevents lifecycle conflict: "No transition matching 1 found for current state active"
# when controller_server/planner_server are still alive from a previous session.
info "Clearing any existing Nav2 navigation nodes..."
pkill -f "controller_server" 2>/dev/null || true
pkill -f "planner_server" 2>/dev/null || true
pkill -f "bt_navigator" 2>/dev/null || true
pkill -f "waypoint_follower" 2>/dev/null || true
sleep 2
ok "Nav2 navigation nodes cleared"

NAV2_PARAMS="${SCRIPT_DIR}/nav2_params.yaml"
SLAM_PARAMS="${SCRIPT_DIR}/slam_toolbox_params.yaml"

# ── Verify prerequisites (retry loop for slow DDS discovery on Jetson Nano) ──
info "Checking prerequisites..."

SCAN_FOUND=0
for i in $(seq 1 15); do
    if ros2 topic list 2>/dev/null | grep -q "/scan"; then
        SCAN_FOUND=1
        break
    fi
    info "  /scan not yet visible (attempt $i/15, retrying in 2s)..."
    sleep 2
done
if [[ $SCAN_FOUND -eq 0 ]]; then
    err "LiDAR /scan topic not found after 30s!"
    err "Is start_myagv.sh running?  Debug: ros2 topic list"
    exit 1
fi
ok "/scan topic found"

ODOM_FOUND=0
for i in $(seq 1 10); do
    if ros2 topic list 2>/dev/null | grep -q "/odom"; then
        ODOM_FOUND=1
        break
    fi
    info "  /odom not yet visible (attempt $i/10, retrying in 2s)..."
    sleep 2
done
if [[ $ODOM_FOUND -eq 0 ]]; then
    err "Odometry /odom topic not found after 20s!"
    err "Is start_myagv.sh running?  Debug: ros2 topic list"
    exit 1
fi
ok "/odom topic found"

ok "Prerequisites OK (/scan + /odom active)"

# ── Cleanup on exit ──
PIDS=()
cleanup() {
    info "Shutting down Nav2 stack..."
    ros2 topic pub --once /cmd_vel geometry_msgs/msg/Twist \
        "{linear: {x: 0.0, y: 0.0, z: 0.0}, angular: {x: 0.0, y: 0.0, z: 0.0}}" 2>/dev/null &
    for p in "${PIDS[@]}"; do
        kill "$p" 2>/dev/null || true
    done
    wait 2>/dev/null
    ok "Cleanup done"
}
trap cleanup EXIT INT TERM

echo ""
echo -e "${BLUE}══════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}🧭 VORA Nav2 — Mode: ${MODE}${NC}"
echo -e "${BLUE}══════════════════════════════════════════════════════════${NC}"

# ── TF broadcaster ──
# myagv_odometry_node has TF broadcast commented out in myAGV.cpp
# (EKF from myagv_active.launch.py would broadcast it, but that may not be running).
# We start odom_tf_broadcaster.py to guarantee odom→base_footprint→base_link.
BROADCASTER="${SCRIPT_DIR}/odom_tf_broadcaster.py"
if [[ ! -f "$BROADCASTER" ]]; then
    err "odom_tf_broadcaster.py not found: ${BROADCASTER}"
    exit 1
fi

# odom_tf_broadcaster.py ALWAYS starts — it is the sole publisher of /odom_fused,
# which bt_navigator (odom_topic: /odom_fused) needs for progress checking.
#
# TF conflict prevention: if ekf_node is running it owns the odom→base_footprint TF.
# We pass --skip-odom-tf so the broadcaster publishes /odom_fused only (no TF).
# Without EKF: broadcaster publishes both TF and /odom_fused as normal.
EKF_RUNNING=0
info "Checking if EKF node is running..."
if timeout 5 ros2 node list 2>/dev/null | grep -q "ekf_node"; then
    ok "EKF node detected — broadcaster will skip TF (--skip-odom-tf), publish /odom_fused only"
    EKF_RUNNING=1
else
    info "EKF not running — broadcaster will publish TF + /odom_fused"
fi

info "Starting odom_tf_broadcaster.py..."
if [[ $EKF_RUNNING -eq 1 ]]; then
    python3 "$BROADCASTER" --skip-odom-tf &
else
    python3 "$BROADCASTER" &
fi
PIDS+=($!)
sleep 3  # Wait for broadcaster node init + DDS discovery on Jetson Nano

# Verify TF only when broadcaster is the TF owner (no EKF)
if [[ $EKF_RUNNING -eq 0 ]]; then
    info "Verifying TF odom→base_footprint..."
    if timeout 10 ros2 run tf2_ros tf2_echo odom base_footprint 2>&1 | grep -qm1 "Translation"; then
        ok "TF odom→base_footprint confirmed"
    else
        err "TF not verified — check /odom topic and broadcaster logs"
        err "Debug: ros2 run tf2_ros tf2_echo odom base_footprint"
        err "       ros2 topic echo /odom 2>/dev/null | head -10"
        exit 1
    fi
else
    ok "Broadcaster started in /odom_fused-only mode — TF owned by EKF"
fi

if [[ "$MODE" == "nav" ]]; then
    # ── Localization mode: AMCL + static map + Nav2 ──
    info "Mode: Navigation with AMCL localization"
    info "Map: ${MAP_FILE}"

    if [[ ! -f "$MAP_FILE" ]]; then
        err "Map file not found: ${MAP_FILE}"
        exit 1
    fi

    # 1) Map Server + AMCL
    info "Starting Map Server + AMCL..."
    ros2 launch nav2_bringup localization_launch.py \
        map:="$MAP_FILE" \
        params_file:="$NAV2_PARAMS" \
        use_sim_time:=false &
    PIDS+=($!)
    sleep 5
    ok "Map Server + AMCL started"

    # Bootstrap: publish initial pose so AMCL can establish map→odom TF.
    # set_initial_pose=false in nav2_params.yaml means AMCL waits for this topic.
    # Without it, map frame never appears in TF → global costmap times out → Nav2 stuck.
    # Position is approximate — correct via webapp "📍 Set Pose" after startup.
    #
    # stamp: {sec: 0} is intentional — means "use the latest available TF".
    # If we use stamp=now(), AMCL calls tf_buffer.lookupTransform(odom, map, now()).
    # The odom→base_footprint TF publishes at ~10Hz, so the latest TF can be up to
    # 100ms old. With stamp=now() that lookup fails ("extrapolation into the future").
    # stamp=0 tells tf2: "I don't care about exact time, use whatever you have."
    info "Publishing bootstrap initial pose (${INIT_X}, ${INIT_Y}, yaw=${INIT_YAW})..."
    QZ=$(python3 -c "import math; print(math.sin(${INIT_YAW}/2))")
    QW=$(python3 -c "import math; print(math.cos(${INIT_YAW}/2))")
    ros2 topic pub --once /initialpose geometry_msgs/msg/PoseWithCovarianceStamped \
        "{header: {stamp: {sec: 0, nanosec: 0}, frame_id: 'map'}, pose: {pose: {position: {x: ${INIT_X}, y: ${INIT_Y}, z: 0.0}, orientation: {x: 0.0, y: 0.0, z: ${QZ}, w: ${QW}}}, covariance: [0.5,0,0,0,0,0, 0,0.5,0,0,0,0, 0,0,0,0,0,0, 0,0,0,0,0,0, 0,0,0,0,0,0, 0,0,0,0,0,0.1]}}" \
        2>/dev/null && ok "Initial pose published — AMCL bootstrapped" || warn "Initial pose publish failed (AMCL may not be ready yet)"

    # 2) Nav2 (planner + controller + bt_navigator + recoveries)
    info "Starting Nav2 navigation stack..."
    ros2 launch nav2_bringup navigation_launch.py \
        params_file:="$NAV2_PARAMS" \
        use_sim_time:=false &
    PIDS+=($!)
    sleep 3   # initial pause while nodes register with DDS

    # Wait for lifecycle_manager_navigation to reach "active" state.
    # On Jetson Nano DDS discovery takes 5–15s; polling avoids a fixed long sleep.
    info "Waiting for Nav2 lifecycle_manager_navigation to become active..."
    NAV2_ACTIVE=0
    for i in $(seq 1 20); do
        STATE=$(timeout 5 ros2 service call /lifecycle_manager_navigation/get_state \
            lifecycle_msgs/srv/GetState 2>/dev/null || true)
        if echo "$STATE" | grep -q "active"; then
            NAV2_ACTIVE=1
            break
        fi
        sleep 2
    done

    if [[ $NAV2_ACTIVE -eq 1 ]]; then
        ok "Nav2 lifecycle_manager_navigation: ACTIVE — all nodes managed"
    else
        warn "Nav2 lifecycle state not confirmed in 40s — check terminal for errors"
        warn "Debug: ros2 service call /lifecycle_manager_navigation/get_state lifecycle_msgs/srv/GetState"
    fi

elif [[ "$MODE" == "slam" ]]; then
    # ── SLAM mode: slam_toolbox + Nav2 (no static map) ──
    info "Mode: SLAM + Navigation (online mapping)"

    # 1) SLAM Toolbox
    info "Starting SLAM Toolbox (online mode)..."
    ros2 launch slam_toolbox online_async_launch.py \
        slam_params_file:="$SLAM_PARAMS" \
        use_sim_time:=false &
    PIDS+=($!)
    sleep 5

    # 2) Nav2
    info "Starting Nav2 navigation stack..."
    ros2 launch nav2_bringup navigation_launch.py \
        params_file:="$NAV2_PARAMS" \
        use_sim_time:=false &
    PIDS+=($!)
    sleep 3

    info "Waiting for Nav2 lifecycle_manager_navigation to become active..."
    NAV2_ACTIVE=0
    for i in $(seq 1 20); do
        STATE=$(timeout 5 ros2 service call /lifecycle_manager_navigation/get_state \
            lifecycle_msgs/srv/GetState 2>/dev/null || true)
        if echo "$STATE" | grep -q "active"; then
            NAV2_ACTIVE=1
            break
        fi
        sleep 2
    done

    if [[ $NAV2_ACTIVE -eq 1 ]]; then
        ok "Nav2 lifecycle_manager_navigation: ACTIVE (SLAM + navigation)"
    else
        warn "Nav2 lifecycle state not confirmed in 40s — check terminal for errors"
        warn "Debug: ros2 service call /lifecycle_manager_navigation/get_state lifecycle_msgs/srv/GetState"
    fi

elif [[ "$MODE" == "explore" ]]; then
    # ── Explore mode: slam_toolbox + Nav2 + explore_lite ──
    info "Mode: Autonomous exploration (SLAM + Nav2 + explore_lite)"

    # 1) SLAM Toolbox
    info "Starting SLAM Toolbox (online mode)..."
    ros2 launch slam_toolbox online_async_launch.py \
        slam_params_file:="$SLAM_PARAMS" \
        use_sim_time:=false &
    PIDS+=($!)
    sleep 5

    # 2) Nav2
    info "Starting Nav2 navigation stack..."
    ros2 launch nav2_bringup navigation_launch.py \
        params_file:="$NAV2_PARAMS" \
        use_sim_time:=false &
    PIDS+=($!)
    sleep 5

    # 3) explore_lite (frontier exploration)
    info "Starting explore_lite (frontier exploration)..."
    ros2 run explore_lite explore \
        --ros-args \
        -p robot_base_frame:=base_footprint \
        -p costmap_topic:=global_costmap/costmap \
        -p visualize:=true \
        -p planner_frequency:=0.33 \
        -p progress_timeout:=30.0 \
        -p potential_scale:=3.0 \
        -p orientation_scale:=0.0 \
        -p gain_scale:=1.0 \
        -p transform_tolerance:=0.3 \
        -p min_frontier_size:=0.5 &
    PIDS+=($!)

    ok "Explore mode running (SLAM + Nav2 + explore_lite)"
fi

echo ""
echo -e "${GREEN}══════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}🟢 Nav2 stack is running. Press Ctrl+C to stop.${NC}"
echo -e "${GREEN}══════════════════════════════════════════════════════════${NC}"
echo ""
info "Send goals via:"
echo "  ros2 topic pub /goal_pose geometry_msgs/PoseStamped ..."
echo "  or use Gateway visual_search (Nav2 mode)"
echo ""

# Wait for all background processes
wait
