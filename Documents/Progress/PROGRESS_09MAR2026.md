# VORA Progress Report — 9 March 2026

## 📋 สรุปภาพรวม

วันนี้ทำ 3 เรื่องหลัก:
1. **อัปเกรด VLM** จาก qwen3-vl:8b → qwen3-vl:32b + แก้ prompt echo
2. **แก้ Phase 2 exploration** ให้หุ่นฉลาดขึ้น (LLM Navigator + LiDAR cap + Spatial Memory)
3. **ทดสอบจริง 3 รอบ** — พบปัญหาใหม่ที่ต้องแก้ต่อ

---

## ✅ สิ่งที่แก้ไขสำเร็จ

### 1. VLM Upgrade: 8B → 32B + English Prompts
| รายการ | ก่อน | หลัง |
|---|---|---|
| VLM Model | qwen3-vl:8b (6.1GB) | **qwen3-vl:32b (20GB)** |
| VLM Prompt | Thai | **English** (`/no_think`) |
| Prompt echo | เกิดบ่อย ~50% | **0% (ไม่เกิดเลยใน 3 รอบทดสอบ)** |
| VLM Output | Thai (บ่อยครั้ง echo prompt กลับ) | **English ล้วน, บรรยายฉากได้ละเอียด** |

**ไฟล์ที่แก้:**
- `app/core/settings.py` — `OLLAMA_VLM_MODEL = "qwen3-vl:32b"`
- `app/api/vlm_router.py` — default prompt เปลี่ยนเป็น English

### 2. Phase 2 Movement: LiDAR Cap + LLM Navigator
**ปัญหาเดิม:** LiDAR best direction = +15° ตลอด เพราะ `∞` (infinity) ได้ score สูงสุดเสมอ → หุ่นเดินทิศเดิมทุกรอบ

**แก้ไข:**
- `Gateway/gateway/obstacle_avoidance.py` — cap `avg_dist` และ `min_dist` ที่ 2.5m ก่อน scoring
- `Gateway/gateway/main.py` — เพิ่ม `_ask_llm_navigate()` ให้ LLM เลือกทิศจาก exploration memory + LiDAR data
- เพิ่ม English wall keywords: "white panel", "no object", "uniform gray" ฯลฯ

**ผลลัพธ์:** LLM Navigator ทำงาน — override LiDAR ได้ เช่น +15° → -45°, +15° → +135° เป็นต้น

### 3. Phase 2: ปิด MAP_BLOCK Loop
MAP_BLOCK ทำให้หุ่นติดวนหมุน 360° ไม่สิ้นสุด → ปิด (hardcoded `MapCheck=False`)

### 4. Query Truncation Fix
**ปัญหาเดิม:** "หาขวดน้ำให้ผมหน่อยครับ Avias รูปนกบนสีขาวบนฉลาก" → ตัดเหลือแค่ "ขวดน้ำ"

**แก้ไข:** `Gateway/gateway/intent_parser.py` — `_clean_search_target()` เก็บ brand/description ไว้ (max 80 chars)

### 5. Semantic Spatial Memory (ใหม่ทั้งหมด)
**สร้างไฟล์ใหม่:** `Gateway/gateway/spatial_memory.py` (310 บรรทัด)

4 ฟีเจอร์:
1. **Record** — บันทึก VLM observation + odom pose + heading ทุกจุด
2. **Exploration Summary** — สรุปพื้นที่ที่สำรวจแล้วให้ LLM
3. **Skip-duplicate** — ข้ามการ scan ซ้ำ ถ้า position+heading เดิมภายใน TTL
4. **Co-location** — LLM วิเคราะห์ว่าเป้าหมาย likely อยู่ใกล้ landmark ไหน

**Persistence:** `Gateway/data/spatial_memory.json` (auto-expire 1hr, cap 200 records)

### 6. CoT Prefix Stripping (English-only)
VLM ตอนนี้ output English ล้วนแต่ยังมี CoT prefix "So, let's look at the image." ทุกครั้ง
→ เพิ่ม regex stripping สำหรับ English-only output (Strategy B)

**ผลจาก log:** `stripped English CoT prefix (1230→1201 chars)` ✅ ทำงานทุกรอบ

---

## ❌ ปัญหาที่เจอจากการทดสอบจริง (ต้องแก้ต่อ)

### ปัญหา 1: Skip-Duplicate กิน VLM Check ที่สำคัญ (แก้แล้วบางส่วน)
**อาการ:** หลังเดินไปข้างหน้า 25cm → ระบบ SKIP ไม่ดูว่าเจออะไร
```
SKIP [phase2_cycle2_fwd] — same position+heading observed recently
SKIP [phase2_cycle2_left] — same position+heading observed recently
SKIP [phase2_cycle2_right] — same position+heading observed recently
```

**สาเหตุ:** `NEAR_RADIUS_M = 0.30m` แต่ก้าวละ 0.25m → ระบบคิดว่าตำแหน่งเดิม

**แก้ไขแล้ว (อยู่ใน code แต่ยังไม่ได้ deploy):**
- `NEAR_RADIUS_M`: 0.30 → **0.12m**
- `NEAR_ANGLE_DEG`: 40° → **30°**
- `SKIP_TTL_SECONDS`: 300s → **120s**
- เพิ่ม `force=True` parameter ใน `_do_vlm_and_check()` → หลังเดินหน้าทุกรอบ force=True ห้าม SKIP

### ปัญหา 2: การหมุน Calibration ขาด 10-20°
**อาการ:** สั่งหมุน 90° แต่หมุนจริง ~70-80° เท่านั้น
**สาเหตุ:** `rotation_cal=0.87` อาจต่ำเกินไปสำหรับพื้นแบบใหม่

**ต้องทำ:** ปรับ `rotation_cal` ขึ้นเป็น ~0.95-1.0 หรือใช้ odom feedback loop ตรวจว่าหมุนถึงมุมจริงหรือยัง

### ปัญหา 3: LiDAR ±15° = ∞ ตลอด (น่าจะ webcam บัง)
**อาการ:** ทุก LiDAR scan → `+15°: ∞` และ `-15°: ∞` เสมอ
```
+15°:      ∞ ✅
-15°:      ∞ ✅
```
**สาเหตุ:** webcam วางบน LiDAR motor → ตัว webcam บัง laser beam ที่มุม ±15° พอดี
**ต้องทำ:** ย้าย webcam ขึ้นสูง 15-20cm เหนือ LiDAR, เยื้องไปด้านหน้า

### ปัญหา 4: หุ่นเดินทะลุขอบ Map ใน Webapp
**อาการ:** หุ่นเดินไปจนตำแหน่งออกนอก SLAM map canvas
**สาเหตุ:** webapp ไม่มี auto-scale/pan ตาม robot position
**ต้องทำ:** แก้ frontend ให้ auto-center ตาม robot position หรือ zoom out อัตโนมัติ

### ปัญหา 5: กล้องอยู่ต่ำ → เห็นแต่ฉลากล่างๆ อ่านข้อความบนฉลากไม่ได้
**อาการ:** VLM เห็น "clear glass or bottle" แต่ไม่เห็นป้ายชื่อ "Avias" / "A"
**สาเหตุ:** กล้องอยู่ระดับ LiDAR (~15cm จากพื้น) → มุมมองเห็นแต่ขาขวด ไม่เห็นป้าย
**ผู้ใช้ยกกล้องขึ้น 30cm แล้ว** แต่ยังต้องปรับมุมก้ม (tilt down 10-15°)

### ปัญหา 6: เห็นขวดแล้วแต่หมุนหนี
**อาการ:** VLM เจอ "water bottle with blue cap" → LLM ตัดสินว่าไม่ใช่ Avias → found=false → หุ่นหันหนี
**สาเหตุ 2 อย่าง:**
1. กล้องอยู่ไกล → VLM อ่านป้ายไม่ชัด → ต้องเข้าใกล้ก่อน
2. LLM strict เกินไป — เห็น "bottle" ควร approach เข้าไปดูใกล้ๆ ก่อนตัดสินใจ

**ต้องทำ:** เพิ่ม "partial match" logic — ถ้า VLM เห็น bottle แต่ยังไม่ชัดว่าใช่ → approach เข้าไปดูใกล้ แทนหมุนหนี

---

## 📁 ไฟล์ที่แก้ไขทั้งหมด

| ไฟล์ | สถานะ | รายละเอียด |
|---|---|---|
| `Gateway/gateway/spatial_memory.py` | **ใหม่** | Semantic Spatial Memory (310 บรรทัด) |
| `Gateway/gateway/main.py` | แก้ไข | LLM Navigator, spatial memory integration, skip-duplicate force, CoT strip English, wall keywords, VLM English prompts |
| `Gateway/gateway/obstacle_avoidance.py` | แก้ไข | Cap infinity score ที่ 2.5m |
| `Gateway/gateway/intent_parser.py` | แก้ไข | Query truncation fix |
| `app/core/settings.py` | แก้ไข | VLM model → qwen3-vl:32b |
| `app/api/vlm_router.py` | แก้ไข | Default prompt → English |

---

## 📊 ผลทดสอบจริง (3 รอบ)

### รอบ 1 (16:44) — ค้นหา "ขวดน้ำ ลายนกบินสีขาว Avias"
- VLM checks: 21 | Prompt echo: 0 | CoT stripped: ✅
- **Skip-duplicate กิน 9 checks** → ขวดหลุด
- VLM เจอ "water bottle with blue label Aqua" → LLM ตัดสินว่าไม่ใช่ Avias (ถูก)
- LLM Navigator ทำงาน: override LiDAR 3 ครั้ง
- **ผลลัพธ์: NOT FOUND** (skip-duplicate ร้ายแรง + น่าจะเจอจริงแต่หลุดไป)

### รอบ 2 (17:09-17:18) — ค้นหา "ขวดน้ำที่มีป้ายสีน้ำเงินขึ้นต้นด้วยตัว A"
- VLM checks: 21 | Prompt echo: 0 | CoT stripped: 9 ครั้ง ✅
- Skip-duplicate: 3 ครั้ง (ลดลงหลังจากแก้ code ลงบางส่วน)
- กล้อง Phase 1 เจอ "clear glass or bottle" → LLM: ไม่ชัดเจน → found=false
- LLM Navigator override LiDAR: -45°, +75°, +135° (กระจายทิศดี)
- VLM เห็นแต่ walls, panels, floor, corridor ตลอด
- **เดินทะลุ map boundary** (odom drift 0.83m)
- **ผลลัพธ์: NOT FOUND**

### รอบ 3 (จากรูป screenshot, 17:15)
- หุ่นเดินออกนอก map canvas (เห็นจาก screenshot)
- LiDAR สีแดงปรากฏเฉพาะตอนเดินหน้า
- Position X:0.52 Y:-0.58 — ไกลจากจุดเริ่มต้นมาก

---

## 🎯 งานที่ต้องทำต่อ (Priority Order)

### Priority 1 — Hardware
- [ ] **ย้าย webcam** ขึ้นสูง 15-20cm เหนือ LiDAR, เยื้องไปด้านหน้า
- [ ] **เอียงกล้อง** ก้มลง 10-15° ให้เห็นทั้งพื้นและระดับตา
- [ ] **เดินสาย USB** อ้อมด้านหลังไม่ให้บัง LiDAR

### Priority 2 — Rotation Calibration
- [ ] ปรับ `rotation_cal` จาก 0.87 → ทดลอง 0.95-1.0
- [ ] หรือเพิ่ม odom feedback loop: หมุนจนถึง target angle จริงๆ (closed-loop rotation)

### Priority 3 — Partial Match / Approach Behavior
- [ ] ถ้า VLM เจอ "bottle" หรือ object ที่คล้าย target → approach เข้าไปดูใกล้ๆ แทนหมุนหนี
- [ ] เพิ่ม LLM logic: `confidence > 0.3 && < 0.7` → "น่าสนใจ เข้าไปดู" แทน "ไม่เจอ"

### Priority 4 — Deploy Fixes ที่แก้แล้ว
- [ ] Copy ไฟล์ 4 ตัวไป Windows Gateway:
  1. `Gateway/gateway/spatial_memory.py` (ไฟล์ใหม่)
  2. `Gateway/gateway/main.py` (skip-duplicate fix + force=True)
  3. `Gateway/gateway/obstacle_avoidance.py` (LiDAR cap)
  4. `Gateway/gateway/intent_parser.py` (query truncation)

### Priority 5 — Webapp Map
- [ ] Auto-center map ตาม robot position
- [ ] หรือ zoom out อัตโนมัติเมื่อหุ่นออกนอกขอบ

### Priority 6 — Low Priority
- [ ] Nav2 TF diagnosis
- [ ] ลบ model เก่า: `ollama rm qwen3-vl:8b`

---

## 💡 สถาปัตยกรรมปัจจุบัน

```
Memory Architecture (4 Layers):
┌─────────────────────────────────────────────┐
│ 1. ObjectMemory    (Gateway, persistent)    │ ← จำว่าเจอของที่ไหน
│ 2. SpatialMemory   (Gateway, persistent)    │ ← จำว่าเห็นอะไรที่ตำแหน่งไหน (NEW)
│ 3. ExplorationLog  (Gateway, per-search)    │ ← log การสำรวจ search นี้
│ 4. ConversationMem (Server, in-memory)      │ ← ประวัติสนทนา
└─────────────────────────────────────────────┘

Visual Search Pipeline:
    Phase 0: Check current view
        ↓ (not found)
    Phase 1: Look left 90° → right 180° → behind
        ↓ (not found)
    Phase 1.5: Diagonal ±45° gap scan
        ↓ (not found)
    Phase 2: LiDAR + LLM Navigator exploration (4 cycles)
        Each cycle: LLM picks direction → move forward → scan left/right
        ↓ (not found)
    Phase 3: Report NOT FOUND

Models (A6000 48GB):
    VLM:    qwen3-vl:32b     (20 GB)
    LLM:    gemma3:27b-it-qat (18 GB)
    Refine: gemma3:12b-it-qat (8.9 GB)
    Total:  ~38 GB / 48 GB
```

---

## 📝 บันทึก

- **VLM 32B ทำงานดีมาก** — 0 prompt echo, บรรยายฉากภาษาอังกฤษละเอียด
- **LLM Navigator ทำงาน** — override LiDAR ได้ดี เลือกทิศที่โล่งและยังไม่ได้สำรวจ
- **ปัญหาใหญ่สุดตอนนี้คือ hardware** — ตำแหน่งกล้อง + rotation calibration
- **ปัญหา logic สำคัญ** — เห็นขวดแล้วหมุนหนี เพราะไม่มี "partial match → approach" behavior
