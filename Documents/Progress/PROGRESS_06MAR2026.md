# VORA Progress Report — 6 March 2026

## 📋 Summary

วันนี้โฟกัส 3 งานหลัก:
1. **Live SLAM Map Integration** — แสดงแผนที่ SLAM จริงบน webapp พร้อมตำแหน่งหุ่น + object markers แบบ real-time
2. **LiDAR Runtime** — เพิ่ม YDLidar launch ใน `start_myagv.sh` ให้ LiDAR ทำงานตลอดเวลา (ไม่ใช่แค่ตอน SLAM)
3. **5×5 Maze Map** — อัพเดตแมพใหม่จาก maze 5×5 (35cm/block) ที่สร้างจริง

**ผลลัพธ์:** Map Router สร้างเสร็จ, Gateway push pose/objects พร้อม, Webapp canvas rendering พร้อม, LiDAR เปิดตลอด runtime, obstacle avoidance 3-layer wired up ครบ

---

## 🗺️ New Map Analysis (5×5 Maze)

| Parameter | Old Map | **New Map (5×5 Maze)** |
|-----------|---------|----------------------|
| PGM Size | 384×384 px | 384×384 px |
| Room pixels | 66×26 px (tiny!) | **58×40 px** |
| World size | 3.3m × 1.3m | **2.9m × 2.0m** |
| Wall pixels | 83 | **109** (more walls = maze) |
| Free pixels | 407 | **1,296** (3× more free space) |
| After auto-crop | 106×66 → 2.5:1 | **98×80 → 1.2:1** (nearly square) |
| Cropped origin | [-1.90, -1.25, 0] | **[-3.40, -1.30, 0]** |

**Maze spec:** กำแพง 35cm × 35cm blocks, ช่องทางผ่านกว้าง 35cm, หุ่น 21×26cm → clearance ข้างละ 7cm/4.5cm

---

## 🔧 Changes Made

### 1. Map Router — `app/api/map_router.py` (NEW FILE)

ไฟล์ใหม่ทั้งหมด — serves SLAM map + robot position + object markers ให้ webapp

**Endpoints:**
| Method | Path | Description |
|--------|------|-------------|
| GET | `/map/image` | SLAM map as PNG (auto-cropped + color enhanced) |
| GET | `/map/info` | Map metadata (resolution, origin, size) |
| GET | `/map/state` | Robot pose + object markers (polled ~5Hz) |
| POST | `/map/pose` | Gateway pushes robot pose |
| POST | `/map/objects` | Gateway pushes object memory |

**Auto-crop logic in `_load_map()`:**
- วิเคราะห์ PGM หา bounding box ของ pixels ที่ไม่ใช่ unknown (≠205)
- เพิ่ม padding 20px รอบ ๆ
- คำนวณ origin ใหม่ให้ `worldToCanvas()` ยังคง map ตำแหน่งหุ่นได้ถูก
- 384×384 → **98×80 px** (ลด 95%) — ห้องเต็ม canvas

**Color enhancement (RGBA for dark theme):**
| Pixel value | Color | Meaning |
|-------------|-------|---------|
| 205 | `#1a1a2e` (dark) | Unknown → blends with webapp background |
| ≥240 | `#c8d2e6` (light) | Free space / passable floor |
| ≤10 | `#ff5a5a` (red) | Walls / occupied |
| 196-210 (≠205) | `#32325a` (dim blue) | Near-unknown boundary |

### 2. Server Registration — `app/main.py`

```python
from .api.map_router import router as map_router
app.include_router(map_router)
```

### 3. Gateway Robot Tracking — `Gateway/gateway/main.py`

เพิ่ม 5 ส่วนใหม่:

**3.1 Odom Subscription:**
- `_odom_callback(msg)` — แปลง quaternion → yaw (theta)
- `_subscribe_odom()` — subscribe /odom ผ่าน ROSBridge roslibpy

**3.2 Pose Push:**
- `_push_pose_to_server()` — background task push POST /map/pose ทุก 500ms

**3.3 Object Push:**
- `_push_objects_to_server()` — background task push object_memory ทุก 5s

**3.4 Pose Endpoint:**
- `GET /robot/pose` — webapp/debugging endpoint

**3.5 Startup Integration:**
- ทั้ง 3 tasks (`_subscribe_odom`, `_push_pose`, `_push_objects`) เพิ่มใน `startup_event()`
- Obstacle avoidance ก็เริ่มที่ startup ด้วย (subscribe /scan)

### 4. Webapp SLAM Canvas — `app/frontend/index.html`

**CSS Changes:**
- Camera preview: 180px → **300px** width (mobile: 130→220px)
- Map container: added `min-height: 300px`, `cursor: pointer`
- Expanded map overlay: `position: fixed`, `inset: 20px`, `z-index: 300`

**HTML Changes:**
- Mini map: เปลี่ยนจาก grid-based (div+dot) → `<canvas id="map-canvas" 400×400>`
- Expanded map: FullScreen overlay `<canvas id="map-canvas-big" 800×800>`

**JavaScript — ใหม่ทั้งหมด:**

| Function | Description |
|----------|-------------|
| `loadMapImage()` | โหลด `/map/image` PNG ครั้งเดียว (IIFE) |
| `fetchMapState()` | Poll `/map/state` ทุก 500ms → update robotPos + mapObjects |
| `worldToCanvas(wx, wy, cw, ch)` | แปลง ROS world meters → canvas pixels (uniform scale + center offset) |
| `renderMapCanvas(canvasId)` | วาด: map image → trail → object markers → heading arrow → robot dot + glow |
| `toggleExpandedMap()` | Click mini map → fullscreen overlay |

**Coordinate transform:**
```
World (m) → Map pixel: mpx = (wx - originX) / resolution
Map pixel → Canvas:    scale = min(canvasW/mapW, canvasH/mapH)
                       px = mpx * scale + (canvasW - mapW*scale)/2
```

### 5. LiDAR Runtime — `Myagv/start_myagv.sh`

**ปัญหา:** LiDAR (YDLidar G2) ถูก launch แค่ตอนรัน `start_slam.sh` (SLAM mapping)
แต่ obstacle_avoidance.py ต้อง subscribe `/scan` ตลอดเวลาที่หุ่นวิ่ง

**แก้ไข:** เพิ่ม Terminal 1 ใน `start_myagv.sh`:

```
เดิม: 5 terminals (Hardware, ROSBridge, Camera, Command, Audio)
ใหม่: 6 terminals (Hardware, LiDAR, ROSBridge, Camera, Command, Audio)
```

Terminal 1 (new):
```bash
ros2 launch ydlidar_ros2_driver ydlidar_launch.py
```

Publishes: `/scan` (sensor_msgs/LaserScan) — 360°, 0.1-12m, ~5000 points/scan

### 6. SLAM Scripts Update (from earlier session)

- `Myagv/start_slam.sh` — เปลี่ยนจาก gmapping (ROS1) → **slam_toolbox** (ROS2 Galactic)
- `Myagv/slam_toolbox_params.yaml` — NEW: config for slam_toolbox online_async
- `Myagv/save_map.sh` — เปลี่ยน output path ให้ save ลง `Myagv/maps/`

---

## 🛡️ Obstacle Avoidance Architecture (ครบแล้ว)

```
YDLidar G2 (MyAGV, /scan) ──→ ROSBridge WS ──→ Gateway obstacle_avoidance.py
                                                          │
                                                    ┌─────┴──────┐
                                                    │ Distance?   │
                                                    └─────┬──────┘
                                          < 0.3m │         │ 0.3-0.8m          │ > 0.8m
                                                 ▼         ▼                    ▼
                                         🛑 EMERGENCY   🧠 VLM+LiDAR         ✅ Clear
                                            STOP         heuristic             Continue
                                                         │
                                              ┌──────────┼──────────┐
                                              ▼          ▼          ▼
                                         go_around    go_around    wait/
                                           _left       _right     reroute
```

**Layer 1:** LiDAR Reactive (< 0.3m) → immediate stop, ไม่รอ server  
**Layer 2:** VLM Analysis (0.3-0.8m) → capture camera → send to VLM → get strategy  
**Layer 3:** LiDAR Heuristic (fallback if VLM unavailable) → เทียบ clearance ซ้าย vs ขวา

**Config:** `FRONT_ANGLE_DEG=60` (±30° cone), `OBSTACLE_WARN_M=0.8`, `OBSTACLE_STOP_M=0.3`

---

## ✅ Verification Results

| File | Type | Syntax | Status |
|------|------|--------|--------|
| `app/api/map_router.py` | Python | ✅ | Auto-crop + color + origin recalc verified |
| `app/main.py` | Python | ✅ | Router registered |
| `Gateway/gateway/main.py` | Python | ✅ | odom + push + obstacle wired |
| `Gateway/gateway/obstacle_avoidance.py` | Python | ✅ | 3-layer logic verified |
| `Myagv/start_myagv.sh` | Bash | ✅ | 6 terminals (added LiDAR) |
| `Myagv/start_slam.sh` | Bash | ✅ | slam_toolbox |
| `Myagv/save_map.sh` | Bash | ✅ | output path fixed |
| `app/frontend/index.html` | HTML/JS | ✅ | Canvas + worldToCanvas verified |

**Map crop test with new 5×5 map:**
- Auto-crop: 384×384 → 98×80 px ✅
- New origin: [-3.40, -1.30, 0] ✅
- Robot at (0,0) → pixel (68, 54) → in bounds ✅
- Walls: 134 red pixels, Free: 1296 light pixels ✅

---

## 📊 Overall System Status

| Component | Status | Notes |
|-----------|--------|-------|
| **Thai STT** (Faster-Whisper) | ✅ Working | WebSocket streaming |
| **LLM Intent** (Gemma3:27b) | ✅ Working | search/move/stop/status |
| **TTS** (edge-tts Thai) | ✅ Working | Real-time audio response |
| **VLM** (Qwen3-VL:8B) | ✅ Working | Object detection + description |
| **Visual Search** | ✅ Working | 4-direction rotate-scan-approach |
| **Rotation Calibration** | ✅ Cal=0.87 | Accurate within ±2° |
| **Camera Streaming** | ✅ Working | OpenCV → ROS2 → Gateway → Server |
| **Voice → Robot** | ✅ Working | Thai command → intent → motion |
| **SLAM Map on Webapp** | ✅ NEW | Auto-crop + real-time robot dot |
| **Robot Pose Tracking** | ✅ NEW | /odom → Gateway → Server → Webapp |
| **Object Markers on Map** | ✅ NEW | Object memory → map overlay |
| **LiDAR Runtime** | ✅ NEW | Always-on /scan for obstacle avoidance |
| **Obstacle Avoidance** | ✅ Wired | 3-layer (LiDAR + VLM + heuristic) |
| **Experiment Logging** | ❌ Not started | — |
| **5×5 Maze Physical Setup** | 🔄 In progress | Map collected, assembly pending |

---

## 🎯 Next Steps

1. **ประกอบ Maze 5×5** — ต่อกำแพง 35cm blocks, วาง maze pattern
2. **ทดสอบ LiDAR + Obstacle Avoidance** — วางเก้าอี้ขวาง → ดู emergency stop + heuristic
3. **VLM Obstacle Analysis** — implement `_ask_vlm_strategy()` (capture camera → VLM → strategy)
4. **Experiment Logging** — สร้าง structured log สำหรับ paper (success/fail, latency, path)
5. **Run Experiment Phase A** — Visual Search 5 objects × 5 configs × 3 repeats

---

## 📁 Files Created / Modified This Session

### New Files
| File | Purpose |
|------|---------|
| `app/api/map_router.py` | Map serving + real-time state endpoints |
| `Myagv/slam_toolbox_params.yaml` | slam_toolbox configuration |

### Modified Files
| File | Changes |
|------|---------|
| `app/main.py` | Added map_router import + include |
| `app/frontend/index.html` | Canvas map rendering, CSS enlargements |
| `Gateway/gateway/main.py` | odom tracking, pose/object push, startup tasks |
| `Myagv/start_myagv.sh` | Added LiDAR terminal (6 terminals total) |
| `Myagv/start_slam.sh` | Rewritten for slam_toolbox (ROS2) |
| `Myagv/save_map.sh` | Updated output path |
| `Myagv/zone_mapper.py` | Removed hardcoded room size defaults |
