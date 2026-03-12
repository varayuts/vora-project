# 🚀 VORA Deployment Quick Reference

**Date:** 29 มกราคม 2026  
**Status:** Ready for Production Testing

---

## 📊 Network Configuration

| Device | Platform | IP Address | Role |
|--------|----------|------------|------|
| **VORA Server** | A6000 (Linux) | `user.tail87d9fe.ts.net` | Main AI Server |
| **Gateway** | Windows Notebook | `192.168.0.60` (Static ✅) | Audio proxy + Command router |
| **MyAGV** | Jetson Nano | `192.168.0.111` (Static ✅) | Robot control |

---

## ⚡ Quick Start

### 1️⃣ Start VORA Server (A6000)
```bash
cd /home/user/vora_project/VORA/VORA
source vora_env/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

**Verify:**
```bash
curl https://user.tail87d9fe.ts.net/health
# Should return: {"status":"ok"}
```

---

### 2️⃣ Start Gateway (Windows Notebook)
```bash
cd Gateway
bash start_gateway.sh
```

**Verify:**
```bash
curl http://localhost:9001/health
# Should return: {"status":"ok","server_connected":true}

# Test from MyAGV
curl http://192.168.0.60:9001/health
```

---

### 3️⃣ Start MyAGV (Jetson Nano)

**Option A: Automated (Recommended)**
```bash
cd Myagv
./start_myagv.sh 192.168.0.60
```

**Option B: Manual (3 terminals)**
```bash
# Terminal 1: ROSBridge
ros2 launch rosbridge_server rosbridge_websocket_launch.xml

# Terminal 2: Command Executor
source ~/ros2_ws/install/setup.bash
ros2 run vora_robot_bridge command_executor

# Terminal 3: Audio Stream
python3 send_audio_to_gateway.py \
  --gateway-ws ws://192.168.0.60:9001/gw/audio \
  --device "ReSpeaker" \
  --rate 16000
```

**Verify:**
```bash
# Check MyAGV can reach Gateway
ping 192.168.0.60

# Check ROSBridge
ros2 topic list
# Should show: /vora/command, /vora/status, /vora/result
```

---

## 🌐 Access Points

| Service | URL | Access From |
|---------|-----|-------------|
| VORA Web UI | `https://user.tail87d9fe.ts.net` | Internet (Tailscale) |
| VORA API | `https://user.tail87d9fe.ts.net/docs` | Internet (Tailscale) |
| Gateway Health | `http://192.168.0.60:9001/health` | Local network |
| Gateway WS | `ws://192.168.0.60:9001/gw/audio` | MyAGV → Gateway |
| MyAGV ROSBridge | `ws://192.168.0.111:9090` | Gateway → MyAGV |

---

## ✅ Pre-Flight Checklist

### Network
- [ ] VORA Server accessible via Tailscale
- [ ] Gateway has static IP: 192.168.0.60
- [ ] MyAGV has static IP: 192.168.0.111
- [ ] Gateway can ping MyAGV: `ping 192.168.0.111`
- [ ] MyAGV can ping Gateway: `ping 192.168.0.60`

### Services
- [ ] VORA Server running on port 8080
- [ ] Gateway running on port 9001
- [ ] ROSBridge running on MyAGV port 9090
- [ ] Command executor subscribed to `/vora/command`

### Configuration
- [ ] Gateway `.env` has correct IPs
- [ ] Audio device working on MyAGV
- [ ] ROS2 workspace sourced on MyAGV

---

## 🧪 End-to-End Test

### Test 1: Network Connectivity
```bash
# On Gateway
ping 192.168.0.111
curl http://192.168.0.60:9001/health

# On MyAGV
ping 192.168.0.60
curl http://192.168.0.60:9001/health
```

### Test 2: Gateway → VORA Server
```bash
# On Gateway
curl -X GET http://localhost:9001/test/server
# Should return VORA server status
```

### Test 3: MyAGV → Gateway → VORA
```bash
# On MyAGV - Test stop command
ros2 topic pub --once /vora/command std_msgs/String \
  '{"data":"{\"intent\":\"stop\",\"query_id\":\"test-001\"}"}'

# Check result
ros2 topic echo /vora/result --once
# Should show: {"status":"completed","intent":"stop"}
```

### Test 4: Voice Command (Full Pipeline)
1. Start audio stream on MyAGV
2. Say Thai wake word: "โวร่า"
3. Say command: "หยุด"
4. Verify robot stops

Expected flow:
```
MyAGV Mic → Gateway → VORA STT → LLM → Command → MyAGV
```

---

## 🐛 Troubleshooting

### Problem: Gateway cannot reach MyAGV
```bash
# Check network
ping 192.168.0.111

# Check ROSBridge
telnet 192.168.0.111 9090

# Check firewall on MyAGV
sudo ufw status
sudo ufw allow 9090/tcp
```

### Problem: MyAGV cannot reach Gateway
```bash
# Check Gateway is listening
netstat -an | grep 9001

# Check firewall on Windows
# Open port 9001 in Windows Firewall
```

### Problem: No audio streaming
```bash
# On MyAGV - check audio device
python3 -c "import sounddevice as sd; print(sd.query_devices())"

# Test recording
python3 -c "import sounddevice as sd; import numpy as np; sd.rec(16000, samplerate=16000, channels=1); sd.wait(); print('OK')"
```

### Problem: VORA Server not responding
```bash
# Check Tailscale
tailscale status

# Check VORA Server
curl https://user.tail87d9fe.ts.net/health

# Check logs on A6000
journalctl -u vora -f
```

---

## 📂 File Locations

### Gateway (Windows)
```
Gateway/
├── .env                    # Config (has static IPs)
├── start_gateway.sh        # Startup script
├── gateway/main.py         # Main server
└── gateway/requirements.txt
```

### MyAGV (Jetson Nano)
```
Myagv/
├── start_myagv.sh          # Automated startup
├── send_audio_to_gateway.py
└── vora_robot_bridge/
    └── command_executor.py  # ROS2 node
```

### VORA Server (A6000)
```
/home/user/vora_project/VORA/VORA/
├── app/main.py             # FastAPI main
├── app/api/stt_ws.py       # STT WebSocket
└── vora_env/               # Python environment
```

---

## 🔄 Restart Services

### Quick Restart Gateway
```bash
pkill -f "uvicorn.*gateway.main"
cd Gateway && bash start_gateway.sh
```

### Quick Restart MyAGV Services
```bash
# Kill all
pkill -f rosbridge
pkill -f command_executor
pkill -f send_audio_to_gateway

# Restart
cd Myagv && ./start_myagv.sh 192.168.0.60
```

### Restart VORA Server
```bash
# On A6000
sudo systemctl restart vora
# or manual:
pkill -f "uvicorn.*app.main"
cd /home/user/vora_project/VORA/VORA
source vora_env/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

---

## 📝 Important Notes

1. **Static IPs are locked** - won't change after reboot ✅
2. **Gateway listens on 0.0.0.0:9001** - accessible from MyAGV
3. **Audio streaming is chunked** - 1024 frames per chunk
4. **Wake word required** - Say "โวร่า" before command
5. **Single query mode** - Web UI stops STT after processing

---

## 📚 Related Documents

- **Gateway Setup:** [Gateway/README_DEPLOY.txt](Gateway/README_DEPLOY.txt)
- **MyAGV Setup:** [Myagv/README_MYAGV_ONLY.md](Myagv/README_MYAGV_ONLY.md)
- **Windows Static IP:** [Gateway/SETUP_STATIC_IP_WINDOWS.md](Gateway/SETUP_STATIC_IP_WINDOWS.md)
- **Linux Static IP:** [Myagv/SETUP_STATIC_IP.md](Myagv/SETUP_STATIC_IP.md)
- **Network Options:** [Gateway/NETWORK_OPTIONS.md](Gateway/NETWORK_OPTIONS.md)

---

## 🎯 Success Criteria

✅ VORA Server responds to health check  
✅ Gateway connects to VORA Server  
✅ Gateway connects to MyAGV ROSBridge  
✅ MyAGV can stream audio to Gateway  
✅ Voice commands trigger robot actions  
✅ Web UI shows responses

**Ready to deploy!** 🚀
