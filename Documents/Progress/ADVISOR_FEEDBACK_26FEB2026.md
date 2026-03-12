# VORA — สรุปวิเคราะห์ตามคำแนะนำอาจารย์ + แนวทาง DAAAM Paper
**วันที่:** 26 กุมภาพันธ์ 2026  
**บริบท:** อาจารย์ comment หลังดู progress presentation ว่า scope กว้างเกินไป ให้เจาะจงสภาพแวดล้อมและออกแบบ experiment ให้ชัดเจน  
**Paper อ้างอิง:** *"Describe Anything, Anywhere, At Any Moment" (DAAAM)* — Gorlo, Schmid, Carlone (MIT, Nov 2025)

---

## 📄 สรุป DAAAM Paper

### แก่นของ Paper
DAAAM สร้าง **4D Scene Graph** (พิกัด 3D + เวลา) จาก RGB-D sensor stream แบบ real-time โดย:
1. **Frontend:** ติดตาม object fragments ด้วย SAM + BoT-SORT, เลือก frame ที่ดีที่สุดผ่าน optimization-based frame selection แล้ว batch inference ด้วย DAM (Describe Anything Model) เพื่อสร้าง detailed natural language descriptions
2. **Backend:** Global optimization ของ node positions, reconciliation (รวม observations ซ้ำ), region clustering (Hydra algorithm) สร้าง hierarchical memory
3. **Inference:** LLM tool-calling agent ค้นหาข้อมูลจาก 4D SG เพื่อตอบคำถาม spatio-temporal

### ผลลัพธ์เด่น
- **OC-NaVQA:** Question Accuracy 71.1% (vs ReMEmbR 46.3%), Positional Error 41.75m (vs 53.47m), Temporal Error 1.79 min (vs 2.29 min)
- **SG3D Task Grounding:** t-acc 11.22% (vs ASHiTA 8.78%)
- **Real-time:** 10 Hz บน single NVIDIA RTX 5090

### สิ่งที่ DAAAM ทำได้ vs VORA
| ด้าน | DAAAM (MIT) | VORA (เรา) |
|------|------------|-----------|
| **Sensor** | RGB-D (stereo depth + full 3D reconstruction) | RGB only (2D monocular camera) |
| **Scale** | Outdoor + Indoor large-scale (1.64 km, 35 min) | Indoor เท่านั้น (ห้อง 30-50 ตร.ม.) |
| **Memory** | 4D Scene Graph ที่ persist ตลอด session | ไม่มี persistent memory (เดิมมี object_memory.py แต่ยังไม่เชื่อมจริง) |
| **Object Detection** | SAM + BoT-SORT tracking + DAM description | VLM (Qwen3-VL:8B) single-frame description (ไม่มี tracking) |
| **Spatial Reasoning** | 3D metric positions + topological graph | 2D location codes (left/right/top/bottom) |
| **Temporal Reasoning** | Full history with timestamps per entity | ไม่มี |
| **Hardware** | RTX 5090 GPU | NVIDIA A6000 (server) + Jetson Nano (robot) |
| **Robot** | ไม่มีหุ่นจริง (dataset only) | Elephant myAGV 2023 จริง |
| **Voice** | ไม่มี | Thai STT + LLM + TTS pipeline เต็มรูปแบบ |
| **Language** | English only | Thai-first (bilingual reasoning) |

### **ข้อสังเกตสำคัญ:** 
DAAAM ไม่มีหุ่นจริง (ประเมินบน dataset) และไม่มี voice — แต่ scene understanding ลึกมาก (3D + temporal)  
VORA มีหุ่นจริง + voice pipeline — แต่ scene understanding ตื้น (2D single-frame, ไม่มี memory)  

**สิ่งที่ VORA ควรเรียนรู้จาก DAAAM:**
1. **Explicit Memory** — สร้าง memory structure ที่จำวัตถุ + ตำแหน่ง + เวลาที่เจอ
2. **Hierarchical Description** — แบ่ง scene เป็น regions (ไม่ใช่แค่ object-level)
3. **Quality-based Frame Selection** — เลือก frame ที่ดีที่สุดส่ง VLM (ไม่ใช่ส่งทุก frame)
4. **LLM Tool-calling Agent** — ให้ LLM เลือกเครื่องมือเอง (search, retrieve, navigate)

---

## 🎯 ตอบคำถามอาจารย์ทั้ง 10 ข้อ

---

### ข้อ 1: กำหนด Environment ว่าเป็นแบบไหน

#### สิ่งที่อาจารย์ต้องการ
ต้องระบุชัดเจนว่าสภาพแวดล้อมที่ทดสอบคืออะไร ไม่ใช่แค่ "ห้องแล็บ" กว้างๆ

#### แนวทางที่เสนอ — "IT Lab Controlled Environment"

```
╔═══════════════════════════════════════════════════╗
║  IT Lab Environment — ขนาด ~6m × 8m (48 ตร.ม.)   ║
╠═══════════════════════════════════════════════════╣
║                                                   ║
║   ┌──────┐   ┌──────┐      ┌──────────────────┐  ║
║   │Desk A│   │Desk B│      │  Workbench       │  ║
║   │      │   │      │      │  (เครื่องมือ)      │  ║
║   └──────┘   └──────┘      └──────────────────┘  ║
║                                                   ║
║       ⬡ Start                                     ║
║       (Robot)        ┌──────┐                     ║
║                      │Shelf │  ← วัตถุทดสอบ        ║
║   ┌──────────┐       │      │                     ║
║   │ Table C  │       └──────┘                     ║
║   │          │                                    ║
║   └──────────┘              ┌───────┐             ║
║                             │ Door  │             ║
╚═══════════════════════════════════════════════════╝
```

#### Specification ที่ต้องระบุ

| Parameter | Value | เหตุผล |
|-----------|-------|--------|
| **ขนาดห้อง** | 6×8m (48 ตร.ม.) | พอดีกับ range ของ YDLidar (~12m) |
| **พื้นผิว** | เรียบ, กระเบื้อง/ลามิเนต | Mecanum wheels ต้องการพื้นเรียบ |
| **แสงสว่าง** | Indoor fluorescent, 300-500 lux | VLM ต้องการแสงพอเห็นวัตถุ ไม่สะท้อนมาก |
| **WiFi** | 2.4/5GHz, <10ms latency ภายในห้อง | ROSBridge + Gateway สื่อสาร |
| **สิ่งกีดขวาง** | Static: furniture / Dynamic: ไม่มีคนเดินขวาง | ขั้นแรก static only |
| **จำนวนวัตถุทดสอบ** | 10 ชนิด, วางบนพื้นผิวต่างกัน | ดูรายละเอียดข้อ 3 |
| **จุดเริ่มต้นหุ่น** | กลางห้อง, หันหน้าไปทาง Door | ทิศทาง reference ที่ทำซ้ำได้ |
| **Noise level** | <50 dB (ห้องปิด) | STT ต้องการ SNR ดี |

#### เหตุผล
- **ทำไมห้องแล็บ ไม่ใช่ corridor/outdoor:** Mecanum wheels มี traction ต่ำ, LiDAR range จำกัด, WiFi ต้อง stable
- **ทำไม 48 ตร.ม.:** เพียงพอให้หุ่นต้องหมุนค้นหา + เดินเข้าหา แต่ไม่ใหญ่จนหลุด WiFi/LiDAR range
- **ทำไม static obstacles เท่านั้น:** ขั้นแรกต้องพิสูจน์ว่า scene understanding + navigation ทำงาน ค่อยเพิ่ม dynamic ทีหลัง

---

### ข้อ 2: ถ้ามี Obstacle ขวาง จะทำอย่างไร

#### สถานะปัจจุบัน
- มี `obstacle_avoidance.py` ที่ใช้ **LiDAR reactive avoidance** (YDLidar ±30° cone)
- Warning distance: 0.8m, Emergency stop: 0.3m
- 5 strategies: stop, go_around_left, go_around_right, wait, reroute
- **VLM-assisted obstacle analysis ยังไม่ได้ implement** (`_ask_vlm_strategy()` return None)
- ทุกอย่างเป็น **open-loop** (สั่งเวลาคงที่ ไม่มี feedback)

#### แนวทางที่เสนอ — 3-Layer Obstacle Handling

```
Layer 1: LiDAR Reactive (Real-time, <100ms)
├── ตรวจจับ obstacle ที่ < 0.3m → EMERGENCY STOP
├── ตรวจจับ obstacle ที่ < 0.8m → WARNING + ลดความเร็ว
└── ใช้ YDLidar scan ±30° หน้าหุ่น

Layer 2: VLM Scene Assessment (1-3s)  ← ยังไม่ implement
├── ถ่ายภาพ obstacle
├── VLM อธิบาย: "มีเก้าอี้อยู่ข้างหน้า ด้านซ้ายว่าง"
└── LLM ตัดสินใจ: go_around_left

Layer 3: Re-plan (3-5s)  ← ยังไม่ implement
├── ถ้า obstacle ใหญ่เกินอ้อม → ถอยกลับ + หาทางใหม่
└── แจ้ง user ผ่าน TTS: "มีสิ่งกีดขวาง ขออ้อมทางซ้ายครับ"
```

#### สิ่งที่รับรอง (Guarantee)
- **Safety:** Emergency stop ที่ 0.3m ทำงานแม้ VLM/LLM ล่ม (LiDAR-only, hardware-level)
- **Graceful Degradation:** ถ้า Layer 2/3 fail → fallback เป็น Layer 1 (stop + retry)
- **Measurable:** จะวัด obstacle avoidance success rate จาก log

#### สิ่งที่ต้องทำ
1. Implement `_ask_vlm_strategy()` — ส่งภาพ obstacle ให้ VLM วิเคราะห์
2. เพิ่ม TTS feedback เมื่อเจอ obstacle
3. Log ทุก obstacle event เพื่อวิเคราะห์ย้อนหลัง

---

### ข้อ 3: Conditions ของ Experiment

#### Experiment Conditions ที่เสนอ

**Independent Variables (สิ่งที่เปลี่ยน):**

| Variable | Levels | เหตุผล |
|----------|--------|--------|
| **ระยะห่างวัตถุ** | ใกล้ (<2m), กลาง (2-4m), ไกล (>4m) | ทดสอบ VLM resolution + approach phase |
| **ตำแหน่งวัตถุ** | 0° (หน้า), 90° (ซ้าย), 180° (หลัง), 270° (ขวา) | ทดสอบ Rotate-Scan-First ว่าหาเจอทุกทิศ |
| **ชนิดวัตถุ** | 10 ชนิด (ดูรายการด้านล่าง) | ทดสอบ VLM recognition accuracy |
| **สีพื้นหลัง** | สีเข้ม (โต๊ะไม้), สีอ่อน (กระดาษขาว), ลวดลาย | ทดสอบ contrast sensitivity |
| **แสงสว่าง** | ปกติ (~400 lux), สลัว (~150 lux) | ทดสอบ VLM robustness |
| **Obstacle** | ไม่มี / Static 1 ชิ้น / Static 2 ชิ้น | ทดสอบ obstacle avoidance |
| **Input Method** | Webapp (phone), Webapp (desktop), Mic บนหุ่น | ทดสอบ STT latency ต่างกัน |

**Dependent Variables (สิ่งที่วัด):**

| Metric | คำอธิบาย | วิธีวัด |
|--------|---------|--------|
| **Task Success Rate** | หาวัตถุเจอ + เข้าถึงได้ (boolean) | Log: found=true + approach_complete |
| **Search Time** | เวลาตั้งแต่สั่ง → เจอวัตถุ (seconds) | Timestamp diff in log |
| **VLM Accuracy** | VLM ระบุวัตถุถูก/ผิด per frame | Manual verification vs VLM output |
| **Position Accuracy** | ตำแหน่งที่ VLM ระบุ vs ตำแหน่งจริง | Compound location comparison |
| **STT Accuracy (WER)** | Word Error Rate | Compare transcript vs ground truth |
| **E2E Latency** | พูด → หุ่นเริ่มขยับ (seconds) | Timestamp from audio → first cmd_vel |
| **Rotation Accuracy** | มุมจริง vs มุมสั่ง (degrees) | Measure with protractor / IMU |
| **Obstacle Avoidance Rate** | หลบได้ / ชน (boolean per trial) | Visual observation + log |

**Control Variables (สิ่งที่คงที่):**

| Variable | ค่าคงที่ | เหตุผล |
|----------|----------|--------|
| Robot start position | กลางห้อง, heading 0° | Reproducibility |
| Robot battery | >60% | ป้องกัน motor performance drop |
| WiFi channel | Fixed, 5GHz | ลด latency variance |
| VLM model | Qwen3-VL:8B | ไม่เปลี่ยนระหว่าง experiment |
| LLM model | Gemma3:27b-it-qat | ไม่เปลี่ยนระหว่าง experiment |
| Angular speed | 0.50 rad/s | Calibrated |
| Linear speed | 0.10 m/s | Safe speed |
| ROTATION_CALIBRATION | 0.87 | Fine-tuned |

**รายการวัตถุทดสอบ 10 ชนิด:**

| # | วัตถุ | ขนาดโดยประมาณ | สี | ความยาก |
|---|-------|--------------|-----|---------|
| 1 | กระเป๋าสตางค์ | 12×9 cm | ดำ | ง่าย (high contrast) |
| 2 | ปากกา | 14×1.5 cm | น้ำเงิน | ปานกลาง (เล็ก) |
| 3 | ดินสอ | 19×0.7 cm | เหลือง | ปานกลาง (เล็ก+บาง) |
| 4 | กุญแจ | 7×3 cm | เงิน | ยาก (สะท้อนแสง) |
| 5 | โทรศัพท์มือถือ | 15×7 cm | ดำ | ง่าย |
| 6 | แก้วน้ำ | 8×8 cm (Ø) | ใส/ขาว | ยาก (โปร่งใส) |
| 7 | ไขควง | 20×3 cm | แดง-ดำ | ปานกลาง |
| 8 | สมุดโน้ต | 21×15 cm | ขาว | ง่าย (ใหญ่) |
| 9 | USB Flash Drive | 6×2 cm | ดำ | ยาก (เล็กมาก) |
| 10 | กรรไกร | 18×6 cm | เงิน-ดำ | ปานกลาง |

---

### ข้อ 4: ออกแบบการทดลองอย่างไร + เหตุผล

#### Experiment Design — Within-subject Repeated Measures

##### Phase A: VLM Recognition Accuracy Test (ไม่เคลื่อนที่)
**วัตถุประสงค์:** วัดความแม่นยำของ VLM ในการระบุวัตถุจากภาพกล้องหุ่น  
**วิธีการ:**
1. วางวัตถุ 10 ชนิด × 3 ตำแหน่ง (ซ้าย/กลาง/ขวา) × 2 พื้นหลัง = **60 trials**
2. หุ่นอยู่นิ่ง (ไม่เคลื่อนที่) — กล้องถ่ายภาพ → VLM วิเคราะห์
3. บันทึก: found/not-found, location, confidence, response time

**เหตุผล:** แยก VLM performance ออกจาก robot performance — ถ้า VLM ไม่ดี ไม่มี robot ไหนช่วยได้

##### Phase B: Full Visual Search Test (เคลื่อนที่)
**วัตถุประสงค์:** วัดประสิทธิภาพการค้นหาวัตถุแบบครบวงจร (voice → search → approach)  
**วิธีการ:**
1. วางวัตถุ 1 ชิ้นในห้อง ที่ตำแหน่งต่างกัน (4 ทิศ × 3 ระยะ = **12 configurations**)
2. ผู้ใช้สั่งเสียง: "หา [วัตถุ]"
3. หุ่นทำ Rotate-Scan-First → Approach → รายงานผล
4. ทำซ้ำ 3 ครั้งต่อ configuration = **36 trials per object**
5. ทดสอบ 5 วัตถุ (เลือกจากข้อ 3: ง่าย 2, กลาง 2, ยาก 1) = **180 trials total**

**เหตุผล:** ใช้ repeated measures เพื่อลด variance, ทดสอบทุกทิศทางเพื่อพิสูจน์ว่า 360° scan ทำงาน

##### Phase C: Obstacle Avoidance Test
**วัตถุประสงค์:** วัดความสามารถในการหลบสิ่งกีดขวางระหว่างค้นหา  
**วิธีการ:**
1. วางวัตถุเป้าหมายที่ตำแหน่งคงที่
2. วาง Static obstacle ระหว่างหุ่นกับวัตถุ (กล่อง/เก้าอี้)
3. สั่ง "หา [วัตถุ]" — สังเกตว่าหุ่นหลบหรือชน
4. **10 trials** (5 ตำแหน่ง obstacle × 2 ชนิด obstacle)

##### Phase D: STT + Latency Benchmark
**วัตถุประสงค์:** วัด end-to-end latency ตาม input method  
**วิธีการ:**
1. ทดสอบ 3 input methods: Phone webapp, Desktop webapp, Robot mic
2. พูดคำสั่ง 20 ประโยค (10 simple + 10 complex) × 3 input methods × 3 ซ้ำ = **180 trials**
3. วัด: STT latency, WER, intent accuracy, total response time

##### สรุป Experiment Matrix

| Phase | Trials | วัดอะไร | เวลาประมาณ |
|-------|--------|--------|-----------|
| A: VLM Static | 60 | VLM accuracy, location accuracy | 2-3 ชม. |
| B: Full Search | 180 | Task success, search time, approach | 6-8 ชม. |
| C: Obstacle | 10 | Avoidance rate, strategy choice | 1 ชม. |
| D: STT/Latency | 180 | WER, latency, intent accuracy | 3-4 ชม. |
| **Total** | **430** | | **12-16 ชม. (2-3 วัน)** |

---

### ข้อ 5: ทำอะไรที่แตกต่างจากที่มี

#### Novelty ของ VORA เทียบกับงานที่มี

| ด้าน | งานที่มีอยู่ | VORA (ต่างอย่างไร) |
|------|-------------|-------------------|
| **ภาษา** | ส่วนมาก English-only (DAAAM, ReMEmbR, SayPlan) | **Thai-first** — ระบบ STT→LLM→TTS ทั้งหมดรองรับภาษาไทย พร้อม bilingual reasoning |
| **Voice + Robot** | Voice assistants (Alexa, Google) ไม่ควบคุมหุ่น / Robot systems ไม่มี voice (DAAAM ไม่มี voice เลย) | **Thai Voice + Mobile Robot** — สั่งด้วยเสียงไทยธรรมชาติ ไม่ใช่คำสั่งตายตัว |
| **Hardware Cost** | Commercial lab robots ราคา 200,000+ บาท, DAAAM ใช้ RTX 5090 | **Low-cost:** ~40,000 บาท total (Elephant myAGV + Jetson Nano) + remote GPU |
| **Hybrid Intent** | ส่วนมากใช้ LLM อย่างเดียว (ช้า) หรือ rule-based อย่างเดียว (limited) | **Regex + LLM Hybrid** — simple commands ผ่าน regex (<100ms), complex ผ่าน LLM (2-3s) |
| **VLM+LLM 2-stage** | DAAAM ใช้ DAM สำหรับ description + LLM สำหรับ reasoning (แยก 2 stage) | เราก็ใช้ **2-stage** เหมือนกัน: VLM (Qwen3-VL) describe → LLM (Gemma3) reason — แนวทางเดียวกับ DAAAM แต่ **lightweight** กว่ามาก |
| **Deployment** | ส่วนมาก centralized GPU | **3-tier distributed:** Robot (Jetson) → Gateway (Windows) → Server (A6000) ผ่าน Tailscale VPN |
| **Scene Understanding** | DAAAM: 3D + temporal, ลึกมากแต่ไม่มีหุ่นจริง | VORA: **2D + voice + real robot** — ตื้นกว่าแต่ end-to-end functional |

#### Core Differentiation Statement
> "VORA เป็น end-to-end Thai voice-controlled robot system ที่ทำ scene understanding ด้วย VLM+LLM 2-stage pipeline บน low-cost hardware — ต่างจากงานวิจัยอื่นที่เน้น scene understanding ลึกแต่ไม่มี voice interaction และไม่ทดสอบกับหุ่นจริง"

---

### ข้อ 6: จะ Compare ผลอะไรบ้าง

#### Comparison Plan

##### A. Internal Comparison (ทำได้ทันที)

| Comparison | Condition A | Condition B | Metric |
|------------|-------------|-------------|--------|
| **Hybrid vs LLM-only Intent** | Regex + LLM | LLM only (ปิด regex) | Intent accuracy, latency |
| **VLM model comparison** | Qwen3-VL:8B | (ถ้ามีเวลา) ลอง Qwen2.5-VL:7B | Detection accuracy, speed |
| **Input method** | Phone webapp | Desktop webapp vs Robot mic | STT WER, latency |
| **With vs Without Approach** | Rotate-Scan only | Rotate-Scan + Approach | Task completion rate |
| **With vs Without Obstacle Avoidance** | Obstacle avoidance ON | Obstacle avoidance OFF (ปิด LiDAR) | Collision count, success rate |
| **Light conditions** | Normal (400 lux) | Dim (150 lux) | VLM accuracy |
| **Background contrast** | Dark surface | Light surface | VLM accuracy, false positive rate |

##### B. External Comparison (จาก literature)

| Metric | VORA | DAAAM (MIT) | SayPlan | Commercial (est.) |
|--------|------|-----------|---------|-------------------|
| **ภาษาไทย** | ✅ | ❌ | ❌ | Limited |
| **Real robot** | ✅ | ❌ (dataset) | ✅ | ✅ |
| **E2E latency** | 4-5s | N/A (offline) | 10-15s | 2-5s |
| **Hardware cost** | ~40K | GPU only | High | >200K |
| **VLM accuracy** | วัดเอง | 71.1% QA | N/A | N/A |
| **Task success** | วัดเอง | N/A | N/A | N/A |

**หมายเหตุ:** fair comparison กับ DAAAM ทำไม่ได้โดยตรง (คนละ task, คนละ scale) — แต่ compare **แนวคิด** และ **architectural approach** ได้

##### C. Ablation Study (ถ้ามีเวลา)

| Component ที่ปิด | ทดสอบอะไร |
|-----------------|---------|
| ปิด VLM (ใช้แค่ LLM จาก text description) | VLM มีประโยชน์แค่ไหน |
| ปิด LLM Reasoning (ใช้แค่ VLM keyword match) | LLM reasoning จำเป็นไหม |
| ปิด Position Hints ใน VLM prompt | Prompt engineering ช่วยไหม |
| ปิด Bilingual reasoning | ภาษาทำให้ accuracy ต่างกันไหม |

---

### ข้อ 7: กล้องควรตั้งมุมไหน + ของจริงหรือปริ้น A4

#### สถานะปัจจุบัน
- กล้อง USB mounted บนหุ่น (forward-facing, ~eye-level ของหุ่น ≈ 25-30 cm จากพื้น)
- Resolution: 640×480 YUYV → resize เป็น 480×360 สำหรับ VLM
- FOV: ~60-70° horizontal (USB webcam ทั่วไป)

#### คำตอบ: ใช้ของจริง + เหตุผล

**ของจริง ✅ (แนะนำ)**

| ข้อดี | เหตุผล |
|-------|--------|
| Realistic | สะท้อนสภาพใช้งานจริงในห้องแล็บ |
| VLM challenge ที่แท้จริง | แสง, เงา, สะท้อน, มุมมอง — ทำให้ results น่าเชื่อถือ |
| 3D characteristics | วัตถุจริงมีความลึก + เงาที่กระดาษไม่มี |
| Reproducible ได้ | ถ้าระบุ position + แสง ให้ชัดเจน |

**กระดาษ A4 ❌ (ไม่แนะนำ)**

| ข้อเสีย | เหตุผล |
|---------|--------|
| ไม่สมจริง | VLM อาจ detect กระดาษเป็น "poster" ไม่ใช่ "key" |
| ไม่มี depth cue | ภาพ 2D บนกระดาษ ≠ วัตถุ 3D ที่มีเงาและ perspective |
| Reviewer จะ reject | ใน paper จริง เขาใช้ของจริงกัน |

#### กล้อง mount position

| Option | มุม | ข้อดี | ข้อเสีย |
|--------|-----|-------|---------|
| **A: Forward horizontal (ปัจจุบัน)** | 0° tilt, ~25cm สูง | เห็นไกล, cover ทั้งห้อง | วัตถุบนพื้นเห็นยาก |
| **B: Forward + slight downward tilt** | -15° tilt | เห็นทั้งไกลและใกล้ | FOV จำกัด |
| **C: Higher mount + downward** | 45cm สูง, -30° tilt | เห็นพื้นที่ตรงหน้า ชัด | ระยะไกลมองไม่ถึง |

**แนะนำ Option B: Forward + slight downward tilt (-15°)** — compromise ระหว่างเห็นไกลและเห็นวัตถุบนโต๊ะ/พื้น

**เหตุผลที่ต้องระบุ:**
- VLM ทำงานกับ 2D image — ถ้ากล้องตั้งเอียงผิด จะมี blind spot
- มุม -15° ทำให้ at distance 2m, กล้อง cover จากพื้นถึงโต๊ะ (~75cm)
- ต้องทำ **camera FOV diagram** แสดงว่า field of view cover พื้นที่เท่าไหร่ที่แต่ละระยะ

---

### ข้อ 8: ทำไม Mic บนหุ่น latency สูงกว่า Webapp

#### Root Cause Analysis

```
Path A: Webapp (Phone/Desktop) — ⚡ Fast
Phone Mic → WebSocket → Server STT (Faster-Whisper)
Total: ~2.5s

Path B: Robot Mic — 🐌 Slow  
MyAGV USB Mic → ROS audio → Gateway proxy → WebSocket → Server STT
Total: ~5-8s
```

#### สาเหตุทีละ hop

| Hop | Latency | สาเหตุ |
|-----|---------|--------|
| **USB Mic → ROS audio_common** | +200-500ms | ROS audio publisher overhead, topic serialization |
| **ROS audio → Gateway proxy** | +100-300ms | ROSBridge WebSocket (192.168.0.111→192.168.0.60), batching |
| **Gateway proxy → Server** | +50-100ms | Tailscale VPN (Gateway → Server), re-encoding |
| **Audio format conversion** | +100-300ms | FFmpeg process spawn (ถ้า sample rate ≠ 16kHz) |
| **Total extra** | **+450-1200ms** | vs webapp ที่ส่งตรงไป server |

#### เหตุผลเชิงเทคนิค
1. **Extra network hop:** Robot→Gateway→Server = 2 hops vs Phone→Server = 1 hop
2. **ROS serialization:** audio_common package encode เป็น ROS message ก่อน → decode ที่ Gateway
3. **Buffer accumulation:** Gateway batches audio chunks (~200ms) เพื่อลด WebSocket overhead → เพิ่ม latency
4. **Sample rate mismatch:** MyAGV mic อาจ ≠ 16kHz → ต้อง resample → FFmpeg เพิ่ม latency
5. **WiFi local vs Tailscale:** Phone→Server ผ่าน Tailscale (WireGuard, low overhead), Robot→Gateway ผ่าน local WiFi (อาจ congested)

#### Data ที่ต้องเก็บเพื่อพิสูจน์
```python
# Log timestamps at each hop:
t0 = time.time()  # Audio captured at mic
t1 = time.time()  # Received at Gateway proxy
t2 = time.time()  # Sent to Server WebSocket
t3 = time.time()  # STT transcription complete
t4 = time.time()  # Response sent back

# Per-hop latency:
mic_to_gateway = t1 - t0
gateway_to_server = t2 - t1
stt_processing = t3 - t2
total = t4 - t0
```

---

### ข้อ 9: เก็บ Logs ข้อมูลหลังเทสต์

#### สถานะปัจจุบัน
- **ไม่มี file logging เลย** — ทุกอย่างแค่ print ใน console แล้วหายหมด
- มี WebSocket broadcast status ไปยัง webapp แต่ไม่ persist

#### แนวทางที่เสนอ — Structured Logging System

##### A. Server-side Logging
```python
# app/core/experiment_logger.py (NEW)
import json, os
from datetime import datetime

class ExperimentLogger:
    def __init__(self, experiment_name: str):
        self.log_dir = f"logs/{experiment_name}_{datetime.now():%Y%m%d_%H%M}"
        os.makedirs(self.log_dir, exist_ok=True)
        
    def log_stt(self, audio_duration, transcript, wer, latency_ms):
        """Log STT performance per utterance"""
        
    def log_vlm(self, frame_id, prompt, response, found, location, confidence, latency_ms):
        """Log VLM inference per frame"""
        
    def log_llm(self, scene_desc, target, result_json, latency_ms):
        """Log LLM reasoning per query"""
        
    def log_search(self, target, success, search_time, total_checks, final_location):
        """Log full search session"""
        
    def log_motion(self, command, duration, calibration):
        """Log robot movement"""
        
    def log_obstacle(self, distance, strategy, success):
        """Log obstacle events"""
```

##### B. Log Formats

**Per-trial JSON log:**
```json
{
    "trial_id": "B_wallet_front_near_001",
    "timestamp": "2026-02-26T14:30:00+07:00",
    "experiment": "Phase_B_FullSearch",
    "target": "กระเป๋าสตางค์",
    "target_en": "wallet",
    "position": {"direction": "front", "distance": "near"},
    
    "stt": {
        "input_method": "phone_webapp",
        "transcript": "หากระเป๋าสตังค์ให้หน่อย",
        "ground_truth": "หากระเป๋าสตางค์ให้หน่อย",
        "wer": 0.0,
        "latency_ms": 2100
    },
    
    "search": {
        "success": true,
        "total_time_s": 12.5,
        "scans": 2,
        "approach_steps": 3,
        "vlm_checks": [
            {"direction": 0, "found": false, "response": "...", "latency_ms": 1800},
            {"direction": 90, "found": true, "location": "center_right", "confidence": 0.85, "latency_ms": 1600}
        ]
    },
    
    "motion": {
        "total_rotations": 1,
        "total_forward_m": 1.5,
        "obstacle_events": 0
    },
    
    "result": "success"
}
```

##### C. Summary CSV (สำหรับ Excel / statistical analysis)
```csv
trial_id,target,distance,direction,input,success,search_time,vlm_accuracy,stt_wer,e2e_latency
B_wallet_front_near_001,wallet,near,front,phone,1,12.5,1.0,0.0,2.1
B_wallet_left_mid_001,wallet,mid,left,phone,1,18.3,0.67,0.05,2.3
```

##### D. สิ่งที่ต้อง Implement
1. **ExperimentLogger class** — สร้างใหม่ ใช้ได้ทั้ง server + gateway
2. **File rotation** — RotatingFileHandler (max 50MB per file)
3. **Timestamp injection** — ทุก hop เก็บ timestamp (ตอบข้อ 8 ได้ด้วย)
4. **Post-analysis script** — Python script อ่าน JSON logs → สร้าง summary table + charts

---

### ข้อ 10: การเดินของหุ่น — เดินยังไง ทำไมถึงเดินท่านี้

#### Motion Strategy ปัจจุบัน

##### A. Rotate-Scan-First (หมุนก่อน)

```
Step 1: เช็คหน้า (0°) ← ไม่ต้องหมุน
Step 2: หมุน +90° (ซ้าย) → เช็ค
Step 3: หมุน +90° (หลัง) → เช็ค
Step 4: หมุน +90° (ขวา) → เช็ค
Step 5: หมุน +90° (กลับหน้าเดิม)

= 360° scan จากจุดเดิม
```

**ทำไมถึงหมุนก่อนเดิน?**
1. **Energy efficient:** หมุนใช้พลังงาน < เดิน (Mecanum in-place rotation)
2. **Information gain:** 360° scan ให้ข้อมูลทั้งห้องก่อนตัดสินใจ
3. **Safe:** ไม่เสี่ยงชน (อยู่กับที่)
4. **DAAAM ก็ใช้แนวคิดคล้ายกัน:** scan ก่อน → build memory → แล้วค่อยเคลื่อนที่

##### B. Approach Phase (เดินเข้าหา)

```
เมื่อ VLM เจอวัตถุ:
1. หมุนหันหน้าเข้าหาวัตถุ (±30° ถ้าอยู่ซ้าย/ขวา)
2. เดินตรงเข้าไป:
   - Object at top (ไกล) → เดิน 3.0s (0.30m)
   - Object at center    → เดิน 1.8s (0.18m)
   - Object at bottom (ใกล้) → เดิน 0.8s (0.08m)
3. VLM เช็คซ้ำ
4. ถ้ายังไม่ถึง → ทำซ้ำ (สูงสุด 6 steps)
```

**ทำไมถึงเดินท่านี้?**
1. **Proportional to distance:** วัตถุอยู่ด้านบนของภาพ = ไกลจากหุ่น (perspective geometry)
2. **Conservative steps:** ก้าวเล็กๆ + VLM re-check ทุก step ป้องกันหุ่นเดินเลยไป
3. **Max 6 steps:** Safety limit ป้องกัน infinite loop ถ้า VLM hallucinate
4. **Open-loop control:** ไม่มี odometry feedback → ใช้ visual feedback แทน (VLM)

##### C. ทำไมไม่ใช้ Nav2 / SLAM Navigation?

| ปัจจัย | Nav2/SLAM | Open-loop + VLM |
|--------|-----------|----------------|
| **Complexity** | สูง (ต้อง map ก่อน + localize) | ต่ำ (ไม่ต้อง map) |
| **Setup time** | นาน (20+ min mapping) | ทันที |
| **Flexibility** | ต้อง re-map ถ้าห้องเปลี่ยน | ทำงานได้ทุกห้อง |
| **Accuracy** | สูง (cm-level) | ต่ำ (visual estimate) |
| **Resource** | ต้อง Jetson GPU สำหรับ Nav2 | Jetson ทำแค่ ROSBridge |

**เหตุผลที่เลือก Open-loop + VLM:**
- Jetson Nano 4GB ไม่มี resource รัน Nav2 + SLAM + Camera พร้อมกัน
- Visual search ไม่ต้องการ cm-level accuracy — แค่เข้าใกล้พอเห็นก็พอ
- Rotate-Scan-First เป็น "VLM-based exploration" — ไม่ต้องรู้ map ก่อน

##### D. Movement Diagram

```
                    Phase 1: 360° Scan
                    ┌─── 0° (front) ← CHECK
                    │ 
            270° ←──┼──→ 90° 
            (right) │    (left)
                    │
                    └─── 180° (back)
                    
    ไม่เจอ → Move forward 2.5s → ทำซ้ำ (Phase 2)
    เจอ → Approach Phase ▼

                    Approach Phase
                    
    Object         ┌─ Turn 30° toward object
    at right ──→  └─ Move forward (proportional)
                   └─ VLM re-check
                   └─ Repeat (max 6x)
```

---

## 📌 Action Items — สิ่งที่ต้องทำ (Priority Order)

### 🔴 Critical (ทำก่อน defense)

| # | Task | เหตุผล | ใช้เวลา |
|---|------|--------|---------|
| 1 | **Implement ExperimentLogger** | ตอบข้อ 9 + เก็บ data สำหรับทุก experiment | 1 วัน |
| 2 | **วาด Environment Diagram** | ตอบข้อ 1 ต้องมี floor plan ใน paper | 0.5 วัน |
| 3 | **ออกแบบ Experiment Protocol** | ตอบข้อ 3, 4 ต้องมี clear protocol ก่อนเริ่มเทส | 0.5 วัน |
| 4 | **เก็บ Hop-by-hop Latency** | ตอบข้อ 8 ต้องมีตัวเลขพิสูจน์ | 0.5 วัน |
| 5 | **รัน Phase A: VLM Static Test** | ง่ายที่สุด, ได้ data เร็ว, พิสูจน์ VLM accuracy | 1 วัน |

### 🟡 Important (ทำก่อน paper)

| # | Task | เหตุผล | ใช้เวลา |
|---|------|--------|---------|
| 6 | รัน Phase B: Full Search Test | Main experiment | 2-3 วัน |
| 7 | รัน Phase D: STT/Latency | ตอบข้อ 8 + data สำหรับ paper | 1 วัน |
| 8 | เขียน Comparison Table | ตอบข้อ 5, 6 | 0.5 วัน |
| 9 | Implement VLM obstacle analysis | ตอบข้อ 2 Layer 2 | 1 วัน |
| 10 | Camera mount + FOV documentation | ตอบข้อ 7 | 0.5 วัน |

### 🟢 Nice-to-have (ถ้ามีเวลา)

| # | Task | เหตุผล |
|---|------|--------|
| 11 | Ablation study | ตอบข้อ 6 ลึกขึ้น |
| 12 | Phase C: Obstacle Test | ต้อง implement Layer 2 ก่อน |
| 13 | Object Memory integration | ใช้ memory ข้อมูลเก่ามาช่วย search |
| 14 | Dim light testing | cover เงื่อนไขพิเศษ |

---

## 💡 แนวทางจาก DAAAM ที่นำมาประยุกต์ได้

### สิ่งที่ควรนำมาใช้ (Practical for VORA)

1. **Explicit Object Descriptions → Object Memory**
   - DAAAM เก็บ natural language description per object
   - VORA ควร: เก็บ VLM description + location + timestamp ลง `object_memory.py` (มีแล้ว แต่ยังไม่เชื่อม search logic)
   - **Action:** เชื่อม `object_memory.get_search_hint()` ให้มีผลจริงต่อ search order

2. **Quality-based Frame Selection**
   - DAAAM เลือก frame ที่ _ดีที่สุด_ ส่ง VLM (ไม่ส่งทุก frame)
   - VORA ควร: เช็ค frame quality (brightness, blur) ก่อนส่ง VLM
   - **Action:** เพิ่ม simple quality check (mean brightness > threshold, Laplacian variance > threshold)

3. **LLM Tool-calling Agent**
   - DAAAM ใช้ agent ที่มี tools: search objects, get regions, get agent info
   - VORA ควร: ให้ LLM เลือกเอง "ต้องหมุนไหม?", "ต้องเดินเข้าไปไหม?"
   - **Action:** เป็น future work — ปัจจุบัน hardcoded logic ยังทำงานได้

4. **Region Clustering for Room Understanding**
   - DAAAM แบ่ง scene เป็น regions ("Science Hall Entrance", "Computer Lab")
   - VORA ควร: แบ่งห้องเป็น zones (โต๊ะ A, ตู้, ประตู) — อ้างอิง priority zones ที่มีแล้วใน `object_memory.py`
   - **Action:** map VLM location → zone name

### สิ่งที่ไม่จำเป็นต้องทำ (Overkill for VORA scope)

| DAAAM Feature | ทำไมไม่ต้องทำ |
|---------------|-------------|
| 3D reconstruction | ไม่มี depth sensor, scope เป็น 2D |
| SAM segmentation | Heavy computation, Qwen3-VL ทำ scene-level ได้เลย |
| BoT-SORT tracking | Single-frame approach เพียงพอสำหรับ static objects |
| OC-NaVQA benchmark | Dataset-based evaluation ≠ real-robot evaluation |
| Batch inference pipeline | VORA ส่ง 1 frame ต่อ VLM call เพียงพอ (ไม่ต้อง batch) |

---

## 📊 สรุปเปรียบเทียบ Scope เดิม vs Scope ใหม่ (แนะนำ)

| ด้าน | Scope เดิม (กว้างเกินไป) | Scope ใหม่ (เจาะจง) |
|------|------------------------|-------------------|
| **Environment** | "ห้องแล็บ" | "IT Lab 6×8m, พื้นเรียบ, 300-500 lux, static obstacles" |
| **Objects** | "อุปกรณ์ทั่วไป" | "10 ชนิด ขนาด 6-21cm, ระบุสี + ความยากชัดเจน" |
| **Task** | "ค้นหาวัตถุ" | "Voice-commanded visual search: 4-direction scan → approach → report" |
| **Metrics** | "Accuracy, Latency" | "6 metrics: SR, Time, VLM acc, Position acc, WER, Rotation acc" |
| **Experiment** | ไม่ได้ออกแบบ | "4 phases, 430 trials, within-subject repeated measures" |
| **Comparison** | ไม่มี | "7 internal + 4 external comparison dimensions" |
| **Logging** | Console only | "Structured JSON logs + CSV summary per trial" |

---

**สรุป:** อาจารย์ถูกต้องที่บอกว่า scope กว้างเกินไป — การเจาะจง environment, conditions, และ experiment design จะทำให้ผลลัพธ์น่าเชื่อถือและ publishable ได้ DAAAM paper เป็นตัวอย่างที่ดีของการออกแบบ experiment ที่ชัดเจน (4 experiments, ablation study, multiple baselines) แม้ว่า hardware/approach จะต่างจาก VORA มาก แต่ **methodology** ในการ evaluate เป็นแนวทางที่ควรทำตาม
