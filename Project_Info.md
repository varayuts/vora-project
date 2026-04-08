# VORA — Project Information Export

**Export Date:** 2026-03-26  
**Source:** Stored session memories + conversation history + codebase analysis

---

## 1. Instructions

_No persistent user-scoped instructions were stored in memory. All session memories are technical analysis notes (auto-cleared). No "always do X" / "never do Y" rules were recorded._

---

## 2. Identity

_No personal identity information (name, age, location, education) was stored in memory. The user communicates primarily in Thai and works in an IT Lab environment at an educational institution (implied by advisor meetings, thesis/เล่มจบ references, and experiment design tasks)._

---

## 3. Career

_No explicit career information stored. Context implies: graduate student (master's or senior undergraduate) working on a robotics thesis project with an advisor, approaching a deadline ("เหลือเวลาไม่ถึงเดือนก่อนต้องเก็บ experiments และทำเล่มจบ" — less than a month before experiments and thesis book are due)._

---

## 4. Projects

### VORA — Voice Oriented Robotics Assistant

**What it does:** A Thai-language voice-controlled laboratory assistant robot. Users speak commands in Thai → the system transcribes speech, reasons about intent via LLM, controls a mobile robot to navigate and find objects using vision, and responds via Thai TTS. Full pipeline: Voice → STT → LLM Reasoning → VLM Perception → Navigation → TTS Response.

**Current Status (March 26, 2026):** ~85% complete. Core pipeline working end-to-end. Robot can successfully find objects via voice command (e.g., "หาขวดน้ำ"), navigate toward them, and report back. Major bugs fixed (forward STUCK, AMCL relocalize, map distortion, VLM parrot). Nav2 stack configured but not yet fully deployed/tested on hardware. Less than 1 month until thesis deadline — need to collect experiment data.

---

## 5. Preferences

_No explicit broad preferences stored. Working-style observations from sessions:_

- [unknown] - Communicates in Thai for discussion, code comments mix Thai/English
- [unknown] - Prefers fixing root causes over workarounds — deep code analysis before patching
- [unknown] - Uses sequential test-fix-verify cycles (Test 7→8→9→10→11, each with log analysis)
- [unknown] - Tracks progress meticulously in dated markdown files (PROGRESS_DDMMMYYYY.md)

---

---

# VORA — Complete Project Information

## Table of Contents

1. [Project Overview](#project-overview)
2. [System Architecture](#system-architecture)
3. [Hardware Inventory](#hardware-inventory)
4. [Software Stack](#software-stack)
5. [Codebase Structure](#codebase-structure)
6. [Key Modules Detail](#key-modules-detail)
7. [AI Models](#ai-models)
8. [Data Flow](#data-flow)
9. [Navigation System](#navigation-system)
10. [LiDAR System](#lidar-system)
11. [Visual Search Pipeline](#visual-search-pipeline)
12. [Frontend / Webapp](#frontend--webapp)
13. [Configuration & Environment Variables](#configuration--environment-variables)
14. [Key Constants & Parameters](#key-constants--parameters)
15. [Development Timeline](#development-timeline)
16. [Test History](#test-history)
17. [Bug Fix History](#bug-fix-history)
18. [Advisor Feedback & Experiment Design](#advisor-feedback--experiment-design)
19. [Known Issues & Remaining Work](#known-issues--remaining-work)
20. [Performance Metrics](#performance-metrics)

---

## Project Overview

**Full Name:** VORA — Voice Oriented Robotics Assistant  
**Type:** Thai Voice-Controlled Laboratory Assistant Robot  
**Goal:** Enable Thai-speaking users to control a mobile robot via voice commands to find objects, navigate spaces, and perform laboratory assistance tasks.  
**Cost:** ~40,000 THB (vs 200,000+ commercial alternatives)  
**Language:** Thai-first (bilingual Thai/English reasoning)

**Key Innovations:**
- 🇹🇭 Native Thai language support (STT + TTS + LLM reasoning in Thai)
- ⚡ Low latency (2-3s STT, ~4.5s total response)
- 🤝 Hybrid intent parser (Regex + LLM fallback)
- 👁️ VLM-primary navigation (camera + LiDAR fusion for object search)
- 💰 Cost-effective (~40,000 THB hardware budget)

---

## System Architecture

### 3-Tier Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                        TIER 1: SERVER                            │
│                   (NVIDIA A6000, Ubuntu)                          │
│                                                                  │
│  FastAPI (port 8080)                                             │
│  ├── STT: Faster-Whisper (distil-whisper-th-large-v3)           │
│  ├── LLM: Gemma3:27b-it-qat (via Ollama)                       │
│  ├── VLM: Qwen3-VL:32b (via Ollama)                            │
│  ├── TTS: gTTS (Thai)                                           │
│  ├── Agent Pipeline (intent → plan → execute)                   │
│  ├── Map Router (SLAM map serving, robot position tracking)     │
│  ├── Camera Router (frame proxy from Gateway)                   │
│  └── Frontend (index.html — dashboard webapp)                   │
│                                                                  │
│  Access: https://user.tail87d9fe.ts.net (Tailscale VPN)         │
└──────────────────────┬───────────────────────────────────────────┘
                       │ HTTPS / WSS (Tailscale)
                       │
┌──────────────────────┴───────────────────────────────────────────┐
│                       TIER 2: GATEWAY                            │
│                   (Windows PC, 192.168.0.60)                     │
│                                                                  │
│  FastAPI (port 9001)                                             │
│  ├── Visual Search Agent (LLM-driven multi-step object search)  │
│  ├── Motion Control (cmd_vel publisher via ROSBridge)            │
│  ├── Obstacle Avoidance (LiDAR 360° real-time)                  │
│  ├── Camera Stream (ROS image → JPEG → Server)                  │
│  ├── Nav2 Client (action client for path planning)              │
│  ├── Intent Parser (regex + multi-intent)                       │
│  ├── Object Memory (found objects persistence)                  │
│  ├── Spatial Memory (exploration history)                       │
│  └── Odom/AMCL Tracking (pose fusion + dead reckoning)          │
│                                                                  │
│  ROSBridge: ws://192.168.0.111:9090                             │
└──────────────────────┬───────────────────────────────────────────┘
                       │ ROSBridge WebSocket
                       │ WiFi (192.168.0.x LAN)
                       │
┌──────────────────────┴───────────────────────────────────────────┐
│                        TIER 3: ROBOT                             │
│                  (Jetson Nano 4GB, 192.168.0.111)                │
│                                                                  │
│  ROS2 Galactic                                                   │
│  ├── MyAGV Driver (Mecanum wheels, encoder odometry)            │
│  ├── YDLidar G2 Driver (360° LiDAR, /scan topic)               │
│  ├── USB Camera (640×480, /image_raw topic)                     │
│  ├── ROSBridge Server (port 9090)                               │
│  ├── odom_tf_broadcaster.py (TF: odom→base_footprint→base_link)│
│  ├── Nav2 Stack (AMCL + bt_navigator + controllers)            │
│  └── SLAM Toolbox (map generation)                              │
│                                                                  │
│  Robot: Elephant Robotics MyAGV 2023 (Mecanum, 21cm × 26cm)    │
└──────────────────────────────────────────────────────────────────┘
```

### Network Topology

```
Internet ←→ Tailscale VPN ←→ A6000 Server (HTTPS :8080)
                                    │
                              Tailscale tunnel
                                    │
                              Gateway PC (:9001)
                                    │
                              WiFi LAN 192.168.0.x
                                    │
                              MyAGV Robot (:9090 ROSBridge)
```

---

## Hardware Inventory

### Server (AI Processing)
| Component | Specification |
|-----------|--------------|
| GPU | NVIDIA RTX A6000 (48GB VRAM) |
| RAM | 32GB+ |
| Storage | 100GB+ (for models) |
| OS | Ubuntu 20.04+ |
| CUDA | 12.1+ |
| Network | Tailscale VPN |

### Gateway (Robot Control Bridge)
| Component | Specification |
|-----------|--------------|
| Platform | Windows 11 PC |
| IP | 192.168.0.60 |
| Port | 9001 (FastAPI) |
| RAM | 8GB+ |
| Network | WiFi (LAN) + Tailscale VPN |

### Robot (MyAGV)
| Component | Specification |
|-----------|--------------|
| Platform | Elephant Robotics MyAGV 2023 |
| Computer | NVIDIA Jetson Nano 4GB |
| OS | Ubuntu 20.04 + ROS2 Galactic |
| Drive | 4× Mecanum wheels (omnidirectional) |
| Dimensions | 21cm × 26cm |
| LiDAR | YDLidar G2 (360°, 0.1-12m, ~5Hz) |
| Camera | USB Camera (640×480, /image_raw) |
| IP | 192.168.0.111 |
| ROSBridge | Port 9090 |

### Robot LiDAR Specifications (YDLidar G2)
- Range: 0.1m – 12.0m
- Scan Rate: ~5Hz
- ~230 rays per scan (depends on angular resolution)
- Topic: `/scan` (sensor_msgs/LaserScan)
- Frame: `laser_frame`
- Mounting: Forward-facing (0° = forward)
- Dead Zone: ±0–15° (physical obstruction, skipped in clearance calculations)
- Mirror: `LIDAR_MIRROR=1` (default ON — corrects left/right swap from mounting)

---

## Software Stack

### Server Dependencies (environment.yml)
```yaml
name: vora
channels: [pytorch, nvidia, conda-forge, defaults]
dependencies:
  - python=3.11
  - pytorch + torchvision + torchaudio (CUDA 12.1)
  - fastapi, uvicorn, websockets
  - numpy, pydub, ffmpeg, libsndfile
  - pip:
    - faster-whisper
    - piper-tts
    - httpx==0.27.2
    - python-multipart
    - trafilatura, readability-lxml (web content extraction)
```

### Gateway Dependencies
```
fastapi, uvicorn, httpx
roslibpy (ROSBridge client)
numpy, Pillow (image processing)
python-dotenv
websockets
```

### Robot Software
- ROS2 Galactic
- YDLidar ROS2 driver
- MyAGV ROS2 driver (custom firmware)
- Nav2 (navigation2, SLAM Toolbox)
- ROSBridge Server

---

## Codebase Structure

```
VORA/
├── app/                          # Main Server Application
│   ├── main.py                   # FastAPI entry point
│   ├── api/                      # API Routers
│   │   ├── stt_ws.py             # WebSocket STT endpoint
│   │   ├── llm_router.py         # LLM inference endpoints
│   │   ├── vlm_router.py         # VLM vision endpoints (Qwen3-VL)
│   │   ├── agent_router.py       # Agent pipeline router
│   │   ├── plan_router.py        # Planning router
│   │   ├── pipeline_router.py    # Full pipeline router
│   │   ├── server_router.py      # Server APIs (TTS, Queue, State)
│   │   ├── camera_router.py      # Camera proxy from Gateway
│   │   ├── map_router.py         # SLAM map + robot position
│   │   └── robot_planner.py      # Robot planning router
│   ├── core/                     # Business Logic
│   │   ├── settings.py           # Configuration (Ollama, TTS, Gateway)
│   │   ├── agent.py              # Agent logic
│   │   ├── session_manager.py    # Session management
│   │   ├── state_manager.py      # State tracking
│   │   ├── vora_pipeline.py      # Pipeline orchestration
│   │   ├── vora_memory.py        # Memory system
│   │   ├── memory.py             # Chat memory
│   │   ├── command_queue.py      # Command queue
│   │   └── text_preprocessor.py  # Thai text preprocessing
│   ├── providers/                # External Service Providers
│   │   ├── llm/
│   │   │   ├── base.py           # LLM base class
│   │   │   ├── ollama.py         # Ollama LLM provider
│   │   │   └── qwen_vlm.py       # Qwen3-VL provider
│   │   ├── tts/
│   │   │   └── typhoon2.py       # Typhoon2 TTS (disabled)
│   │   └── search/               # Search providers
│   ├── services/
│   │   └── thai_tts.py           # Thai TTS service (gTTS)
│   ├── schemas/                  # Pydantic models
│   │   ├── agent.py, llm.py, search.py
│   └── frontend/                 # Web Interface
│       ├── index.html            # Main dashboard (3-column layout)
│       ├── debug.html            # Debug interface
│       └── https_server.py       # HTTPS server
│
├── Gateway/                      # Robot Control Gateway
│   └── gateway/
│       ├── main.py               # Gateway WebSocket server + visual search agent
│       ├── intent_parser.py      # Regex-based Thai command parser
│       ├── ros_cmd.py            # ROS command executor (cmd_vel, TF)
│       ├── obstacle_avoidance.py # LiDAR obstacle detection (360° sectors)
│       ├── camera_stream.py      # ROS camera → JPEG streaming
│       ├── nav2_client.py        # Nav2 action client (path planning)
│       ├── object_memory.py      # Found object persistence
│       ├── spatial_memory.py     # Exploration history (310 lines)
│       ├── waypoint.py           # Waypoint management
│       ├── audio_proxy.py        # STT audio proxy
│       └── requirements.txt      # Gateway dependencies
│
├── Myagv/                        # Robot-Specific Code (Jetson Nano)
│   ├── odom_tf_broadcaster.py    # TF publisher + dead reckoning
│   ├── nav2_params.yaml          # Nav2/AMCL configuration
│   ├── slam_toolbox_params.yaml  # SLAM parameters
│   ├── start_myagv.sh            # Base driver + LiDAR + ROSBridge
│   ├── start_nav2.sh             # Nav2 stack launcher (3 modes)
│   ├── start_slam.sh             # SLAM mode launcher
│   ├── start_camera.sh           # Camera publisher
│   ├── ros_camera_pub.py         # Camera ROS2 node
│   ├── zone_mapper.py            # Zone mapping utility
│   ├── maps/                     # SLAM maps (lab_room.yaml/pgm)
│   └── vora_robot_bridge/        # ROS2 bridge package
│
├── models/                       # AI Models
│   └── asr/                      # Whisper STT model (distil-whisper-th-large-v3-ct2)
│
├── Documents/                    # Documentation
│   ├── Progress/                 # Dated progress reports
│   │   ├── PROGRESS_12FEB2026.md ... PROGRESS_25MAR2026.md
│   │   ├── ADVISOR_FEEDBACK_26FEB2026.md
│   │   ├── ADVISOR_SUMMARY_07MAR2026.md
│   │   └── Advisor.md            # Advisor task list
│   ├── AI_PROMPT_*.txt           # LLM prompt templates
│   └── check_deployment.sh       # Health check script
│
├── tests/                        # Test Suite
│   ├── test_stt.py               # STT WebSocket test
│   ├── test_websocket.py         # Connection test
│   └── test_tts_only.sh          # TTS test
│
├── environment.yml               # Conda environment spec
├── README_NEW.md                 # Main README
├── STRUCTURE.md                  # Folder structure docs
└── start_dev.sh                  # Dev environment startup
```

---

## Key Modules Detail

### Gateway/gateway/main.py (~3000+ lines)
The largest and most critical file. Contains:
- **Visual Search Agent** — LLM/VLM-driven multi-step object search
- **Motion Control** — `_rotate_deg()`, `_nav2_forward()`, forward/backward execution
- **Obstacle Avoidance Integration** — LiDAR pre-scan, blocked direction handling
- **Odom/AMCL Tracking** — pose fusion, dead reckoning, AMCL suppress
- **Server WebSocket** — bidirectional communication with Server
- **Camera Frame Management** — grabbing frames for VLM analysis
- **LLM Plan Action** — prompts LLM to choose next search action
- **Approach Phase** — `_search_found()` — walk toward detected object
- **Stuck Detection** — odom delta check, auto-reverse, escape rotation

### Gateway/gateway/obstacle_avoidance.py
- 12 sectors × 30° = 360° LiDAR coverage
- `find_best_direction()` — scored ranking of passable directions
- `get_forward_clearance()` — forward distance (skips ±0-15° dead zone)
- `check_and_avoid()` — reactive obstacle response
- `can_robot_fit()` — checks if robot width fits in direction
- `is_obstacle_detected` / `min_distance` — real-time obstacle state
- Environment vars: `LIDAR_OFFSET_DEG`, `LIDAR_MIRROR`, `SERVER_BASE`

### Gateway/gateway/ros_cmd.py
- `MotionPublisher` — publishes `/cmd_vel` Twist at 10Hz
- `exec_motion()` — timed motion with LiDAR interrupt callback
- `ensure_ros()` — singleton ROSBridge connection (asyncio lock)
- Stop command sent 3× after motion (reliability for WebSocket drops)
- `MIN_ROTATE_DUR = 0.5s` — minimum rotation duration for Mecanum response

### Gateway/gateway/intent_parser.py
- Regex-based Thai/English command parser
- Supports: forward, backward, turn left/right, stop, search
- `_parse_degree()` — extracts angle from Thai text, regex `([+-]?\d+)`
- `ROTATION_CALIBRATION = 0.87` (after fine-tuning)
- `ANGULAR_SPEED = 0.50 rad/s`

### Myagv/odom_tf_broadcaster.py
- Publishes TF: `odom → base_footprint → base_link`
- **Dead Reckoning Fix:** Integrates `/cmd_vel` velocity at 20Hz for x,y (hardware odom x,y = 0)
- Publishes `/odom_fused` topic for Nav2
- Watchdog: stops robot if no `/cmd_vel` for 1s (threshold 0.05 to avoid noise spam)

### app/core/settings.py
```python
OLLAMA_HOST = "http://127.0.0.1:11434"
OLLAMA_MODEL = "gemma3:27b-it-qat"          # Main reasoning LLM
OLLAMA_REFINE_MODEL = "gemma3:27b-it-qat"   # Text cleaning
OLLAMA_VLM_MODEL = "qwen3-vl:32b"           # Vision-Language Model
OLLAMA_TIMEOUT = 600                          # 10 minutes
OLLAMA_KEEP_ALIVE = "30m"
OLLAMA_JSON_MAX_TOKENS = 200
MEMORY_TTL_MIN = 60
MEMORY_MAX_TURNS = 12
MEMORY_MAX_CHARS = 4000
TTS_BACKEND = "gtts"
GATEWAY_URL = "http://192.168.0.60:9001"
```

---

## AI Models

| Model | Purpose | Size | Framework |
|-------|---------|------|-----------|
| **Gemma3:27b-it-qat** | Main LLM reasoning + text cleaning | ~16GB | Ollama |
| **Qwen3-VL:32b** | Vision-Language Model (scene description) | ~20GB | Ollama |
| **distil-whisper-th-large-v3-ct2** | Thai STT (speech-to-text) | ~1.5GB | Faster-Whisper (CTranslate2) |
| **gTTS** | Thai TTS (text-to-speech) | Cloud API | Google TTS |

### Model Evolution
- VLM: qwen3-vl:8b (6.1GB) → **qwen3-vl:32b** (20GB) — upgraded March 9 for better accuracy + less prompt echo
- LLM: Was separate 12b model for refine → merged to 27b to avoid VRAM model swap
- VLM prompts changed from Thai → English (with `/no_think`) — eliminated ~50% prompt echo rate

### GPU VRAM Layout (~48GB total)
```
Gemma3:27b-it-qat    ~16GB
Qwen3-VL:32b         ~20GB
Faster-Whisper        ~1.5GB
PyTorch overhead      ~2-3GB
─────────────────────────────
Total                 ~40GB / 48GB available
```

---

## Data Flow

### Voice Command Pipeline
```
User speaks Thai
    ↓
Browser: MediaRecorder → WebSocket (PCM 16kHz)
    ↓
Server: /ws/stt → Faster-Whisper → Thai text
    ↓
Server: Agent → Intent classification (regex + LLM)
    ├── Simple command → direct execution
    └── "find object" → visual search
            ↓
        Server → Gateway WebSocket: {cmd: "search", target: "ขวดน้ำ"}
            ↓
        Gateway: visual_search() agent loop
            ↓
        LiDAR scan + VLM check + LLM plan → motion commands
            ↓
        ROSBridge → /cmd_vel → MyAGV motors
            ↓
        Result → Server → TTS → User hears Thai response
```

### Camera Frame Pipeline
```
MyAGV USB Camera → /image_raw (ROS2 YUYV)
    ↓ ROSBridge
Gateway: camera_stream.py → YUYV→JPEG conversion
    ↓ POST /camera/frame
Server: camera_router.py → stores latest frame
    ↓ GET /camera/frame (polling)
Frontend: displays in <img> @ ~10fps
```

### Robot Position Pipeline
```
MyAGV Encoders → /odom (theta only reliable, x,y ≈ 0)
    ↓
odom_tf_broadcaster.py → dead reckoning x,y from /cmd_vel
    ↓ TF: odom→base_footprint→base_link
AMCL → /amcl_pose (map frame, corrected position)
    ↓ ROSBridge
Gateway: _amcl_pose_callback + _odom_callback
    ↓ Pose fusion: AMCL > odom_xy (if moving) > dead_reckoning
    ↓ POST /map/pose (every 500ms)
Server: map_router.py → stores _robot_pose
    ↓ GET /map/state (frontend polls every 500ms)
Frontend: worldToCanvas() → renders on SLAM map canvas
```

---

## Navigation System

### TF Frame Chain
```
map
 └→ odom (published by AMCL)
      └→ base_footprint (published by odom_tf_broadcaster from /odom + DR)
           └→ base_link (identity static TF)
                └→ laser_frame (published by ydlidar driver, x=0.065, z=0.08, yaw=π)
```

### Pose Source Priority (Gateway)
1. **AMCL** (map frame) — highest priority when available
2. **Odom x,y** (if `_odom_xy_moving = True`, verified >5cm drift)
3. **Dead Reckoning** — always runs for x,y (integrated from cmd_vel)
4. **Theta** always from /odom (encoder/IMU more accurate than DR)

### Nav2 Configuration (nav2_params.yaml)
```yaml
amcl:
  robot_model_type: "omni"              # Mecanum IS omnidirectional
  max_particles: 2000
  min_particles: 500
  max_beams: 60
  laser_max_range: 12.0                 # YDLidar G2
  laser_min_range: 0.1
  laser_model_type: "likelihood_field"
  set_initial_pose: false               # Use /initialpose topic
  update_min_d: 0.1                     # 10cm
  update_min_a: 0.2                     # ~11°

controller_server:
  max_vel_x: 0.15                       # m/s (conservative for indoor)
  max_vel_theta: 0.50                   # rad/s
  trans_stopped_velocity: 0.05          # was 0.15 = max_vel! fixed
  
local_costmap:
  robot_radius: 0.115                   # 21cm / 2 ≈ 10.5cm + margin
  
recovery_server:
  required_movement_radius: 0.15        # was 0.30m, reduced for tight spaces
```

### start_nav2.sh Modes
```bash
./start_nav2.sh            # Default: localization + navigation
./start_nav2.sh --slam     # SLAM mode (build new map)
./start_nav2.sh --explore  # SLAM + explore_lite (auto-explore)
```

### AMCL Initial Pose Bootstrap
- `INIT_X`, `INIT_Y`, `INIT_YAW` environment variables
- Uses `stamp: {sec: 0, nanosec: 0}` (tf2 TimePointZero) to bypass TF timing gap
- Retry loop 3× with 2s gap
- `sleep 20s` after launch for TF warming

### Dead Reckoning (Hardware Limitation)
MyAGV Mecanum wheel encoders provide **reliable theta (yaw)** but **x,y stays near 0**. The system works around this:
1. `odom_tf_broadcaster.py` integrates `/cmd_vel` at 20Hz for x,y
2. Gateway's `_update_dead_reckoning()` also tracks x,y from motion commands
3. AMCL overwrites x,y when it publishes (highest accuracy)

---

## LiDAR System

### Obstacle Avoidance Architecture
```
YDLidar G2 → /scan → ROSBridge → Gateway (obstacle_avoidance.py)
```

### 12-Sector System
- 360° divided into 12 sectors × 30° each
- Centers: 15°, 45°, 75°, 105°, 135°, 165°, -165°, -135°, -105°, -75°, -45°, -15°
- Sign convention: + = left (CCW), - = right (CW), 0° = forward

### LIDAR_MIRROR
- Default: `LIDAR_MIRROR=1` (enabled)
- Negates all angles after sector computation
- Corrects physical left/right swap from LiDAR mounting orientation
- If wrong: robot turns toward walls when LLM picks "open" direction

### 3-Layer Safety
```
Layer 1: LiDAR Reactive (<100ms)
├── Emergency stop: < 0.30m
├── Warning: < 0.80m → reduce speed
└── YDLidar scan ±30° forward cone

Layer 2: VLM Scene Assessment (1-3s)
├── Corridor/path detection → shortcut forward
└── Negative keyword filtering ("dead end", "blocked")

Layer 3: Re-plan (3-5s)
├── Back up + find new direction
└── TTS feedback to user
```

---

## Visual Search Pipeline

### Agent Loop (Gateway/gateway/main.py)
```
Phase 0: Check current camera frame (before any motion)
    ↓
Agent Loop (MAX_AGENT_STEPS = 16):
    1. Fresh LiDAR scan → find_best_direction()
    2. Get open_directions + sector_summary
    3. VLM check current frame → scene description
    4. Camera corridor shortcut (3-layer safety gate):
       - Negative keyword check
       - LiDAR blocked check (±30°, <0.30m)
       - Forward clearance check (≥0.30m)
    5. If no shortcut → LLM plan action:
       - Receives: target, lidar_summary, open_directions,
         checked_dirs, cumulative_rotation, step, wall_streak,
         turns_at_position, blocked_dirs
       - Returns: {action: "turn"|"forward"|"done", angle: deg}
    6. Execute action (turn/forward)
    7. Stuck detection (if moved < 30% expected → auto-reverse + escape)
    ↓
If found → _search_found() → Approach Phase (MAX_APPROACH_STEPS = 6)
```

### LLM Planner Prompt Rules (Priority Order)
1. **HIGHEST:** If camera sees open corridor/hallway/path → MUST choose 'forward' angle=0
2. Never turn toward checked directions
3. If wall_streak ≥ 3 → forward to widest open direction
4. Prefer unchecked open directions sorted by distance
5. If all checked → forward to widest

### Approach Phase (_search_found)
1. Announce object found via TTS
2. Loop up to 6 steps:
   - LiDAR check: if front < 0.25m → stop (close enough)
   - Turn toward object (30° based on location string)
   - Move forward (distance based on proximity: near=0.8s, far=3.0s, else=1.8s)
   - Re-check with VLM after each move
   - Stop if: near+centered, or conf≥0.8+centered after step 2, or step≥4

### VLM Configuration
- Model: Qwen3-VL:32b
- Input: JPEG 480×360 (~25-35KB)
- Prompt: English (with `/no_think`)
- Timeout: 60s per call (Gateway-side; server-side uses 600s)
- Inference time: 50-155s per call (model loading + forward pass + generation)
- Parrot guard: if output < 100 chars and contains target name → reject + retry

### VLM Call Chain
```
Gateway → POST /vlm/describe-bytes (JPEG body) → Server
Server → qwen_vlm.py → Ollama API → Qwen3-VL:32b inference
Server → returns scene description text
Gateway → POST /generate (scene + target + reasoning prompt) → Server
Server → ollama.py → Gemma3:27b → JSON {found, location, confidence}
```

---

## Frontend / Webapp

### Architecture
- Single-page HTML dashboard (`app/frontend/index.html`)
- 3-column layout: Left (Map + Telemetry), Center (Chat), Right (Camera + Console)
- Polls `/map/state` every 500ms for robot position
- Polls `/camera/frame` at ~10fps for live camera
- WebSocket for STT audio streaming
- CSS variables for theming, responsive design

### Map Rendering
- SLAM map rendered on HTML5 Canvas (400×400 small, 800×800 expanded)
- `getMapViewport()` — auto-expanding viewport tracking robot position
- `worldToCanvas()` / `canvasToWorld()` — coordinate transformation
- Canvas resolution synced with CSS display size (prevents distortion)
- Uniform scale: `Math.min(cw/vpW, ch/vpH)` with centering offsets
- Features: robot dot (purple), heading arrow (red), trail, object markers, Set Pose

### Set Pose Feature
- Click on expanded map canvas to set robot position
- Sends POST `/map/relocalize` → forwarded to Gateway → publishes `/initialpose`
- AMCL suppress window: 3s after relocalize (prevents snap-back)
- `/initialpose` published 3× with 0.2s intervals for reliability

### Chat Interface
- Markdown rendering: bold, italic, code blocks, lists, blockquotes, headings
- Chain-of-Thought (CoT) display with collapsible cards
- Voice recording button with visual feedback

---

## Configuration & Environment Variables

### Gateway .env
```
SERVER_BASE=https://user.tail87d9fe.ts.net
SERVER_WS=wss://user.tail87d9fe.ts.net/ws/stt
ROSBRIDGE=ws://192.168.0.111:9090
CMD_VEL=/cmd_vel
DEBUG=1
MOCK_ROBOT=0
USE_NAV2=0                    # Set to 1 to use Nav2 path planning
LIDAR_OFFSET_DEG=0
LIDAR_MIRROR=1
```

### Server settings.py
```
OLLAMA_HOST=http://127.0.0.1:11434
OLLAMA_MODEL=gemma3:27b-it-qat
OLLAMA_VLM_MODEL=qwen3-vl:32b
OLLAMA_TIMEOUT=600
TTS_BACKEND=gtts
GATEWAY_URL=http://192.168.0.60:9001
```

### Robot start_nav2.sh
```
INIT_X=0.0    (adjustable via env var)
INIT_Y=0.0    (adjustable via env var)
INIT_YAW=0.0  (adjustable via env var)
```

---

## Key Constants & Parameters

### Motion
| Constant | Value | Location |
|----------|-------|----------|
| `MAX_AGENT_STEPS` | 16 | main.py L769 |
| `MAX_FORWARD_MOVES` | 4 | main.py |
| `MAX_APPROACH_STEPS` | 6 | main.py |
| `APPROACH_SPEED` | 0.10 m/s | main.py |
| `move_speed` | 0.15 m/s | main.py |
| `move_duration` | 4.0s (= 0.40m) | main.py |
| `ANGULAR_SPEED` | 0.50 rad/s | intent_parser.py |
| `ROTATION_CALIBRATION` | 0.87 | intent_parser.py (was 0.85, 0.95, 1.0) |
| `SCAN_ROTATION_CAL` | 0.95 | main.py L280 |
| `MIN_ROTATE_DUR` | 0.5s | ros_cmd.py |

### Safety
| Constant | Value | Purpose |
|----------|-------|---------|
| Emergency stop | 0.30m | LiDAR min distance |
| Warning distance | 0.80m | LiDAR warning |
| Safety margin | 0.15m | Forward distance limit = clearance - 0.15m |
| Forward clearance check | 0.30m | Corridor shortcut gate |
| Stuck threshold | 30% | If moved < 30% expected → auto-reverse |

### LLM
| Constant | Value | Purpose |
|----------|-------|---------|
| `max_tokens` (plan) | 200 | LLM planning response (was 128) |
| `temperature` | 0.3 | LLM planning temperature |
| `OLLAMA_TIMEOUT` | 600s | General Ollama timeout |
| `vlm_timeout` | 60.0s | VLM per-call timeout |
| `MEMORY_MAX_TURNS` | 12 | Chat memory turns |
| `MEMORY_MAX_CHARS` | 4000 | Chat memory char limit |

### Nav2
| Constant | Value | Purpose |
|----------|-------|---------|
| Nav2 goal timeout | 30.0s | Per-goal timeout |
| AMCL max_particles | 2000 | Particle filter |
| AMCL min_particles | 500 | Prevent collapse |
| `max_vel_x` | 0.15 m/s | Controller max speed |
| `max_vel_theta` | 0.50 rad/s | Controller max rotation |
| `robot_radius` | 0.115m | For costmap |
| `bond_timeout` | 30.0s | Was 10.0s |
| `required_movement_radius` | 0.15m | Was 0.30m |

---

## Development Timeline

| Date | Milestone |
|------|-----------|
| Jan 2026 | Initial VORA system: STT + LLM + TTS pipeline, basic robot control |
| Jan 26 | Progress presentation to advisor |
| Jan 28 | Code reorganization, STRUCTURE.md created |
| Jan 30 | Folder restructure complete |
| Feb 12 | Rotation calibration (0.85→1.0), connection leak fix (ensure_ros singleton) |
| Feb 19 | Camera streaming implementation (MyAGV → Gateway → Server → Webapp) |
| Feb 20 | Visual Search Pipeline v1: VLM+LLM object finding, approach phase, rotation cal→0.87 |
| Feb 26 | Advisor feedback analysis: scope definition, experiment design, DAAAM paper comparison |
| Mar 6 | Pipeline fixes (LiDAR invert, backup cooldown, VLM prompt echo) |
| Mar 7 | Nav2 full stack installation, TF tree debugging, Visual Search Phase 0-1.5 working |
| Mar 9 | VLM upgrade 8b→32b, Phase 2 exploration (LLM Navigator), Spatial Memory |
| Mar 12 | Search strategy overhaul: new LLM planner prompt, camera blind guard, force forward |
| Mar 18 | Nav2 stabilization on Jetson Nano, AMCL tracking, watchdog spam fix |
| Mar 20 | Map position analysis, Technical Summary document |
| Mar 23 | Tests 7-8: VLM parrot guard, turn-when-blocked, escape-after-stuck. **Object found!** |
| Mar 24 | Tests 9-10-11: Regex bug, force-forward filter, LLM navigation fix, STUCK analysis |
| Mar 25 | P0-P4 fixes: DR AMCL fix, Nav2 caching, relocalize persistence, map distortion |
| Mar 26 | Code verification, live test success, odom DR fix deployed, AMCL TF timing fix |

---

## Test History

### Test 7 (Mar 23) — ขวดน้ำฝาสีน้ำเงิน
- Issues: VLM parrot false positive, Nav2 3s timeout ×6, trapped-when-blocked loop, STUCK ×3
- Status: **Failed** (found false positive, wasted steps)

### Test 8 (Mar 23) — หาขวดน้ำ
- **SUCCESS ✅** — VLM found object, robot approached until LiDAR 0.20m
- Issues: Wall collision during approach, map position wrong
- Fixes applied: VLM parrot guard, USE_NAV2=0, turn-when-blocked, escape-after-stuck

### Test 9 (Mar 24) — ค้นหาวัตถุ
- Issues: Regex bug (`+45` not parsed), force-forward selecting backward direction
- Fixes: regex `([+-]?\d+)`, force-forward ±90° filter

### Test 10 (Mar 24) — ค้นหาวัตถุ
- Issues: LLM ignored camera corridors, Set Pose button broken
- Fixes: Camera corridor shortcut, Set Pose CSS fix, LLM prompt Rule 1 strengthened

### Test 11 (Mar 24) — ค้นหา "ขวดน้ำ"
- **Failed** — 12 steps, forward STUCK 6/8 times (0.000m moved)
- Root cause: `_amcl_pose_active = True` blocked dead reckoning entirely
- Led to P0-P4 priority fix list

### Live Test (Mar 25) — Post-P0-P4 fixes
- **Forward movement working ✅** — moved 0.31m, 0.15m, 0.31m actual distances
- AMCL tracking confirmed correct
- LiDAR obstacle interrupt working
- Wall steering working ("Wall RIGHT 0.17m → steering left")
- ROSBridge shared connection working (1 client, no factory spam)
- Nav2 still failing (stack not running on robot)

---

## Bug Fix History (Major Fixes)

### Critical Fixes

| Date | Bug | Root Cause | Fix |
|------|-----|-----------|-----|
| Feb 12 | ROSBridge connection leak | `ensure_ros()` created new connection every call | Singleton pattern + asyncio lock |
| Feb 12 | Rotation undershoot 10-15% | `ROTATION_CALIBRATION=0.85` at 0.50 rad/s wrong | Changed to 1.0 (later tuned to 0.87, then 0.95) |
| Mar 7 | LiDAR direction inverted | `best_angle` should be × -1 | Fixed `invert_lidar_direction` |
| Mar 9 | VLM prompt echo ~50% | Thai prompts to qwen3-vl:8b | Upgraded to 32b + English prompts |
| Mar 12 | Robot spins in place (11/12 steps) | LLM read raw LiDAR table wrong | Rewrote planner prompt, pre-filter directions |
| Mar 12 | Camera blind → crash into wall | No camera = wall_streak → force forward | Added camera_blind guard |
| Mar 18 | AMCL SIGSEGV crash | OmniMotionModel + 2000 particles > 4GB RAM | Changed to differential + reduced particles |
| Mar 23 | VLM parrot false positive | VLM returns just target name | Added parrot guard (< 100 chars + target name check) |
| Mar 24 | Set Pose button broken | Inline `style="display:none"` overrode CSS class | Removed inline style |
| Mar 24 | Relocalize `math` error | Local `import math` in elif block scoped to entire function | Removed 3 redundant imports |
| Mar 24 | Regex `+45` not parsed | Pattern didn't capture leading `+` | Changed to `([+-]?\d+)` |
| Mar 25 | **Forward STUCK (P0 CRITICAL)** | `_amcl_pose_active = True` disabled dead reckoning; x,y frozen | Removed `if _amcl_pose_active: return` from DR |
| Mar 25 | Nav2 3s timeout per step | Nav2 action server not running | Added `_nav2_failed_this_session` flag |
| Mar 25 | Relocalize snap-back | Single `/initialpose` publish; AMCL overwrites | 3× publish + 3s AMCL suppress window |
| Mar 25 | Map distortion (แมพยืด) | Canvas 800×800 CSS-stretched to non-square container | Sync `canvas.width/height` with `clientWidth/clientHeight` |
| Mar 25 | VLM parrot → wall_streak → blind forward | target_parrot double-counted as wall | Don't count parrot as wall_empty |
| Mar 25 | Odom x,y = 0 always | MyAGV encoders don't update x,y for Mecanum | DR in odom_tf_broadcaster.py integrates cmd_vel at 20Hz |
| Mar 25 | AMCL initial pose TF timing | `/initialpose` stamp newer than latest TF | Use TimePointZero stamp + 20s warmup + 3× retry |

### Configuration Fixes

| Date | Change | Before | After |
|------|--------|--------|-------|
| Feb 12 | ROTATION_CALIBRATION | 0.85 | 1.0 |
| Feb 20 | ROTATION_CALIBRATION | 1.0 | 0.87 |
| Mar 9 | VLM model | qwen3-vl:8b | qwen3-vl:32b |
| Mar 12 | move_duration | 2.5s | 4.0s |
| Mar 18 | AMCL robot_model_type | differential | omni |
| Mar 18 | AMCL max_particles | 2000→500 (crash fix) | 2000 (restored after omni) |
| Mar 24 | SCAN_ROTATION_CAL | variable | 0.95 |
| Mar 25 | MAX_AGENT_STEPS | 12 | 16 |
| Mar 25 | max_tokens (LLM plan) | 128 | 200 |
| Mar 25 | AMCL set_initial_pose | true | false |
| Mar 25 | bond_timeout | 10.0 | 30.0 |
| Mar 25 | trans_stopped_velocity | 0.15 | 0.05 |

---

## Advisor Feedback & Experiment Design

### Advisor Comments (Feb 26, 2026)
- Scope is too broad — need to specify exact environment
- Need clear experiment design with measurable outcomes
- Reference: DAAAM Paper (MIT, Nov 2025) — 4D Scene Graph from RGB-D

### Defined Environment
- **IT Lab Controlled Environment** — ~6m × 8m (48 sq.m)
- Fixed object placement for repeatable experiments
- SLAM map: `lab_room.pgm` (384×384 px @ 0.05 m/px, cropped to 86×81 px)

### Experiment Design (from Advisor.md)

**Experiment 1: STT Performance**
- Metric: Word Error Rate (WER), accuracy
- Test: Thai voice commands at various distances/noise levels

**Experiment 2: VLM Object Recognition**
- Metric: Recognition accuracy, detection reliability
- Variables: object distance (near/mid/far), direction (0°/90°/180°/270°), object type (10 types)

**Experiment 3: End-to-End System**
- Pipeline: Voice → STT → LLM → VLM → Navigation → TTS
- Metrics: Latency, task success rate, pipeline reliability

**Experiment 4: Navigation**
- Test: Follow predefined waypoints
- Metric: Navigation accuracy, positional error

### Key Comparisons: VORA vs DAAAM (MIT)
| Aspect | DAAAM | VORA |
|--------|-------|------|
| Sensor | RGB-D (3D) | RGB only (2D) |
| Scale | Indoor+Outdoor, 1.64km | Indoor only, ~48 sq.m |
| Memory | 4D Scene Graph | Object memory + spatial memory |
| Object Detection | SAM + BoT-SORT tracking | VLM single-frame description |
| Hardware | RTX 5090 | A6000 + Jetson Nano |
| Robot | No real robot (dataset only) | Real MyAGV robot |
| Voice | None | Full Thai STT+TTS pipeline |
| Language | English | Thai-first (bilingual) |

---

## Known Issues & Remaining Work

### Unresolved Issues (as of Mar 26, 2026)

| # | Issue | Severity | Status |
|---|-------|----------|--------|
| 1 | Nav2 action client fails | 🔴 HIGH | Nav2 stack not running on robot; `start_nav2.sh` fix not yet deployed |
| 2 | AMCL not tested after fix | 🔴 HIGH | TF timing fix + DR fix need hardware validation |
| 3 | VLM parrot still occurs | 🟡 MED | Mitigated (not counted as wall) but qwen3-vl:32b sometimes returns just object name |
| 4 | Nav2Client uses separate roslibpy.Ros | 🟢 LOW | Not shared `ensure_ros()` singleton; only matters when USE_NAV2=1 |
| 5 | Gateway latency ~1000ms | 🟢 LOW | Likely Tailscale proxy hop |

### Remaining Work (Priority)

1. **Deploy & test start_nav2.sh on Jetson Nano** — AMCL initial pose, DR odom, TF timing
2. **Test AMCL tracking + Dead Reckoning** — Set Pose → walk 1-2m → verify robot dot follows
3. **Test Nav2 action client** — Full Nav2 stack running → verify path planning works
4. **Run full search experiments** — Multiple objects, distances, directions
5. **Collect experiment data** — For thesis
6. **Write thesis** — Less than 1 month deadline

---

## Performance Metrics

| Metric | Before Optimization | After | Target |
|--------|-------------------|-------|--------|
| STT Latency | 10s | 2.5s | <3s ✅ |
| LLM Inference | 5s | 2s | <3s ✅ |
| Total Response | 15s | 4.5s | <5s ✅ |
| Intent Accuracy | 70% | 85% | >90% 🔄 |
| Rotation Accuracy | 78% | 97% | >99% 🔄 |
| Forward STUCK Rate | 75% (Test 11) | 0% (Test 25 Mar) | 0% ✅ |
| VLM Prompt Echo | 50% (8b Thai) | ~0% (32b English) | 0% ✅ |
| ROSBridge Connections | 6 factories (leak) | 1 shared client | 1 ✅ |
| Object Search Success | 0/3 (Tests 7,9,10) | 1/1 (Test 8) | TBD |

---

## Startup Commands

### Server
```bash
conda activate vora
./start_tailscale.sh  # If using Tailscale for remote access
```

### Gateway (Windows PC)
```bash
cd Gateway
./start_gateway.sh
```

### Robot (Jetson Nano SSH)
```bash
# Base driver + LiDAR + ROSBridge
./start_myagv.sh

# Nav2 (optional, separate terminal)
./start_nav2.sh

# Camera (separate terminal)
./start_camera.sh
```

### Access
```
Web UI: https://user.tail87d9fe.ts.net/app
API Docs: https://user.tail87d9fe.ts.net/docs
Health: https://user.tail87d9fe.ts.net/health
```
