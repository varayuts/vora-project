# 🚀 VORA Deployment Guide
## Gateway + MyAGV Robot - Production Ready

**Updated:** 29 มกราคม 2026  
**Status:** ✅ Ready for Deployment

---

## 📋 System Architecture

```
┌──────────────┐
│  Mobile App  │  (https://user.tail87d9fe.ts.net/app)
└──────┬───────┘
       │ HTTPS/WSS (Tailscale)
       ↓
┌────────────────────────────────────────┐
│  VORA Server (A6000)                    │
│  - FastAPI + WebSocket STT              │
│  - LLM (Ollama gemma3:27b)              │
│  - Pipeline (Intent + Planner)          │
└─────────┬──────────────────────────────┘
          │ HTTP/WS
          ↓
    ┌──────────────┐
    │   Gateway    │  (Port 9001)
    │  - Proxy STT │
    │  - Wake Word │
    │  - ROS Cmd   │
    └──────┬───────┘
           │
    ┌──────┴──────┬──────────────┐
    │             │              │
    ↓             ↓              ↓
┌─────────┐  ┌─────────┐   ┌───────────┐
│ Robot 1 │  │ Robot 2 │   │ Dashboard │
│ (MyAGV) │  │         │   │  (Web)    │
└─────────┘  └─────────┘   └───────────┘
```

---

## 🖥️ Part 1: VORA Server (Already Running)

Server รันอยู่ที่: `https://user.tail87d9fe.ts.net`

**หากต้อง restart:**
```bash
cd /home/user/vora_project/VORA/VORA
./start_tailscale.sh
```

**ตรวจสอบ:**
```bash
curl -s https://user.tail87d9fe.ts.net/health
```

---

## 🌉 Part 2: Gateway Deployment

### 2.1 Prerequisites
- Python 3.8+
- Network access to VORA Server (Tailscale)
- Network access to Robot (ROS2 rosbridge)

### 2.2 Configuration

**📍 Important: Set Static IP on MyAGV First!**

MyAGV (Jetson Nano) needs a static IP so Gateway can always find it.

**Quick Setup:**
```bash
# On Jetson Nano (MyAGV)
# See Myagv/SETUP_STATIC_IP.md for detailed guide

sudo nmcli connection modify "YourWiFi" \
  ipv4.method manual \
  ipv4.addresses "192.168.0.111/24" \
  ipv4.gateway "192.168.0.1" \
  ipv4.dns "8.8.8.8"

sudo nmcli connection down "YourWiFi"
sudo nmcli connection up "YourWiFi"
```

**Alternative: Auto-discover MyAGV**
```bash
# On Gateway machine
cd Gateway
python3 find_myagv.py

# This will scan network and update .env automatically!
```

**Manual Configuration:**

Edit [Gateway/.env](Gateway/.env):
```bash
# VORA Server (Tailscale - Valid HTTPS)
SERVER_BASE=https://user.tail87d9fe.ts.net
SERVER_WS=wss://user.tail87d9fe.ts.net/ws/stt

# Robot Connection (ตรวจสอบ IP ของ myAGV)
ROSBRIDGE=ws://192.168.0.111:9090
CMD_VEL=/cmd_vel

# Debug Mode
DEBUG=1
MOCK_ROBOT=0  # เปลี่ยนเป็น 1 ถ้าทดสอบไม่มีหุ่นจริง
```

### 2.3 Install Dependencies

```bash
cd Gateway
pip install -r gateway/requirements.txt
```

Requirements:
- fastapi
- uvicorn[standard]
- websockets
- python-dotenv
- httpx
- roslibpy (for ROS communication)

### 2.4 Start Gateway

```bash
cd Gateway
./start_gateway.sh
```

**Expected Output:**
```
╔═══════════════════════════════════════════════════════════╗
║              ✅ VORA GATEWAY READY!                       ║
╚═══════════════════════════════════════════════════════════╝

   📡 Endpoints:
      Health:    http://localhost:9001/health
      Audio WS:  ws://localhost:9001/gw/audio
      Dashboard: ws://localhost:9001/gw/dashboard
```

### 2.5 Test Gateway

```bash
curl http://localhost:9001/health
```

Expected response:
```json
{
  "status": "ok",
  "server_connected": true,
  "rosbridge": "ws://192.168.0.111:9090",
  ...
}
```

---

## 🤖 Part 3: MyAGV Robot Deployment

### 3.1 Prerequisites
- Jetson Nano with ROS2 Galactic
- Microphone (ReSpeaker or USB)
- Network connection to Gateway

### 3.2 Setup ROS2 Package

```bash
# On Jetson Nano
cd ~/ros2_ws/src
cp -r /path/to/Myagv/vora_robot_bridge .

cd ~/ros2_ws
source /opt/ros/galactic/setup.bash
colcon build --packages-select vora_robot_bridge
source install/setup.bash
```

### 3.3 Start ROS2 Components

**Terminal 1: ROSBridge**
```bash
ros2 launch rosbridge_server rosbridge_websocket_launch.xml
```

**Terminal 2: VORA Command Executor**
```bash
ros2 run vora_robot_bridge command_executor
```

**Expected Output:**
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

### 3.4 Start Audio Stream

**Terminal 3: Audio Client**
```bash
cd Myagv

# Find Gateway IP (e.g., 192.168.0.113)
python3 send_audio_to_gateway.py \
  --gateway-ws ws://GATEWAY_IP:9001/gw/audio \
  --device "ReSpeaker" \
  --rate 16000
```

**Expected Output:**
```
============================================================
[INFO] Connecting to Gateway: ws://192.168.0.113:9001/gw/audio
============================================================

[INFO] Sent init config: {'rate': 16000, 'lang': 'th'}
[INFO] 🎤 Recording... Press Ctrl+C to stop
============================================================
```

---

## 🎯 Testing End-to-End

### Test 1: Basic Voice Command
1. พูดที่ Robot: **"VORA หยุด"**
2. Expected:
   - Gateway logs: `🎯 COMMAND EXTRACTED: 'หยุด'`
   - Robot logs: `[STOP] query_id=...`
   - Robot stops immediately

### Test 2: Motion Command
1. พูด: **"VORA เดินหน้า"**
2. Expected:
   - Gateway logs: `✅ Motion Intent Detected: forward`
   - Robot moves forward (if motion parser + ROS cmd integrated)

### Test 3: Object Finding
1. พูด: **"VORA หาไขควง"**
2. Expected:
   - Gateway logs: `🧠 Trying LLM Planner...`
   - Server processes with LLM
   - Gateway receives plan and executes

---

## 📊 Monitoring & Debugging

### Check Gateway Logs
```bash
# Terminal with Gateway running will show:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📝 RAW TRANSCRIPT: 'โวร่าหยุด'
🎯 COMMAND EXTRACTED: 'หยุด'
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎮 Trying Motion Parser...
✅ Motion Intent Detected: stop
✅ Motion command executed
```

### Check ROS Topics
```bash
# On Robot
ros2 topic echo /vora/command
ros2 topic echo /vora/status
ros2 topic echo /vora/result
```

### Test Without Robot (Mock Mode)
```bash
# In Gateway/.env
MOCK_ROBOT=1
```
Gateway will log commands without sending to ROS.

---

## 🐛 Troubleshooting

### Problem: Gateway can't connect to Server
**Solution:**
```bash
# Check Tailscale connection
tailscale status

# Test server directly
curl https://user.tail87d9fe.ts.net/health
```

### Problem: Audio not streaming
**Solution:**
```bash
# List audio devices
python3 -c "import sounddevice as sd; print(sd.query_devices())"

# Try without device filter
python3 send_audio_to_gateway.py \
  --gateway-ws ws://GATEWAY_IP:9001/gw/audio
```

### Problem: Robot not responding
**Solution:**
```bash
# 1. Check if MyAGV IP changed (if using DHCP)
# Run auto-discovery:
cd Gateway
python3 find_myagv.py

# 2. Check ROSBridge
ros2 topic list | grep vora

# 3. Test command manually
ros2 topic pub /vora/command std_msgs/String \
  "data: '{\"query_id\":\"test\",\"intent\":\"stop\"}'"
```

### Problem: Gateway can't find MyAGV after reboot
**Solution:**
```bash
# MyAGV IP changed! Need static IP.
# See Myagv/SETUP_STATIC_IP.md

# Quick fix: Re-discover
cd Gateway
python3 find_myagv.py
./start_gateway.sh
```

### Problem: Wake word not detected
**Solution:**
- Check Gateway logs for raw transcript
- Try clearer pronunciation: "โว-ร่า หยุด"
- Add more wake word variants in `gateway/main.py`

---

## 📝 Quick Reference

**URLs:**
- VORA Web App: `https://user.tail87d9fe.ts.net/app`
- Server API: `https://user.tail87d9fe.ts.net/docs`
- Gateway Health: `http://localhost:9001/health`

**Ports:**
- VORA Server: 8080 (internal), 443 (Tailscale)
- Gateway: 9001
- ROSBridge: 9090

**Wake Words:**
`VORA`, `โวร่า`, `โวรา`, `วอร่า`, `โบรา`, etc.

**Quick Commands:**
- `VORA หยุด` - Stop robot
- `VORA เดินหน้า` - Move forward
- `VORA ถอยหลัง` - Move backward
- `VORA หันซ้าย` - Turn left
- `VORA หันขวา` - Turn right
- `VORA หาไขควง` - Find screwdriver (LLM)
- `VORA หาคีม` - Find pliers (LLM)

---

## ✅ Deployment Checklist

### VORA Server
- [ ] Server running at https://user.tail87d9fe.ts.net
- [ ] Health check returns OK
- [ ] WebSocket `/ws/stt` working

### Gateway
- [ ] `.env` configured with correct IPs
- [ ] Dependencies installed
- [ ] Gateway running on port 9001
- [ ] Can connect to VORA Server
- [ ] Can connect to ROSBridge

### MyAGV Robot
- [ ] ROS2 workspace built
- [ ] ROSBridge running (port 9090)
- [ ] command_executor node running
- [ ] Audio stream connected to Gateway
- [ ] Can receive and execute commands

---

**สำเร็จ! ระบบพร้อม deploy แล้ว** 🚀
