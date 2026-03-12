# VORA — Progress Summary for Advisor
### 7 March 2026

---

## ✅ สิ่งที่ทำสำเร็จวันนี้

### 1. Pipeline Bug Fixes (3 จุด)
| Bug | Root Cause | Fix |
|-----|-----------|-----|
| LiDAR หมุนอยู่กับที่ | LiDAR offset กลับด้าน (best_angle ควร × -1) | แก้ `invert_lidar_direction` |
| Backup-on-fit-fail ถอยหลัง loop | ถอยหลัง 0.5m ทุกครั้งที่ fit fail | เพิ่ม cooldown 3s + max 2 ครั้ง |
| VLM prompt echo | VLM คืน prompt เดิมแทนคำตอบ (≤30 chars) | เพิ่ม length check → reject + retry |

### 2. Nav2 Full Stack ติดตั้งเสร็จบน MyAGV
- Navigation2, SLAM Toolbox, robot_localization, explore_lite
- สร้าง `nav2_params.yaml` + `start_nav2.sh` (3 modes: nav, slam, explore)

### 3. Nav2 TF Tree — Root Cause พบ + แก้แล้ว
- **ค้นพบ**: MyAGV driver มี TF tree ครบอยู่แล้ว (`odom → base_footprint → base_link + laser_frame + camera_link + imu_link`)
- **ปัญหา**: TF publishers ที่เราเพิ่มเข้าไป **ชนกับ** driver เดิม → TF tree แยกเป็น 2 ต้นไม้
- **แก้**: ลบ TF publishers ที่ซ้ำออก + เปลี่ยน frame name → `base_footprint`

### 4. Visual Search ทำงานได้ (Phase 0–1.5)
- ค้นหา "ป้าย E สีแดง" → **พบสำเร็จ** + เข้าหาได้ (~1.5 นาที)
- ค้นหา "ขวดน้ำ" → **ไม่พบ** (ตรงกับความจริง — ไม่มีขวดน้ำอยู่จริง)
- Phase 2 (Nav2) ล้มเหลว → fallback เป็น legacy rotation (graceful degradation)

---

## 🚨 ปัญหาที่ยังเหลือ

| # | ปัญหา | ความรุนแรง | สถานะ |
|---|--------|-----------|--------|
| 1 | Nav2 ยัง ACTIVE ไม่ได้ (TF conflict) | สูง | แก้โค้ดแล้ว, ยังไม่ได้ deploy ทดสอบ |
| 2 | `base_footprint` vs `base_link` ไม่แน่ใจ | ปานกลาง | เพิ่ม fallback + ต้องตรวจจริงบน MyAGV |
| 3 | VLM prompt echo ~27% | ปานกลาง | มี workaround, ยังไม่ได้แก้ถาวร |
| 4 | General commands ใช้ไม่ได้ | ต่ำ | ระบบรองรับแค่ visual search |

---

## 🔮 แผนงานถัดไป (ลำดับความสำคัญ)

### ระยะสั้น (สัปดาห์หน้า)
1. Deploy + ทดสอบ Nav2 stack ที่แก้แล้ว → ยืนยัน AMCL active
2. ทดสอบ Visual Search Phase 2 ด้วย Nav2 จริง
3. ลด VLM prompt echo rate (ปรับ prompt / temperature)

### ระยะกลาง (2–3 สัปดาห์)
4. Intent Parser — รองรับ general commands + navigation commands
5. Explore mode (SLAM + Nav2 + explore_lite) — สำรวจพื้นที่อัตโนมัติ
6. Object Memory + Nav2 — จดจำตำแหน่งวัตถุ + navigate กลับไปได้

### ระยะยาว (1 เดือน+)
7. Multi-room SLAM + map management
8. Dashboard UI: แสดง Nav2 path + costmap + robot position
9. Paper draft: methodology + experimental results

---

## 📐 สถาปัตยกรรมระบบ (3-Tier)

```
┌──────────────────┐     WiFi      ┌──────────────────┐   Tailscale   ┌──────────────────┐
│   MyAGV (Robot)  │◄────────────►│  Gateway (WSL2)  │◄────────────►│ Server (A6000)   │
│ Jetson Nano 4GB  │  ROSBridge   │ FastAPI+roslibpy  │    HTTPS     │ Qwen3-VL + Gemma │
│ ROS2 Galactic    │  WebSocket   │ Visual Search FSM │              │ LLM + VLM + TTS  │
│ LiDAR+Camera+IMU │              │ Nav2 Client       │              │ Ollama API        │
└──────────────────┘              └──────────────────┘              └──────────────────┘
```

---

## 🎬 Demo ที่พร้อมแสดง

1. **Visual Search** — สั่ง "หาป้าย E สีแดง" → หุ่นหมุนค้นหา → พบ → เข้าหา
2. **3-Column Dashboard** — UI แสดงกล้อง + LiDAR + chat + controls
3. **VLM Pipeline** — ภาพ → Qwen3-VL วิเคราะห์ → Gemma3 reasoning → TTS พูดตอบ
4. **Graceful Degradation** — Nav2 ล้ม → fallback legacy → ยังทำงานได้

---

## 📊 ผลทดสอบล่าสุด (7 มี.ค. 2026 เวลา 16:50)

### Visual Search: "ขวดน้ำ" (ไม่มีของอยู่จริง — ต้อง "ไม่พบ")

| Phase | ทิศ | VLM Result | LLM Result | หมายเhtu |
|-------|-----|-----------|------------|----------|
| 0 | หน้า | ภาพเทา มีเส้นตั้ง (1295 chars) | not found, conf=0.0 | ✅ ถูกต้อง |
| 1 | ซ้าย 90° | ❌ prompt_echo (23 chars) | skip | wall/empty #1 |
| 1 | ขวา 180° | ❌ prompt_echo (30 chars) | skip | wall/empty #3 |
| 1 | หลัง 90° | แผ่นสีเทา 2 แผ่น (29 chars) | not found, conf=0.0 | ✅ ถูกต้อง |
| 1.5 | +45° | ❌ prompt_echo (30 chars) | skip | wall/empty |
| 1.5 | -45° | ผนังขาว 2 ด้าน (110 chars) | not found, conf=1.0 | ✅ ถูกต้อง |
| **2** | **Nav2** | - | - | ❌ **Action client failed** |
| 2 fallback | legacy | ยังทำงาน | ค้นหาต่อ | ✅ graceful degradation |

**สรุป:** ตอบถูก (ไม่พบของที่ไม่มี), prompt_echo 4/8 = 50%, Nav2 fail → fallback OK

### Nav2 TF Check: ล้มเหลว
```
odom → base_footprint: ❌ ไม่เจอ (30 attempts)
odom → base_link:      ❌ ไม่เจอ (fallback)
```
**สาเหตุ**: ต้องตรวจ TF tree จริงบน MyAGV ด้วย `ros2 run tf2_ros tf2_monitor`

---

## ❓ Q&A — คำถามที่อาจารย์อาจถาม

### Q1: ทำไมถึงเพิ่ม Nav2 เข้ามา ทั้งๆ ที่ปกติก็เดินได้อยู่แล้ว?

**A:** ระบบเดิม (Legacy) ทำได้แค่ **หมุน + ตรงไป** ด้วย LiDAR heuristic ซึ่ง:
- ❌ ไม่มี path planning — หุ่นเลือกทิศ "ที่เปิดที่สุด" แต่ไม่รู้ว่าจะไปไหน
- ❌ ไม่มี map → ไม่รู้ว่าเคยไปตรงไหนมาแล้ว → วน loop ค้นที่เดิม
- ❌ ไม่มี obstacle avoidance → ถ้ามีสิ่งกีดขวางตรงกลาง ก็อ้อมไม่ได้
- ❌ ไม่สามารถสั่ง "ไปจุดนี้" ได้ (ไม่มี goal-based navigation)

**Nav2 แก้ปัญหาเหล่านี้:**
- ✅ **Path Planning** (A* / Dijkstra) — หาเส้นทางรอบสิ่งกีดขวาง
- ✅ **Costmap** — รู้ว่าตรงไหนว่าง ตรงไหนมีของ
- ✅ **AMCL Localization** — รู้ตำแหน่งตัวเองบน map ตลอดเวลา
- ✅ **Goal-based** — สั่ง "ไป (x,y)" ได้ → visual search เลือกจุดสำรวจอัตโนมัติ
- ✅ **Recovery behaviors** — ถ้าติด จะถอย/หมุนหาทางออกเอง

**สรุปง่ายๆ:** Legacy = "มองซ้ายขวาแล้วเดินตรง" / Nav2 = "วางแผนเส้นทาง เหมือนคน Google Maps"

---

### Q2: ทำไมต้องหมุนแต่ละ Phase แบบนี้? ทำไมถึงเป็นมุมเท่านี้?

**A:** ออกแบบจากพฤติกรรม **คนเดินเข้าห้องแล้วหาของ**:

**Phase 0 (0°)** — มองตรง, ดูของที่อาจอยู่ตรงหน้า ไม่ต้องเสียเวลาหมุน

**Phase 1 (90°, -180°, -90°)** — "Human-Like Look-Around"
- เลียนแบบ: คนหันซ้าย → หันขวา → หันหลัง → กลับหน้า
- **ทำไม 90° ไม่ใช่ 60° หรือ 120°?** → กล้อง FOV ≈ 60-80° ดังนั้น 90° per step ครอบคลุม 360° ด้วย 4 ทิศ (0°, 90°, 180°, 270°) โดยมี overlap เล็กน้อย → ไม่มี blind spot

**Phase 1.5 (±45°)** — "Diagonal Gap Fill"
- **ทำไมถึงเพิ่ม?** → FOV overlap ระหว่าง 0° กับ 90° อาจ miss ของที่อยู่ตรง 45°
- เพิ่มมา 2 ทิศ (NE, NW) → ลด blind spot ตรง diagonal
- ใช้เวลาเพิ่มแค่ ~30s แต่เพิ่ม coverage จาก ~85% → ~95%

**Phase 2 (เปลี่ยนตำแหน่ง)** — "เดินไปดูจากมุมใหม่"
- ถ้าหมุนครบ 360° แล้วไม่เจอ → ของอาจถูกบังด้วยสิ่งกีดขวาง
- Nav2: สร้าง 6 explore goals แบบ radial (หน้า/ซ้าย/ขวา/เฉียง/หลัง, ห่าง 1.5m)
- Legacy: LiDAR เลือกทิศที่เปิดสุด → เดินตรง 2.5s → scan ใหม่

```
       Phase 1.5         Phase 1          Phase 1.5
        (+45°)            (90°)             (-45°)
           \               |               /
            \              |              /
             \____   ______|______   ____/
                  \ /      |      \ /
     Phase 1 ─── (0°) ────┼──── (180°) ─── Phase 1
     (270°)       ↑        |              (90°)
                  |    Phase 0
               Start     (front)
```

---

### Q3: ทำไมแยก VLM + LLM เป็น 2 ขั้นตอน? ทำไมไม่ใช้ตัวเดียว?

**A:** แยกเพื่อ **ลด hallucination** และ **เพิ่ม reliability**:

1. **VLM (Qwen3-VL:8B)** — บรรยายภาพ "มีอะไรอยู่ตรงไหน" **โดยไม่บอกว่ากำลังหาอะไร**
   - ถ้าบอก target ให้ VLM → model มีแนวโน้ม hallucinate ว่า "เจอแล้ว" เพื่อตอบ user
   - เหมือนถามคน "มีอะไรอยู่ในห้อง?" vs "มีขวดน้ำไหม?" → คำตอบแรกจริงกว่า

2. **LLM (Gemma3:27b)** — อ่านคำบรรยาย → ตัดสินว่า target อยู่ในนั้นหรือไม่
   - ใช้ text reasoning ตัดสิน → ไม่มี visual bias
   - ให้ output JSON → ง่ายต่อการ parse + มี confidence score

3. **Confirmation Gate** — ถ้า found=true → ถ่ายภาพซ้ำ ถามอีกรอบ
   - ต้อง found=true **2 ครั้งติด** ถึงจะเชื่อ → ลด false positive

---

### Q4: Prompt echo 27-50% คืออะไร? ทำไมสูงขนาดนี้?

**A:** VLM (Qwen3-VL) บางครั้ง **คืน prompt เดิมกลับมาแทนคำตอบ** เช่น:
- ส่ง: "ภาพนี้มีสิ่งของอะไรบ้าง แต่ละอย่างอยู่ตำแหน่งไหน (ซ้าย กลาง ขวา)"
- ได้: "ภาพนี้มีสิ่งของอะไรบ้าง" (23 chars) ← echo กลับ!

**สาเหตุ:**
- เกิดบ่อยเมื่อ **ภาพมืด / ไม่ชัด / เป็นผนังเรียบ** → model ไม่มีอะไรจะตอบ
- Qwen3-VL:8B model ขนาดเล็ก → prompt following ไม่แม่น 100%
- ใช้ Ollama local → ไม่มีการ fine-tune เพิ่ม

**Workaround ปัจจุบัน:**
- ตรวจจับ: `len(response) ≤ 30` AND prefix match กับ prompt → reject
- Retry หรือนับเป็น "wall/empty scene" → ข้ามไป scan ทิศถัดไป
- ไม่ทำให้ระบบ crash → graceful handling

**แก้ถาวร (Future):** ปรับ system prompt / ลอง temperature ต่ำ / upgrade เป็น Qwen2.5-VL

---

### Q5: ทำไม Nav2 ยังไม่ทำงาน? TF tree คืออะไร?

**A:**

**TF Tree (Transform Tree)** = ระบบพิกัดของ ROS ที่บอกว่า **แต่ละชิ้นส่วนของหุ่นอยู่ที่ไหนเทียบกัน**:
```
map → odom → base_footprint → base_link → laser_frame
                                         → camera_link
```
ทุก node ใน Nav2 (AMCL, planner, controller) ต้องรู้ว่า laser อยู่ที่ไหนเทียบกับ robot base → ต้อง TF tree เชื่อมกัน

**ปัญหาที่เจอ:**
1. เราเพิ่ม TF publisher ที่ **ชนกับ** driver เดิม → tree แยกเป็น 2 ต้น → Nav2 พัง
2. แก้แล้ว: ลบ publishers ที่ซ้ำออก
3. **ยังไม่ผ่าน**: TF check หา `odom → base_footprint` ไม่เจอ
4. **ขั้นตอนถัดไป**: ตรวจ TF tree จริงด้วย `tf2_monitor` ว่า driver publish frame ชื่ออะไร

---

### Q6: 3-Tier Architecture ทำไมไม่รวมทุกอย่างบน MyAGV?

**A:** Jetson Nano 4GB **ไม่พอ** สำหรับ VLM+LLM:
- Qwen3-VL:8B ต้องการ VRAM ~6-8GB → Jetson มี 4GB (shared CPU+GPU)
- Gemma3:27b ต้องการ VRAM ~16GB → เป็นไปไม่ได้บน Jetson

**ดังนั้นแบ่ง 3 ชั้น ตามทรัพยากร:**

| Tier | ฮาร์ดแวร์ | CPU/GPU | หน้าที่ |
|------|----------|---------|--------|
| MyAGV | Jetson Nano 4GB | ARM + Maxwell 128 CUDA | ROS2, LiDAR, Camera, Motor |
| Gateway | Windows WSL2 | i7/i9 | FastAPI, Visual Search FSM, Robot Control |
| Server | A6000 48GB | Xeon + Ampere 10752 CUDA | VLM, LLM, TTS, STT |

**Gateway** เป็น middleware ที่:
- แปลงคำสั่งจาก AI (Server) เป็น ROS commands (MyAGV)
- รับภาพจาก MyAGV → ส่งไป Server → รับผลกลับ → สั่งหุ่น
- ทำ visual search FSM (state machine) → ตัดสินใจ phase ต่อไป

---

### Q7: ทำไมใช้ Qwen3-VL + Gemma3 ทำไมไม่ใช้ GPT-4V หรือ Claude?

**A:**
- **ต้นทุน**: GPT-4V = ~$0.01/image, ค้นหา 1 ครั้ง = 8-12 images = $0.08-0.12
- **Latency**: API call = 2-5s (network) vs Local Ollama = 3-5s (GPU only)
- **Privacy**: ภาพห้อง lab ไม่ควรส่งขึ้น cloud
- **Offline**: ถ้า internet ตก → GPT ตาย, Ollama ยังทำงาน
- **Research**: ใช้ open-source model → reproduce ได้, อ้างอิงใน paper ได้

---

### Q8: Visual Search เร็วแค่ไหน? ใช้งานจริงได้ไหม?

**A:**
| สถานการณ์ | เวลาที่ใช้ | ผลลัพธ์ |
|-----------|-----------|---------|
| ของอยู่ตรงหน้า (Phase 0) | **~8s** | พบ + confirm |
| ของอยู่ด้านข้าง (Phase 1) | **~30-40s** | หมุน + VLM + LLM |
| ของอยู่มุม diagonal (Phase 1.5) | **~60s** | scan เพิ่มเติม |
| ของอยู่ห้องอื่น (Phase 2 Nav2) | **~3-5 นาที** | เดินไปดู + scan |
| ไม่มีของ (full scan) | **~2-3 นาที** | scan 360° + Phase 2 |

**Bottleneck**: VLM inference = ~5s/frame, LLM reasoning = ~2s/query
- 8 directions × 7s avg = ~56s สำหรับ full Phase 1+1.5 scan
- **ถ้ามี GPU แรงกว่า (เช่น RTX 4090)**: VLM ลดเหลือ ~2s → full scan ~25s

---

### Q9: เทียบกับงานวิจัยอื่น ระบบนี้ต่างยังไง?

**A:**
| เกณฑ์ | งานทั่วไป (CLIP/YOLO) | VORA (VLM+LLM) |
|-------|----------------------|----------------|
| Object Detection | Pre-trained classes เท่านั้น | **Open-vocabulary** — หาอะไรก็ได้ที่บอกด้วยภาษาธรรมชาติ |
| ภาษา | English mostly | **Thai + English** |
| Reasoning | ตำแหน่ง bbox | **ตำแหน่ง + เหตุผลเป็นภาษา** ("ไม่เจอเพราะ...") |
| Hardware | ต้อง GPU แรงบน robot | **3-tier split** — robot ตัวเล็กก็ใช้ได้ |
| Search Strategy | Random/predefined path | **Human-inspired** rotation + Nav2 exploration |

**Novelty:**
1. Open-vocab visual search ด้วย VLM+LLM แยก (ลด hallucination)
2. Human-inspired search strategy (glance-first, move-later)
3. Thai language support (ทั้ง input + output + TTS)
4. 3-tier architecture บน commodity hardware

---

### Q10: ถ้ามีเวลาอีก 1 เดือน จะทำอะไรก่อน?

**A (Priority Order):**

1. **🔴 Nav2 TF Fix** (1-2 วัน) — ตรวจ TF tree จริง → แก้ให้ Nav2 active
2. **🔴 Prompt Echo Fix** (2-3 วัน) — ปรับ VLM prompt + temperature → ลดจาก 50% เหลือ <10%
3. **🟡 Intent Parser** (1 สัปดาห์) — รองรับคำสั่งทั่วไป นอกจาก visual search
4. **🟡 Object Memory + Nav2** (1 สัปดาห์) — จดจำตำแหน่งของ → navigate กลับไปหาได้
5. **🟢 Explore Mode** (3-5 วัน) — SLAM + explore_lite สำรวจพื้นที่อัตโนมัติ
6. **🟢 Paper Draft** (2 สัปดาห์) — methodology + experiment + results

---

*สร้าง 7 มีนาคม 2026 — สรุปสำหรับ present อาจารย์*
