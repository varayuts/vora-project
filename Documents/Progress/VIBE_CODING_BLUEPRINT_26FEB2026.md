# VORA — Actionable Blueprint สำหรับ Vibe Coding
**วันที่:** 26 กุมภาพันธ์ 2026  
**บริบท:** ประมวลผลจากคำแนะนำอาจารย์ + DAAAM Paper + ChatGPT analysis + สถานะจริงของ codebase

---

## 0. Definition of Done — 3 ข้อที่ทำให้โปรเจกต์ "จบ"

ก่อนทำอะไรทั้งหมด ต้องตกลง DoD ให้ชัด เพราะถ้าไม่มี → ทำไม่จบ

```
DoD-Search: ภายใน IT Lab 6×8m หุ่นสามารถค้นหาวัตถุที่กำหนด (5 ชนิด)
            ได้สำเร็จ ≥ 80% ภายใน 60 วินาที/ครั้ง
            ภายใต้แสง 150–500 lux และไม่มี dynamic obstacles (คนเดิน)

DoD-Voice:  คำสั่ง ค้นหา/หยุด/กลับฐาน intent accuracy ≥ 90%
            end-to-end latency ≤ 5s (phone webapp)

DoD-Safety: ระยะ <0.3m หยุด 100% (LiDAR only) แม้ server ล่ม
```

**ทำไมถึงตั้งแบบนี้:**
- 80% search success = realistic สำหรับ 2D monocular VLM (ไม่ใช่ 3D depth)
- 60 วินาที = 4-direction scan (~11s rotation × 4 + VLM 2s × 4 + approach ~15s)
- 5 ชนิด = พอสำหรับ statistical analysis (5 objects × 12 configs × 3 repeats = 180 trials)
- LiDAR safety 0.3m = hardware-level guarantee ที่ไม่ขึ้นกับ software

---

## 1. Object Memory — Upgrade Spec

### สถานะปัจจุบัน (มีอะไรแล้ว)
- `object_memory.py` มี: `remember()`, `recall()`, `get_priority_zones()`, `get_search_hint()`
- `main.py` เรียก `remember()` ตอนเจอวัตถุแล้ว ✅
- `main.py` เรียก `get_search_hint()` ตอนเริ่ม search → TTS ประกาศ ✅
- **แต่** `get_priority_zones()` ไม่ได้ถูกใช้จริงในการเลือกทิศที่จะ scan

### สิ่งที่ต้องเพิ่ม

#### 1.1 เพิ่ม Field ใน ObjectLocation

```python
# object_memory.py — เพิ่ม field
@dataclass
class ObjectLocation:
    object_name: str
    display_name: str
    zone: str               # NEW: "DeskA" / "Shelf" / "FloorCenter" / "DoorArea"
    pose_hint: str           # RENAME location → pose_hint ("center_right", "top_left")
    last_seen_ts: float      # RENAME timestamp → last_seen_ts
    seen_count: int = 1      # NEW: จำนวนครั้งที่เจอที่โซนนี้
    confidence: float = 0.8
    snapshot_path: str = ""  # NEW: path ไปยังภาพที่ confirm
    status: str = "active"   # NEW: active / stale / removed
```

#### 1.2 เพิ่ม Status Policy

```python
# TTL & Decay Policy
STALE_MINUTES = 30      # ไม่เจอเกิน 30 นาที → stale
CONFIDENCE_DECAY = 0.1  # ลด 0.1 ต่อ 10 นาที
REMOVE_HOURS = 24       # ไม่เจอเกิน 24 ชม. → removed

def update_status(self):
    """เรียกทุกครั้งที่ access memory"""
    for obj_name, locations in self._memory.items():
        for loc in locations:
            age_min = (time.time() - loc.last_seen_ts) / 60
            if age_min > STALE_MINUTES and loc.status == "active":
                loc.status = "stale"
            if age_min > REMOVE_HOURS * 60:
                loc.status = "removed"
            # Decay confidence
            loc.confidence = max(0.1, loc.confidence - (age_min / 10) * CONFIDENCE_DECAY)
```

#### 1.3 เพิ่ม 3 API ที่ทำให้ search ฉลาดขึ้น

```python
def remember_detection(self, detection: dict):
    """บันทึกจาก VLM detection result โดยตรง
    detection = {name, name_en, zone, pose_hint, confidence, snapshot_path}
    ถ้ามีอยู่แล้ว → เพิ่ม seen_count, อัปเดต zone/confidence
    """

def get_search_prior(self, target_label: str) -> List[dict]:
    """คืน zone priorities สำหรับ search planner
    Returns: [{"zone": "DeskA", "score": 8}, {"zone": "Shelf", "score": 3}, ...]
    score = type_prior + memory_boost + recency_bonus
    """

def mark_not_found_in_zone(self, target: str, zone: str):
    """เรียกเมื่อ scan ทิศนั้นแล้วไม่เจอ → ลด score ของ zone นั้น
    ทำให้ search ไม่วนกลับไป scan ทิศที่เช็คแล้ว
    """
```

#### 1.4 เชื่อม Memory → Search Logic

```python
# main.py visual_search() — เปลี่ยนจาก fixed 90°×4 ให้ใช้ memory
async def visual_search(...):
    priors = object_memory.get_search_prior(target)
    
    # Sort zones by score → scan high-priority zones ก่อน
    sorted_zones = sorted(priors, key=lambda z: z["score"], reverse=True)
    
    for zone in sorted_zones:
        # rotate to zone direction
        # vlm_check
        # if not found → mark_not_found_in_zone(target, zone)
        # if found → remember_detection + approach
```

**Implementation Task:** ~2-3 ชั่วโมง เพราะ skeleton มีแล้ว แค่เพิ่ม field + เชื่อม logic

---

## 2. Frame Quality Gate

### สิ่งที่ต้องสร้าง: `Gateway/gateway/frame_quality.py`

```python
"""
Frame Quality Gate — ตรวจสอบคุณภาพ frame ก่อนส่ง VLM
ถ้าไม่ผ่าน → ไม่เรียก VLM (ประหยัดเวลา 1.5-2s)
"""

import numpy as np
from PIL import Image
import io

# Thresholds (ปรับตาม environment จริง)
MIN_BRIGHTNESS = 40       # mean pixel value (0-255), ต่ำกว่านี้ = มืดเกิน
MAX_BRIGHTNESS = 240      # สูงกว่านี้ = overexposed
MIN_SHARPNESS = 50        # Laplacian variance, ต่ำกว่านี้ = เบลอ

def check_frame_quality(jpeg_bytes: bytes) -> dict:
    """
    ตรวจสอบคุณภาพ frame
    
    Returns:
        {
            "accepted": bool,
            "reason": str,           # "" ถ้า accepted, "too_dark"/"too_bright"/"too_blurry" ถ้าไม่
            "quality_score": float,   # 0.0 - 1.0
            "brightness": float,      # mean pixel value
            "sharpness": float,       # laplacian variance
        }
    """
    img = Image.open(io.BytesIO(jpeg_bytes)).convert("L")  # grayscale
    arr = np.array(img, dtype=np.float32)
    
    brightness = arr.mean()
    # Laplacian = second derivative → high variance = sharp image
    laplacian = np.array([[0, 1, 0], [1, -4, 1], [0, 1, 0]], dtype=np.float32)
    # Simple convolution for sharpness
    from scipy.ndimage import convolve
    lap = convolve(arr, laplacian)
    sharpness = lap.var()
    
    # Quality score: normalize both metrics to 0-1
    b_score = 1.0 - abs(brightness - 128) / 128  # best at 128
    s_score = min(sharpness / 200, 1.0)            # saturates at 200
    quality_score = 0.5 * b_score + 0.5 * s_score
    
    # Decide
    if brightness < MIN_BRIGHTNESS:
        return {"accepted": False, "reason": "too_dark", "quality_score": quality_score,
                "brightness": brightness, "sharpness": sharpness}
    if brightness > MAX_BRIGHTNESS:
        return {"accepted": False, "reason": "too_bright", "quality_score": quality_score,
                "brightness": brightness, "sharpness": sharpness}
    if sharpness < MIN_SHARPNESS:
        return {"accepted": False, "reason": "too_blurry", "quality_score": quality_score,
                "brightness": brightness, "sharpness": sharpness}
    
    return {"accepted": True, "reason": "", "quality_score": quality_score,
            "brightness": brightness, "sharpness": sharpness}
```

### เชื่อมกับ `_vlm_check()` ใน main.py

```python
from gateway.frame_quality import check_frame_quality

async def _vlm_check(target, timeout):
    frame = cam.get_frame()
    
    # Quality gate
    quality = check_frame_quality(frame)
    if not quality["accepted"]:
        logger.warning(f"⚠️ Frame rejected: {quality['reason']} "
                      f"(brightness={quality['brightness']:.0f}, sharpness={quality['sharpness']:.0f})")
        return False, "", ""   # Skip VLM call — save 1.5-2s
    
    # ... ต่อ VLM pipeline ปกติ
```

**Implementation Task:** ~1 ชั่วโมง (ง่าย, numpy + PIL ที่มีอยู่แล้ว)  
**ข้อควรระวัง:** ต้องทดสอบ threshold กับห้องจริง อาจต้อง calibrate

---

## 3. Motion Controller — Closed-loop แบบเบา

### สถานะปัจจุบัน
- ทุก motion เป็น open-loop: `publish cmd_vel for N seconds → stop`
- Approach phase: turn 30° → move forward → VLM re-check (pseudo-closed-loop)
- ไม่มี "align object to center" logic
- ไม่มี "stop & re-acquire" เมื่อวัตถุหายจากภาพ

### Upgrade: 3 Primitives ที่พอทำได้

#### 3.1 `align_object_to_center(location)` — Visual Servoing แบบง่าย

```python
async def align_object_to_center(location: str, max_attempts: int = 3):
    """หมุนทีละเล็กน้อยจนวัตถุอยู่ตรงกลางภาพ"""
    for attempt in range(max_attempts):
        if "left" in location:
            await motion.exec_motion({"type": "move", "linear_x": 0, 
                                       "angular_z": 0.3, "duration": 0.3})
        elif "right" in location:
            await motion.exec_motion({"type": "move", "linear_x": 0, 
                                       "angular_z": -0.3, "duration": 0.3})
        else:
            break  # centered
        
        await asyncio.sleep(0.3)
        found, new_loc, _ = await _vlm_check(target, 15.0)
        if not found:
            break  # lost object
        location = new_loc
    
    return location
```

#### 3.2 `approach_with_verify(target, location)` — Stop & Re-acquire

**ปรับ approach phase ที่มีอยู่** ให้มี re-acquire logic:

```python
# ใน approach phase, หลัง VLM check
if not found:
    lost_count += 1
    if lost_count <= 2:
        # Re-acquire: ถอยเล็กน้อย + scan มุมเล็ก
        logger.info("👀 Object lost — backing up + small scan")
        await motion.exec_motion({"type": "move", "linear_x": -0.10, 
                                   "angular_z": 0, "duration": 0.5})
        # scan ±30°
        for angle in [20, -40, 20]:  # left, right, back to center
            await rotate_degrees(angle)
            found, new_loc, _ = await _vlm_check(target, 15.0)
            if found:
                location = new_loc
                lost_count = 0
                break
    else:
        logger.info("👀 Object lost 3 times — stopping approach")
        break
```

#### 3.3 Stop Conditions (ทุก primitive ต้องมี)

```python
# ใส่ไว้ใน motion controller ทุกจังหวะ:
STOP_CONDITIONS = {
    "lidar_emergency": lambda: obstacle_avoidance.min_front_distance < 0.3,
    "timeout": lambda step_start: (time.time() - step_start) > 10.0,
    "no_vision": lambda lost_count: lost_count >= 3,
    "cancel": lambda: _search_cancel,
}
```

**Implementation Task:** ~3-4 ชั่วโมง — แก้ใน approach phase ที่ existing แล้ว ไม่ต้องสร้างจาก 0

---

## 4. Experiment Logger — Schema + Manifest

### สถานะปัจจุบัน
- ไม่มี file logging เลย → ทุกอย่าง console only

### สิ่งที่ต้องสร้าง

#### 4.1 `manifest.json` — สร้างอัตโนมัติตอนเริ่ม experiment session

```json
{
    "session_id": "20260226_143000",
    "git_commit": "9326bee",
    "room": "IT_Lab_6x8m",
    "light_condition": "normal_400lux",
    "models": {
        "vlm": "qwen3-vl:8b",
        "llm": "gemma3:27b-it-qat",
        "stt": "distil-whisper-th-large-v3-ct2"
    },
    "params": {
        "rotation_cal": 0.87,
        "angular_speed": 0.50,
        "linear_speed": 0.10,
        "vlm_max_tokens": 300,
        "min_rotate_dur": 0.5
    },
    "camera": {
        "resolution": "640x480",
        "vlm_resize": "480x360",
        "mount": "forward_-15deg"
    },
    "prompt_versions": {
        "vlm_prompt": "v3_position_hints",
        "llm_system": "v2_bilingual"
    }
}
```

#### 4.2 `trial_schema` — field บังคับทุก trial

```python
REQUIRED_FIELDS = [
    "trial_id",            # "B_wallet_front_near_001"
    "timestamp",           # ISO 8601
    "experiment_phase",    # "A_static" / "B_search" / "C_obstacle" / "D_stt"
    "target",              # "กระเป๋าสตางค์"
    "target_en",           # "wallet"
    "success",             # bool
    "duration_s",          # float
    "failure_reason",      # "" if success, otherwise failure taxonomy code
]

FAILURE_TAXONOMY = [
    "stt_misheard",         # STT แปลผิด
    "vlm_missed",           # VLM ไม่เจอวัตถุ (อยู่ในภาพแต่ไม่ detect)
    "vlm_false_positive",   # VLM บอกเจอแต่ไม่มีจริง
    "vlm_blur",             # Frame quality ต่ำ
    "vlm_low_light",        # แสงไม่พอ
    "llm_wrong_reason",     # LLM reason ผิด (VLM เห็นแต่ LLM ตัดว่าไม่ใช่)
    "occlusion",            # วัตถุถูกบัง
    "motion_overshoot",     # หุ่นหมุน/เดินเกิน
    "obstacle_blocked",     # สิ่งกีดขวางขวาง
    "network_timeout",      # API timeout
    "object_reflective",    # วัตถุสะท้อนแสง
    "object_too_small",     # วัตถุเล็กเกินกว่า VLM จะเห็น
]
```

#### 4.3 Implementation: `Gateway/gateway/experiment_logger.py`

```python
import json, os, csv, time, subprocess
from datetime import datetime
from pathlib import Path

class ExperimentLogger:
    def __init__(self, experiment_name: str):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_dir = Path(f"logs/{experiment_name}_{ts}")
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._trials = []
        self._write_manifest()
    
    def _write_manifest(self):
        manifest = {
            "session_id": self.log_dir.name,
            "git_commit": self._get_git_commit(),
            "started_at": datetime.now().isoformat(),
            # ... room config, model versions, params ...
        }
        (self.log_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False))
    
    def log_trial(self, trial: dict):
        """Log one trial — validates required fields"""
        for field in REQUIRED_FIELDS:
            assert field in trial, f"Missing field: {field}"
        if trial.get("failure_reason") and trial["failure_reason"] not in FAILURE_TAXONOMY:
            print(f"WARNING: Unknown failure reason: {trial['failure_reason']}")
        
        self._trials.append(trial)
        # Append to JSONL (one JSON per line → easy to parse)
        with open(self.log_dir / "trials.jsonl", "a") as f:
            f.write(json.dumps(trial, ensure_ascii=False) + "\n")
    
    def save_summary_csv(self):
        """Export CSV สำหรับ Excel analysis"""
        if not self._trials:
            return
        with open(self.log_dir / "summary.csv", "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=self._trials[0].keys())
            writer.writeheader()
            writer.writerows(self._trials)
    
    def _get_git_commit(self):
        try:
            return subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"], text=True).strip()
        except:
            return "unknown"
```

**Implementation Task:** ~2 ชั่วโมง — standalone module, ไม่กระทบ code เดิม

---

## 5. Experiment Design — Refined (ลดขนาดจาก 430 → สิ่งที่ทำได้จริง)

### Baseline Comparison (สำคัญที่สุด)

```
Baseline A: Fixed Scan (ปัจจุบัน)
  = หมุน 90° × 4 ทุกครั้ง, ไม่ใช้ memory

Baseline B: Memory + Zone-first (ใหม่)
  = ใช้ memory prior เลือกทิศแรก, scan ทิศที่น่าจะเจอก่อน
  
Compare: search_time, success_rate, #vlm_calls
```

นี่คือ "1 experiment ที่ impact สูงสุด" — พิสูจน์ว่า memory ช่วยจริง

### Experiment Phases (ปรับลด)

| Phase | Trials | เวลา | Priority |
|-------|--------|------|----------|
| **A: VLM Static** (หุ่นนิ่ง) | 50 | 2 ชม. | 🔴 ทำก่อน |
| **B: Full Search (Baseline A vs B)** | 120 | 4-6 ชม. | 🔴 ทำก่อน |
| **D: STT/Latency** | 60 | 2 ชม. | 🟡 ทำถ้ามีเวลา |
| **C: Obstacle** | 10 | 1 ชม. | 🟢 optional |
| **Total** | **240** | **~10 ชม.** | |

### Failure Taxonomy (ทุก trial ที่ fail ต้องติด tag)

ดู `FAILURE_TAXONOMY` list ด้านบน — เป็น paper-grade analysis

---

## 6. Future Capability Roadmap

### Level 1 — จบโปรเจกต์นี้ (Feb-Mar 2026)

| Feature | Status | DoD |
|---------|--------|-----|
| Voice-commanded visual search | ✅ Working | ≥80% success, ≤60s |
| Rotate-Scan-First + Approach | ✅ Working | — |
| Object Memory (zone prior) | ⏳ Partial | `get_search_prior` ใช้จริงใน search |
| Frame Quality Gate | ⏳ Not started | Reject dark/blurry ก่อน VLM |
| Experiment Logger | ⏳ Not started | JSONL + CSV per session |
| LiDAR Safety Stop | ✅ Working | 100% stop at <0.3m |
| Thai STT + TTS | ✅ Working | WER <15%, latency <5s |
| Baseline Comparison | ⏳ Not started | Fixed vs Memory search |

### Level 2 — เทอมหน้า / ต่อยอด

| Feature | Description |
|---------|-------------|
| Dynamic obstacles | Pause/resume เมื่อคนเดินตัดหน้า |
| Multi-object request | "หา A แล้วหา B" → sequential search |
| Visual servoing | `align_object_to_center` + stop & re-acquire |
| Simple room map | Waypoint + relocalize (ไม่ต้อง full SLAM) |
| VLM obstacle analysis | Layer 2: ดูภาพ obstacle → ตัดสินใจอ้อมซ้าย/ขวา |

### Level 3 — อนาคต

| Feature | Description |
|---------|-------------|
| Semantic zone mapping | Auto-label zones จาก VLM ("นี่คือโต๊ะทำงาน") |
| Multi-room navigation | ออกห้อง → ค้นหาห้องอื่น |
| Proactive assistance | "เห็นของตกพื้น" → แจ้งเตือนเอง |
| Continuous memory | Long-term 4D SG แบบ DAAAM (lightweight version) |

---

## 7. Final System Behavior Statement (ใส่ paper ได้)

> **VORA uses a zone-first search policy guided by an explicit object memory. 
> The robot navigates to predefined zone viewpoints, performs zone-specific 
> visual sweeps with frame-quality gating, and executes a closed-loop approach 
> using visual feedback (object centering + short forward steps). Each confirmed 
> detection updates the memory (zone, timestamp, snapshot), reducing future 
> search time and increasing success rate.**

---

## 8. Vibe Coding Task List — เรียงตาม Priority

### Sprint 1: Foundation (ทำวันนี้-พรุ่งนี้)

| # | Task | File | เวลา | DoD ที่เกี่ยว |
|---|------|------|------|-------------|
| **T1** | สร้าง `experiment_logger.py` + manifest + trial schema | `Gateway/gateway/experiment_logger.py` | 2h | ทุก DoD (เก็บ data) |
| **T2** | สร้าง `frame_quality.py` + เชื่อม `_vlm_check()` | `Gateway/gateway/frame_quality.py` + `main.py` | 1h | DoD-Search |
| **T3** | Upgrade `object_memory.py` — เพิ่ม field + status policy | `Gateway/gateway/object_memory.py` | 1.5h | DoD-Search |
| **T4** | เพิ่ม `get_search_prior()` + `mark_not_found_in_zone()` | `Gateway/gateway/object_memory.py` | 1h | DoD-Search |

### Sprint 2: Integration (หลัง Sprint 1)

| # | Task | File | เวลา | DoD ที่เกี่ยว |
|---|------|------|------|-------------|
| **T5** | เชื่อม memory → search logic (zone-first scan order) | `Gateway/gateway/main.py` | 2h | DoD-Search |
| **T6** | เพิ่ม stop & re-acquire ใน approach phase | `Gateway/gateway/main.py` | 1.5h | DoD-Search |
| **T7** | เพิ่ม `align_object_to_center()` basic version | `Gateway/gateway/main.py` | 1h | DoD-Search |
| **T8** | Inject logger เข้า visual_search + _vlm_check | `Gateway/gateway/main.py` | 1h | ทุก DoD |

### Sprint 3: Experiment (หลัง Sprint 2)

| # | Task | File | เวลา |
|---|------|------|------|
| **T9** | รัน Phase A: VLM Static Test (50 trials) | — | 2h |
| **T10** | รัน Phase B: Baseline A vs B comparison (120 trials) | — | 5h |
| **T11** | รัน Phase D: STT/Latency benchmark (60 trials) | — | 2h |
| **T12** | วิเคราะห์ผล + สร้าง result tables | — | 2h |

### Total Estimate: ~3-4 วัน (coding 2 วัน + experiment 1-2 วัน)

---

## 9. สรุปสิ่งที่เห็นด้วย / ไม่เห็นด้วยจาก ChatGPT Analysis

### ✅ เห็นด้วยและควรทำ
- **DoD** — ต้องมี ทำให้ scope ชัดเจน จบได้
- **Object Memory upgrade** — มีพื้นฐานแล้ว เพิ่มอีกนิดก็ใช้ได้จริง
- **Frame Quality Gate** — ง่าย ได้ผลดี ประหยัด VLM calls
- **Experiment Logger + manifest + schema** — ถ้าไม่มีนี่ ข้อมูล experiment ไม่มีค่า
- **Failure Taxonomy** — ทำให้ paper ดู professional มาก
- **Baseline comparison (Fixed vs Memory)** — impact สูง ทำง่าย
- **Stop & Re-acquire** — practical upgrade สำหรับ approach phase
- **3-level roadmap** — realistic, อาจารย์ชอบ

### ⚠️ เห็นด้วยแต่ยังไม่ต้องทำตอนนี้
- **Search Planner (zone sweep pattern)** — ดีแต่ซับซ้อน ตอนนี้แค่ปรับ scan order ตาม memory ก็พอ
- **`align_object_to_center()`** — ไม่ยาก แต่ต้องเทสจริง ค่อยทำ Sprint 2
- **Ablation study** — ดี แต่ถ้าเวลาจำกัด เอา baseline comparison ก่อน

### ❌ ไม่ควรทำตอนนี้
- **Camera mount/tilt optimization** — ตามที่พี่บอก ควรโฟกัส process ก่อน mount ค่อยปรับทีหลัง
- **430 trials** — มากเกิน ลดเหลือ **240 trials** เพียงพอ statistical significance + ทำจริงได้ใน 2 วัน
- **VLM obstacle analysis (Layer 2)** — cool แต่ LiDAR Layer 1 ก็ "พอใช้" สำหรับ static obstacle ที่เป็น scope

---

## 10. Dependency Graph — ทำอะไรก่อนอะไร

```
T1: ExperimentLogger ─────────────────────────────────────┐
                                                          ▼
T2: FrameQuality ──┐                              T8: Inject Logger
                   ▼                                      │
T3: Memory Upgrade ──► T4: Search Prior API ──► T5: Zone-first Search ──► T9-T12: Experiments
                                                          │
T6: Stop & Re-acquire ──► T7: Align to Center ────────────┘
```

**Critical path:** T1 → T3 → T4 → T5 → T8 → T9 (experiment)  
**Parallel path:** T2 (independent), T6+T7 (independent)

---

**สรุปสุดท้าย:** โฟกัส 4 อย่าง → ExperimentLogger, Frame Quality Gate, Object Memory upgrade, Zone-first search integration  
ที่เหลือเป็น enhancement ที่ทำเมื่อพื้นฐาน solid แล้ว
