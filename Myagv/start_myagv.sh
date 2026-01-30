#!/bin/bash

# 🤖 VORA MyAGV - Automated Startup Script
# Usage: ./start_myagv.sh [gateway_ip]
# Example: ./start_myagv.sh 192.168.0.60

set -e

# ============================================================
# Color output
# ============================================================
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

print_header() {
    echo -e "${BLUE}══════════════════════════════════════════════════════════${NC}"
    echo -e "${BLUE}$1${NC}"
    echo -e "${BLUE}══════════════════════════════════════════════════════════${NC}"
}

print_success() {
    echo -e "${GREEN}✅ $1${NC}"
}

print_error() {
    echo -e "${RED}❌ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠️  $1${NC}"
}

print_info() {
    echo -e "${BLUE}ℹ️  $1${NC}"
}

# ============================================================
# Configuration
# ============================================================
GATEWAY_IP="${1:-}"
AUDIO_DEVICE="ReSpeaker"
ROS2_WS="${HOME}/ros2_ws"

print_header "🤖 VORA MyAGV Startup"

# ============================================================
# Check Gateway IP
# ============================================================
if [ -z "$GATEWAY_IP" ]; then
    print_error "Gateway IP not provided!"
    echo ""
    echo "Usage: $0 <gateway_ip>"
    echo ""
    echo "Examples:"
    echo "  $0 192.168.0.113        # Local network"
    echo "  $0 100.102.217.45       # Tailscale"
    echo ""
    print_info "To find Gateway IP, run on Gateway machine:"
    echo "  hostname -I"
    echo "  tailscale ip -4"
    exit 1
fi

print_info "Gateway IP: ${GATEWAY_IP}"

# ============================================================
# 1. Check Prerequisites
# ============================================================
print_header "📋 Checking Prerequisites"

# Check ROS2
if ! command -v ros2 &> /dev/null; then
    print_error "ROS2 not found! Please install ROS2 Galactic"
    exit 1
fi
print_success "ROS2 found"

# Check Python packages
if ! python3 -c "import sounddevice" 2>/dev/null; then
    print_warning "sounddevice not installed"
    print_info "Installing: pip3 install sounddevice numpy websockets"
    pip3 install sounddevice numpy websockets
fi
print_success "Python packages OK"

# Check audio device
print_info "Checking audio devices..."
if ! python3 -c "import sounddevice as sd; devices = sd.query_devices(); print([d['name'] for d in devices])" | grep -q "$AUDIO_DEVICE"; then
    print_warning "Audio device '$AUDIO_DEVICE' not found"
    print_info "Available devices:"
    python3 -c "import sounddevice as sd; [print(f\"  - {d['name']}\") for d in sd.query_devices()]"
    echo ""
    read -p "Enter audio device name [ReSpeaker]: " INPUT_DEVICE
    AUDIO_DEVICE="${INPUT_DEVICE:-ReSpeaker}"
fi
print_success "Audio device: $AUDIO_DEVICE"

# Check network connectivity
if ! ping -c 1 -W 2 "$GATEWAY_IP" &> /dev/null; then
    print_error "Cannot reach Gateway at $GATEWAY_IP"
    print_info "Check network connection and Gateway IP"
    exit 1
fi
print_success "Network connectivity OK"

# Check static IP
CURRENT_IP=$(hostname -I | grep -o '192\.168\.0\.[0-9]*' || echo "")
if [ "$CURRENT_IP" != "192.168.0.111" ]; then
    print_warning "Expected IP 192.168.0.111, got: $CURRENT_IP"
    print_info "Static IP might not be configured correctly"
    read -p "Continue anyway? [y/N]: " CONTINUE
    if [[ ! "$CONTINUE" =~ ^[Yy]$ ]]; then
        exit 1
    fi
else
    print_success "Static IP configured: $CURRENT_IP"
fi

# ============================================================
# 2. Source ROS2 Environment
# ============================================================
print_header "🔧 Setting up ROS2 Environment"

if [ -f "/opt/ros/galactic/setup.bash" ]; then
    source /opt/ros/galactic/setup.bash
    print_success "Sourced ROS2 Galactic"
else
    print_error "ROS2 Galactic not found at /opt/ros/galactic"
    exit 1
fi

if [ -f "$ROS2_WS/install/setup.bash" ]; then
    source "$ROS2_WS/install/setup.bash"
    print_success "Sourced workspace: $ROS2_WS"
else
    print_warning "Workspace not found: $ROS2_WS/install/setup.bash"
    print_info "Make sure vora_robot_bridge is built:"
    echo "  cd $ROS2_WS"
    echo "  colcon build --packages-select vora_robot_bridge"
    read -p "Continue anyway? [y/N]: " CONTINUE
    if [[ ! "$CONTINUE" =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# ============================================================
# 3. Check Gateway Health
# ============================================================
print_header "🏥 Checking Gateway Health"

HEALTH_URL="http://${GATEWAY_IP}:9001/health"
print_info "Testing: $HEALTH_URL"

if curl -s --max-time 5 "$HEALTH_URL" | grep -q '"status":"ok"'; then
    print_success "Gateway is healthy"
else
    print_error "Gateway health check failed"
    print_info "Make sure Gateway is running:"
    echo "  cd Gateway"
    echo "  ./start_gateway.sh"
    read -p "Continue anyway? [y/N]: " CONTINUE
    if [[ ! "$CONTINUE" =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# ============================================================
# 4. Start Services
# ============================================================
print_header "🚀 Starting Services"

print_info "This will open 4 terminals:"
echo "  0. MyAGV Hardware Driver (motor control)"
echo "  1. ROSBridge WebSocket (port 9090)"
echo "  2. VORA Command Executor (ROS2 node)"
echo "  3. Audio Stream Client (to Gateway)"
echo ""
read -p "Press Enter to continue..."

# Terminal 0: MyAGV Hardware Driver
print_info "Starting MyAGV Hardware Driver in new terminal..."
gnome-terminal --title="VORA: MyAGV Hardware" -- bash -c "
    echo '══════════════════════════════════════════════════════════';
    echo '🤖 Starting MyAGV Hardware Driver (Motor Controller)';
    echo '══════════════════════════════════════════════════════════';
    echo 'Device: /dev/ttyS0 (115200 baud)';
    echo 'Subscribes: /cmd_vel';
    echo 'Publishes: /odom, /imu';
    echo '══════════════════════════════════════════════════════════';
    source /opt/ros/galactic/setup.bash;
    source ~/myagv_ros2/install/setup.bash;
    echo 'Starting myagv_odometry_node...';
    ros2 run myagv_odometry myagv_odometry_node;
    exec bash
" &
sleep 3

# Terminal 1: ROSBridge
print_info "Starting ROSBridge in new terminal..."
gnome-terminal --title="VORA: ROSBridge" -- bash -c "
    echo '══════════════════════════════════════════════════════════';
    echo '🌉 Starting ROSBridge WebSocket Server';
    echo '══════════════════════════════════════════════════════════';
    source /opt/ros/galactic/setup.bash;
    ros2 launch rosbridge_server rosbridge_websocket_launch.xml;
    exec bash
" &
sleep 2

# Terminal 2: Command Executor
print_info "Starting Command Executor in new terminal..."
gnome-terminal --title="VORA: Command Executor" -- bash -c "
    echo '══════════════════════════════════════════════════════════';
    echo '🤖 Starting VORA Command Executor';
    echo '══════════════════════════════════════════════════════════';
    source /opt/ros/galactic/setup.bash;
    source $ROS2_WS/install/setup.bash 2>/dev/null || true;
    ros2 run vora_robot_bridge command_executor;
    exec bash
" &
sleep 2

# Terminal 3: Audio Client
print_info "Starting Audio Client in new terminal..."
GATEWAY_WS="ws://${GATEWAY_IP}:9001/gw/audio"
gnome-terminal --title="VORA: Audio Stream" -- bash -c "
    echo '══════════════════════════════════════════════════════════';
    echo '🎤 Starting Audio Stream to Gateway';
    echo '══════════════════════════════════════════════════════════';
    echo 'Gateway: $GATEWAY_WS';
    echo 'Device:  $AUDIO_DEVICE';
    echo 'Auto-detecting sample rate...';
    echo '══════════════════════════════════════════════════════════';
    sleep 3;
    cd $(dirname $0);
    python3 send_audio_to_gateway.py \
        --gateway-ws '$GATEWAY_WS' \
        --device '$AUDIO_DEVICE';
    exec bash
" &

sleep 1

# ============================================================
# Done
# ============================================================
print_header "✅ All Services Started"

echo ""
print_success "ROSBridge:         ws://192.168.0.111:9090"
print_success "Command Executor:  Listening on /vora/command"
print_success "Audio Stream:      $GATEWAY_WS"
echo ""
print_info "Check terminal windows for service status"
print_info "Press Ctrl+C in terminals to stop services"
echo ""
print_warning "To stop all services:"
echo "  pkill -f rosbridge"
echo "  pkill -f command_executor"
echo "  pkill -f send_audio_to_gateway"
echo ""
