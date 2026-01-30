# 🤖 VORA MyAGV - Quick Start Guide

**Updated:** 29 มกราคม 2026  
**MyAGV IP:** `192.168.0.111` (Static)

---

## 📋 Prerequisites Checklist

- [x] ✅ Static IP configured (`192.168.0.111`)
- [ ] ROS2 Galactic installed
- [ ] ROSBridge package installed
- [ ] Audio device (ReSpeaker/USB Mic) connected
- [ ] Network connection to Gateway

---

## 🚀 Quick Start (4 Terminals)

### Terminal 0: MyAGV Hardware Driver (Motor Controller)
```bash
source ~/myagv_ros2/install/setup.bash
ros2 run myagv_odometry myagv_odometry_node
```

**Expected output:**
```
Serial buffer cleared successfully
setAutoReportState sending data: fe fe 01 0c 01 0e 
restore sending data: fe fe 01 00 01 02 
[INFO] [myagv_odometry_node]: myAGV initialized successful!
```

**What it does:**
- ✅ Subscribes to `/cmd_vel` (receives motion commands)
- ✅ Communicates with MyAGV via `/dev/ttyS0` (115200 baud)
- ✅ Publishes `/odom` (odometry data)
- ✅ Publishes `/imu` (IMU sensor data)

---

### Terminal 1: ROSBridge WebSocket
```bash
ros2 launch rosbridge_server rosbridge_websocket_launch.xml
```

**Expected output:**
```
[INFO] [rosbridge_websocket]: Rosbridge WebSocket server started on port 9090
```

---

### Terminal 2: VORA Command Executor
```bash
cd ~/ros2_ws  # or your workspace path
source install/setup.bash
ros2 run vora_robot_bridge command_executor
```

**Expected output:**
```
══════════════════════════════════════════════════════════
🤖 VORA Command Executor - Ready
══════════════════════════════════════════════════════════
📥 Listening: /vora/command
📤 Status:    /vora/status
📤 Result:    /vora/result
🚨 Safety:    /cmd_vel
══════════════════════════════════════════════════════════
```

---

### Terminal 3: Audio Stream to Gateway

**⚠️ IMPORTANT: Replace GATEWAY_IP with your Gateway's IP address!**

```bash
# Example 1: Gateway on same network
python3 send_audio_to_gateway.py \
  --gateway-ws ws://192.168.0.113:9001/gw/audio \
  --device "ReSpeaker" \
  --rate 16000

# Example 2: Gateway via Tailscale
python3 send_audio_to_gateway.py \
  --gateway-ws ws://100.102.217.45:9001/gw/audio \
  --device "ReSpeaker" \
  --rate 16000

# Example 3: USB Microphone (Yeti)
python3 send_audio_to_gateway.py \
  --gateway-ws ws://GATEWAY_IP:9001/gw/audio \
  --device "Yeti" \
  --rate 48000
```

**Expected output:**
```
============================================================
[INFO] Connecting to Gateway: ws://GATEWAY_IP:9001/gw/audio
============================================================

[INFO] Sent init config: {'rate': 16000, 'lang': 'th'}
[INFO] 🎤 Recording... Press Ctrl+C to stop
============================================================
```

---

## 🔍 Find Gateway IP

**Option 1: Auto-discover (on Gateway machine)**
```bash
cd Gateway
python3 find_myagv.py
# This also shows Gateway IP!
```

**Option 2: Check manually (on Gateway machine)**
```bash
hostname -I
# or
ip addr show
```

**Option 3: Use Tailscale IP**
```bash
# On Gateway machine
tailscale ip -4
# Example: 100.102.217.45
```

---

## 🛠️ Installation

### 1. Install ROS2 Package
```bash
cd ~/ros2_ws/src
cp -r /path/to/Myagv/vora_robot_bridge .

cd ~/ros2_ws
source /opt/ros/galactic/setup.bash
colcon build --packages-select vora_robot_bridge
source install/setup.bash
```

### 2. Install Python Dependencies
```bash
pip3 install sounddevice numpy websockets
```

### 3. Test Audio Device
```bash
python3 -c "import sounddevice as sd; print(sd.query_devices())"
```

Look for your microphone (e.g., "ReSpeaker", "Yeti", "USB Audio")

---

## ✅ Verification & Testing

### Check ROSBridge is running
```bash
# On MyAGV
ros2 topic list
# Should see: /rosout, /parameter_events, etc.
```

**Test from Gateway machine:**
```bash
# Install roslibpy if needed
pip3 install roslibpy

# Test connection
python3 -c "import roslibpy; client = roslibpy.Ros(host='192.168.0.111', port=9090); client.run(); print('✅ Connected!' if client.is_connected else '❌ Failed'); client.terminate()"
```

### Test VORA Command
```bash
# On MyAGV - test stop command
ros2 topic pub --once /vora/command std_msgs/String \
  "{data: '{\"query_id\":\"test1\",\"intent\":\"stop\"}'}"

# Check result
ros2 topic echo /vora/result --once
```

**Expected output:**
```json
{"query_id": "test1", "status": "completed", "intent": "stop", "result": "stopped"}
```

---

## 🐛 Troubleshooting

### 1. No audio devices found
```bash
# Check devices
python3 -c "import sounddevice as sd; print(sd.query_devices())"

# Test recording
python3 -c "import sounddevice as sd; import numpy as np; data = sd.rec(16000, samplerate=16000, channels=1); sd.wait(); print('✅ Recording works!')"
```

### 2. Cannot connect to Gateway
```bash
# Check network
ping GATEWAY_IP

# Check Gateway is running
curl http://GATEWAY_IP:9001/health
# Should return: {"status":"ok",...}
```

### 3. ROSBridge not working
```bash
# Check if running
ps aux | grep rosbridge

# Restart
pkill -f rosbridge
ros2 launch rosbridge_server rosbridge_websocket_launch.xml
```

### 4. Robot receives /cmd_vel but doesn't move
```bash
# Check if motor controller is running
ros2 node list | grep myagv

# If not listed, start it
source ~/myagv_ros2/install/setup.bash
ros2 run myagv_odometry myagv_odometry_node

# Test manual movement
ros2 topic pub --once /cmd_vel geometry_msgs/Twist \
  "{linear: {x: 0.1, y: 0.0, z: 0.0}, angular: {x: 0.0, y: 0.0, z: 0.0}}"

# Check serial device
ls -la /dev/ttyS0
# Should show: crwxrwxrwx 1 root tty 4, 64

# If device missing, check hardware connection
```

### 5. Static IP lost after reboot
```bash
# Verify connection
nmcli connection show "RA-Admin"

# Should see:
# ipv4.method: manual
# ipv4.addresses: 192.168.0.111/24

# If lost, reapply
sudo nmcli connection up "RA-Admin"
```

---

## 📚 References

- **VORA Server:** `https://user.tail87d9fe.ts.net`
- **Gateway Health:** `http://GATEWAY_IP:9001/health`
- **MyAGV ROSBridge:** `ws://192.168.0.111:9090`
- **Full Deployment Guide:** See `/DEPLOYMENT.md`
- **Static IP Setup:** See `/Myagv/SETUP_STATIC_IP.md`

---

## 🎯 Quick Commands Reference

```bash
# Start all services (4 terminals)
# Terminal 0 - Motor Controller
source ~/myagv_ros2/install/setup.bash
ros2 run myagv_odometry myagv_odometry_node

# Terminal 1
ros2 launch rosbridge_server rosbridge_websocket_launch.xml

# Terminal 2
ros2 run vora_robot_bridge command_executor

# Terminal 3 - UPDATE GATEWAY_IP FIRST!
python3 send_audio_to_gateway.py --gateway-ws ws://GATEWAY_IP:9001/gw/audio --device "ReSpeaker" --rate 16000
```

**Remember:** Always check Gateway IP first with `hostname -I` on Gateway machine!

Publish a find-object request:
```bash
ros2 topic pub /vora/command std_msgs/String "{data: '{"query_id":"t2","intent":"find_object","target":"screwdriver"}'}"
```

Watch outputs:
```bash
ros2 topic echo /vora/status
ros2 topic echo /vora/result
```

## Notes
- Keep this on the robot only. Gateway code stays on the notebook gateway.
- When your vision/mapping modules finish, have them publish `/vora/result` with the same `query_id`.
