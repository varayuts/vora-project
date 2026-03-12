# 📦 Gateway & MyAGV - Final Deployment Recap

**Date:** 29 มกราคม 2026  
**Status:** ✅ Ready for Production Testing

---

## 🌐 Network Configuration (LOCKED)

```yaml
Network: RA-Admin (WiFi 2.4/5GHz)
Router: 192.168.0.1

Devices:
  VORA_Server:
    Platform: A6000 (Ubuntu)
    Tailscale: user.tail87d9fe.ts.net
    Port: 8080
    
  Gateway:
    Platform: Windows Notebook
    WiFi_IP: 192.168.0.60 (STATIC ✅)
    Tailscale_IP: 100.73.232.94 (STATIC ✅)
    Port: 9001
    
  MyAGV:
    Platform: Jetson Nano (Ubuntu 20.04 + ROS2 Galactic)
    WiFi_IP: 192.168.0.111 (STATIC ✅)
    ROSBridge_Port: 9090
```

---

## 📁 Folder 1: Gateway

### 📂 Structure
```
Gateway/
├── .env ✅                         # Config with static IPs
├── start_gateway.sh ✅              # Automated startup script
├── README_DEPLOY.txt ✅             # Full deployment guide
├── SETUP_STATIC_IP_WINDOWS.md ✅    # Windows static IP setup
├── NETWORK_OPTIONS.md ✅            # Network configuration options
├── find_myagv.py ✅                 # Auto-discover MyAGV tool
├── test_gateway.py                 # Testing script
├── gateway/
│   ├── main.py ✅                   # FastAPI server (port 9001)
│   ├── audio_proxy.py ✅            # Proxy audio to VORA
│   ├── intent_parser.py ✅          # Thai → Motion intent
│   ├── ros_cmd.py ✅                # Send commands to ROSBridge
│   ├── waypoint.py ✅               # Create PoseStamped messages
│   └── requirements.txt            # Dependencies
```

### ✅ Configuration Status

**`.env` (Ready)**
```bash
# VORA Server (via Tailscale)
SERVER_BASE=https://user.tail87d9fe.ts.net
SERVER_WS=wss://user.tail87d9fe.ts.net/ws/stt

# MyAGV (Static IP)
ROSBRIDGE=ws://192.168.0.111:9090
CMD_VEL=/cmd_vel
```

### 🚀 How to Run

**Method 1: Automated Script** (Recommended)
```bash
cd Gateway
bash start_gateway.sh
```

**Method 2: Manual**
```bash
cd Gateway
source ../vora_env/bin/activate  # if available
pip install -r gateway/requirements.txt
uvicorn gateway.main:app --host 0.0.0.0 --port 9001
```

### ✅ Verification
```bash
# On Gateway machine
curl http://localhost:9001/health

# From MyAGV
curl http://192.168.0.60:9001/health
```

**Expected Response:**
```json
{
  "status": "ok",
  "server_connected": true,
  "rosbridge": "ws://192.168.0.111:9090"
}
```

---

## 📁 Folder 2: Myagv

### 📂 Structure
```
Myagv/
├── start_myagv.sh ✅                # NEW: Automated 3-terminal startup
├── README_MYAGV_ONLY.md ✅          # Complete setup guide with static IPs
├── SETUP_STATIC_IP.md ✅            # Linux static IP setup (nmcli)
├── send_audio_to_gateway.py ✅      # Stream mic → Gateway
├── vora_robot_bridge/ ✅
│   ├── package.xml
│   ├── setup.py
│   └── vora_robot_bridge/
│       ├── __init__.py
│       └── command_executor.py ✅   # ROS2 node: receive commands
├── save_map.sh                     # Nav2 map saver
├── start_slam.sh                   # Start SLAM (gmapping)
└── teleop_map.sh                   # Teleoperation for mapping
```

### ✅ ROS2 Package Status

**Package:** `vora_robot_bridge`
- **Node:** `command_executor`
- **Subscribes:** `/vora/command` (std_msgs/String - JSON)
- **Publishes:**
  - `/vora/status` (status updates)
  - `/vora/result` (command results)
  - `/cmd_vel` (emergency stop)

**Supported Intents:**
- ✅ `stop` - Immediate stop (implemented)
- 🔧 `navigate` - Navigate to waypoint (placeholder)
- 🔧 `find_object` - Object detection + patrol (placeholder)
- 🔧 `start_slam`, `stop_slam`, `save_map` (placeholders)

### 🚀 How to Run

**Method 1: Automated Script** (Recommended)
```bash
cd Myagv
./start_myagv.sh 192.168.0.60
```

This will:
1. Check prerequisites (ROS2, Python packages, audio device)
2. Verify network connectivity to Gateway
3. Open 3 terminals:
   - Terminal 1: ROSBridge (`ws://192.168.0.111:9090`)
   - Terminal 2: Command Executor (ROS2 node)
   - Terminal 3: Audio Stream → Gateway

**Method 2: Manual** (3 separate terminals)
```bash
# Terminal 1: ROSBridge
ros2 launch rosbridge_server rosbridge_websocket_launch.xml

# Terminal 2: Command Executor
source ~/ros2_ws/install/setup.bash
ros2 run vora_robot_bridge command_executor

# Terminal 3: Audio Streaming
python3 send_audio_to_gateway.py \
  --gateway-ws ws://192.168.0.60:9001/gw/audio \
  --device "ReSpeaker" \
  --rate 16000
```

### ✅ Verification
```bash
# Check ROS topics
ros2 topic list
# Expected: /vora/command, /vora/status, /vora/result, /cmd_vel

# Test stop command
ros2 topic pub --once /vora/command std_msgs/String \
  '{"data":"{\"intent\":\"stop\",\"query_id\":\"test-001\"}"}'

# Check result
ros2 topic echo /vora/result --once
```

**Expected Output:**
```json
{
  "query_id": "test-001",
  "status": "completed",
  "intent": "stop",
  "result": "stopped"
}
```

---

## 🔗 Communication Flow

```
┌─────────────┐
│  Web/Mobile │
│   Browser   │
└──────┬──────┘
       │ HTTPS/WSS
       │ (Tailscale)
       ▼
┌──────────────┐
│ VORA Server  │ ← https://user.tail87d9fe.ts.net
│   (A6000)    │   Port 8080
└──────┬───────┘
       │ HTTP/WS
       │ (Tailscale)
       ▼
┌──────────────┐
│   Gateway    │ ← 192.168.0.60 (Static)
│  (Windows)   │   Port 9001
└──────┬───────┘
       │ ROSBridge WebSocket
       │ ws://192.168.0.111:9090
       ▼
┌──────────────┐
│    MyAGV     │ ← 192.168.0.111 (Static)
│ (Jetson Nano)│   Port 9090 (ROSBridge)
└──────────────┘
```

---

## ✅ What's Updated

### Gateway Folder
- [x] `.env` - Static IP configuration (192.168.0.111)
- [x] `start_gateway.sh` - Added network info display
- [x] `README_DEPLOY.txt` - Updated with static IPs and scripts
- [x] `SETUP_STATIC_IP_WINDOWS.md` - NEW: Windows static IP guide
- [x] `NETWORK_OPTIONS.md` - NEW: Network configuration comparison

### Myagv Folder
- [x] `start_myagv.sh` - NEW: Automated startup script with checks
- [x] `README_MYAGV_ONLY.md` - Complete rewrite with:
  - Static IP configuration (192.168.0.111, 192.168.0.60)
  - Quick Start (3 terminals)
  - Gateway IP discovery methods
  - Troubleshooting guide
- [x] `send_audio_to_gateway.py` - Updated examples with correct IPs
- [x] `SETUP_STATIC_IP.md` - Already configured for 192.168.0.111

---

## 🧪 Pre-Deployment Tests

### Test 1: Network Connectivity ✅
```bash
# On Gateway
ping 192.168.0.111  # Should reply

# On MyAGV
ping 192.168.0.60   # Should reply
```

### Test 2: Gateway Health ✅
```bash
# On Gateway
curl http://localhost:9001/health

# From MyAGV
curl http://192.168.0.60:9001/health
```

### Test 3: ROSBridge Connection ✅
```bash
# On Gateway - test from Python
python3 -c "import roslibpy; client = roslibpy.Ros(host='192.168.0.111', port=9090); client.run(); print('Connected!' if client.is_connected else 'Failed'); client.terminate()"
```

### Test 4: Audio Device ✅
```bash
# On MyAGV
python3 -c "import sounddevice as sd; print(sd.query_devices())"
# Should list ReSpeaker or USB Mic
```

### Test 5: End-to-End Command ✅
```bash
# On MyAGV - after starting all services
ros2 topic pub --once /vora/command std_msgs/String \
  '{"data":"{\"intent\":\"stop\",\"query_id\":\"e2e-test\"}"}'

# Check result
ros2 topic echo /vora/result --once
```

---

## 🚀 Deployment Steps

### Step 1: Prepare Gateway (Windows)
1. Copy `Gateway/` folder to Windows notebook
2. Set static IP: `192.168.0.60` (see `SETUP_STATIC_IP_WINDOWS.md`)
3. Verify `.env` configuration
4. Install dependencies: `pip install -r gateway/requirements.txt`

### Step 2: Prepare MyAGV (Jetson Nano)
1. Copy `Myagv/` folder to Jetson Nano
2. Static IP already set: `192.168.0.111` ✅
3. Build ROS2 package:
   ```bash
   cd ~/ros2_ws/src
   cp -r Myagv/vora_robot_bridge .
   cd ~/ros2_ws
   colcon build --packages-select vora_robot_bridge
   source install/setup.bash
   ```
4. Install Python deps: `pip3 install sounddevice numpy websockets`

### Step 3: Start Services (in order)
1. **VORA Server** (A6000) - should already be running
2. **Gateway** (Windows): `cd Gateway && bash start_gateway.sh`
3. **MyAGV** (Jetson): `cd Myagv && ./start_myagv.sh 192.168.0.60`

### Step 4: Verify
1. Check all health endpoints
2. Test ROS topics
3. Try voice command: "โวร่า หยุด"

---

## 📝 Key Files Reference

| File | Purpose | Location | Status |
|------|---------|----------|--------|
| Gateway/.env | Configuration | Gateway | ✅ Updated |
| start_gateway.sh | Gateway startup | Gateway | ✅ Updated |
| start_myagv.sh | MyAGV startup | Myagv | ✅ NEW |
| README_DEPLOY.txt | Gateway guide | Gateway | ✅ Updated |
| README_MYAGV_ONLY.md | MyAGV guide | Myagv | ✅ Updated |
| command_executor.py | ROS2 node | Myagv/vora_robot_bridge | ✅ Ready |
| send_audio_to_gateway.py | Audio stream | Myagv | ✅ Updated |

---

## 🎯 Next Steps

1. **Deploy to Gateway Windows machine**
2. **Deploy to MyAGV Jetson Nano**
3. **Run pre-deployment tests**
4. **Start services in order**
5. **Test end-to-end voice commands**

---

## 📞 Quick Commands Reference

```bash
# Gateway (Windows)
cd Gateway && bash start_gateway.sh

# MyAGV (Jetson)
cd Myagv && ./start_myagv.sh 192.168.0.60

# Check health
curl http://192.168.0.60:9001/health

# Test ROS command
ros2 topic pub --once /vora/command std_msgs/String '{"data":"{\"intent\":\"stop\",\"query_id\":\"t1\"}"}'
```

---

**✅ Both folders are ready for deployment!** 🚀
