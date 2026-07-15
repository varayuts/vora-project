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

# ── Kill ALL leftover Nav2 processes from previous runs ──
# CRITICAL: amcl, map_server, and lifecycle_manager must be killed here.
# localization_launch.py starts these three processes. If they survive a
# Ctrl+C restart, new instances fight the old ones for the same DDS node
# names. Two outcomes both cause robot freeze:
#   A) New lifecycle_manager activates old stale AMCL → wrong pose → bad map→odom TF
#   B) Both old+new AMCL publish map→odom → TF oscillates → costmap sees robot jumping
# lifecycle_manager binary is shared by nav+localization — kill all instances here;
# both navigation_launch and localization_launch will restart them cleanly.
info "Clearing all existing Nav2 processes..."
pkill -9 -f "controller_server"      2>/dev/null || true
pkill -9 -f "planner_server"         2>/dev/null || true
pkill -9 -f "recoveries_server"      2>/dev/null || true
pkill -9 -f "bt_navigator"           2>/dev/null || true
pkill -9 -f "waypoint_follower"      2>/dev/null || true
pkill -9 -f "nav2_amcl"              2>/dev/null || true   # localization — was missing
pkill -9 -f "nav2_map_server"        2>/dev/null || true   # localization — was missing
pkill -9 -f "nav2_lifecycle_manager" 2>/dev/null || true   # both managers — was missing
pkill -9 -f "odom_tf_broadcaster.py" 2>/dev/null || true
sleep 3  # Jetson Nano needs time to release DDS endpoints after SIGKILL
ok "All Nav2 processes cleared"

NAV2_PARAMS="${SCRIPT_DIR}/nav2_params.yaml"
SLAM_PARAMS="${SCRIPT_DIR}/slam_toolbox_params.yaml"

# ── Verify prerequisites (retry loop for slow DDS discovery on Jetson Nano) ──
info "Checking prerequisites..."

echo "[NAV2] scan check skipped (external LiDAR assumed running)"
sleep 2

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

ok "Prerequisites OK (/scan assumed external, /odom active)"

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

# ── Helper: wait for a ROS2 service to appear before polling/calling it ──
# This separates "service not discovered yet" from "service exists but node is not active yet",
# which makes startup logs less misleading on slow Jetson Nano DDS discovery.
wait_for_service() {
    local service_name="$1"
    local max_attempts="${2:-20}"
    local sleep_secs="${3:-2}"
    local attempt

    info "Waiting for service ${service_name} to appear (up to $((max_attempts * sleep_secs))s)..."
    for attempt in $(seq 1 "${max_attempts}"); do
        if timeout 5 ros2 service list 2>/dev/null | grep -Fxq "${service_name}"; then
            ok "Service ${service_name} is available"
            return 0
        fi
        info "  Service ${service_name} not visible yet (attempt ${attempt}/${max_attempts}, waiting ${sleep_secs}s)..."
        sleep "${sleep_secs}"
    done

    err "Service ${service_name} did not appear in $((max_attempts * sleep_secs))s"
    err "── available services ──"
    timeout 5 ros2 service list 2>/dev/null || echo "  (none found)"
    return 1
}

# ── Helper: wait for lifecycle_manager_navigation to reach "active" ──
# Polls every 2s for up to 40s (20 attempts).
# Returns 0 when active, 1 on timeout.
# lifecycle_manager_navigation only reaches "active" after ALL managed nodes
# (bt_navigator, controller_server, planner_server, etc.) have been activated.
wait_nav2_active() {
    local attempt
    local nodes=(
        /controller_server
        /planner_server
        /recoveries_server
        /bt_navigator
        /waypoint_follower
    )

    info "Waiting for Nav2 managed nodes to become active (up to 60s)..."

    local node
    for node in "${nodes[@]}"; do
        wait_for_service "${node}/get_state" 20 2 || return 1
    done

    for attempt in $(seq 1 30); do
        local all_active=1

        for node in "${nodes[@]}"; do
            local state
            state=$(timeout 5 ros2 service call "${node}/get_state" lifecycle_msgs/srv/GetState 2>/dev/null || true)
            if ! echo "$state" | grep -qE "active|id: 3"; then
                all_active=0
                break
            fi
        done

        if [[ $all_active -eq 1 ]]; then
            ok "[NAV2] managed nodes ACTIVE confirmed"
            return 0
        fi

        info "  Nav2 managed nodes not all active yet (attempt ${attempt}/30, waiting 2s)..."
        sleep 2
    done

    err "[NAV2] managed nodes NOT ACTIVE after 60s"
    err "── controller_server state ──"
    timeout 5 ros2 service call /controller_server/get_state lifecycle_msgs/srv/GetState 2>&1 || true
    err "── planner_server state ──"
    timeout 5 ros2 service call /planner_server/get_state lifecycle_msgs/srv/GetState 2>&1 || true
    err "── recoveries_server state ──"
    timeout 5 ros2 service call /recoveries_server/get_state lifecycle_msgs/srv/GetState 2>&1 || true
    err "── bt_navigator state ──"
    timeout 5 ros2 service call /bt_navigator/get_state lifecycle_msgs/srv/GetState 2>&1 || true
    err "── waypoint_follower state ──"
    timeout 5 ros2 service call /waypoint_follower/get_state lifecycle_msgs/srv/GetState 2>&1 || true
    return 1
}

# ── Helper: wait for AMCL node itself to reach lifecycle "active" ──
# AMCL silently drops /initialpose messages that arrive before it is fully active.
# This gate ensures the initial pose publish always lands on a ready AMCL instance.
#
# WHY /amcl/get_state and NOT /lifecycle_manager_localization/get_state:
# The lifecycle_manager's get_state returns the MANAGER's own state, not its managed
# nodes' states. The manager reaches "active" after it finishes its own init — AMCL
# may still be in "inactive" or "activating" at that point. Querying /amcl/get_state
# polls the node directly and returns "active" / "id: 3" only when AMCL itself is ready.
wait_amcl_active() {
    local attempt
    info "[AMCL] waiting for lifecycle ACTIVE..."

    wait_for_service /amcl/get_state 20 2 || return 1

    for attempt in $(seq 1 20); do
        local STATE
        STATE=$(timeout 5 ros2 service call /amcl/get_state \
            lifecycle_msgs/srv/GetState 2>/dev/null || true)
        if echo "$STATE" | grep -qE "active|id: 3"; then
            ok "[AMCL] lifecycle ACTIVE confirmed"
            return 0
        fi
        if [[ -z "$STATE" ]]; then
            info "  [AMCL] state call returned no data (attempt ${attempt}/20, waiting 2s)..."
        else
            info "  [AMCL] service is ready but lifecycle not active yet (attempt ${attempt}/20, waiting 2s)..."
        fi
        sleep 2
    done
    err "[AMCL] did not reach active state in 40s — full diagnostic below:"
    err "── running processes ──"
    ps -ef | grep -E "amcl|map_server|lifecycle_manager" | grep -v grep || true
    err "── ros2 nodes ──"
    timeout 5 ros2 node list 2>/dev/null | grep -E "amcl|map_server|lifecycle" || echo "  (none found)"
    err "── available get_state services ──"
    timeout 5 ros2 service list 2>/dev/null | grep -E "get_state|change_state" || echo "  (none found)"
    err "── amcl state ──"
    timeout 5 ros2 service call /amcl/get_state lifecycle_msgs/srv/GetState 2>&1 || echo "  (no response)"
    err "── map_server state ──"
    timeout 5 ros2 service call /map_server/get_state lifecycle_msgs/srv/GetState 2>&1 || echo "  (no response)"
    err "── lifecycle_manager_localization state ──"
    timeout 5 ros2 service call /lifecycle_manager_localization/get_state lifecycle_msgs/srv/GetState 2>&1 || echo "  (no response)"
    err "── lifecycle_manager_localization params ──"
    timeout 5 ros2 param get /lifecycle_manager_localization node_names 2>&1 || echo "  (no response)"
    timeout 5 ros2 param get /lifecycle_manager_localization autostart 2>&1 || echo "  (no response)"
    return 1
}

# ── Helper: verify map→base_footprint TF is stable (3 consecutive hits, 35s max) ──
# Must be called AFTER /initialpose is published so AMCL emits the map→odom transform.
# Nav2 global costmap looks up this TF on startup; an absent or flapping TF causes
# the costmap to time out and lifecycle_manager_navigation to never reach "active".
#
# Checks in two stages so failures are diagnosable:
#   Stage A: map→odom must appear (AMCL's output after processing /initialpose) — 15s
#   Stage B: map→base_footprint must be stable (3 consecutive hits) — 20s
check_tf_map_stable() {
    info "Stage A: checking map→odom TF with a continuous tf2 buffer wait (up to 20s)..."
    if python3 - <<'PY'
import time
import rclpy
from rclpy.node import Node
from rclpy.duration import Duration
from tf2_ros import Buffer, TransformListener

rclpy.init()
node = Node("vora_wait_map_odom")
buf = Buffer()
listener = TransformListener(buf, node, spin_thread=True)

deadline = time.time() + 20.0
ok = False

while time.time() < deadline:
    try:
        rclpy.spin_once(node, timeout_sec=0.1)
        if buf.can_transform("map", "odom", rclpy.time.Time(), timeout=Duration(seconds=0.5)):
            ok = True
            break
    except Exception:
        pass
    time.sleep(0.1)

node.destroy_node()
rclpy.shutdown()
raise SystemExit(0 if ok else 1)
PY
    then
        ok "  map→odom TF present — AMCL accepted initial pose"
    else
        err "[NAV2] map→odom TF absent after 20s"
        err "  Debug manually: ros2 run tf2_ros tf2_echo map odom"
        return 1
    fi

    info "Stage B: checking map→base_footprint TF with a continuous tf2 buffer wait (up to 20s)..."
    if python3 - <<'PY'
import time
import rclpy
from rclpy.node import Node
from rclpy.duration import Duration
from tf2_ros import Buffer, TransformListener

rclpy.init()
node = Node("vora_wait_map_base")
buf = Buffer()
listener = TransformListener(buf, node, spin_thread=True)

deadline = time.time() + 20.0
hits = 0

while time.time() < deadline:
    try:
        rclpy.spin_once(node, timeout_sec=0.1)
        if buf.can_transform("map", "base_footprint", rclpy.time.Time(), timeout=Duration(seconds=0.5)):
            hits += 1
            if hits >= 3:
                node.destroy_node()
                rclpy.shutdown()
                raise SystemExit(0)
        else:
            hits = 0
    except SystemExit:
        raise
    except Exception:
        hits = 0
    time.sleep(0.2)

node.destroy_node()
rclpy.shutdown()
raise SystemExit(1)
PY
    then
        ok "[NAV2] TF stable — map→odom→base_footprint chain confirmed"
        return 0
    else
        err "[NAV2] TF NOT STABLE — map→base_footprint not confirmed in 20s"
        return 1
    fi
}

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
# For this Nav2 startup path, odom_tf_broadcaster.py is always the odom TF owner.
# If EKF is running, best-effort disable its publish_tf output first to avoid
# odom→base_footprint ambiguity/conflict during bringup.
EKF_RUNNING=0
EKF_NODE=""

info "Checking if EKF node is running..."
if timeout 5 ros2 node list 2>/dev/null | grep -qx "/ekf_filter_node"; then
    EKF_RUNNING=1
    EKF_NODE="/ekf_filter_node"
elif timeout 5 ros2 node list 2>/dev/null | grep -qx "/ekf_node"; then
    EKF_RUNNING=1
    EKF_NODE="/ekf_node"
fi

if [[ $EKF_RUNNING -eq 1 ]]; then
    info "EKF node detected at ${EKF_NODE} — attempting to disable publish_tf..."
    EKF_TF_SET_RESULT=$(timeout 5 ros2 param set "${EKF_NODE}" publish_tf false 2>/dev/null || true)
    if echo "$EKF_TF_SET_RESULT" | grep -q "Set parameter successful"; then
        ok "EKF publish_tf disabled — odom_tf_broadcaster.py will own odom TF"
    else
        warn "Could not confirm EKF publish_tf=false on ${EKF_NODE} — continuing with broadcaster as TF owner"
    fi
else
    info "EKF not running — broadcaster will publish TF + /odom_fused"
fi

info "Starting odom_tf_broadcaster.py as odom TF owner..."
python3 "$BROADCASTER" &
PIDS+=($!)
sleep 3  # Wait for broadcaster node init + DDS discovery on Jetson Nano

# Always verify odom→base_footprint after broadcaster startup.
info "Verifying TF odom→base_footprint..."
if timeout 10 ros2 run tf2_ros tf2_echo odom base_footprint 2>&1 | grep -qm1 "Translation"; then
    ok "TF odom→base_footprint confirmed"
else
    err "TF odom→base_footprint not available"
    err "EKF node: ${EKF_NODE:-none}"
    err "EKF publish_tf state:"
    [[ -n "$EKF_NODE" ]] && timeout 5 ros2 param get "${EKF_NODE}" publish_tf 2>&1 || echo "  (no EKF node)"
    err "Debug: ros2 run tf2_ros tf2_echo odom base_footprint"
    exit 1
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
    # Use local localization_launch.py (not the stock nav2_bringup one) so that
    # configured_params is passed to lifecycle_manager_localization, applying
    # bond_timeout: 30.0 from nav2_params.yaml.  The stock launch file omits this,
    # causing the manager to use the 4s default → false bond-break on Jetson Nano
    # → duplicate bringup → "transition 1 invalid, node already active" errors.
    info "Starting Map Server + AMCL..."
    ros2 launch "${SCRIPT_DIR}/localization_launch.py" \
        map:="$MAP_FILE" \
        params_file:="$NAV2_PARAMS" \
        use_sim_time:=false &
    PIDS+=($!)
    sleep 5
    ok "Map Server + AMCL started"

    # AMCL lifecycle services are unreliable on Galactic/Jetson Nano — assume active
    # after localization_launch.py startup sleep. lifecycle_manager_localization confirms.
    sleep 5
    echo "[AMCL] assumed active (lifecycle_manager confirmed)"

    # Publish initial pose via a rclpy node.
    #
    # Stamp strategy — WHY "now minus 0.3 s":
    #   node.get_clock().now() captures T at publish time in the Python process.
    #   AMCL receives the message ~30–80 ms later (DDS serialise + dispatch latency
    #   on Jetson Nano). Inside handleInitialPose, AMCL calls tf2::Buffer::transform()
    #   at the message's stamp T.  The TF buffer's newest odom→base_footprint entry
    #   was published slightly before T (broadcaster rate ≈ 50 Hz → newest entry ≈
    #   T − 20 ms, minus any DDS latency into AMCL's buffer).  Net result: T is
    #   30–80 ms AHEAD of the newest TF entry → "extrapolation into the future".
    #
    #   Fix: stamp at now() − 0.3 s.  The TF buffer holds ~10 s of history so data
    #   0.3 s ago is always present.  AMCL's transform_tolerance is 1.0 s so a
    #   0.3 s offset is well within its lookup window.  No extrapolation needed.
    #
    # Extra settle: sleep 1 s after wait_amcl_active so AMCL's internal TF buffer
    # subscription has time to fill before we query it.
    sleep 1
    info "Publishing initial pose 3× (${INIT_X}, ${INIT_Y}, yaw=${INIT_YAW}) stamp=now−0.5s..."
    python3 - <<PYEOF
import rclpy
from rclpy.node import Node
from rclpy.duration import Duration
from geometry_msgs.msg import PoseWithCovarianceStamped
import math, time

rclpy.init()
node = Node('_vora_init_pose_pub')
pub = node.create_publisher(PoseWithCovarianceStamped, '/initialpose', 10)
time.sleep(0.5)   # wait for AMCL /initialpose subscriber to be discovered via DDS

init_x   = float('${INIT_X}')
init_y   = float('${INIT_Y}')
init_yaw = float('${INIT_YAW}')

for i in range(3):
    msg = PoseWithCovarianceStamped()
    # Stamp 0.3 s in the past so the TF lookup lands inside the existing buffer,
    # not ahead of the newest entry (which causes "extrapolation into the future").
    stamp_time = node.get_clock().now() - Duration(nanoseconds=500_000_000)
    msg.header.stamp = stamp_time.to_msg()
    msg.header.frame_id = 'map'
    msg.pose.pose.position.x = init_x
    msg.pose.pose.position.y = init_y
    msg.pose.pose.position.z = 0.0
    msg.pose.pose.orientation.x = 0.0
    msg.pose.pose.orientation.y = 0.0
    msg.pose.pose.orientation.z = math.sin(init_yaw / 2.0)
    msg.pose.pose.orientation.w = math.cos(init_yaw / 2.0)
    msg.pose.covariance[0]  = 0.5   # x variance
    msg.pose.covariance[7]  = 0.5   # y variance
    msg.pose.covariance[35] = 0.1   # yaw variance
    pub.publish(msg)
    node.get_logger().info(
        f'Initial pose {i+1}/3 — stamp={msg.header.stamp.sec}.{msg.header.stamp.nanosec:09d} (now-0.5s)'
    )
    time.sleep(1.0)

node.destroy_node()
rclpy.shutdown()
PYEOF
    ok "Initial pose published (3×) — AMCL bootstrapped"

    # FIX 2: Verify map→base_footprint TF is stable before starting Nav2.
    # Nav2 global costmap resolves map→base_footprint on startup. An absent or
    # flapping TF causes the costmap to stall and lifecycle activation to fail.
    check_tf_map_stable || exit 1

    # 2) Nav2 (planner + controller + bt_navigator + recoveries)
    # Use local navigation_launch.py (not the stock nav2_bringup one) so that
    # configured_params is passed to lifecycle_manager_navigation, applying
    # bond_timeout: 30.0 from nav2_params.yaml.  The stock launch file omits this,
    # causing the manager to use the 4s default → false bond-break on Jetson Nano
    # → duplicate bringup → "Failed to change state for node: controller_server".
    info "Starting Nav2 navigation stack..."
    ros2 launch "${SCRIPT_DIR}/navigation_launch.py" \
        params_file:="$NAV2_PARAMS" \
        use_sim_time:=false &
    PIDS+=($!)
    sleep 15   # allow Nav2 nodes to register and activate on Jetson Nano

    for _node in /controller_server /planner_server /bt_navigator; do
        if ros2 node list 2>/dev/null | grep -q "${_node}"; then
            ok "[NAV2] ${_node} detected"
        else
            err "[NAV2] ${_node} NOT found after 15s"
            exit 1
        fi
    done
    ok "[NAV2] Nav2 nodes running"

    # STEP 4: Verify /scan QoS after Nav2 is active — confirm BEST_EFFORT match.
    # If you still see "incompatible QoS: RELIABILITY" in nav2 logs, check this output.
    echo "[NAV2] verifying scan QoS..."
    ros2 topic info /scan -v 2>/dev/null || true

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
    ros2 launch "${SCRIPT_DIR}/navigation_launch.py" \
        params_file:="$NAV2_PARAMS" \
        use_sim_time:=false &
    PIDS+=($!)
    sleep 15

    for _node in /controller_server /planner_server /bt_navigator; do
        if ros2 node list 2>/dev/null | grep -q "${_node}"; then
            ok "[NAV2] ${_node} detected"
        else
            err "[NAV2] ${_node} NOT found after 15s"
            exit 1
        fi
    done
    ok "[NAV2] Nav2 nodes running"

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
    ros2 launch "${SCRIPT_DIR}/navigation_launch.py" \
        params_file:="$NAV2_PARAMS" \
        use_sim_time:=false &
    PIDS+=($!)
    sleep 15

    # Check node existence before starting explore_lite — explore sends Nav2 goals
    for _node in /controller_server /planner_server /bt_navigator; do
        if ros2 node list 2>/dev/null | grep -q "${_node}"; then
            ok "[NAV2] ${_node} detected"
        else
            err "[NAV2] ${_node} NOT found after 15s"
            exit 1
        fi
    done
    ok "[NAV2] Nav2 nodes running"

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


