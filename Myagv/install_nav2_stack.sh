#!/usr/bin/env bash
# ============================================================
#  VORA Nav2 + Exploration Stack Install
#  MyAGV — ROS2 Galactic — Ubuntu 20.04 (Jetson Nano)
#  Usage: bash install_nav2_stack.sh
# ============================================================
set -eo pipefail

# ── Colors ──────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { echo -e "${GREEN}[OK]  $*${NC}"; }
info() { echo -e "${YELLOW}[>>]  $*${NC}"; }
err()  { echo -e "${RED}[ERR] $*${NC}"; exit 1; }

# ── 0) Source ROS2 ──────────────────────────────────────────
info "Sourcing ROS2 Galactic..."
[ -f /opt/ros/galactic/setup.bash ] || err "/opt/ros/galactic/setup.bash not found — is ROS2 Galactic installed?"
set +u; source /opt/ros/galactic/setup.bash; set -u
ok "ROS2 Galactic sourced"

# ── Swap guard (Jetson Nano 4 GB) ───────────────────────────
TOTAL_RAM_MB=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
SWAP_MB=$(awk '/SwapTotal/ {print int($2/1024)}' /proc/meminfo)
info "RAM: ${TOTAL_RAM_MB} MB | Swap: ${SWAP_MB} MB"
if (( TOTAL_RAM_MB + SWAP_MB < 5000 )); then
    info "Low memory detected — creating 4 GB swapfile (requires sudo)..."
    if [ ! -f /swapfile ]; then
        sudo fallocate -l 4G /swapfile
        sudo chmod 600 /swapfile
        sudo mkswap /swapfile
    fi
    sudo swapon /swapfile 2>/dev/null || true
    ok "Swap activated"
fi

# ── 1) Nav2 ─────────────────────────────────────────────────
info "Step 1/5 — Installing Nav2..."
sudo apt-get update -q
sudo apt-get install -y \
    ros-galactic-navigation2 \
    ros-galactic-nav2-bringup \
    ros-galactic-nav2-msgs \
    ros-galactic-nav2-map-server \
    ros-galactic-nav2-lifecycle-manager \
    ros-galactic-nav2-bt-navigator \
    ros-galactic-nav2-planner \
    ros-galactic-nav2-controller \
    ros-galactic-nav2-recoveries \
    ros-galactic-nav2-costmap-2d
ok "Nav2 installed"

# ── 2) SLAM Toolbox ─────────────────────────────────────────
info "Step 2/5 — Installing SLAM Toolbox..."
sudo apt-get install -y ros-galactic-slam-toolbox
ok "SLAM Toolbox installed"

# ── 3) robot_localization (EKF) ─────────────────────────────
info "Step 3/5 — Installing robot_localization..."
sudo apt-get install -y ros-galactic-robot-localization
ok "robot_localization installed"

# ── 4) Common TF / BT dependencies ─────────────────────────
info "Step 4/5 — Installing TF2 + BehaviorTree dependencies..."
sudo apt-get install -y \
    ros-galactic-tf2-ros \
    ros-galactic-tf2-geometry-msgs \
    ros-galactic-behaviortree-cpp-v3 \
    ros-galactic-bond \
    ros-galactic-bondcpp
ok "Dependencies installed"

# ── 5) explore_lite (build from source) ─────────────────────
info "Step 5/5 — Building explore_lite (m-explore-ros2)..."
COLCON_WS="${HOME}/colcon_ws"
mkdir -p "${COLCON_WS}/src"

if [ ! -d "${COLCON_WS}/src/m-explore-ros2" ]; then
    # Clone and pin to last galactic-compatible commit (d75bb07, Jul 2022)
    # main branch uses humble+/.hpp headers; this commit uses .h headers
    git clone https://github.com/robo-friends/m-explore-ros2.git \
        "${COLCON_WS}/src/m-explore-ros2" 2>&1 | tail -5
    cd "${COLCON_WS}/src/m-explore-ros2"
    git checkout d75bb07
    cd "${COLCON_WS}"
    ok "m-explore-ros2 cloned and pinned to galactic-compatible commit d75bb07"
else
    info "m-explore-ros2 already cloned — skipping"
fi

cd "${COLCON_WS}"
set +u; source /opt/ros/galactic/setup.bash; set -u
colcon build \
    --packages-select explore_lite \
    --cmake-args -DCMAKE_BUILD_TYPE=Release \
    2>&1 | tail -20
set +u; source "${COLCON_WS}/install/setup.bash"; set -u
ok "explore_lite built"

# ── Persist workspace source in .bashrc ─────────────────────
SETUP_LINE="source ${COLCON_WS}/install/setup.bash"
if ! grep -qF "${SETUP_LINE}" ~/.bashrc; then
    echo "" >> ~/.bashrc
    echo "# VORA colcon workspace" >> ~/.bashrc
    echo "${SETUP_LINE}" >> ~/.bashrc
    ok "Added colcon workspace to ~/.bashrc"
else
    info "colcon workspace already in ~/.bashrc"
fi

# ── 6) Verify ───────────────────────────────────────────────
echo ""
echo "=============================================="
echo " Verification"
echo "=============================================="
set +u; source "${COLCON_WS}/install/setup.bash" 2>/dev/null || true; set -u
FOUND=$(ros2 pkg list 2>/dev/null | grep -E "^(nav2|slam_toolbox|robot_localization|explore_lite)" | sort || true)
if [ -n "${FOUND}" ]; then
    echo -e "${GREEN}${FOUND}${NC}"
    echo ""
    ok "Install complete!"
    echo ""
    echo "  Nav2          → path planning + obstacle avoidance + recovery"
    echo "  SLAM Toolbox  → online incremental mapping (replaces gmapping)"
    echo "  robot_localization → EKF fuse odom → no more dead-reckoning drift"
    echo "  explore_lite  → frontier-based autonomous exploration"
    echo ""
    echo "  explore_lite lives in: ${COLCON_WS}"
    echo "  Remember to:  source ${COLCON_WS}/install/setup.bash"
else
    err "No matching packages found — something may have failed above"
fi
