# VORA Progress Report — 20 March 2026

## 📋 Summary

วันนี้โฟกัส **Project Technical Summary** + **แก้ตำแหน่งหุ่นบน Map**:

1. **สร้าง Technical Summary** — อ่านทุกไฟล์โปรเจคอย่างละเอียด สร้างเอกสาร VORA_TECHNICAL_SUMMARY.md
2. **วิเคราะห์ปัญหาตำแหน่งหุ่นบน map** — คำนวณ geometry ของ map system พบ root causes

---

## 📐 Map Position Analysis

### ปัญหาที่เห็น
จาก webapp screenshot: หุ่น (จุดม่วง) แสดงที่ X:0.00 Y:0.00 ซึ่งอยู่ที่ **ขอบขวาของห้อง** ใกล้กำแพง

### Map Geometry (คำนวณจาก lab_room.pgm จริง)
```
Full PGM:      384×384 px @ 0.05 m/px
Origin:        [-10, -10, 0]
Full bounds:   X=[-10.0, 9.2] Y=[-10.0, 9.2]

Auto-crop:     non-unknown rows [167,207] cols [159,204] → +20px pad
Cropped:       86×81 px (ตรงกับ webapp "Map: 86×81")
Cropped origin: [-3.05, -2.20, 0]
Cropped bounds: X=[-3.05, 1.25] Y=[-2.20, 1.85]

Free-space centroid: col=39 row=40 → world (-1.10, -0.20)
SLAM origin (0,0):  col=200 in full map → ขอบขวาของห้อง
```

### Root Causes (2 ปัญหาซ้อนกัน)

**ปัญหาที่ 1: Gateway ยังไม่ subscribe `/amcl_pose`**
- โค้ดเพิ่ม `/amcl_pose` callback แล้วเมื่อ 18 มี.ค. แต่ **ยังไม่ได้ deploy** ไปเครื่อง Windows
- Gateway ยังใช้ `/odom` ซึ่ง odom frame ≠ map frame
- /odom x,y ค้างที่ (0,0) เพราะ Mecanum encoder ไม่ค่อยเปลี่ยน → `_odom_xy_moving = False`
- Dead reckoning ก็ไม่ทำงานเพราะหุ่นไม่ได้รับคำสั่งเคลื่อนที่

**ปัญหาที่ 2: `initial_pose` ตั้งไว้ (0, 0, 0)**
- ค่า (0,0) คือจุดที่หุ่นอยู่ตอนทำ SLAM (SLAM origin)
- ตำแหน่งนี้อยู่ที่ **ขอบขวาของห้อง** ในแผนที่
- ถ้าหุ่นเริ่มจากจุดเดียวกับตอนทำ SLAM → ค่านี้ถูกต้อง
- ถ้าหุ่นเริ่มจากจุดอื่น → ต้องเปลี่ยน initial_pose ให้ตรง

### สิ่งที่ต้องทำเพื่อแก้

1. **Deploy Gateway `/amcl_pose` subscription** — ให้ใช้ตำแหน่ง map-frame จริง
2. **ปรับ `initial_pose`** — ให้ตรงกับตำแหน่งเริ่มต้นจริงของหุ่น
3. **ทดสอบ** — AMCL จะ converge หา scan match ภายใน 5-10 วินาทีหลังหุ่นเริ่มหมุน

---

## 📄 Technical Summary Created

สร้างไฟล์ `Documents/VORA_TECHNICAL_SUMMARY.md` ครอบคลุม:

- System Architecture (3-tier: Server ↔ Gateway ↔ Robot)
- Data Flow diagrams (Voice, Camera, Position)
- Hardware Inventory (A6000, Windows PC, Jetson Nano, MyAGV)
- Software Components ทั้ง 4 layers:
  - Server: 8 API routers, 3 AI models, STT, TTS
  - Gateway: Audio proxy, motion parser, visual search, obstacle avoidance
  - Robot: 6 ROS2 services, Nav2 stack, TF chain
  - Frontend: Canvas map, telemetry, camera feed
- Map System: coordinate system, cropping, rendering pipeline
- Known Issues & Status
- Complete file tree

---

## 📊 สถานะรวมโปรเจค

| Layer | Component | Status |
|---|---|---|
| **Server** | FastAPI + Ollama LLMs | ✅ ทำงานปกติ |
| **Server** | Faster-Whisper STT | ✅ ทำงานปกติ |
| **Server** | VLM (Qwen3-VL 32b) | ⚠️ ช้า (60s timeout) |
| **Server** | Map Router | ✅ Crop + render ถูกต้อง |
| **Server** | Frontend Webapp | ✅ แสดง map, telemetry |
| **Gateway** | Audio Proxy | ✅ ทำงานปกติ |
| **Gateway** | Visual Search | ✅ Rotate-scan + VLM |
| **Gateway** | Obstacle Avoidance | ✅ LiDAR mirror fixed |
| **Gateway** | Position Tracking | ⚠️ ยังใช้ /odom, ไม่มี /amcl_pose |
| **Robot** | Base Driver + LiDAR | ✅ ทำงานปกติ |
| **Robot** | Camera | ✅ OpenCV publisher เสถียร |
| **Robot** | Nav2 AMCL | ✅ ไม่ crash, localized สำเร็จ |
| **Robot** | Nav2 Navigation | ✅ All nodes active |
| **Robot** | Watchdog | ✅ แก้ spam แล้ว |

---

## � Session 2: System Integration Fix (VLM + Position + Model Swap)

### วิเคราะห์ปัญหาจาก Live Test Logs

ทดสอบ Visual Search (ค้นหา "ขวด" / "ขวดน้ำ") พบปัญหา 4 ข้อ:

| # | ปัญหา | Root Cause | ความร้ายแรง |
|---|---|---|---|
| 1 | **VLM/LLM timeout 60s** | Gateway vlm_timeout=60s แต่ Qwen3-VL:32B ใช้ 60-150s | สูง |
| 2 | **LLM plan error** ทุก step | `/generate` timeout=12s + VRAM model swap ช้า | สูง |
| 3 | **Camera dead** | สาย USB หลุด (physical) | N/A |
| 4 | **Position (0,0)** | initial_pose ผิด + AMCL ยังไม่ converge | ปานกลาง |

### VRAM Model Swap Problem (Root Cause หลัก)

A6000 (48GB VRAM) รัน 3 model พร้อมกัน:
```
gemma3:27b-it-qat  ~16-20GB  (Reasoning)
gemma3:12b-it-qat  ~8-10GB   (Agent/Refine)  ← ลบออก
qwen3-vl:32b       ~20GB     (VLM)
รวม:               ~48-50GB  → model swapping เกิดขึ้น!
```

เมื่อ Ollama ต้อง swap model (unload → load) จะใช้เวลา 5-15s ทำให้:
- VLM call (60s timeout) → timeout เพราะต้อง swap 27b/12b ออก + load VLM
- LLM plan call (12s timeout) → timeout เพราะ 27b ถูก swap ออกระหว่าง VLM

### การแก้ไขที่ทำ

**1. ลบ 12b model — `settings.py`**
```
OLLAMA_REFINE_MODEL: "gemma3:12b-it-qat" → "gemma3:27b-it-qat"
```
ผลลัพธ์: ใช้ 27b ตัวเดียวทำทั้ง Agent + Reasoning → ไม่ต้อง swap  
VRAM: 27b (16GB) + VLM 32b (20GB) = 36GB < 48GB ✅

**2. เพิ่ม VLM timeout — `Gateway/gateway/main.py`**
```
vlm_timeout: 60.0 → 180.0  (ให้ Qwen3-VL:32B มีเวลาพอ)
```

**3. เพิ่ม LLM plan timeout — `Gateway/gateway/main.py`**
```
_llm_plan_action httpx timeout: 12.0 → 30.0
```

**4. แก้ initial_pose — `Myagv/nav2_params.yaml`**
```
initial_pose: {x: 0.0, y: 0.0} → {x: -1.1, y: -0.2}
```
ค่า (-1.1, -0.2) = centroid ของ free-space ในแผนที่ (กลางห้อง)

---

## 📊 สถานะรวมโปรเจค (Updated)

| Layer | Component | Status |
|---|---|---|
| **Server** | FastAPI + Ollama LLMs | ✅ ทำงานปกติ |
| **Server** | Faster-Whisper STT | ✅ ทำงานปกติ |
| **Server** | VLM (Qwen3-VL 32b) | ✅ Fixed — timeout 60→180s, no model swap |
| **Server** | Model Config | ✅ Fixed — ลบ 12b, ใช้ 27b ตัวเดียว |
| **Server** | Map Router | ✅ Crop + render ถูกต้อง |
| **Server** | Frontend Webapp | ✅ แสดง map, telemetry |
| **Gateway** | Audio Proxy | ✅ ทำงานปกติ |
| **Gateway** | Visual Search | ✅ Fixed — VLM timeout 180s |
| **Gateway** | LLM Plan Agent | ✅ Fixed — timeout 12→30s |
| **Gateway** | Obstacle Avoidance | ✅ LiDAR mirror fixed |
| **Gateway** | Position Tracking | ✅ /amcl_pose subscribed (code ready) |
| **Robot** | Base Driver + LiDAR | ✅ ทำงานปกติ |
| **Robot** | Camera | ⚠️ สาย USB หลุด (physical) |
| **Robot** | Nav2 AMCL | ✅ Fixed — initial_pose กลางห้อง |
| **Robot** | Nav2 Navigation | ✅ All nodes active |
| **Robot** | Watchdog | ✅ แก้ spam แล้ว |

---

## 📝 TODO — Next Steps

### 🔴 Immediate (Deploy)
1. **Deploy Server** → Restart FastAPI (settings.py changed, 12b removed)
2. **Deploy Gateway main.py** → Copy to Windows PC (VLM/LLM timeout fixed)
3. **Deploy nav2_params.yaml** → SCP to Nano + restart Nav2
4. **ต่อสาย USB Camera** → Physical reconnect

### 🟡 Short-term (ทดสอบ)
5. **ทดสอบ Visual Search** → VLM ต้องไม่ timeout + LLM plan ต้อง work
6. **ทดสอบ Position** → Webapp ต้องแสดง `🗺️ AMCL` + ตำแหน่งกลางห้อง
7. **ทดสอบ Nav2 goal navigation** → ส่ง goal_pose ให้หุ่นเดินไป

### 🟢 Long-term
8. **SLAM re-scan** → ทำแผนที่ใหม่ให้ตรงกับห้องปัจจุบัน
9. **Zone mapping** → กำหนดโซนในห้อง (โต๊ะ, ตู้, ชั้นวาง)
10. **Intent accuracy 90%+** → Fine-tune prompt / add examples

---

## 📁 Files Changed/Created Today

| File | Action |
|---|---|
| `Documents/VORA_TECHNICAL_SUMMARY.md` | ✨ Created — Full project technical summary |
| `Documents/Progress/PROGRESS_20MAR2026.md` | ✨ Created — This file |
| `app/core/settings.py` | 🔧 Changed OLLAMA_REFINE_MODEL: 12b → 27b |
| `Gateway/gateway/main.py` | 🔧 vlm_timeout: 60→180s, LLM plan timeout: 12→30s |
| `Myagv/nav2_params.yaml` | 🔧 initial_pose: (0,0) → (-1.1, -0.2) |
