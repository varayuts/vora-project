# VORA Progress — 24 March 2026

## สรุปผล Test 11 (ค้นหา "ขวดน้ำ")

### ผลลัพธ์: ไม่พบ — 12 steps, 6 VLM checks, forward ทุกครั้ง STUCK

---

## ปัญหาที่พบ (4 ข้อจากการทดสอบ)

### 1. Nav2 หลุดตั้งแต่ action แรก
**อาการ:** ทุกครั้งที่พยายาม forward → Nav2 ล้มเหลว → fallback เป็น legacy cmd_vel
```
Nav2 client connection failed: Action client failed to connect, no status received.
```
**สาเหตุ:** Nav2 action server ไม่ทำงานบน MyAGV — ต้องเปิด Nav2 stack ก่อน:
```bash
# บน MyAGV SSH:
ros2 launch nav2_bringup navigation_launch.py params_file:=nav2_params.yaml
```
หรือถ้ายังไม่ได้ config Nav2 ให้ set `USE_NAV2=0` ใน `.env` เพื่อใช้ legacy cmd_vel โดยตรง

**ปัญหาที่เกิดจาก fallback:** Legacy cmd_vel ส่งคำสั่ง forward (linear_x=0.15) แต่หุ่นยนต์ **ไม่ขยับจริง** ทุกครั้ง:
```
🚨 STUCK DETECTED: moved 0.000m but expected ≥0.091m. Auto-reversing...
```
**6 ครั้งเต็ม** ที่สั่ง forward → stuck ทุกครั้ง → ถอยกลับ → หมุนหนี = วนอยู่กับที่

**สาเหตุ STUCK ที่เป็นไปได้:**
- Motor ไม่ตอบสนอง linear_x (หมุนได้ = angular_z ทำงาน แต่เดินหน้าไม่ได้)
- Odom ไม่อัพเดตเร็วพอหลัง forward (sleep 0.3s อาจไม่พอ)
- Physical obstruction / ล้อไม่กริป / พื้นลื่น
- cmd_vel topic ถูกต้องแต่ linear_x ไม่ work กับ Mecanum wheel config

**ข้อสังเกต:** Odom θ เปลี่ยนตาม rotation ทุกครั้ง (38.7° → 79.5° → 158.4°) แต่ x,y แทบไม่เปลี่ยนระหว่าง forward → บ่งชี้ว่าหุ่นไม่ได้เคลื่อนที่จริง

### 2. Set Pose ใช้งานไม่ได้ — 2 ปัญหา

**ปัญหา A: ปุ่มกดไม่ได้ (แก้แล้ว ✅)**
- สาเหตุ: `style="display:none"` inline บน `<div id="map-expanded">` มี CSS specificity สูงกว่า `.map-expanded.show{display:flex}`
- แก้ไข: ลบ `style="display:none"` ออก — CSS class `.map-expanded{display:none}` จัดการให้

**ปัญหา B: `math` variable scoping bug (แก้แล้ว ✅)**
```
❌ Command execution error: cannot access local variable 'math' where it is not associated with a value
```
- สาเหตุ: ฟังก์ชัน `execute_server_command()` มี `import math` ใน `elif cmd == "rotate"` block (L2626)
- Python compiler เห็น `import math` ในฟังก์ชัน → ถือว่า `math` เป็น local variable ของทั้งฟังก์ชัน
- เมื่อเข้า path `relocalize` (ไม่ผ่าน `rotate`) → `math` ยัง unbound → error
- แก้ไข: ลบ `import math` ทั้ง 3 จุดที่ซ้ำซ้อน — ใช้ top-level `import math` ที่ L1

**ปัญหา C: Pose ย้ายแล้ว 1 วิ กลับที่เดิม**
- สาเหตุ: AMCL ยัง publish `/amcl_pose` ตำแหน่งเดิมซ้ำ → Gateway เอา AMCL pose มาทับ manual pose
- AMCL ยังไม่ converge ไปที่ใหม่ หรือ particle cloud ยังไม่แก้ไข
- แก้ได้โดย: publish `/initialpose` หลายครั้ง + รอ AMCL update (ตอนนี้ publish ครั้งเดียว)

**ปัญหา D: Reset Origin ไปจุดแปลกๆ**
- `resetPoseOrigin()` ส่ง x=0, y=0, θ=0 → ถ้า map origin ไม่ตรงกับตำแหน่งจริงของหุ่นก็จะไปผิดที่
- Map origin ขึ้นอยู่กับ SLAM — ไม่จำเป็นว่า (0,0) = ตำแหน่งจริงปัจจุบัน

**ปัญหา E: แมพยืด (Map Distortion)**
- อาจเกิดจาก: viewport calculation ใน canvas render ผิดเมื่อหุ่นออกนอก boundary
- หรือ SLAM map data มี resolution ไม่สม่ำเสมอ
- ต้องตรวจสอบ `getMapViewport()` ใน index.html

### 3. จบ Step 12 พอดีตรงจุดที่เห็นขวดน้ำ
- Robot หมุนวนอยู่กับที่ 12 steps (stuck ทุก forward)
- พอ step สุดท้าย force forward → หมุน -45° → ลงเอยหันหน้าเข้าขวดน้ำพอดี
- ถ้าเพิ่ม MAX_AGENT_STEPS จะหาเจอได้ในรอบถัดไป

### 4. เพิ่ม Agent Steps / ให้คิดมากขึ้น
- ปัจจุบัน `MAX_AGENT_STEPS = 12` — สามารถเพิ่มเป็น 16-20 ได้
- LLM `temperature = 0.3` + `max_tokens = 128` — เพิ่ม max_tokens เป็น 256 ให้ reason ละเอียดขึ้น
- แต่ปัญหาหลักไม่ใช่จำนวน steps — คือ **forward ไม่ทำงาน** → stuck ทุกครั้ง → เสีย steps ไปกับ stuck/reverse loop

---

## แก้ไขที่ทำวันนี้

### ✅ Fix 1: Set Pose ปุ่มไม่ทำงาน (index.html)
- ลบ `style="display:none"` จาก `<div id="map-expanded">`
- CSS class `.map-expanded{display:none}` + `.map-expanded.show{display:flex}` ทำงานถูกต้องแล้ว

### ✅ Fix 2: Relocalize `math` scoping bug (main.py)
- ลบ `import math` ที่ซ้ำซ้อน 3 จุดในฟังก์ชัน:
  - L340 `visual_search()` — ลบ
  - L2155 `_search_found()` — ลบ
  - L2626 `execute_server_command()` → `elif cmd == "rotate"` — ลบ (ตัวนี้ทำให้ relocalize พัง)
- ใช้ top-level `import math` (L1) ที่มีอยู่แล้ว

### ✅ Fix 3: Camera Corridor Shortcut (main.py)
- เพิ่มการตรวจจับ corridor/hallway/doorway จาก VLM description ล่าสุด
- ถ้าเห็น path → เลือก "forward" ทันทีโดยไม่ถาม LLM
- **Safety gates 3 ชั้น:**
  1. Negative keyword check ("no path", "dead end", "blocked" ฯลฯ)
  2. LiDAR blocked check (±30°, < 0.30m)
  3. Forward clearance check (`get_forward_clearance()` ≥ 0.30m)
- ถ้า clearance ไม่พอ → skip shortcut → ถาม LLM ตามปกติ

### ✅ Fix 4: LLM Prompt Navigation (main.py)
- ปรับ Rule 1 จาก "prefer" เป็น "MUST":
  > HIGHEST PRIORITY: If the LATEST camera observation describes an open corridor, hallway, doorway, path → you MUST choose 'forward' with angle=0.
  > DO NOT turn to check unchecked directions when you already see a navigable path ahead.

### ✅ Fix 5: Markdown Rendering (index.html)
- เพิ่ม `renderMarkdown()` function สำหรับ chat messages (role='vora')
- รองรับ: bold, italic, code blocks, lists, blockquotes, headings, horizontal rules

---

## สิ่งที่ต้องแก้ต่อ (Priority Order)

### 🔴 P0: Forward Movement ไม่ทำงาน (CRITICAL)
- หุ่นหมุนได้ (angular_z ✅) แต่เดินหน้าไม่ได้ (linear_x ❌)
- STUCK DETECTED ทุกครั้ง → ทำให้ search ไม่มีประสิทธิภาพ
- **ต้องตรวจ:**
  - Motor driver / firmware ของ MyAGV
  - `cmd_vel` topic format สำหรับ Mecanum wheel
  - Physical obstruction / ล้อ / พื้น
  - Odom update lag (sleep 0.3s อาจไม่พอ)

### 🟡 P1: Nav2 ไม่เชื่อมต่อ
- Action client ล้มเหลวทุกครั้ง
- ต้องเปิด Nav2 stack บน MyAGV หรือ set `USE_NAV2=0`

### 🟡 P2: AMCL Relocalize ไม่ stick
- Publish `/initialpose` ครั้งเดียวไม่พอ — AMCL ทับค่ากลับทันที
- อาจต้อง publish หลายครั้ง หรือ reinitialize AMCL particles

### 🟢 P3: เพิ่ม MAX_AGENT_STEPS
- เพิ่มจาก 12 → 16-20 เพื่อให้สำรวจได้ทั่วถึงขึ้น
- เพิ่ม max_tokens LLM 128 → 256

### 🟢 P4: Map Distortion
- ตรวจ viewport calculation ใน canvas rendering

---

## Test 11 Timeline สรุป

| Step | Action | Angle | ผลลัพธ์ |
|------|--------|-------|---------|
| Phase 0 | VLM check | 0° | Wall — ไม่มีวัตถุ |
| 1 | turn | +45° | Wall — ไม่มี path |
| 2 | forward | +45° | Nav2 fail → legacy → **STUCK** → reverse → escape +45° |
| 3 | forward | +45° | Nav2 fail → legacy → **STUCK** → reverse → escape +45° |
| 4 | forward | -105° | Nav2 fail → legacy → **STUCK** → reverse → escape -105° |
| 5 | forward | -45° | Nav2 fail → legacy → **STUCK** → reverse → escape -45° |
| 6 | forward | -75° | Nav2 fail → legacy → **STUCK** → reverse → escape -75° |
| 7 | forward | +75° | Nav2 fail → legacy → VLM: ไม่พบขวดน้ำ |
| 8 | forward | +45° | Nav2 fail → legacy → **STUCK** → reverse → escape +45° |
| 9 | turn | +45° | VLM: ไม่พบขวดน้ำ |
| 10 | turn | -45° | VLM: ไม่พบ |
| 11 | turn | -45° | VLM: กล่องกระดาษ, ผนังขาว |
| 12 | forward (force) | -45° | **STUCK** → reverse → search จบ |

**สถิติ:** 6/8 forward attempts → STUCK (75%), VLM checks = 6, forward_move_count = 2 (only 2 counted as successful despite 8 attempts)

---

## Configuration ปัจจุบัน
- **USE_NAV2:** True (แต่ Nav2 ไม่ทำงาน → fallback legacy ทุกครั้ง)
- **MOCK_ROBOT:** False
- **MAX_AGENT_STEPS:** 12
- **MAX_FORWARD_MOVES:** 4
- **move_speed:** 0.15 m/s
- **LiDAR:** YDLidar G2, 230 rays, MIRROR=ON
- **VLM:** qwen3-vl:32b | **LLM:** gemma3:27b-it-qat
- **AMCL:** Active (pose tracking = DR, xy_live=NO)
