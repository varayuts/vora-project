# 🤖 VORA - Voice Oriented Robotics Assistant

**Thai Voice-Controlled Laboratory Assistant Robot**

[![Status](https://img.shields.io/badge/Status-Development-yellow)]()
[![Progress](https://img.shields.io/badge/Progress-85%25-green)]()
[![Python](https://img.shields.io/badge/Python-3.10-blue)]()
[![ROS](https://img.shields.io/badge/ROS-Noetic-blue)]()

---

## 📋 Table of Contents

- [Overview](#overview)
- [Quick Start](#quick-start)
- [Project Structure](#project-structure)
- [Documentation](#documentation)
- [Features](#features)
- [System Requirements](#system-requirements)
- [Contributing](#contributing)

---

## 🎯 Overview

VORA is a Thai language voice assistant integrated with a mobile robot (Elephant myAGV 2023) for laboratory assistance tasks. The system uses:

- **Faster-Whisper** for Thai speech recognition
- **Gemma3 LLM** (via Ollama) for intent understanding
- **Thai TTS** for voice responses
- **ROS** for robot control

**Key Innovations:**
- 🇹🇭 Native Thai language support
- ⚡ Low latency (2-3s response time)
- 🤝 Hybrid intent parser (Regex + LLM)
- 💰 Cost-effective (~40,000 THB vs 200,000+ commercial)

---

## 🚀 Quick Start

### Prerequisites
- NVIDIA GPU (tested on A6000)
- Ubuntu 20.04+
- Python 3.10+
- CUDA 12.1+

### Installation

```bash
# Clone repository
git clone <repository-url>
cd VORA

# Create conda environment
conda env create -f environment.yml
conda activate vora

# Start Server
python -m uvicorn app.main:app --host 0.0.0.0 --port 8080

# Start Gateway (Windows PC)
cd Gateway
python gateway/main.py

# Access Web Interface
http://localhost:8080/app
```

See [Documents/DEPLOYMENT_QUICK_START.md](Documents/DEPLOYMENT_QUICK_START.md) for detailed setup.

---

## 📁 Project Structure

```
VORA/
├── app/                        # Main application
│   ├── api/                    # FastAPI endpoints
│   ├── core/                   # Business logic
│   ├── providers/              # LLM, TTS, Search
│   ├── services/               # Thai TTS service
│   ├── schemas/                # Data models
│   └── frontend/               # Web interface
│
├── Gateway/                    # Robot control gateway
│   └── gateway/                # ROSBridge integration
│
├── Myagv/                      # Robot-specific code
│   └── vora_robot_bridge/      # ROS bridge package
│
├── models/                     # AI models
│   └── asr/                    # Whisper models
│
├── Documents/                  # 📚 All documentation
│   ├── Progress/               # Progress reports
│   ├── DEPLOYMENT_QUICK_START.md
│   ├── PROJECT_REVIEW.md
│   └── README.md (main docs)
│
├── tests/                      # 🧪 Test suite
│   ├── test_stt.py
│   ├── test_websocket.py
│   └── test_tts_only.sh
│
├── environment.yml             # Conda environment
└── README.md                   # This file
```

---

## 📚 Documentation

### Quick Links
- **[Presentation](Documents/Progress/PROGRESS_PRESENTATION_JAN2026.md)** - For advisor meetings
- **[Project Review](Documents/PROJECT_REVIEW.md)** - Architecture analysis
- **[Deployment Guide](Documents/DEPLOYMENT_QUICK_START.md)** - Setup instructions
- **[Web App Setup](Documents/WEBAPP_SETUP.md)** - Frontend configuration
- **[Test Guide](tests/README.md)** - How to run tests

### For Developers
- Architecture: See `Documents/PROJECT_REVIEW.md` Section 6
- API Docs: Visit `/docs` endpoint when server running
- Code Style: PEP 8 with async/await patterns

---

## ✨ Features

### Completed ✅
- [x] Thai speech recognition (Faster-Whisper)
- [x] Voice command processing (STT → LLM → TTS)
- [x] Robot motion control (forward, backward, rotate)
- [x] Multi-step command execution
- [x] WebSocket real-time audio streaming
- [x] Hybrid intent parser (Regex + LLM)
- [x] Tailscale VPN deployment
- [x] Web interface (mobile + desktop)

### In Progress 🔄
- [ ] Rotation calibration (97% accuracy, target 99%)
- [ ] Object detection integration (YOLOv8)
- [ ] Advanced navigation (SLAM + waypoints)

### Planned 📋
- [ ] Multi-robot coordination
- [ ] Custom Whisper fine-tuning
- [ ] Production hardening
- [ ] Open-source release

---

## 💻 System Requirements

### Server (AI Processing)
- **GPU:** NVIDIA A6000 (48GB VRAM) or equivalent
- **RAM:** 32GB minimum
- **Storage:** 100GB (for models)
- **OS:** Ubuntu 20.04+

### Gateway (Robot Control)
- **OS:** Windows 11 / Ubuntu 20.04
- **RAM:** 8GB minimum
- **Network:** WiFi + Tailscale VPN

### Robot (MyAGV)
- **Platform:** Jetson Nano (4GB)
- **OS:** Ubuntu 20.04 + ROS Noetic
- **Network:** WiFi (192.168.0.x)

---

## 📊 Performance Metrics

| Metric | Before | After | Target |
|--------|--------|-------|--------|
| **STT Latency** | 10s | 2.5s | <3s ✅ |
| **LLM Inference** | 5s | 2s | <3s ✅ |
| **Total Response** | 15s | 4.5s | <5s ✅ |
| **Intent Accuracy** | 70% | 85% | >90% 🔄 |
| **Rotation Accuracy** | 78% | 97% | >99% 🔄 |

---

## 🛠️ Development

### Run Tests
```bash
# All tests
cd tests
python test_stt.py
python test_websocket.py
./test_tts_only.sh
```

### Check Server Health
```bash
curl http://localhost:8080/health
```

### View Logs
```bash
tail -f /tmp/vora_server.log
```

---

## 🤝 Contributing

This is currently an academic project (Master's thesis). For collaboration:
1. Contact project advisor
2. See `Documents/PROJECT_REVIEW.md` for code guidelines
3. Follow existing code patterns

---

## 📄 License

See [LICENSE](Documents/LICENSE) file.

---

## 👥 Team

**Developer:** นักศึกษาปริญญาโท สาขา IT  
**Advisor:** [ระบุชื่อ]  
**Institution:** มหาวิทยาลัย

---

## 📞 Support

- 📧 Email: [Add email]
- 📚 Documentation: `Documents/INDEX.md`
- 🐛 Issues: See troubleshooting in `Documents/PROJECT_REVIEW.md`

---

**Last Updated:** January 30, 2026  
**Version:** 0.85 (Development)
