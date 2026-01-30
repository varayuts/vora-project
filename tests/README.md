# 🧪 VORA Test Suite

This directory contains test scripts for different components of VORA.

## 📂 Test Files

### Audio & Speech Tests
- **test_stt.py** - STT WebSocket test (simulates audio streaming)
- **test_tts_only.sh** - TTS endpoint test (Thai speech synthesis)

### Network Tests  
- **test_websocket.py** - WebSocket connection test client
- (Add more network tests here)

### Integration Tests
- (Future: End-to-end pipeline tests)
- (Future: Robot command tests)
- (Future: Multi-component tests)

## 🚀 How to Run Tests

### Test STT WebSocket
```bash
cd /home/user/vora_project/VORA/VORA
source vora_env/bin/activate
python tests/test_stt.py
```

### Test TTS Only
```bash
cd /home/user/vora_project/VORA/VORA
./tests/test_tts_only.sh
```

### Test WebSocket Connection
```bash
cd /home/user/vora_project/VORA/VORA
source vora_env/bin/activate
python tests/test_websocket.py
```

## ✅ Expected Results

### test_stt.py
```
🔊 Generating 2s test audio at 16000Hz...
✅ Generated 64000 bytes
📡 Connecting to ws://localhost:8000/ws/stt...
📨 Sending init: {"rate": 16000}
📨 Chunk 1: 6400 bytes
...
✅ Response: {"type": "final", "text": "..."}
```

### test_tts_only.sh
```
=== Testing TTS ===
✅ TTS Success!
/tmp/test_tts.wav: RIFF (little-endian) data, WAVE audio
-rw-rw-r-- 1 user user 52K Jan 30 16:00 /tmp/test_tts.wav
```

### test_websocket.py
```
✅ Connected to ws://localhost:8000/ws/stt
📨 Sent: {'type': 'start_session', ...}
🔊 Sent test audio frame (3200 bytes)
📥 Received: {'type': 'transcript', ...}
```

## 📝 Notes

- Make sure Server is running before tests: `ps aux | grep uvicorn`
- Check logs if tests fail: `tail -f /tmp/vora_server.log`
- All tests use port 8000 by default (change in code if needed)

## 🔜 Future Tests

- [ ] Unit tests for agent.py (intent classification)
- [ ] Integration tests for full pipeline
- [ ] Performance benchmarks (latency measurements)
- [ ] Stress tests (concurrent users)
- [ ] Robot motion accuracy tests

---

**Last Updated:** January 30, 2026
