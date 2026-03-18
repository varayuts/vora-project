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

# ── TF check ──
# MyAGV driver (start_myagv.sh) already provides the full TF tree:
#   odom → base_footprint (EKF) → base_link (URDF)
#                                → laser_frame (ydlidar static TF)
#                                → camera_link, imu_link
# Do NOT add extra TF publishers here — they conflict and create split trees.
info "Waiting for TF: odom → base_footprint (from myagv driver EKF)..."
TF_OK=0
for i in $(seq 1 30); do
    # tf2_echo in Galactic has no --wait-for-transform flag
    # Run with a 2s timeout, check if Translation line appears
    if timeout 2 ros2 run tf2_ros tf2_echo odom base_footprint 2>&1 | grep -q 'Translation'; then
        ok "TF odom → base_footprint confirmed (attempt $i)"
        TF_OK=1
        break
    fi
    warn "TF not ready yet ($i/30)..."
    sleep 1
done
if [[ $TF_OK -eq 0 ]]; then
    warn "TF odom → base_footprint not found. Trying base_link as fallback..."
    # Some MyAGV firmware versions use odom → base_link directly
    if timeout 2 ros2 run tf2_ros tf2_echo odom base_link 2>&1 | grep -q 'Translation'; then
        ok "TF odom → base_link found — will use base_link as base frame"
        warn "NOTE: nav2_params.yaml uses base_footprint — update if Nav2 fails"
        TF_OK=1
    fi
fi
if [[ $TF_OK -eq 0 ]]; then
    warn "No TF from odom found — launching odom_tf_broadcaster.py as fallback..."
    BROADCASTER="${SCRIPT_DIR}/odom_tf_broadcaster.py"
    if [[ -f "$BROADCASTER" ]]; then
        python3 "$BROADCASTER" &
        PIDS+=($!)
        info "Waiting for odom_tf_broadcaster to publish TF..."
        for j in $(seq 1 15); do
            sleep 2
            if timeout 3 ros2 run tf2_ros tf2_echo odom base_footprint 2>&1 | grep -q 'Translation'; then
                ok "TF odom → base_footprint confirmed via odom_tf_broadcaster.py"
                TF_OK=1
                break
            fi
            warn "Broadcaster TF not visible yet ($j/15)..."
        done
    fi
    if [[ $TF_OK -eq 0 ]]; then
        err "Still no TF after broadcaster fallback."
        err "Is start_myagv.sh running? Check: ros2 run tf2_ros tf2_monitor"
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
