#!/usr/bin/env bash
# ============================================================
#  Install ROS2 Humble (minimal) on WSL2 Ubuntu 22.04
#  Purpose: View SLAM map only (map_server + rviz2)
# ============================================================
set -euo pipefail

echo "========================================="
echo " ROS2 Humble Minimal Install (Map Viewer)"
echo "========================================="

# ── 1. Locale ─────────────────────────────────────────────
echo "[1/5] Setting locale..."
sudo apt-get update && sudo apt-get install -y locales
sudo locale-gen en_US en_US.UTF-8
sudo update-locale LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8
export LANG=en_US.UTF-8

# ── 2. Add ROS2 apt repository ────────────────────────────
echo "[2/5] Adding ROS2 repository..."
sudo apt-get install -y software-properties-common curl
sudo curl -sSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.key \
     -o /usr/share/keyrings/ros-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] \
http://packages.ros.org/ros2/ubuntu $(. /etc/os-release && echo $UBUNTU_CODENAME) main" | \
sudo tee /etc/apt/sources.list.d/ros2.list > /dev/null
sudo apt-get update

# ── 3. Install minimal packages ───────────────────────────
echo "[3/5] Installing ROS2 Humble packages (minimal for map viewing)..."
sudo apt-get install -y --no-install-recommends \
    ros-humble-ros-base \
    ros-humble-rviz2 \
    ros-humble-nav2-map-server \
    ros-humble-nav2-lifecycle-manager \
    ros-humble-nav2-util

# ── 4. Auto-source in bashrc ──────────────────────────────
echo "[4/5] Adding ROS2 source to ~/.bashrc..."
SETUP_LINE="source /opt/ros/humble/setup.bash"
if ! grep -qF "$SETUP_LINE" ~/.bashrc; then
    echo "$SETUP_LINE" >> ~/.bashrc
    echo "  Added to ~/.bashrc"
else
    echo "  Already in ~/.bashrc"
fi

# ── 5. Verify ─────────────────────────────────────────────
echo "[5/5] Verifying installation..."
source /opt/ros/humble/setup.bash
ros2 --help > /dev/null 2>&1 && echo "  ros2 CLI: OK" || echo "  ros2 CLI: FAILED"
which rviz2 > /dev/null 2>&1 && echo "  rviz2:    OK" || echo "  rviz2:    FAILED"

echo ""
echo "========================================="
echo " Installation complete!"
echo " Run: source ~/.bashrc"
echo " Then: bash view_map.sh"
echo "========================================="
