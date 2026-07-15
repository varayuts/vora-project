<div align="center">

# 🤖 VORA — Voice-Oriented Robotic Assistant

**Final-Year Project · KMUTNB Robotics Engineering**

[![Python](https://img.shields.io/badge/Python-3.10-3776AB?style=flat-square&logo=python)](https://python.org)
[![ROS2](https://img.shields.io/badge/ROS2-Galactic-22314E?style=flat-square&logo=ros)](https://docs.ros.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-009688?style=flat-square&logo=fastapi)](https://fastapi.tiangolo.com)
[![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)](LICENSE)

> An autonomous indoor mobile robot driven entirely by **Thai voice commands**,  
> powered by a complete multimodal AI pipeline running on **Jetson Nano**.

</div>

---

## 🎯 Overview

VORA is an end-to-end AI-powered robotic assistant that understands **natural Thai speech**, reasons about its environment using **visual language models**, and navigates autonomously using **ROS2 Nav2**.

The system integrates 4 AI models in a real-time pipeline — all running on edge hardware (Jetson Nano).

---

## 🏗️ System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    USER (Thai Voice Input)                   │
└──────────────────────────┬──────────────────────────────────┘
                           │
                    🎤 Whisper STT
                  (Thonburian Thai Model)
                           │
                    🧠 Gemma-3 27B
                  (Intent Reasoning LLM)
                      ┌────┴────┐
                      │        │
               Navigate?   Describe Scene?
                      │        │
               ROS2 Nav2  👁️ Qwen3-VL 8B
               AMCL Path  (Visual Reasoning)
               Planning        │
                      │        │
                    🔊 gTTS (Text-to-Speech)
                           │
                    🤖 Robot Action
              (MYAGV Elephant Jetson nano 2023)
```

---

## 🧩 AI Pipeline Components

| Stage | Model | Role |
|-------|-------|------|
| 🎤 **STT** | Whisper Thonburian | Thai speech → text |
| 🧠 **LLM** | Gemma-3 27B | Intent classification & command reasoning |
| 👁️ **VLM** | Qwen3-VL 8B | Scene description & spatial awareness |
| 🔊 **TTS** | gTTS | Text → Thai audio response |
| 🗺️ **Navigation** | ROS2 Nav2 + AMCL | Path planning & localization |

---

## ⚙️ Tech Stack

```
Backend:        FastAPI · WebSocket · Python 3.10
AI Inference:   Ollama (local LLM/VLM serving)
Robotics:       ROS2 Galactic · Nav2 · AMCL · TF2
Hardware:       MYAGV Elephant Jetson nano 2023
Networking:     Tailscale (gateway-robot VPN tunnel)
```

---

## 🔧 Key Engineering Challenges Solved

- **🔄 Gateway-Robot Sync** — WebSocket heartbeat protocol to prevent command drift between gateway server and ROS2 robot
- **🧠 Stale Memory Management** — Sliding window semantic memory to prevent context accumulation causing LLM hallucination
- **🗺️ Map Consistency** — Dynamic map update pipeline preventing Nav2 planning failures on stale SLAM maps
- **⚡ Edge Deployment** — Optimized inference pipeline to run Qwen3-VL 8B on constrained Jetson Nano hardware

---

## 🚀 Setup & Run

### Prerequisites
- Jetson Nano (2023) with JetPack 5.x
- ROS2 Galactic installed
- Ollama running Gemma-3 27B + Qwen3-VL 8B

### Installation
```bash
git clone https://github.com/varayuts/VORA-robot.git
cd VORA-robot
pip install -r requirements.txt
```

### Run Gateway Server
```bash
python gateway/main.py
```

### Run ROS2 Navigation Stack
```bash
ros2 launch vora_bringup vora.launch.py
```

---

## 📁 Project Structure

```
VORA-robot/
├── gateway/            # FastAPI + WebSocket server
│   ├── main.py
│   ├── llm_handler.py  # Gemma-3 + Qwen3-VL integration
│   └── memory.py       # Semantic memory management
├── ros2_ws/            # ROS2 workspace
│   └── vora_bringup/   # Launch files & Nav2 config
├── stt/                # Whisper Thonburian STT module
├── tts/                # gTTS response module
└── requirements.txt
```

---

## 👤 Author

**Varayuts Hattasuwan** — Robotics Engineering, KMUTNB  
[varayutgs@gmail.com](mailto:varayutgs@gmail.com) · [GitHub](https://github.com/varayuts)

---

<div align="center">
<i>Final-Year Project · King Mongkut's University of Technology North Bangkok · 2025–2026</i>
</div>
