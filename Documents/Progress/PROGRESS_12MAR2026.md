# VORA Progress Report — 12 March 2026

## 📋 Summary

วันนี้โฟกัส **Search Strategy Overhaul** — วิเคราะห์ปัญหาจาก live test ที่หุ่นหมุนดูกำแพงซ้ำ 11 ครั้งจาก 12 steps,
เขียน LLM Planner prompt ใหม่ทั้งหมด, เพิ่ม safety guard ไม่ให้เดินหน้าตอนกล้องตาย, และแก้ Webapp ให้โชว์ VLM description เต็ม

**ผลการทดสอบ:** Run แรก — LLM Planner เลือกทิศถูก (widest direction) แต่กล้องไม่ส่ง frame → wall_streak พุ่ง → หุ่นเดินหน้าตาบอดชนกำแพง  
**แก้ไขเพิ่ม:** เพิ่ม camera_blind guard — บล็อก forward เมื่อกล้องตาย, ไม่นับ camera-dead เป็น wall_streak

---

## 🔧 Changes Made (เทียบกับ session ก่อน)

### 1. LLM Planner Prompt — เขียนใหม่ทั้งหมด

**ปัญหาเดิม:** LLM อ่าน raw LiDAR table (❌/✅) แล้วตีความผิด เลือกมุมที่ LiDAR บอกว่าเป็นกำแพง
- Step 2 เลือก +45° (0.22m ❌) แล้วบอก "โล่งกว้าง"
- หุ่นหมุน 11 ครั้ง เดินหน้า 1 ครั้ง ใน 12 steps — ติดหมุนอยู่กับที่

**แก้ไข (Gateway/gateway/main.py — `_llm_plan_action`):**
- **ลบ raw LiDAR table** ออกจาก prompt — ต้นเหตุหลักที่ LLM เลือกมุมผิด
- **Pre-filter ข้อมูล** ก่อนส่ง LLM: แสดงเฉพาะ "UNCHECKED OPEN directions" ที่ผ่าน LiDAR เรียงจากกว้างสุด
- **Prompt เป็น English** — Gemma3 เข้าใจ structured format ดีกว่า Thai
- เพิ่ม **"Best forward direction"** เพื่อให้ LLM รู้ว่าเดินหน้าไปทางไหนดีสุด
- เพิ่ม **wall_streak warning** + **TURNS HERE counter**
- **Decision Rules** ชัดเจน 5 ข้อ เรียงตาม priority

### 2. Post-validate LLM Turn Angle

**ปัญหา:** LLM อาจเลือกมุมที่ไม่ตรงกับทิศที่โล่งจริง

**แก้ไข:** หลัง parse JSON จาก LLM → ตรวจว่ามุม turn อยู่ใกล้ทิศ open (±30°)
- ถ้าไม่ตรง → **แก้อัตโนมัติ** ไปทิศ unchecked ที่กว้างสุด
- ถ้าไม่มี unchecked เหลือ → **แปลงเป็น forward** แทน
- ป้องกัน LLM เลือกทิศที่ LiDAR บอกว่าตัน

### 3. Force Forward หลังหมุน 4 ครั้ง

**ปัญหา:** หุ่นหมุนหลายรอบโดยไม่เคยเดินหน้า (spinning in place)

**แก้ไข:** เพิ่ม `turns_at_position` counter
- นับ turn ที่ตำแหน่งเดิม, reset เป็น 0 เมื่อ forward
- ถ้าหมุน **≥ 4 ครั้ง** → **บังคับ forward** ไปทิศที่โล่งสุด (ไม่ถาม LLM)
- หุ่นจะเริ่มสำรวจพื้นที่ใหม่แทนหมุนซ้ำ

### 4. เพิ่มระยะเดินหน้า 25cm → 40cm

**เดิม:** `move_duration = 2.5s` at 0.10 m/s = 25cm → แทบไม่เปลี่ยนมุมมอง  
**ใหม่:** `move_duration = 4.0s` at 0.10 m/s = **40cm** → มุมมองเปลี่ยนเยอะขึ้นมาก  
ยังมี LiDAR obstacle check ระหว่างเดิน — ปลอดภัย

### 5. 🚨 Camera Blind Safety Guard (Critical Fix)

**ปัญหา (พบจาก live test วันนี้):**
- กล้องไม่ส่ง frame ("No local camera frame" ×8 ซ้อน)
- `_is_wall_or_empty("")` return True → wall_empty_count พุ่งเป็น 5+
- Prompt บอก LLM: "WALL STREAK 5 → FORWARD" → หุ่นเดินหน้าตาบอด → ชนกำแพง

**แก้ไข:**
1. **ไม่นับ camera-dead เป็น wall**: `is_empty = True` → skip wall_empty_count++ (เฉพาะ VLM ที่เห็นกำแพงจริงๆ เท่านั้น)
2. **Block forward เมื่อ camera_blind**: `stale_count >= 2` → บล็อกทั้ง force-forward และ LLM forward
3. **ไม่ส่ง wall_streak ให้ LLM** เมื่อกล้องตาย → ส่ง 0 แทน ไม่ให้ LLM เข้าใจผิด
4. **หุ่นจะหมุนแทน** เมื่อ forward ถูกบล็อก → หวังว่ากล้องจะกลับมาทำงาน

### 6. Webapp VLM Description — ไม่ตัดอีกต่อไป

**ปัญหา:** VLM description ถูกตัดที่ 100-150 ตัวอักษร แสดง "..." ดูไม่ครบ

**แก้ไข:**
- **CSS**: `max-height: 4.5em` + `overflow: hidden` → ย่อแบบสวย
- **Click to expand**: `.expanded` class toggle → กดเพื่อดูเต็ม
- ลบ JS truncation (`substring(0, 120)`) → ส่งข้อความเต็มจาก gateway
- Broadcast description limit เพิ่ม 200 → **500 chars**
- Exploration log เพิ่ม 200 → **500 chars** (ข้อมูลดีขึ้นสำหรับ scene memory)

### 7. Heuristic Fallback — ฉลาดขึ้น

**เดิม:** เลือก unchecked → ถ้าไม่มี → forward  
**ใหม่:** ถ้า `wall_streak >= 3` → **force forward เลย** ไม่หมุนเพิ่ม (escape dead area)

---

## 📊 Live Test Results

### Run 1 (ก่อนแก้ camera blind guard)

```
19:03:52 | VISUAL SEARCH START: 'ขวดน้ำ'
19:03:55 | LiDAR: 3 passable directions (-105°, -75°, -45°)
19:03:57 | Plan: turn -75° — "Widest unchecked direction" ✅ ถูกต้อง!
19:04:00 | ⚠️ No local camera frame (×2)
19:04:03 | Plan: forward +45° — "Wall streak high" ❌ กล้องตาย แต่เดิน
19:04:11 | ⚠️ No local camera frame (×3)
19:04:14 | Plan: forward -75° — "Wall streak high" ❌ เดินซ้ำตาบอด
... (รวม 4 forward ตาบอด)
19:04:56 | 🛑 Aborting: 8 consecutive NO DATA → ชนกำแพง/ทะลุแมพ
```

**วิเคราะห์:**
- ✅ LLM Planner เลือกทิศถูก (widest: -75° = 0.50m✅ หรือ -45° = 0.87m✅)
- ❌ กล้องไม่ทำงาน → wall_streak พุ่ง → forward ตาบอด × 4
- ✅ Duration 4.0s ทำงาน (40 messages at 10Hz)

**Root Cause:** Camera stream จาก MyAGV ไม่ส่ง frame มาที่ Gateway (ROSBridge connected แต่ไม่ได้ frame)

---

## 📁 Files Modified

| File | Changes |
|------|---------|
| `Gateway/gateway/main.py` | LLM planner prompt rewrite, post-validate angle, force-forward, camera_blind guard, move_duration 2.5→4.0, broadcast 200→500 chars |
| `app/frontend/index.html` | VLM description expandable (CSS + click-to-expand), ลบ truncation |

---

## 🚀 Next Steps

1. **แก้กล้อง** — ตรวจสอบ ROSBridge camera topic ว่าทำไมไม่ส่ง frame มา
2. **ทดสอบใหม่** — หลังกล้องทำงาน เพื่อดูว่า LLM Planner + safety guard ทำงานร่วมกัน
3. **ลอง Qwen3:14b** — `ollama pull qwen3:14b` เพื่อลด LLM latency จาก ~8s เหลือ ~3-4s
4. **ปรับ move_duration** — อาจต้องลดเหลือ 3.5s ถ้า 40cm เกินไป (ดูจาก LiDAR clearance)

---

## 🏗️ Architecture Summary (Current)

```
MyAGV (Jetson Nano) ──ROSBridge──> Gateway (Win11) ──HTTPS──> Server (A6000)
  - /image_raw                     - LLM Planner           - Ollama VLM/LLM
  - /scan (LiDAR)                  - Motion control         - qwen3-vl:32b
  - /odom                          - Obstacle avoidance     - gemma3:27b-it-qat
  - /cmd_vel                       - Object/Spatial memory  - TTS Thai
```

**Search Flow (Updated):**
1. Phase 0: Check current frame
2. Agent Loop (max 12 steps, 4 moves):
   - LiDAR scan → pre-filter open directions
   - LLM sees: unchecked/checked dirs + scene memory + wall streak
   - Post-validate: ensure turn angle matches open direction
   - Force forward if 4 turns without moving
   - **Block forward if camera dead** ← NEW
3. VLM: 1 call per step → LLM reasoning per target
