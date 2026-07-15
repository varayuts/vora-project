# 🧪 Testing Fixes - 30 Jan 2026

## ✅ Changes Made

### 1. Command Executor (command_executor.py)
- ➕ Added motion command handlers: `move_forward`, `move_backward`, `turn_left`, `turn_right`, `strafe_left`, `strafe_right`
- ➕ Added `execute_motion()` method that publishes `/cmd_vel` at 10Hz for specified duration
- ✅ Robot will now move for the FULL duration (e.g., 2 seconds)

**Before:** Published `/cmd_vel` once → robot moved ~0.5s  
**After:** Publishes `/cmd_vel` at 10Hz for 2s → robot moves 2s

### 2. Audio Streaming (send_audio_to_gateway.py)
- ➕ Silence detection with RMS threshold (default: 400)
- ➕ Auto-stop after 3 seconds of silence
- ➕ Smaller default chunk size: 512 frames (was 1024) = **LOWER LATENCY**
- ➕ Latency logging (verbose mode)
- ➕ Better progress logging every 5 seconds
- ✅ Support for both 16kHz (ReSpeaker) and 48kHz (Yeti GX) with auto-resampling

**Latency Improvements:**
- Chunk duration: 512/16000 = 32ms (was 64ms)
- Auto-stop prevents sending unnecessary silence
- Verbose logging shows send latency per chunk

---

## 🧪 Test Plan

### Test 1: Network Latency
```bash
ping -c 10 192.168.0.60
# Expected: avg < 10ms ✅
# Actual: 8.4ms ✅
```

### Test 2: Motor Duration Fix
```bash
# Terminal 1: Start command executor
cd ~/vora_ws
source install/setup.bash
ros2 run vora_robot_bridge command_executor

# Terminal 2: Monitor /cmd_vel
ros2 topic echo /cmd_vel

# Terminal 3: Send command
ros2 topic pub --once /vora/command std_msgs/String \
  '{"data":"{\"query_id\":\"test1\",\"intent\":\"move_forward\",\"params\":{\"duration\":2.0,\"speed\":0.1}}"}'

# Expected: See /cmd_vel messages for 2 seconds
# Result: ___ (fill in after test)
```

### Test 3: Audio Silence Detection
```bash
cd ~/Desktop/VORA_myAGV_only_ros2_package/new

# Test with ReSpeaker (if available)
python3 send_audio_to_gateway.py \
  --gateway-ws ws://192.168.0.60:9001/gw/audio \
  --device "ReSpeaker" \
  --verbose

# OR Test with Yeti GX
python3 send_audio_to_gateway.py \
  --gateway-ws ws://192.168.0.60:9001/gw/audio \
  --device "Yeti GX" \
  --verbose

# Action: Speak for 2-3 seconds, then be silent
# Expected: Auto-stops after 3 seconds of silence
# Result: ___ (fill in after test)
```

### Test 4: End-to-End Voice Command
```bash
# Start full system
./start_myagv.sh 192.168.0.60

# Speak: "วูล่า ไปข้างหน้า"
# Expected: 
# 1. Audio stops after 3s silence
# 2. Gateway processes speech
# 3. Robot moves forward for ~2s
# Result: ___ (fill in after test)
```

---

## 📊 Performance Targets

| Metric | Before | After | Target | Status |
|--------|--------|-------|--------|--------|
| Audio chunk latency | 64ms | 32ms | < 50ms | ✅ |
| Motor duration | 0.5s | ? | 2s (commanded) | ⏳ |
| Auto-stop on silence | Never | ? | 3s | ⏳ |
| Network ping | 8.4ms | 8.4ms | < 10ms | ✅ |

---

## 🔍 Diagnostic Commands

### Check Command Executor is running
```bash
ros2 node list | grep vora
# Should show: /vora_command_executor
```

### Monitor motion commands
```bash
ros2 topic echo /vora/command
ros2 topic echo /vora/result
ros2 topic echo /cmd_vel
```

### Test manual motor control
```bash
# Move forward for 5 seconds
ros2 topic pub --rate 10 /cmd_vel geometry_msgs/Twist \
  "{linear: {x: 0.1, y: 0.0, z: 0.0}, angular: {x: 0.0, y: 0.0, z: 0.0}}" &
sleep 5
pkill -f "topic pub"
```

### Check audio parameters
```bash
# List available devices
python3 -c "import sounddevice as sd; print(sd.query_devices())"

# Test silence threshold
python3 send_audio_to_gateway.py \
  --gateway-ws ws://192.168.0.60:9001/gw/audio \
  --device "ReSpeaker" \
  --silence-threshold 300 \
  --silence-duration 2.0 \
  --verbose
```

---

## 📝 Next Steps

1. ☐ Test motor duration with command executor
2. ☐ Test audio silence detection
3. ☐ Measure end-to-end latency
4. ☐ Fine-tune silence threshold if needed
5. ☐ Document final settings in README

---

## 🐛 If Issues Persist

### Motor still moves < 2s
- Check: Is motor controller (`myagv_odometry_node`) still running?
- Check: Does it receive /cmd_vel messages?
- Debug: Add logging in execute_motion()

### Audio still high latency
- Try even smaller chunks: `--frames 256`
- Check Gateway processing time
- Measure network bandwidth with iperf3

### Silence detection too sensitive
- Increase threshold: `--silence-threshold 600`
- Increase duration: `--silence-duration 4.0`

### Silence detection not working
- Check verbose logs for RMS values
- Test with: `--no-silence-detection` to compare

---

**Updated:** 30 Jan 2026  
**Status:** Fixes implemented, awaiting testing


