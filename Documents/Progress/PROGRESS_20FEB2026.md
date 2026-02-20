# VORA Progress Report — 20 February 2026

## 📋 Summary

วันนี้โฟกัส **Visual Search Pipeline** — ปรับปรุงการค้นหาวัตถุด้วย VLM + LLM Reasoning ให้ทำงานจริงบนหุ่นได้ ,
ปรับ Rotation Calibration ให้แม่นยำ, เพิ่ม Approach Phase ให้หุ่นเดินเข้าหาวัตถุ, และแก้ไข Webapp ให้แสดง BBox ถูกตำแหน่ง

**ผลการทดสอบ:** ค้นหา "กระเป๋าตังสีดำ" สำเร็จ — VLM เจอวัตถุ, LLM Reasoning ถูกต้อง, หุ่นหมุนหาและเดินเข้าหาวัตถุได้  
**ข้อจำกัด:** วัตถุสะท้อนแสง (เช่น กุญแจสีเงินบนกระดาษขาว) VLM มีปัญหาในการระบุ

---

## 🔧 Changes Made (เทียบกับ 19 Feb 2026)

### 1. Rotation Calibration Fine-tuning

**ปัญหา:** `ROTATION_CALIBRATION = 1.0` (จาก 12 Feb) ทำให้หุ่นหมุนเกินไป ~30° ต่อรอบ  
**ที่มา:** ที่ angular_velocity = 0.50 rad/s, momentum ของ Mecanum wheels ยังทำให้หมุนเกินเล็กน้อย

**การจูน:**
```
1.00 → เกิน ~30° (360° สั่งได้ ~390°)
0.90 → เกินอีกนิด (~5°)
0.87 → ✅ ดีที่สุด (ทดสอบแล้ว)
```

**สูตร:** `duration = (angle_rad / 0.50) * ROTATION_CALIBRATION`  
- 90° = `(π/2 / 0.50) * 0.87 = 2.73s`  
- 360° = `(2π / 0.50) * 0.87 = 10.92s`

**ไฟล์ที่แก้ (ทั้ง 3 จุดใช้ค่าเดียวกัน):**
- `Gateway/gateway/main.py` — `SCAN_ROTATION_CAL = 0.87` (line 280), `ROTATION_CAL = 0.87` (line 770), `ROTATION_CALIBRATION = 0.87` (line 1112)
- `Gateway/gateway/intent_parser.py` — `ROTATION_CALIBRATION = 0.87` (line 12)

### 2. MIN_ROTATE_DUR — Minimum Rotation Duration

**ปัญหา:** มุมเล็ก (<15°) สร้าง duration < 0.3s ซึ่ง Mecanum wheels ไม่ตอบสนองทัน (WebSocket delay + motor ramp-up)

**แก้ไข:** เพิ่ม minimum 0.5s ใน `Gateway/gateway/ros_cmd.py` (line 87-90):
```python
MIN_ROTATE_DUR = 0.5  # seconds (5 messages minimum for rotation)
if abs(az) > 0.01 and abs(lx) < 0.01 and duration < MIN_ROTATE_DUR:
    duration = MIN_ROTATE_DUR
```

### 3. Visual Search — Compound Location Format

**ปัญหา:** VLM ตอบตำแหน่งแค่ "left/right/center" ไม่พอสำหรับ approach phase ที่ต้องรู้ว่าวัตถุอยู่ไกลหรือใกล้

**แก้ไข:** เปลี่ยน LLM JSON output ให้ใช้ compound locations:
```
เดิม: left / right / center / unknown
ใหม่: top_left / top_right / top_center / center_left / center /
       center_right / bottom_left / bottom_right / bottom_center / unknown
```

**ไฟล์ที่แก้:**
- `Gateway/gateway/main.py` — LLM prompt ใช้ compound format, เพิ่ม `_location_to_thai()` (line 911-935) รองรับ 20+ ตำแหน่ง
- `app/frontend/index.html` — `showBBox()` แยก horizontal (left/right) + vertical (top/bottom) mapping

### 4. Approach Phase — หุ่นเดินเข้าหาวัตถุ

**แนวคิด:** หลังจาก VLM เจอวัตถุ, หุ่นจะ:
1. หมุนหันหน้าเข้าหาวัตถุ (ซ้าย/ขวา)
2. เดินตรงเข้าไป (ระยะขึ้นกับตำแหน่ง)
3. VLM เช็คซ้ำ → ถ้ายังไม่ถึง ทำซ้ำ (สูงสุด 6 รอบ)

**ไฟล์:** `Gateway/gateway/main.py` (line 768-870)

**ระยะเดิน (ขึ้นกับตำแหน่งในภาพ):**
- `"bottom"/"near"` (วัตถุอยู่ด้านล่างภาพ = ใกล้หุ่น) → step เล็ก 0.8s
- `"top"/"far"` (วัตถุอยู่ด้านบนภาพ = ไกลจากหุ่น) → step ใหญ่ 3.0s  
- อื่นๆ → step กลาง 1.5s

### 5. VLM Pipeline — Bilingual Reasoning

**ปัญหาหลัก:** Qwen3-VL:8B บางครั้งตอบเป็นภาษาอังกฤษ (Chain-of-Thought) แทนที่จะตอบภาษาไทย  
ตัวอย่าง: `"Okay, let's see. The user is asking about..."` → ไม่มี scene description จริง

**การแก้ไข (3 ส่วน):**

#### 5a. VLM Prompt — เพิ่ม Position Hints + Token Budget
```python
vlm_prompt = (
    f"ภาพนี้มีอะไรบ้าง? มี '{vlm_target}' ไหม? "
    f"บอกวัตถุและตำแหน่ง (ซ้าย/ขวา/กลาง/บน/ล่าง) ตอบภาษาไทยสั้นๆ"
)
# max_tokens: 200 → 300 (ให้ VLM มีที่พอ generate เนื้อหาจริงหลัง CoT preamble)
```

**ไฟล์:** `Gateway/gateway/main.py` (line 555-567)

#### 5b. VLM CoT Handling — Less Aggressive Filtering
เดิม: ถ้า VLM ตอบภาษาอังกฤษ → return empty → search ล้มเหลว  
ใหม่: ถ้ามี Thai text → extract Thai, ถ้าไม่มี → **เก็บ English ไว้ให้ LLM** reason ต่อ

```python
if thai_ratio < 0.2:  # < 20% Thai = mostly English
    thai_segments = re.findall(r'[\u0E00-\u0E7F][\u0E00-\u0E7F\s,.()0-9]{8,}', response_text)
    if thai_segments:
        response_text = max(thai_segments, key=len)  # Use Thai
    else:
        # KEEP English — Gemma3-27b can reason cross-language
        logger.warning("Pure English VLM response — keeping for LLM cross-language reasoning")
```

**ไฟล์:** `app/providers/llm/qwen_vlm.py` (line 288-311)

#### 5c. LLM Reasoning — Full Context + Bilingual System Prompt

เดิม: ส่งแค่ชื่อวัตถุสั้นๆ → LLM ขาด context  
ใหม่: ส่ง **full user target** (เช่น "กุญแจที่วางบนกระดาษสีขาว") เพื่อให้ LLM reasoning ได้เต็มที่  
เพิ่ม: `vlm_target` (English = "key") เป็น reference สำหรับ cross-language matching

```python
llm_system = (
    "...คำอธิบายอาจเป็นภาษาไทยหรือภาษาอังกฤษ — ต้องเข้าใจทั้งสองภาษา..."
)
llm_prompt = (
    f"สิ่งของที่กำลังหา: \"{target}\"\n"
    f"ชื่อภาษาอังกฤษ: \"{vlm_target}\"\n"
    f"สำคัญ: คำอธิบายภาพอาจเป็นภาษาอังกฤษ — ให้ matching ข้ามภาษาได้\n"
)
```

**ไฟล์:** `Gateway/gateway/main.py` (line 593-617)

### 6. Camera Logging — ลด Frame Log Flood

**ปัญหา:** Camera frame count log ท่วม terminal (ทุก ~1 วินาที)  
**แก้ไข:** เปลี่ยน `% 30` → `% 300` = log ทุก ~10 วินาที

**ไฟล์:** `Gateway/gateway/camera_stream.py` (line 178, 205)

### 7. Webapp — CoT Spinner Fix + BBox Position

**CoT Spinner:** `addCotStep()` ตอนนี้ลบ spinner จาก step ก่อนหน้าเมื่อ mark เป็น done  
**BBox:** `showBBox()` แยก horizontal/vertical mapping สำหรับ compound locations (top_left → left + top)

**ไฟล์:** `app/frontend/index.html`

---

## 📊 Architecture — Visual Search Data Flow

```
User: "ช่วยหากระเป๋าตังสีดำ"
         │
         ▼
  ┌──────────────────┐      parse_find_intent()
  │  Intent Parser   │──────────────────────────────┐
  │  (Gateway)       │                              │
  └──────────────────┘                              │
         │ target = "กระเป๋าตัง"                     │
         │ vlm_target = "wallet"                    │
         ▼                                          │
  ┌──────────────────┐   Rotate-Scan-First          │
  │  Visual Search   │   90° × 4 directions         │
  │  Orchestrator    │◄─────────────────────┐       │
  └──────────────────┘                      │       │
         │                                  │       │
         ▼  (each direction)                │       │
  ┌──────────────────┐                      │       │
  │  Camera Frame    │  480×360 JPEG        │       │
  │  (ROSBridge)     │                      │       │
  └──────────────────┘                      │       │
         │                                  │       │
         ▼                                  │       │
  ┌──────────────────┐                      │       │
  │  Qwen3-VL:8B     │  /vlm/describe-bytes │       │
  │  (Server Ollama) │  max_tokens=300      │       │
  └──────────────────┘                      │       │
         │ scene description (Thai/English) │       │
         ▼                                  │       │
  ┌──────────────────┐                      │       │
  │  Gemma3:27b-it   │  LLM Reasoning      │       │
  │  (Server Ollama) │  Bilingual matching  │       │
  └──────────────────┘                      │       │
         │ JSON: {found, location, ...}     │       │
         ▼                                  │       │
    found=true? ──NO──► rotate 90° ─────────┘       │
         │                                          │
        YES                                         │
         │                                          │
         ▼                                          │
  ┌──────────────────┐                              │
  │  Approach Phase  │  หมุนหันหน้า + เดินตรง       │
  │  (max 6 steps)   │  VLM re-check ทุก step       │
  └──────────────────┘                              │
         │                                          │
         ▼                                          │
    ✅ "เจอ {target} แล้ว ที่ {location}"             │
```

---

## 📁 All Modified Files (since last commit)

### Gateway (Windows PC)
| File | Changes |
|------|---------|
| `Gateway/gateway/main.py` | Rotation cal 0.87, compound locations, approach phase, VLM prompt + position hints, LLM bilingual reasoning, `_location_to_thai()` |
| `Gateway/gateway/intent_parser.py` | Rotation cal 0.87 |
| `Gateway/gateway/ros_cmd.py` | MIN_ROTATE_DUR=0.5s for small angles |
| `Gateway/gateway/camera_stream.py` | Frame log `% 30` → `% 300` |

### Server (Linux A6000)
| File | Changes |
|------|---------|
| `app/providers/llm/qwen_vlm.py` | VLM English CoT: less aggressive filtering, keep English for cross-language reasoning |
| `app/api/vlm_router.py` | (default max_tokens=200, Gateway overrides to 300) |
| `app/frontend/index.html` | CoT spinner fix, BBox compound location mapping |

---

## 🧪 Test Results

### ✅ Passed: กระเป๋าตังสีดำ (Black Wallet)
- VLM เห็นวัตถุ → ตอบภาษาไทย + ตำแหน่ง
- LLM Reasoning: `found=true`, location ถูกต้อง
- หุ่นหมุนหาและเดินเข้าหาวัตถุได้สำเร็จ

### ⚠️ Partial: กุญแจที่วางบนกระดาษสีขาว (Silver Key on White Paper)
- VLM มีปัญหาในการมองเห็น (สะท้อนแสง + พื้นหลังขาว = low contrast)
- บางครั้ง VLM ตอบเป็นภาษาอังกฤษ → แก้แล้วด้วย cross-language reasoning
- ต้องปรับปรุง: อาจต้อง prompt engineering เพิ่มสำหรับวัตถุ reflective

### ✅ Rotation Calibration 0.87
- 360° หมุนไม่เกินไป (เดิม 1.0 เกิน ~30°, 0.90 เกินนิดหน่อย → 0.87 พอดี)
- มุมเล็ก (10°-15°) ตอบสนองได้ด้วย MIN_ROTATE_DUR

---

## 📈 Overall Project Status

| Phase | Component | Status | %Complete |
|-------|-----------|--------|-----------|
| 1 | STT Pipeline | ✅ Complete | 100% |
| 2 | Robot Integration | ✅ Complete | 100% |
| 3 | Performance Opt | ✅ Complete | 100% |
| 4 | Deployment | ✅ Complete | 100% |
| 5 | Visual Search | 🔧 In Progress | 80% |

### Phase 5 — Visual Search Breakdown
| Feature | Status | Notes |
|---------|--------|-------|
| Rotate-Scan-First | ✅ Done | หมุน 90° × 4 ทิศ |
| VLM Scene Description | ✅ Done | Qwen3-VL:8B + /no_think |
| LLM Reasoning | ✅ Done | Gemma3:27b bilingual |
| Compound Locations | ✅ Done | 9+ ตำแหน่ง (top_left, bottom_right, etc.) |
| Approach Phase | ✅ Done | หุ่นเดินเข้าหาวัตถุ, max 6 steps |
| BBox Overlay | ✅ Done | Webapp แสดงกรอบตำแหน่ง |
| Cross-language Reasoning | ✅ Done | VLM English → LLM เข้าใจ |
| Reflective Object Detection | ⏳ Todo | กุญแจสีเงิน, แก้ว, etc. |
| Multi-object Search | ⏳ Todo | หาหลายวัตถุพร้อมกัน |
| Object Memory | ⏳ Todo | จำตำแหน่งวัตถุที่เคยเจอ |

---

## ⚠️ Known Limitations

1. **Reflective Objects** — วัตถุสีเงิน/สะท้อนแสง + พื้นหลังขาว = VLM มองไม่เห็น (low contrast)
2. **VLM English CoT** — Qwen3-VL:8B ยังตอบเป็นภาษาอังกฤษบางครั้ง (mitigated ด้วย cross-language LLM reasoning)
3. **Waypoint Navigation** — Nav2 stack ยังไม่ได้ run บน MyAGV (ใช้ /cmd_vel direct control แทน)

---

## 🚀 Next Steps

- [ ] ปรับปรุง VLM prompt สำหรับวัตถุ reflective (contrast enhancement hints)
- [ ] เพิ่ม Object Memory — จำตำแหน่งวัตถุที่เคยเจอไว้ใน session
- [ ] Multi-object search — "หากุญแจกับกระเป๋า" ค้นหาหลายวัตถุพร้อมกัน
- [ ] Agent-level reasoning — ให้ LLM วางแผน search strategy ซับซ้อนขึ้น
- [ ] ทดสอบวัตถุหลากหลาย (ขวดน้ำ, โทรศัพท์, ปากกา, etc.)

---

**Report Date:** 20 February 2026  
**Previous Report:** [PROGRESS_19FEB2026.md](PROGRESS_19FEB2026.md) — Camera Streaming Implementation  
**Next Review:** After multi-object search implementation
