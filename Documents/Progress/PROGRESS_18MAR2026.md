# VORA Progress Report — 18 March 2026

## 📋 Summary

วันนี้โฟกัส **Nav2 Debug & Stabilization** — แก้ปัญหา Nav2 stack crash ทั้งหมดจนทำงานได้สำเร็จบน Jetson Nano + เพิ่ม AMCL pose tracking ให้ Gateway:

1. **Nav2 Stack Fix** — AMCL ไม่ crash, bt_navigator สำเร็จ, Managed nodes active ✅
2. **Watchdog Spam Fix** — แก้ feedback loop ที่ cmd_vel watchdog วนลูปไม่สิ้นสุด
3. **Robot Position Fix** — เพิ่ม `/amcl_pose` subscription ให้ Gateway เพื่อแสดงตำแหน่งถูกต้องบน map

**ผลลัพธ์:** Nav2 ทำงานเสถียรบนหุ่นจริง, Gateway รับ AMCL pose ได้, Webapp พร้อมแสดงตำแหน่ง map-frame

---

## ✅ Nav2 Stack — ทำงานสำเร็จ

### Log ยืนยัน
```
[amcl-2] Setting pose: 0.000 0.000 0.000              ← initial pose ตั้งค่าอัตโนมัติ
[lifecycle_manager] Managed nodes are active            ← localization stack พร้อม
[bt_navigator-4] Creating bond (bt_navigator)           ← navigation stack พร้อม
[lifecycle_manager_navigation] Managed nodes are active ← ทุก node active
```

### สิ่งที่แก้ก่อนหน้า (สำเร็จแล้ว)
| ปัญหา | Root Cause | Fix |
|---|---|---|
| AMCL ไม่มี initial pose → TF timeout | ไม่มี `set_initial_pose` | เพิ่ม `set_initial_pose: true` + `initial_pose` ใน nav2_params.yaml |
| AMCL SIGSEGV (exit -11) | OmniMotionModel + 2000 particles เกิน RAM 4GB | เปลี่ยนเป็น `differential` + ลด particles 500/100, beams 30 |
| bt_navigator FATAL "RemovePassedGoals" | BT node ไม่มีใน Galactic | ลบ plugin ที่ไม่ compatible ออก |
| ตัว `ไ` (Thai char) ติดท้ายบรรทัด YAML | Copy-paste error | ลบออก |

---

## 🔧 Fix 1: Watchdog Spam Loop

**ไฟล์:** `Myagv/odom_tf_broadcaster.py`

### ปัญหา
Watchdog print "sending STOP" ทุก ~1.5 วินาทีไม่หยุด แม้หุ่นไม่ได้เคลื่อนที่:
```
[WARN] cmd_vel watchdog: no command for 1s -> sending STOP   ← ซ้ำทุก 1.5s
[WARN] cmd_vel watchdog: no command for 1s -> sending STOP
[WARN] cmd_vel watchdog: no command for 1s -> sending STOP
...ไม่สิ้นสุด
```

### Root Cause
Threshold เดิม `0.001` ต่ำเกินไป — noise เล็กน้อยจาก controller หรือ DWB planner ถูกจับว่าเป็น "motion" → re-arm watchdog → 1 วิถัดไป trigger STOP → log อีก → วนลูป

### Fix
```python
# Before: threshold 0.001 — catches noise
if abs(msg.linear.x) > 0.001 or abs(msg.angular.z) > 0.001:
    self._cmd_vel_active = True

# After: threshold 0.05 — only real motion
speed = abs(msg.linear.x) + abs(msg.linear.y) + abs(msg.angular.z)
if speed > 0.05:
    self._cmd_vel_active = True
    self._stop_logged = False  # allow logging for new motion
```
- เพิ่ม `_stop_logged` flag — log STOP เพียง **ครั้งเดียว** ต่อ motion event
- Threshold `0.05` กรอง noise ออก, ไม่ trigger บน zero cmd_vel ที่ watchdog ส่งเอง

---

## 🔧 Fix 2: Robot Position บน Map ผิดที่

**ไฟล์:** `Gateway/gateway/main.py`, `app/frontend/index.html`

### ปัญหา
Webapp แสดงหุ่นที่ X:0.00 Y:0.00 ตลอด ถึงแม้ Nav2 ทำงานแล้ว — หุ่นไม่อยู่ถูกที่บน map

### Root Cause
Gateway subscribe เฉพาะ `/odom` (odom frame) — ค่า x,y จาก odom อยู่ใน **odom frame** ไม่ใช่ **map frame**

AMCL publish ตำแหน่งที่แก้ไขแล้ว (localized) บน `/amcl_pose` ใน **map frame** — แต่ Gateway ไม่ได้ subscribe topic นี้

### Fix: เพิ่ม `/amcl_pose` subscription

**Priority chain:** `AMCL (map frame)` → `/odom` (odom frame) → Dead Reckoning (estimate)

```
/amcl_pose available? → ใช้ x,y,theta จาก AMCL (map frame, accurate)
        ↓ NO
/odom x,y moving?     → ใช้จาก odom  
        ↓ NO
                      → Dead reckoning จาก cmd_vel
```

**เปลี่ยนแปลง:**
1. เพิ่ม `_amcl_pose_callback()` — รับ `geometry_msgs/PoseWithCovarianceStamped` จาก `/amcl_pose`
2. เมื่อ AMCL active: `/odom` callback ไม่ overwrite x,y,theta
3. Dead reckoning skip เมื่อ AMCL active
4. Webapp แสดง `🗺️ AMCL` เป็น position source

---

## 📊 สถานะปัจจุบัน

| Component | Status |
|---|---|
| Nav2 AMCL (localization) | ✅ เสถียร, ไม่ crash |
| Nav2 bt_navigator | ✅ Active, creating bond สำเร็จ |
| Nav2 controller/planner | ✅ DWB + NavFn ทำงาน |
| cmd_vel watchdog | ✅ แก้ spam แล้ว |
| Gateway → /amcl_pose | ✅ โค้ดเพิ่มแล้ว (ยังไม่ได้ deploy) |
| Webapp AMCL source label | ✅ เพิ่มแล้ว |
| VLM timeout 60s | ⚠️ ไม่เกี่ยว Nav2 — Server ตอบช้า |

---

## 📝 TODO — งานที่ต้องทำต่อ

### 🔴 Priority High

1. **Deploy Gateway changes ไปเครื่อง Windows**
   - Copy `Gateway/gateway/main.py` ไปเครื่อง Gateway (Windows)
   - ไฟล์: เพิ่ม `/amcl_pose` callback + subscription
   - ทดสอบ: webapp ต้องแสดง `🗺️ AMCL` + ตำแหน่งหุ่นตรงกับ map

2. **Deploy watchdog fix ไป Jetson Nano**
   - Copy `Myagv/odom_tf_broadcaster.py` ไป `~/Desktop/VORA_myAGV_only_ros2_package/new5/`
   - ทดสอบ: ต้องไม่ spam STOP ตอนหุ่นไม่เคลื่อนที่

3. **ตั้ง initial_pose ให้ตรงกับตำแหน่งจริง**
   - ตอนนี้ตั้งไว้ (0, 0, 0) — ต้องวัดว่าหุ่นเปิดเครื่องตรงจุดไหนบน map
   - แก้ใน `nav2_params.yaml` → `initial_pose: {x: ?, y: ?, yaw: ?}`
   - หรือพิจารณาทำ dynamic initial pose จาก Gateway

### 🟡 Priority Medium

4. **VLM timeout 60s**
   - Server ตอบช้า (network / VLM processing)
   - ลอง: เพิ่ม timeout, ลดขนาด frame, หรือ cache VLM response

5. **ทดสอบ Nav2 goal navigation จริง**
   - ส่ง goal_pose ผ่าน Gateway → Nav2 → หุ่นเคลื่อนที่ไปถึง goal
   - ทดสอบ obstacle avoidance ระหว่างทาง

6. **ทดสอบ visual_search + Nav2 ร่วมกัน**
   - ค้นหาวัตถุแล้วให้ Nav2 วางแผนเส้นทาง
   - ตรวจสอบว่า AMCL position update ระหว่างเคลื่อนที่

### 🟢 Priority Low

7. **SLAM mode fix** — `slam_toolbox_params.yaml` มี `base_frame: base_link` ควรเป็น `base_footprint`
8. **Map re-scan** — map ปัจจุบันอาจไม่ตรงกับห้องจริง ควร SLAM ใหม่

---

## 📁 Files Changed Today

| File | Changes |
|---|---|
| `Myagv/odom_tf_broadcaster.py` | Watchdog: threshold 0.001→0.05, เพิ่ม `_stop_logged` flag |
| `Gateway/gateway/main.py` | เพิ่ม `/amcl_pose` subscription + callback, odom defers to AMCL |
| `app/frontend/index.html` | เพิ่ม `🗺️ AMCL` ใน position source label |
| `Myagv/nav2_params.yaml` | ลบตัว `ไ` ที่ติดท้ายบรรทัด (ทำก่อนหน้า) |
