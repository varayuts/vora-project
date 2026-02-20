#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
# start_camera.sh - เปิด camera publisher แบบง่าย
# ═══════════════════════════════════════════════════════════════
# ใช้ ros_camera_pub.py แทน usb_cam (ซึ่ง segfault บน Jetson นี้)
#
# Usage:  ./start_camera.sh
# ═══════════════════════════════════════════════════════════════

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

source /opt/ros/galactic/setup.bash 2>/dev/null || true

# Check camera
if [ ! -e /dev/video0 ]; then
    echo "❌ /dev/video0 not found!"
    exit 1
fi

echo ""
echo "📷 Starting Camera Publisher..."
echo "   Device:  /dev/video0"
echo "   Topic:   /camera/compressed  (sensor_msgs/CompressedImage)"
echo "   Quality: JPEG 60"
echo ""
echo "Gateway should subscribe to: /camera/compressed"
echo "Press Ctrl+C to stop."
echo ""

python3 "$SCRIPT_DIR/ros_camera_pub.py"
