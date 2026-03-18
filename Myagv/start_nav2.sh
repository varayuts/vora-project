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

NAV2_PARAMS="${SCRIPT_DIR}/nav2_params.yaml"
SLAM_PARAMS="${SCRIPT_DIR}/slam_toolbox_params.yaml"

# ── Verify prerequisites ──
info "Checking prerequisites..."
if ! ros2 topic list 2>/dev/null | grep -q "/scan"; then
    err "LiDAR /scan topic not found! Is start_myagv.sh running?"
    exit 1
fi
if ! ros2 topic list 2>/dev/null | grep -q "/odom"; then
    err "Odometry /odom topic not found! Is start_myagv.sh running?"
    exit 1
fi
ok "Prerequisites OK (/scan + /odom active)"

# ── Cleanup on exit ──
PIDS=()
cleanup() {
    info "Shutting down Nav2 stack..."
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

# Quick check: is odom→base_footprint already being published? (EKF running)
# timeout 5 is enough: if TF exists, tf2_echo sees it in < 2s.
# grep -qm1: quiet, stop at first match so tf2_echo gets SIGPIPE and exits.
TF_EXISTS=0
info "Checking if TF odom→base_footprint already exists..."
if timeout 5 ros2 run tf2_ros tf2_echo odom base_footprint 2>&1 | grep -qm1 "Translation"; then
    ok "TF already active (myagv EKF running) — broadcaster not needed"
    TF_EXISTS=1
fi

if [[ $TF_EXISTS -eq 0 ]]; then
    info "Starting odom_tf_broadcaster.py (odom→base_footprint→base_link)..."
    python3 "$BROADCASTER" &
    PIDS+=($!)
    sleep 3  # Essential: wait for broadcaster node init + DDS discovery on Jetson Nano

    # Verify: use 10s timeout — allows tf2_echo to start (~2s) + DDS discovery (~1s)
    # + first TF message (broadcaster publishes at 10Hz = every 100ms)
    info "Verifying TF odom→base_footprint..."
    if timeout 10 ros2 run tf2_ros tf2_echo odom base_footprint 2>&1 | grep -qm1 "Translation"; then
        ok "TF odom→base_footprint confirmed"
    else
        err "TF not verified after broadcaster started"
        err "Debug: ros2 run tf2_ros tf2_echo odom base_footprint"
        err "       ros2 topic echo /odom --once 2>/dev/null | head -5"
        exit 1
    fi
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

    # 2) Nav2 (planner + controller + bt_navigator + recoveries)
    info "Starting Nav2 navigation stack..."
    ros2 launch nav2_bringup navigation_launch.py \
        params_file:="$NAV2_PARAMS" \
        use_sim_time:=false &
    PIDS+=($!)
    sleep 3

    ok "Nav2 running (AMCL + navigation)"

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

    ok "Nav2 running (SLAM + navigation)"

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
        -p robot_base_frame:=base_link \
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
