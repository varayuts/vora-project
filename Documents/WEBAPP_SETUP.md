# 📱 VORA Web App Setup Guide

## ✨ What's New
- ✅ Web Audio API for microphone capture
- ✅ Real-time STT transcription
- ✅ Multi-device support (Desktop + Smartphone)
- ✅ Responsive design for mobile

---

## 🚀 Quick Start

### Option 1: One-Command Startup
```bash
cd /home/user/vora_project/VORA/VORA
./start_all.sh
```

### Option 2: Manual Startup

**Terminal 1 - FastAPI Server:**
```bash
cd /home/user/vora_project/VORA/VORA
conda activate vora
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

**Terminal 2 - Frontend HTTP Server:**
```bash
cd /home/user/vora_project/VORA/VORA/app/frontend
python -m http.server 9000 --bind 0.0.0.0
```

---

## 🌐 Access from Different Devices

**Desktop (Same Computer):**
```
http://localhost:9000
```

**Smartphone (Same WiFi Network):**
First, find your computer's IP:
```bash
hostname -I  # Returns: 192.168.x.x (example)
```

Then on your phone's browser:
```
http://192.168.x.x:9000
```

---

## 🎤 How to Use the App

### Desktop / Smartphone
1. **Open** `http://localhost:9000` or `http://192.168.x.x:9000`
2. **Click** 🎤 "Start Listening" button
3. **Allow** microphone access when browser asks
4. **Speak** in Thai
5. **Transcript** appears in the chat
6. **Click** 🧠 "ASK LLM" to process (optional)
7. **Click** ⏹️ "Stop Listening" to end recording

### Features
- **Live Transcript** - See partial transcription in sidebar
- **Real-time Audio** - Capture from phone/desktop microphone
- **Auto-reconnect** - Reconnects to server if connection drops
- **Mobile Responsive** - Works on any screen size

---

## 🔧 Configuration

**Server (app/main.py):**
- Port: **8000**
- Host: **0.0.0.0** (all interfaces)
- Model: Faster-Whisper (Thai)
- LLM: Ollama (gemma3)

**Frontend (app/frontend):**
- Port: **9000**
- Server: Auto-detects from browser URL
- Audio: 16kHz PCM (matching Whisper input)

---

## ⚙️ Audio Pipeline

```
Microphone (16kHz)
    ↓ Web Audio API
PCM Buffer (16-bit signed)
    ↓ WebSocket Binary
Server STT Endpoint
    ↓ Faster-Whisper
Thai Text Transcription
    ↓ JSON Response
Browser Chat Display
```

---

## 🐛 Troubleshooting

### "No response" / "Stuck loading"
- Check if FastAPI server is running: `curl http://localhost:8000/health`
- Check server logs: `tail -f /tmp/vora_server.log`

### Microphone not working
- Allow browser permission (click "Allow" when prompted)
- Check if browser has microphone permission in settings
- Try different browser (Chrome/Firefox work best)

### "Cannot connect to 192.168.x.x"
- Make sure phone and computer on same WiFi
- Disable firewall or allow port 9000
- Check IP with `hostname -I`

### Audio quality poor
- Check microphone placement
- Reduce background noise
- Ensure 16kHz sample rate

---

## 📊 Server Status

Check server health:
```bash
curl http://localhost:8000/health
# Returns: {"status":"ok","gpu_enabled":true,...}
```

Check which IP your computer has:
```bash
hostname -I
```

---

## 🔌 WebSocket Details

**URL:** `ws://localhost:8000/ws/stt`

**Message Format (Browser → Server):**
```json
{
  "type": "start_session",
  "session_id": "web-123456",
  "language": "th"
}
```

**Binary Audio Frames:**
```
PCM16 buffer (Int16Array) sent directly
```

**Response (Server → Browser):**
```json
{
  "type": "partial",
  "text": "สวัสดี"
}
```

---

## 📝 Sample Commands

Get your network IP:
```bash
hostname -I
```

Stop all services:
```bash
pkill -f "uvicorn|http.server"
```

View server logs:
```bash
tail -f /tmp/vora_server.log
```

View frontend logs:
```bash
tail -f /tmp/vora_frontend.log
```

---

## ✅ Checklist

- [ ] FastAPI server running on port 8000
- [ ] HTTP server running on port 9000
- [ ] Can access http://localhost:9000
- [ ] Can access http://192.168.x.x:9000 from phone
- [ ] Microphone permission granted in browser
- [ ] Whisper model loaded (check /health endpoint)
- [ ] Ollama running at http://127.0.0.1:11434

---

## 🎯 Next Steps

1. **Test locally** on desktop first
2. **Share URL** with phone on same network
3. **Grant permissions** when browser asks
4. **Speak Thai** and see transcription in real-time
5. **Monitor logs** for any errors

Happy transcribing! 🎉
