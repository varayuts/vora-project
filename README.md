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

The system is deployed in 3 distributed phases across your infrastructure:

### Prerequisites
- **Server:** GPU-enabled machine running Ollama (Gemma-3 27B + Qwen3-VL 8B)
- **Robot:** MYAGV Elephant Jetson nano 2023 with ROS2 Galactic installed
- **Network:** Connected via Tailscale VPN or Local LAN

### Installation
```bash
git clone https://github.com/varayuts/vora-project.git
cd vora-project
conda env create -f environment.yml
conda activate vora
```

### Phase 1: AI Server
Runs the heavy AI pipelines (Whisper STT, Gemma LLM, Qwen VLM, Typhoon TTS).
```bash
# Start the AI backend server
cd app
python main.py
```

### Phase 2: Gateway Server (Bridge)
Acts as the low-latency WebSocket bridge between the AI Server and the Robot.
```bash
# Start the gateway sync node
cd Gateway
./start_gateway.sh
```

### Phase 3: Robot Hardware (MYAGV)
Launches the ROS2 navigation stack, AMCL localization, and motor drivers on the Jetson Nano.
```bash
cd Myagv
./start_myagv.sh
# In a new terminal, launch navigation:
./start_nav2.sh
```

---

## 📁 Project Structure

```
vora-project/
├── app/                  # Phase 1: AI Server (LLM, VLM, STT, TTS)
│   ├── main.py           # FastAPI server entry point
│   ├── core/             # Intent reasoning & semantic memory
│   └── frontend/         # Web UI chat interface
├── Gateway/              # Phase 2: Gateway Bridge
│   ├── gateway/main.py   # WebSocket handler & ROS2 client wrapper
│   └── start_gateway.sh  # Deployment script
└── Myagv/                # Phase 3: ROS2 Robotics Stack
    ├── start_myagv.sh    # Base hardware & sensor bringup
    ├── start_nav2.sh     # ROS2 Galactic Nav2 stack launcher
    └── maps/             # Pre-mapped environments (lab_room.pgm)
```

---

## 👤 Author

**Varayuts Hattasuwan** — Robotics Engineering, KMUTNB  
[varayutgs@gmail.com](mailto:varayutgs@gmail.com) · [GitHub](https://github.com/varayuts)

---

<div align="center">
<i>Final-Year Project · King Mongkut's University of Technology North Bangkok · 2025–2026</i>
</div>
