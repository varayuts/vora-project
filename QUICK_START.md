# ⚡ VORA Project - Quick Start Guide & Troubleshooting

## 🚀 Quick Reference

### Environment Setup

```bash
# Activate environment
source vora_env/bin/activate

# Set required environment variables
export OLLAMA_HOST="http://127.0.0.1:11434"
export OLLAMA_MODEL="gemma3:12b-it-qat"
export SEARXNG_URL="http://127.0.0.1:8080"
export WHISPER_MODEL="/path/to/distill-whisper-th-large-v3-ct2"

# Optional: JSON logging
export LOG_JSON="true"
```

### Start Services (Order Matters!)

```bash
# 1. Start Ollama (if local)
ollama serve
# OR check if it's already running: curl http://127.0.0.1:11434/api/tags

# 2. Start SearXNG (if needed)
docker run -p 8080:8080 searxng/searxng

# 3. Start VORA Backend
cd /home/user/vora_project/VORA/VORA
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# 4. Open Frontend
# Browser: http://localhost:8000/app/frontend/client.html
# OR: Open app/frontend/client.html directly in browser
```

### Key URLs

| Service | URL | Notes |
|---------|-----|-------|
| VORA Backend | http://localhost:8000 | FastAPI with STT/LLM/TTS |
| Health Check | http://localhost:8000/health | Check GPU, models, services |
| Docs | http://localhost:8000/docs | Interactive API documentation |
| Dashboard | http://localhost:9001/gw/dashboard | WebSocket connection (in client.html) |

---

## 🔧 Common Issues & Solutions

### ❌ "ffmpeg not found"

```bash
# Install FFmpeg
apt-get install ffmpeg          # Debian/Ubuntu
brew install ffmpeg              # macOS
conda install ffmpeg            # If using Conda
```

### ❌ "Cannot connect to Ollama"

```bash
# Check if Ollama is running
curl http://127.0.0.1:11434/api/tags

# If not running, start it
ollama serve

# If running on different host
export OLLAMA_HOST="http://10.0.0.5:11434"
```

### ❌ "Whisper model not found"

```bash
# Check model path
ls -la /home/user/vora_project/VORA/VORA/models/asr/distill-whisper-th-large-v3-ct2

# If missing, download it
cd /home/user/vora_project/VORA/VORA
# Run the initialization script or download manually
```

### ❌ STT shows "DISCONNECTED" in UI

**Check in browser console (F12):**

```javascript
// The WebSocket URL might be wrong
console.log("Trying to connect to:", GATEWAY_WS_URL);
// Should be: ws://localhost:9001/gw/dashboard
```

**Is gateway.py running?**
- The client.html expects a gateway at port 9001
- Currently, main.py runs on port 8000
- You need to either:
  1. Change client.html to connect to ws://localhost:8000/ws/stt directly
  2. Run a separate gateway process on port 9001

### ❌ High GPU Memory Usage

```python
# Check current usage
nvidia-smi

# If stuck, kill Ollama/Python and restart
killall ollama python
ps aux | grep -E "ollama|python"
```

---

## 📊 Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    Frontend (client.html)                   │
│                  WebSocket @ localhost:9001                 │
└──────────────────────────┬──────────────────────────────────┘
                           │
                    ┌──────▼──────┐
                    │   Gateway   │  (optional: gateway.py)
                    │   (Port 9001)
                    └──────┬──────┘
                           │
        ┌──────────────────▼──────────────────┐
        │                                     │
    ┌───▼────────────────────────────┐   ┌──▼────────────────┐
    │   VORA Backend (main.py)       │   │ External Services│
    │   Port 8000                     │   │                  │
    │                                 │   │ • Ollama:11434  │
    │  ┌──────────────┐              │   │ • SearXNG:8080  │
    │  │ STT WebSocket│──────────────┼───┤ • Whisper Model │
    │  │ (/ws/stt)    │              │   │                  │
    │  └──────────────┘              │   │ • FFmpeg         │
    │                                 │   │                  │
    │  ┌──────────────┐              │   │                  │
    │  │ LLM Router   │◄─────────────┼───┼──────────────────┘
    │  │ (/llm)       │              │   
    │  └──────────────┘              │   
    │                                 │   
    │  ┌──────────────┐              │   
    │  │ Agent Router │              │   
    │  │ (/agent)     │              │   
    │  └──────────────┘              │   
    │                                 │   
    └─────────────────────────────────┘   
```

---

## 🧪 Testing

### 1. Test STT (Speech-to-Text)

```bash
# Start VORA and open this in terminal
curl -i -N \
  -H "Connection: Upgrade" \
  -H "Upgrade: websocket" \
  http://localhost:8000/ws/stt

# From browser console:
const ws = new WebSocket("ws://localhost:8000/ws/stt");
ws.onopen = () => ws.send(JSON.stringify({rate: 16000}));
ws.onmessage = (e) => console.log(JSON.parse(e.data));
```

### 2. Test Agent Refine

```bash
curl -X POST http://localhost:8000/agent/refine \
  -H "Content-Type: application/json" \
  -d '{"text": "ไปหน้าครับ", "lang_hint": "th"}'

# Expected response:
{
  "clean_text": "ไปที่ประตูหน้า",
  "intent": "navigate",
  "target": "ประตูหน้า",
  "must_search": false
}
```

### 3. Test Agent Answer

```bash
curl -X POST http://localhost:8000/agent/answer \
  -H "Content-Type: application/json" \
  -d '{
    "text": "วันนี้วันอะไร",
    "lang_hint": "th",
    "session_id": "test-session",
    "search_when": "auto"
  }'

# Expected response:
{
  "answer": "วันนี้คือ[LLM response]",
  "refine": {...},
  "sources": []
}
```

### 4. Test Search

```bash
curl -X GET "http://localhost:8000/search/search?q=ปัญญาประดิษฐ์&lang=th-TH"

# Expected response:
{
  "results": [
    {"title": "...", "url": "...", "snippet": "..."},
    ...
  ]
}
```

---

## 📈 Performance Monitoring

### Check GPU Status

```bash
# Real-time GPU monitoring
watch -n 1 nvidia-smi

# Check specific process
nvidia-smi -i 0 -pm 1

# Monitor memory per process
nvidia-smi pmon -c 1
```

### Check Service Health

```bash
# VORA Backend
curl http://localhost:8000/health | jq .

# Ollama
curl http://127.0.0.1:11434/api/tags | jq '.models | length'

# SearXNG
curl http://127.0.0.1:8080/status | jq '.uptime'
```

### Profile Response Time

```bash
time curl -X POST http://localhost:8000/agent/answer \
  -H "Content-Type: application/json" \
  -d '{"text": "สวัสดี"}'

# Output includes:
# real: wall-clock time
# user: CPU time used
# sys:  system time used
```

---

## 🔍 Debugging Tips

### Enable Debug Logging

```bash
# In app/main.py
logging.basicConfig(level=logging.DEBUG)

# Or in environment
export LOG_LEVEL=DEBUG
```

### Check FFmpeg Encoding

```bash
# Test FFmpeg resampling
echo "test audio" | ffmpeg \
  -f s16le -ar 16000 -ac 1 -i pipe:0 \
  -f s16le -ar 16000 -ac 1 pipe:1

# Monitor FFmpeg subprocess
ps aux | grep ffmpeg
```

### Inspect WebSocket Traffic

```javascript
// In browser console
const ws = new WebSocket("ws://localhost:8000/ws/stt");
const original_send = ws.send.bind(ws);
ws.send = function(data) {
    console.log("SEND:", data);
    return original_send(data);
};
ws.onmessage = function(e) {
    console.log("RECV:", e.data);
};
```

---

## 📋 Checklist Before Production

- [ ] Environment variables set correctly (.env file)
- [ ] FFmpeg installed and in PATH
- [ ] Whisper model downloaded and validated
- [ ] Ollama service running with required models
- [ ] SearXNG available (if search enabled)
- [ ] Gateway.py architecture clarified
- [ ] API documentation reviewed (/docs endpoint)
- [ ] Error handling tested (kill Ollama, etc.)
- [ ] GPU memory monitoring active
- [ ] Logs configured (JSON format for production)
- [ ] Rate limiting enabled
- [ ] CORS configured for specific origins
- [ ] WebSocket heartbeat enabled
- [ ] Startup validation passing

---

## 🚨 Emergency Commands

```bash
# Kill all Python processes
pkill -f "python.*app.main"

# Kill Ollama if stuck
pkill ollama

# Clear GPU memory
nvidia-smi --query-compute-apps=pid --format=csv,noheader | xargs kill -9

# Monitor logs in real-time
tail -f /tmp/vora.log | jq .

# Stress test the STT endpoint
for i in {1..100}; do
  echo "Test $i..." &
  curl -X POST http://localhost:8000/agent/answer \
    -H "Content-Type: application/json" \
    -d '{"text":"Test"}' &
done; wait
```

---

## 📞 Support Resources

| Resource | Link | Purpose |
|----------|------|---------|
| FastAPI Docs | https://fastapi.tiangolo.com | Backend framework |
| Ollama | https://ollama.ai | LLM inference |
| Faster-Whisper | https://github.com/guillaumekln/faster-whisper | STT |
| Piper TTS | https://github.com/rhasspy/piper | Text-to-speech |
| WebSockets | https://websockets.readthedocs.io | Real-time comms |

---

**Last Updated:** January 23, 2026  
**Version:** 1.0  
**Status:** Ready for Review & Implementation
