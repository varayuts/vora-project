#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
# debug_camera.sh - Camera Pipeline Diagnostic Tool
# ═══════════════════════════════════════════════════════════════
# Usage: ./debug_camera.sh
# ═══════════════════════════════════════════════════════════════
set -e

source /opt/ros/galactic/setup.bash 2>/dev/null || true
source ~/myagv_ros2/install/setup.bash 2>/dev/null || true
source ~/ros2_ws/install/setup.bash 2>/dev/null || true

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

ok()   { echo -e "${GREEN}✅ $1${NC}"; }
fail() { echo -e "${RED}❌ $1${NC}"; }
warn() { echo -e "${YELLOW}⚠️  $1${NC}"; }
info() { echo -e "${BLUE}ℹ️  $1${NC}"; }
header() {
    echo ""
    echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
    echo -e "${BLUE}  $1${NC}"
    echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
}

# ═══════════════════════════════════════════════════════════════
header "STEP 1: Check video devices"
# ═══════════════════════════════════════════════════════════════

if [ -e /dev/video0 ]; then
    ok "/dev/video0 exists"
    ls -la /dev/video0
else
    fail "/dev/video0 not found!"
    info "Available video devices:"
    ls -la /dev/video* 2>/dev/null || echo "  (none)"
fi

# List all video devices
info "All video devices:"
for dev in /dev/video*; do
    if [ -e "$dev" ]; then
        echo "  $dev"
        v4l2-ctl --device="$dev" --info 2>/dev/null | grep -E "Card|Driver" | head -2 || true
    fi
done

# ═══════════════════════════════════════════════════════════════
header "STEP 2: Check camera capabilities"
# ═══════════════════════════════════════════════════════════════

if command -v v4l2-ctl &>/dev/null; then
    info "Supported formats for /dev/video0:"
    v4l2-ctl --device=/dev/video0 --list-formats-ext 2>/dev/null | head -40 || warn "Could not query formats"
    
    info "Current format:"
    v4l2-ctl --device=/dev/video0 --get-fmt-video 2>/dev/null || true
else
    warn "v4l2-ctl not installed. Install: sudo apt install v4l-utils"
fi

# ═══════════════════════════════════════════════════════════════
header "STEP 3: Check ROS2 nodes"
# ═══════════════════════════════════════════════════════════════

info "Running ROS2 nodes:"
ros2 node list 2>/dev/null || warn "Could not list nodes"

# ═══════════════════════════════════════════════════════════════
header "STEP 4: Check camera topics"
# ═══════════════════════════════════════════════════════════════

info "All image-related topics:"
ros2 topic list 2>/dev/null | grep -iE "image|camera|compressed" || warn "No image topics found"

info "All topics:"
ros2 topic list 2>/dev/null || warn "Could not list topics"

# ═══════════════════════════════════════════════════════════════
header "STEP 5: Check /image_raw publish rate"
# ═══════════════════════════════════════════════════════════════

info "Measuring /image_raw rate (5 seconds)..."
if timeout 5 ros2 topic hz /image_raw 2>/dev/null | grep -q "average rate"; then
    ok "/image_raw is publishing!"
else
    warn "/image_raw not publishing — is usb_cam running?"
    info "Start camera with:  ./start_camera.sh"
    info "Or manually:  ros2 run usb_cam usb_cam_node_exe --ros-args -p pixel_format:=mjpeg"
fi

info "Measuring /image_raw/compressed rate (5 seconds)..."
if timeout 5 ros2 topic hz /image_raw/compressed 2>/dev/null | grep -q "average rate"; then
    ok "/image_raw/compressed is publishing! (Gateway can use this directly)"
else
    warn "/image_raw/compressed not publishing (needs usb_cam with MJPEG + image_transport)"
fi

# ═══════════════════════════════════════════════════════════════
header "STEP 6: Check /image_raw message details"
# ═══════════════════════════════════════════════════════════════

info "Getting one message from /image_raw (5s timeout)..."
timeout 5 ros2 topic echo /image_raw --no-arr --once 2>/dev/null | grep -E "encoding|width|height|step" || warn "Could not get /image_raw message"

# ═══════════════════════════════════════════════════════════════
header "STEP 7: Check compressed image topics"
# ═══════════════════════════════════════════════════════════════

# Check /image_raw/compressed (auto from usb_cam MJPEG)
if ros2 topic list 2>/dev/null | grep -q "/image_raw/compressed"; then
    ok "/image_raw/compressed topic exists (auto from usb_cam + image_transport)"
    info "Measuring /image_raw/compressed rate (5 seconds)..."
    if timeout 5 ros2 topic hz /image_raw/compressed 2>/dev/null | grep -q "average rate"; then
        ok "/image_raw/compressed is publishing! Gateway can subscribe to this."
    else
        warn "/image_raw/compressed not publishing (usb_cam may not be running)"
    fi
    info "Getting one message format (5s timeout)..."
    timeout 5 ros2 topic echo /image_raw/compressed --no-arr --once 2>/dev/null | grep -E "format" || true
else
    warn "/image_raw/compressed topic NOT found"
    info "Requires: usb_cam with pixel_format:=mjpeg + image_transport installed"
fi

echo ""

# Check /camera/compressed (from camera_bridge node)
if ros2 topic list 2>/dev/null | grep -q "/camera/compressed"; then
    ok "/camera/compressed topic exists (from camera_bridge node)"
    info "Measuring /camera/compressed rate (5 seconds)..."
    if timeout 5 ros2 topic hz /camera/compressed 2>/dev/null | grep -q "average rate"; then
        ok "/camera/compressed is publishing!"
    else
        warn "/camera/compressed not publishing"
    fi
    info "Getting one message format (5s timeout)..."
    timeout 5 ros2 topic echo /camera/compressed --no-arr --once 2>/dev/null | grep -E "format" || true
else
    fail "/camera/compressed topic NOT found"
    info "camera_bridge node is not running."
    info "Start the full pipeline: ./start_camera.sh"
    info "Or manually: ros2 run vora_robot_bridge camera_bridge"
fi

# ═══════════════════════════════════════════════════════════════
header "STEP 8: Check ROSBridge"
# ═══════════════════════════════════════════════════════════════

if ros2 node list 2>/dev/null | grep -q "rosbridge"; then
    ok "ROSBridge is running"
else
    fail "ROSBridge is NOT running"
    info "Start it: ros2 launch rosbridge_server rosbridge_websocket_launch.xml"
fi

# Test ROSBridge WebSocket
info "Testing ROSBridge WebSocket on port 9090..."
if timeout 2 bash -c "echo '' > /dev/tcp/localhost/9090" 2>/dev/null; then
    ok "ROSBridge port 9090 is open"
else
    fail "ROSBridge port 9090 is NOT responding"
fi

# ═══════════════════════════════════════════════════════════════
header "STEP 9: Test subscribe via ROSBridge (Python)"
# ═══════════════════════════════════════════════════════════════

info "Testing compressed image subscription through ROSBridge..."
python3 << 'PYEOF' 2>/dev/null || warn "Python test failed"
import sys
import time
import subprocess

try:
    import roslibpy
except ImportError:
    print("  roslibpy not installed — installing...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "roslibpy", "-q"])
    import roslibpy
    print("  roslibpy installed!")

ros = roslibpy.Ros(host='localhost', port=9090)
try:
    ros.run(timeout=5)
except Exception as e:
    print(f"  ❌ Cannot connect to ROSBridge: {e}")
    sys.exit(0)

if not ros.is_connected:
    print("  ❌ ROSBridge not connected")
    sys.exit(0)

print("  ✅ ROSBridge connected")

results = {}

for topic_name, msg_type in [
    ('/image_raw/compressed', 'sensor_msgs/CompressedImage'),
    ('/camera/compressed',    'sensor_msgs/CompressedImage'),
    ('/image_raw',            'sensor_msgs/Image'),
]:
    counts = [0]
    sizes  = []

    def cb(msg, c=counts, s=sizes):
        c[0] += 1
        data = msg.get('data', '')
        size = len(data) if isinstance(data, (str, bytes, list)) else 0
        s.append(size)
        if c[0] == 1:
            fmt = msg.get('format', msg.get('encoding', '?'))
            print(f"    📸 First frame on {topic_name}: format/encoding={fmt}, ~{size} bytes")

    t = roslibpy.Topic(ros, topic_name, msg_type)
    t.subscribe(cb)
    print(f"  Subscribed to {topic_name!r}, waiting 5s...", flush=True)
    time.sleep(5)
    t.unsubscribe()

    avg = sum(sizes) / len(sizes) if sizes else 0
    if counts[0] > 0:
        status = f"✅ {counts[0]} frames received (avg ~{avg:.0f} bytes)"
    else:
        status = "❌ 0 frames (topic not publishing or ROSBridge dropping)"
    results[topic_name] = status
    print(f"  {topic_name}: {status}")
    print()

ros.close()

print("=== ROSBridge Subscribe Test Results ===")
for k, v in results.items():
    print(f"  {k}: {v}")

# Diagnosis
raw_comp_ok = '✅' in results.get('/image_raw/compressed', '')
cam_comp_ok = '✅' in results.get('/camera/compressed', '')
raw_ok      = '✅' in results.get('/image_raw', '')

if raw_comp_ok or cam_comp_ok:
    topic = '/image_raw/compressed' if raw_comp_ok else '/camera/compressed'
    print(f"\n✅ SOLUTION: Tell Gateway to subscribe to {topic!r}")
elif raw_ok:
    print("\n⚠️  /image_raw works but is too large for Gateway.")
    print("   → Start camera_bridge: ros2 run vora_robot_bridge camera_bridge")
    print("   → Or use ./start_camera.sh")
else:
    print("\n❌ No camera data via ROSBridge.")
    print("   → Start usb_cam:  ros2 run usb_cam usb_cam_node_exe --ros-args -p pixel_format:=mjpeg")
    print("   → Or use ./start_camera.sh")
PYEOF

# ═══════════════════════════════════════════════════════════════
header "STEP 10: Summary & Bandwidth Estimate"
# ═══════════════════════════════════════════════════════════════

echo ""
info "Image size comparison at 640x480 @ 15fps:"
echo "  ┌──────────────────┬───────────────┬──────────────┐"
echo "  │ Format           │ Size/frame    │ Bandwidth    │"
echo "  ├──────────────────┼───────────────┼──────────────┤"
echo "  │ YUYV (raw)       │ ~614 KB       │ ~72 Mbps     │"
echo "  │ RGB8 (raw)       │ ~921 KB       │ ~108 Mbps    │"
echo "  │ MJPEG (camera)   │ ~50-150 KB    │ ~12 Mbps     │"
echo "  │ JPEG Q=60 bridge │ ~30-80 KB     │ ~6 Mbps  ✅  │"
echo "  └──────────────────┴───────────────┴──────────────┘"
echo ""
info "Recommended pipeline:"
echo "  Camera → usb_cam (MJPEG) → /image_raw → camera_bridge → /camera/compressed → ROSBridge → Gateway"
echo ""
info "Gateway should subscribe to: /camera/compressed (sensor_msgs/CompressedImage)"
echo ""


