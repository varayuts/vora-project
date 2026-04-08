#!/usr/bin/env bash
# ============================================================
#  View SLAM map using map_server + RViz2
#  Usage:  bash view_map.sh [map_yaml] [rviz_config]
#  Example: bash view_map.sh lab_room.yaml rviz_map_only.rviz
# ============================================================
set -eo pipefail

# Source ROS2 (disable -u temporarily — ros setup.bash uses unset vars internally)
export AMENT_TRACE_SETUP_FILES="${AMENT_TRACE_SETUP_FILES:-}"
set +u
source /opt/ros/humble/setup.bash
set -u

# Resolve paths
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MAP_YAML="${1:-${SCRIPT_DIR}/lab_room.yaml}"
RVIZ_CONFIG="${2:-${SCRIPT_DIR}/rviz_map_only.rviz}"

# Convert to absolute paths if relative
[[ "$MAP_YAML" != /* ]] && MAP_YAML="${SCRIPT_DIR}/${MAP_YAML}"
[[ "$RVIZ_CONFIG" != /* ]] && RVIZ_CONFIG="${SCRIPT_DIR}/${RVIZ_CONFIG}"

echo "========================================="
echo " VORA Map Viewer"
echo " Map:  ${MAP_YAML}"
echo " RViz: ${RVIZ_CONFIG}"
echo "========================================="

# Validate files exist
if [[ ! -f "$MAP_YAML" ]]; then
    echo "ERROR: Map YAML not found: $MAP_YAML"
    exit 1
fi
if [[ ! -f "$RVIZ_CONFIG" ]]; then
    echo "WARNING: RViz config not found, launching with default"
    RVIZ_CONFIG=""
fi

# Cleanup function
cleanup() {
    echo ""
    echo "Shutting down..."
    kill 0 2>/dev/null
    wait 2>/dev/null
    echo "Done."
}
trap cleanup EXIT INT TERM

# ── 1. Start lifecycle manager (manages map_server lifecycle) ──
echo "[1/3] Starting lifecycle manager..."
ros2 run nav2_lifecycle_manager lifecycle_manager \
    --ros-args \
    -p node_names:="['map_server']" \
    -p autostart:=true \
    -p use_sim_time:=false &
sleep 2

# ── 2. Start map_server ───────────────────────────────────────
echo "[2/3] Starting map_server..."
ros2 run nav2_map_server map_server \
    --ros-args \
    -p yaml_filename:="$MAP_YAML" \
    -p use_sim_time:=false &
sleep 3

# ── 3. Start RViz2 ────────────────────────────────────────────
echo "[3/3] Launching RViz2..."
if [[ -n "$RVIZ_CONFIG" ]]; then
    rviz2 -d "$RVIZ_CONFIG" &
else
    rviz2 &
fi

echo ""
echo "Map server and RViz2 are running."
echo "Press Ctrl+C to stop."
wait
