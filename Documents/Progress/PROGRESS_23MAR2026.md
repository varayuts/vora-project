# VORA Progress — 23 March 2026

## Summary
วันนี้ fix หลายปัญหาจาก Test 7 และ Test 8 — **หุ่นหาขวดน้ำเจอสำเร็จแล้ว!** เดินเข้าหาจนถึงหน้าขวด (LiDAR 0.20m) แต่ยังมีปัญหาเรื่องชนกำแพงระหว่าง approach และ map position ผิด

## Test 7 (ขวดน้ำฝาสีน้ำเงิน) — Issues Found

### Issue 1: VLM Parrot (False Positive)
- VLM ตอบกลับแค่ "ขวดน้ำฝาสีน้ำเงิน" (17 chars) = echo ชื่อ target กลับมา
- LLM เชื่อว่าเจอ (found=True, confidence=1.0) → false positive
- Confirmation catch ได้ (VLM ครั้งที่ 2 describe จริงแล้วไม่เจอ) แต่เสียเวลา

### Issue 2: Nav2 Timeout 3s × 6 = 18s Wasted
- USE_NAV2=1 ใน .env ทำให้ทุก forward attempt ลอง Nav2 ก่อน → timeout 3s → fallback legacy
- 6 forward attempts × 3s = 18 วินาทีเสียเปล่า

### Issue 3: Trapped-When-Blocked Loop (Steps 8-12)
- Front blocked 0.15m → skip forward → LLM สั่ง forward อีก → blocked อีก → loop 5 steps
- ไม่มี VLM check, ไม่มี turn = 5 steps เสียเปล่าโดยสิ้นเชิง

### Issue 4: STUCK × 3
- Robot ส่ง cmd_vel แต่ไม่ขยับ (odom = 0.000m)
- Reverse แล้วลอง forward ใหม่ ซ้ำเดิม → stuck อีก

## Fixes Applied (Test 7 → Test 8)

### Fix 1: VLM Target Parrot Guard
- ถ้า VLM output < 100 chars และมีชื่อ target อยู่ → reject as `target_parrot`
- Auto-retry ด้วย alternate prompt (เหมือน prompt_echo)
- นับเข้า wall_empty_count เพื่อ trigger wall guard

### Fix 2: USE_NAV2=0
- เปลี่ยน `Gateway/.env` จาก `USE_NAV2=1` → `USE_NAV2=0`
- ประหยัด 3s ต่อ forward attempt (ไม่ต้องรอ Nav2 timeout)

### Fix 3: Turn-When-Blocked (แทน Skip-and-Loop)
- เมื่อ front blocked → **หมุนไป open direction ที่ดีที่สุด** แทนที่จะ skip
- Track `consecutive_blocked` — ถ้า ≥3 ครั้ง → break ออกจาก loop
- ป้องกัน dead loop (steps 8-12 จาก Test 7)

### Fix 4: Escape-After-Stuck
- หลัง STUCK detect + reverse → **หมุนไป open direction** เพื่อหนี
- รวม stuck กับ blocked ใน `consecutive_blocked` → ≥3 ครั้ง break
- ป้องกัน stuck-reverse-stuck-reverse loop

## Test 8 (หาขวดน้ำ) — SUCCESS! ✅

### Timeline
- Phase 0: เห็นกำแพงสีเทา → not found
- Step 1: Turn +45° → เจอกำแพงขาว 2 ด้าน → not found
- Step 2: Turn +75° → **เจอขวดน้ำ!** VLM describe เป็น "water bottle, clear cylindrical, blue cap" → LLM: found=True, confidence=1.0
- Confirmation: VLM ยืนยันอีกครั้ง → found=True, confidence=1.0 → **CONFIRMED 2/2**
- Approach 1: เดินหน้า 1.8s → ยังเห็นขวด (center, conf=1.0)
- Approach 2: เดินหน้า 1.8s → ยังเห็นขวด (center, conf=1.0)
- Approach 3: LiDAR 0.20m < 0.25m → **ถึงแล้ว!** หยุด

### Results
- Total VLM checks: ~5-6
- Search time: ~2 minutes (20:35:45 → 20:38:03)
- Object found and approached successfully
- TTS: "ถึงแล้วครับ ขวดน้ำ อยู่ตรงนี้"

## Remaining Issues

### 1. Robot ชนกำแพงระหว่าง Approach
- กำแพงสีขาวอยู่ทางขวาในกล้อง แต่หุ่นเดินตรงไปชนตอน approach
- Approach phase ใช้ `linear_x=0.10` ตรงไปเลย ไม่ check ข้าง
- **TODO**: ใช้ LiDAR check ข้างระหว่าง approach หรือ adjust approach angle

### 2. Map Position ผิด
- SLAM map แสดงตำแหน่งหุ่นผิดที่
- AMCL: x=-0.23, y=0.23 แต่ตำแหน่งจริงไม่ตรง
- อาจต้อง relocalize หรือ reset map

### 3. ยังไม่มี Path Drawing บน Map
- User อยากเห็นเส้นทางเดินของหุ่นบน SLAM map ใน frontend
- **TODO**: วาด trail/path จาก odom history บน canvas

## Files Changed
- `Gateway/gateway/main.py` — VLM parrot guard, turn-when-blocked, escape-after-stuck
- `Gateway/.env` — USE_NAV2=0
- `app/frontend/index.html` — VLM/LLM thinking blocks (from earlier session)

## Previous Session Changes (still in this commit)
- Removed `vlm_suspicious` filter (killed valid detections at conf=0.8)
- `_vlm_check` returns 5-tuple: (found, location, description, confidence, reason)
- Broadcast includes vlm_scene, llm_reason, llm_found, llm_confidence
- Frontend: expandable VLM/LLM thinking blocks (Claude-style)
- Target-aware VLM prompts + max_tokens=500
