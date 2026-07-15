# VORA Project — Technical Summary
**Last Updated: 20 March 2026**

---

## 1. Project Overview

**VORA** (Voice Oriented Robotics Assistant) — หุ่นยนต์ผู้ช่วยห้องปฏิบัติการ ควบคุมด้วยเสียงภาษาไทย
- สั่งงานด้วยเสียง (ภาษาไทย) → หุ่นยนต์เคลื่อนที่, ค้นหาวัตถุ, ตอบคำถาม
- ใช้ AI ท้องถิ่น 100% (Ollama + Faster-Whisper) — ไม่พึ่ง cloud API
- สถานะ: **~85% complete** | Response time: ~4.5s | Intent accuracy: ~85%

---

## 2. System Architecture

```
┌─────────────┐    HTTPS/WSS      ┌──────────────────┐     HTTP      ┌──────────────┐    ROSBridge     ┌───────────────┐
│  Web Client  │ ◄──────────────► │   VORA Server     │ ◄───────────► │   Gateway     │ ◄──────────────► │    MyAGV       │
│  (Browser)   │   Tailscale      │  (Ubuntu, A6000)  │   LAN/TS      │ (Windows PC)  │  ws://9090       │ (Jetson Nano)  │
└─────────────┘                   └────────┬──────────┘               └──────┬────────┘                  └───────┬───────┘
                                           │                                  │                                   │
                              ┌────────────┤                                  │                          ┌────────┤
                     ┌────────▼────────┐   │                                  │                 ┌────────▼────────┐
                     │   Ollama LLMs   │   │                                  │                 │  ROS2 Galactic  │
                     │  Gemma3 27b     │   │                                  │                 │  • MyAGV Driver │
                     │  Gemma3 12b     │   │                                  │                 │  • YDLidar G2   │
                     │  Qwen3-VL 32b   │   │                                  │                 │  • Nav2 Stack   │
                     └─────────────────┘   │                                  │                 │  • Camera Pub   │
                              ┌────────────▼───────┐                          │                 │  • ROSBridge    │
                              │  Faster-Whisper    │                          │                 └─────────────────┘
                              │  Thai STT (CUDA)   │                          │
                              └────────────────────┘                          │
                                                                     ┌────────▼────────┐
                                                                     │  Gateway Logic   │
                                                                     │  • Audio Proxy   │
                                                                     │  • Motion Parser │
                                                                     │  • Obstacle Avoid│
                                                                     │  • Visual Search │
                                                                     │  • Object Memory │
                                                                     │  • Nav2 Client   │
                                                                     └─────────────────┘
```

### Data Flow — Voice Command
```
🎤 Mic → Gateway Audio Proxy → WSS → Server Faster-Whisper → Thai Text
                                                    ↓
                                        Agent LLM (Gemma3 12b) → Intent + Target
                                                    ↓
                                        Reasoning LLM (Gemma3 27b) → Task Plan
                                                    ↓
                                        [VLM (Qwen3-VL 32b)] → Scene Analysis (if needed)
                                                    ↓
                                        Robot Commands → Gateway → MyAGV
```

### Data Flow — Camera
```
📷 MyAGV Camera → /camera/compressed → ROSBridge → Gateway
                                                      ↓
                                              POST /camera/push → Server memory
                                                      ↓
                                              GET /camera/frame → Webapp display
                                              POST /vlm/describe-bytes → VLM analysis
```

### Data Flow — Robot Position
```
🧭 MyAGV /odom → ROSBridge → Gateway _odom_callback()
🗺️ Nav2 /amcl_pose → ROSBridge → Gateway _amcl_pose_callback() [preferred]
                                        ↓
                                POST /map/pose → Server _robot_pose
                                        ↓
                                GET /map/state → Webapp canvas rendering
```

---

## 3. Hardware Inventory

| Component | Spec | Network |
|---|---|---|
| **Server** | Ubuntu, NVIDIA A6000 48GB | Tailscale `user.tail87d9fe.ts.net` |
| **Gateway** | Windows PC | LAN `192.168.0.60:9001` |
| **MyAGV** | Elephant Robotics MyAGV 2023, Mecanum 4-wheel | LAN `192.168.0.111` |
| **Compute** | Jetson Nano 4GB, Ubuntu 20.04, ROS2 Galactic | on MyAGV |
| **LiDAR** | YDLidar G2, 360°, max 12m | USB on Nano |
| **Camera** | USB camera, 640×480, 15fps | USB on Nano |
| **Mic** | ReSpeaker (on MyAGV) | USB on Nano |
| **Robot size** | 21cm × 26cm body, ~15cm circumscribed radius | — |

---

## 4. Software Components

### 4.1 VORA Server (`app/`)

FastAPI backend running on A6000 GPU server.

| File | Purpose |
|---|---|
| `app/main.py` | Entry point — mounts all routers, serves frontend |
| `app/core/settings.py` | Centralized config (env-overridable) |
| `app/core/vora_pipeline.py` | Core AI pipeline: STT → Agent → Reasoning → VLM → Commands |
| `app/core/session_manager.py` | User session management |
| `app/core/command_queue.py` | Priority-based robot command queue |
| `app/core/state_manager.py` | System state tracking |
| `app/core/memory.py` | Conversation memory (TTL-based) |
| `app/core/vora_memory.py` | Extended memory with lab context |
| `app/core/text_preprocessor.py` | Thai text cleaning/normalization |

**API Routers:**

| Router | Prefix | Key Endpoints |
|---|---|---|
| `pipeline_router` | `/pipeline` | `/command` (main), `/vision`, `/quick`, WS `/gateway` |
| `stt_ws` | `/ws/stt` | WebSocket STT streaming |
| `vlm_router` | `/vlm` | `/describe-bytes`, `/find-object-live`, `/obstacle` |
| `camera_router` | `/camera` | `/push`, `/frame`, `/mjpeg` |
| `map_router` | `/map` | `/image`, `/state`, `/pose`, `/objects` |
| `llm_router` | `/generate` | LLM text generation |
| `plan_router` | `/plan` | Rule-based text→waypoints |
| `server_router` | `/api/server` | TTS, queue, sessions, emergency stop |

**AI Models (via Ollama):**

| Model | Role | Size |
|---|---|---|
| `gemma3:12b-it-qat` | Agent — intent classification, command parsing | 12B |
| `gemma3:27b-it-qat` | Reasoning — task planning, conversation | 27B |
| `qwen3-vl:32b` | Vision — scene description, object finding | 32B |

**STT:** `distill-whisper-th-large-v3-ct2` (Faster-Whisper, CTranslate2, CUDA)
**TTS:** gTTS (Google TTS, default) / Typhoon2 (disabled)

### 4.2 Gateway (`Gateway/gateway/`)

FastAPI middleware on Windows PC — bridges Server ↔ Robot.

| Module | Purpose |
|---|---|
| `main.py` | Core gateway — audio proxy, motion parser, visual search, pose tracking |
| `audio_proxy.py` | WebSocket audio relay (MyAGV → Server STT) |
| `intent_parser.py` | Thai/English command parsing (wake word, motion, find) |
| `ros_cmd.py` | ROS cmd_vel publisher via roslibpy |
| `obstacle_avoidance.py` | LiDAR-based obstacle detection (subscribe /scan) |
| `camera_stream.py` | Camera frame relay (ROSBridge → Server) |
| `object_memory.py` | Persistent object location memory |
| `spatial_memory.py` | Spatial reasoning for search |
| `nav2_client.py` | Nav2 goal_pose sender |

**Key Features:**
- Wake word detection: "VORA" + Thai variants
- Visual search: rotate-scan-first → VLM check → LLM plan → move
- LiDAR obstacle avoidance with mirror mode
- Hybrid position tracking: AMCL > odom > Dead Reckoning

**Config (env vars):**
| Var | Default |
|---|---|
| `SERVER_BASE` | `https://user.tail87d9fe.ts.net` |
| `ROSBRIDGE` | `ws://192.168.0.111:9090` |
| `USE_NAV2` | `0` (legacy mode) / `1` (Nav2 mode) |
| `MOCK_ROBOT` | `0` |

### 4.3 MyAGV Robot (`Myagv/`)

ROS2 Galactic nodes on Jetson Nano 4GB.

| File | Purpose |
|---|---|
| `start_myagv.sh` | Launch 6 services: driver, LiDAR, ROSBridge, camera, executor, audio |
| `start_nav2.sh` | Launch Nav2 stack (nav/slam/explore modes) |
| `odom_tf_broadcaster.py` | Publish TF: odom→base_footprint→base_link + cmd_vel watchdog |
| `ros_camera_pub.py` | OpenCV camera → /camera/compressed (replaces broken usb_cam) |
| `nav2_params.yaml` | Full Nav2 configuration (AMCL, DWB, NavFn, costmaps) |
| `slam_toolbox_params.yaml` | SLAM Toolbox config (online async) |
| `zone_mapper.py` | Interactive room zone definition tool |
| `vora_robot_bridge/` | ROS2 package: command_executor.py (JSON→motion) |

**Nav2 Stack:**

| Node | Plugin | Notes |
|---|---|---|
| AMCL | `differential` model | 500/100 particles, 30 beams (Nano-optimized) |
| Map Server | `lab_room.yaml` | 384×384 @ 0.05m/cell |
| Controller | DWB (Dynamic Window) | max 0.15 m/s, Mecanum support |
| Planner | NavFn (A*) | 20cm tolerance |
| BT Navigator | Galactic-compatible plugins | No RemovePassedGoals |
| Recoveries | Spin, Backup, Wait | Standard recovery behaviors |

**TF Chain:**
```
map → odom → base_footprint → base_link → laser_frame
 ↑      ↑          ↑               ↑            ↑
AMCL  odom_tf   odom_tf      odom_tf     ydlidar_launch
      _broadcaster _broadcaster _broadcaster
```

### 4.4 Frontend (`app/frontend/`)

Single-page webapp served from VORA Server.

| Feature | Implementation |
|---|---|
| Voice input | WebSocket → Server STT |
| Chat UI | Dark theme, Thai support |
| SLAM Map | HTML5 Canvas with auto-crop + viewport |
| Robot position | Purple dot + heading arrow + trail |
| Object markers | Green dots on map at detection location |
| Telemetry | Position (x,y,θ), battery, voltage, network |
| Camera feed | MJPEG from Server memory |

---

## 5. Map System

### 5.1 Map Files

| File | Size | Resolution | Origin |
|---|---|---|---|
| `lab_room.pgm/yaml` | 384×384 px | 0.05 m/px | [-10, -10, 0] |
| `new5_map5.pgm/yaml` | 384×384 px | 0.05 m/px | [-10, -10, 0] |

### 5.2 Map Processing (map_router.py)

1. Load PGM (384×384 grayscale)
2. Auto-crop non-unknown pixels (≠205) with 20px padding
3. Result: **86×81 px** (shown in webapp)
4. Cropped origin: **[-3.05, -2.20, 0]**
5. Color mapping: walls=red, free=light gray, unknown=dark

### 5.3 Coordinate System

```
Full map:    X=[-10.0, 9.2] Y=[-10.0, 9.2]  (19.2m × 19.2m)
Cropped map: X=[-3.05, 1.25] Y=[-2.20, 1.85] (4.30m × 4.05m)
Room center: (-1.10, -0.20)  ← free-space centroid
SLAM origin: (0.0, 0.0)      ← where robot was when SLAM was created (right edge of room)
```

### 5.4 Position Display Pipeline

```
Robot → /odom or /amcl_pose → Gateway → POST /map/pose → Server → GET /map/state → Webapp
                                                                          ↓
                                                          worldToCanvas(x, y) → pixel on canvas
```

Webapp `worldToCanvas()` correctly handles:
- Map origin offset
- Resolution scaling  
- Y-axis flip (world Y-up → canvas Y-down)
- Auto-expanding viewport

---

## 6. Known Issues & Status

### ✅ Resolved (recent sessions)
| Issue | Fix | Date |
|---|---|---|
| AMCL no initial pose → TF timeout | `set_initial_pose: true` in nav2_params.yaml | 18 Mar |
| AMCL SIGSEGV crash | Switched to `differential` model, reduced particles | 18 Mar |
| bt_navigator "RemovePassedGoals" | Removed Humble-only plugins | 18 Mar |
| Watchdog spam (zero cmd_vel loop) | Threshold 0.001→0.05, log-once flag | 18 Mar |
| Thai char `ไ` in YAML | Removed stray character | 18 Mar |

### ⚠️ Current Issues
| Issue | Root Cause | Impact |
|---|---|---|
| Robot shows at wrong position on map | initial_pose (0,0) = SLAM origin at room edge + Gateway not using /amcl_pose yet | Position display incorrect |
| VLM timeout 60s | Server VLM processing slow | Visual search delayed |
| Gateway /amcl_pose not deployed | Code added but not copied to Windows PC | Position stays at /odom values |
| `slam_toolbox_params.yaml` base_frame | Uses `base_link` instead of `base_footprint` | SLAM mode would fail |
| `new5_map5.yaml` absolute path | Hardcoded `/home/er/Desktop/...` | Won't work on different machines |

---

## 7. File Tree (Key Files)

```
VORA/
├── app/                          # VORA Server (FastAPI)
│   ├── main.py                   # Entry point
│   ├── core/
│   │   ├── settings.py           # Config (Ollama, TTS, Gateway)
│   │   ├── vora_pipeline.py      # AI pipeline (STT→LLM→VLM→Commands)
│   │   ├── session_manager.py    # User sessions
│   │   ├── command_queue.py      # Robot command queue
│   │   └── memory.py             # Conversation memory
│   ├── api/
│   │   ├── pipeline_router.py    # Main pipeline + Gateway WebSocket
│   │   ├── stt_ws.py             # Speech-to-text WebSocket
│   │   ├── vlm_router.py         # Vision Language Model
│   │   ├── camera_router.py      # Camera frame relay
│   │   ├── map_router.py         # SLAM map + robot pose
│   │   ├── llm_router.py         # Text generation
│   │   ├── plan_router.py        # Rule-based motion planning
│   │   └── server_router.py      # TTS, queue, sessions
│   ├── providers/
│   │   ├── llm/                  # Ollama + Qwen VLM providers
│   │   └── tts/                  # Typhoon2 TTS (disabled)
│   ├── frontend/
│   │   └── index.html            # Single-page webapp
│   └── services/
│       └── thai_tts.py           # gTTS Thai TTS
│
├── Gateway/                      # Gateway (Windows PC)
│   └── gateway/
│       └── main.py               # Core: audio, motion, search, pose tracking
│
├── Myagv/                        # MyAGV Robot (Jetson Nano)
│   ├── start_myagv.sh            # Launch 6 ROS2 services
│   ├── start_nav2.sh             # Launch Nav2 (nav/slam/explore)
│   ├── nav2_params.yaml          # Nav2 full config
│   ├── odom_tf_broadcaster.py    # TF broadcaster + watchdog
│   ├── ros_camera_pub.py         # OpenCV camera node
│   ├── slam_toolbox_params.yaml  # SLAM config
│   ├── zone_mapper.py            # Room zone definition
│   ├── maps/
│   │   ├── lab_room.yaml/pgm     # Current lab map
│   │   └── new5_map5.yaml/pgm    # Alternate map
│   └── vora_robot_bridge/        # ROS2 package
│       └── command_executor.py   # JSON→motion executor
│
├── Documents/                    # Documentation
│   ├── Progress/                 # Daily progress reports
│   └── Paper/                    # Research paper materials
│
├── models/asr/                   # Faster-Whisper model files
├── tests/                        # Test scripts
├── start_dev.sh                  # Dev server launcher
└── environment.yml               # Conda environment
```
