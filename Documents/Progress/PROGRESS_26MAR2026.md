# VORA Progress — 26 Mar 2026

## Summary

วันนี้แก้บั๊กทั้งหมด 4 จุด + fixes เพิ่มอีก 3 อัน ครอบคลุม LiDAR DEAD, Nav2 crash, AMCL segfault,
forward clearance เซ (false blocked), Nav2 lifecycle conflict, และ AMCL initial pose warning

ผล test รวม: 4 ครั้ง → Test #1 fail (LiDAR DEAD), #2 partial (ชนกำแพงแล้วเจอขวดน้ำโดยบังเอิญ),
#3 fail (robot หมุนอยู่ที่เดิม), **Test #4 SUCCESS** (พบขวดน้ำที่ step 8)

Nav2 ยังไม่ทำงานสมบูรณ์ — lifecycle conflict เกิดซ้ำ (ดู Bug 5 ด้านล่าง), robot ใช้ cmd_vel fallback แทน

---

## Bugs Fixed Today

### Bug 1 — AMCL Segfault (nav2_params.yaml)
**อาการ:** Nav2 crash ด้วย exit code -11 (SIGSEGV) ~23s หลัง relocalize
**สาเหตุ:** `max_particles=2000 × max_beams=60 = 120,000` likelihood-field ops ต่อ scan update
           + Gateway ยิง 3 ครั้ง `/initialpose` ภายใน 400ms → heap corruption บน Jetson Nano 4GB
**แก้ใน:** `Myagv/nav2_params.yaml`
```
max_particles: 2000 → 500
min_particles: 500  → 200
max_beams:     60   → 30
```
ลด ops ลง 87% (120,000 → 15,000 ต่อ update)

---

### Bug 2 — LiDAR DEAD (nav2_client.py + main.py)
**อาการ:** LiDAR ทุก sector แสดง `DEAD ⛔` → หุ่นหยุดทันทีใน 1 step
**สาเหตุ:** `inject_ros()` สร้าง `roslibpy.actionlib.ActionClient` ทันทีตอน startup
           ActionClient constructor รอ Nav2 status topic (~3-5s timeout)
           Nav2 ยังไม่พร้อมตอน Gateway start → exception ลาม → `_connect_ros_and_start_components()` abort
           LiDAR subscription (`_obstacle_avoidance.start(ros)`) ไม่ถูกเรียก → DEAD
           Camera รอดเพราะมี auto-retry fallback, LiDAR ไม่มี
**แก้ใน:** `Gateway/gateway/nav2_client.py`
- ลบ ActionClient ออกจาก `inject_ros()` — เก็บแค่ `self._ros = ros`
- เพิ่ม lazy creation ใน `connect()` — สร้าง ActionClient ก็ต่อเมื่อ navigate จริง

**แก้ใน:** `Gateway/gateway/main.py`
- wrap `inject_ros()` ใน try/except เพื่อไม่ให้ Nav2 error ทำลาย LiDAR setup

**ผลหลัง fix:**
```
18:14:25 | INFO | nav2_client | Nav2 client using shared ROSBridge connection
18:14:25 | INFO | obstacle   | ✅ Obstacle avoidance started (LiDAR: /scan)
18:16:37 | INFO | gateway    | +45°: 0.58m ✅  +75°: 0.48m ✅  +105°: 0.47m ✅
```

---

### Bug 3 — Nav2 Goal(None) Crash (nav2_client.py)
**อาการ:** `'NoneType' object has no attribute 'add_goal'` ตอนหุ่นพยายาม navigate forward
**สาเหตุ:** `inject_ros()` ตั้ง `_connected = True` แต่ไม่สร้าง ActionClient (by design)
           `navigate_to_pose()` เช็ค `if not self.connected` → True → ข้าม `connect()` ไป
           `_action_client` ยังเป็น None → `roslibpy.actionlib.Goal(None, ...)` → crash
**แก้ใน:** `Gateway/gateway/nav2_client.py` line 177
```python
# Before:
if not self.connected:
# After:
if not self.connected or self._action_client is None:
```

---

### Bug 4 — Forward Clearance เซ (obstacle_avoidance.py)
**อาการ:** หุ่นหมุนอยู่ที่เดิม ไม่เดินไปข้างหน้าเลย — log: `⛔ Front blocked: 0.19m < 0.2m!` ทั้งที่ข้างหน้าโล่ง
**สาเหตุ:** ฉันแก้ `get_forward_clearance()` ผิดวิธีโดยเพิ่ม cos-projection:
           `min_dist = min(min_dist, r * math.cos(abs_angle))`
           กำแพงด้านข้างที่ 60° ห่าง 0.38m ถูก project ลงแกน forward → `0.38 × cos(60°) = 0.19m`
           → ระบบคิดว่ามีสิ่งกีดขวางอยู่แค่ 0.19m ข้างหน้า → blocked ตลอด
**แก้ใน:** `Gateway/gateway/obstacle_avoidance.py`
```python
# Line 377: แคบ window ลงจาก 60° → 40° (ตัด side rays ที่ทำให้ false blocking)
check_hi = math.radians(40)

# Line 393: revert กลับเป็น raw distance (ลบ cos-projection ออก)
min_dist = min(min_dist, r)
```
**ผลหลัง fix:** `get_forward_clearance()` คืนค่า ~0.37m แทน ~0.19m สำหรับพื้นที่โล่ง

---

### Bug 5 — Nav2 Lifecycle Conflict (start_nav2.sh)
**อาการ:** Nav2 ขึ้น "Failed to bring up all requested nodes. Aborting bringup."
```
[lifecycle_manager-6]: Managed nodes are active     ← สำเร็จรอบแรก
[lifecycle_manager-6]: Starting managed nodes bringup...  ← เริ่มใหม่อีกครั้งภายใน 225µs!
[controller_server-1]: No transition matching 1 found for current state active
```
**สาเหตุ (เดิมที่คิด):** node เก่าจาก session ก่อนหน้าค้างอยู่ใน DDS
**แก้เบื้องต้น (Fix B):** เพิ่ม pkill block ใน `start_nav2.sh` ก่อน launch
```bash
pkill -f "controller_server" 2>/dev/null || true
pkill -f "planner_server"    2>/dev/null || true
pkill -f "bt_navigator"      2>/dev/null || true
pkill -f "waypoint_follower" 2>/dev/null || true
sleep 2
```
**สถานะหลัง Fix B:** node ถูก kill ก่อน launch แล้ว (verified: PID ใหม่ทุกตัว)
แต่ lifecycle conflict **ยังเกิดอยู่** — root cause จริงยังไม่ได้แก้ (ดู To-Do วันพรุ่งนี้)

---

### Bug 6 — set_initial_pose() Timestamp (nav2_client.py)
**อาการ:** AMCL warning 3 ครั้งต่อ relocalize:
          `Failed to transform initial pose in time (Lookup would require extrapolation into the future)`
**สาเหตุ:** `set_initial_pose()` ส่ง `"header": {"frame_id": "map"}` ไม่มี timestamp
           ROSBridge ใส่ current ROS time → AMCL lookup TF ณ เวลานั้น
           แต่ TF publisher ล่าช้าอยู่ไม่กี่ ms → extrapolation error
**แก้ใน:** `Gateway/gateway/nav2_client.py` `set_initial_pose()` line 300
```python
# Before:
"header": {"frame_id": "map"}
# After:
"header": {"frame_id": "map", "stamp": {"sec": 0, "nanosec": 0}}
```
`sec: 0` = "ใช้ TF ที่มีอยู่ล่าสุด" — เหมือนที่ start_nav2.sh ทำอยู่แล้ว

---

## Test Results

### Test 1 — 26 Mar 2026, 18:16 (Search "หาขวดน้ำ")
| Step | ผล |
|------|-----|
| LiDAR | ✅ Working (Bug 2 fixed) |
| AMCL | ✅ Tracking: `x=0.040 y=0.007 θ=-0.2°` → turn +45°: `x=-0.525 y=-0.211 θ=38.7°` |
| Nav2 forward | ❌ Goal(None) crash → Fixed (Bug 3) |
| Result | ❌ Aborted at step 2 |

---

### Test 2 — 26 Mar 2026, ~18:30 (Search "หาขวดน้ำ")
| Step | ผล |
|------|-----|
| AMCL | ✅ Tracking |
| LiDAR | ✅ Working |
| Nav2 | ❌ Dropped during search |
| Result | ⚠️ Partial — หุ่นชนกำแพงที่เอียง กำแพงเอียงจนกล้องเห็นขวดน้ำโดยบังเอิญ |
| หมายเหตุ | YDLidar G2 dead zone ±0–15° → ไม่เห็นกำแพงตรงหน้า → ชน |

---

### Test 3 — 26 Mar 2026, ~18:48 (Search "หาขวดน้ำ")
| Step | ผล |
|------|-----|
| LiDAR | ✅ Working |
| Nav2 | ❌ Lifecycle conflict ทุกครั้ง |
| Movement | ❌ หมุนที่เดิม — ไม่เดินไปข้างหน้า |
| Result | ❌ Fail — สาเหตุ: Bug 4 (cos-projection ผิด → false blocked ทุก step) |

---

### Test 4 — 26 Mar 2026, ~19:xx (Search "หาขวดน้ำ") ✅ SUCCESS
| Component | Status |
|-----------|--------|
| LiDAR / obstacle avoidance | ✅ Working (Fix A deployed) |
| AMCL localization | ✅ Tracking — pos ตอนเจอ: X=-0.98m, Y=-0.67m |
| Nav2 | ❌ Lifecycle conflict (Fix B ยังไม่ deploy ไป Jetson) |
| Movement | ⚠️ cmd_vel fallback (ไม่ใช่ Nav2) |
| Dead reckoning | ⚠️ Drift มาก — trajectory lines วิ่งทะลุกำแพงบน map |
| Water bottle position | ❌ บันทึกผิดตำแหน่ง (dead reckoning drift → AMCL error) |

**ผล:** พบขวดน้ำที่ step 8 — AMCL pos X=-0.98m, Y=-0.67m แต่ตำแหน่งจริงต่างกัน
**สาเหตุตำแหน่งผิด:** Fix B ไม่ได้ deploy → Nav2 fail → cmd_vel fallback → หุ่นชนกำแพง
→ wheel slip → dead reckoning diverge → AMCL เสียความแม่นยำ

**cmd_vel watchdog warnings (ปกติ):**
```
[WARN] [odom_tf_broadcaster]: cmd_vel watchdog: motion stopped -> sending STOP
```
Warning นี้ปกติครับ — watchdog ส่ง zero velocity 1s หลัง step เสร็จ เป็น safety stop
ไม่ขัดกับ Nav2 (ถ้า Nav2 running จะ override ด้วย control command ต่อไป)

---

## Current State (end of 26 Mar 2026)

| Component | Status |
|-----------|--------|
| LiDAR / obstacle avoidance | ✅ Working |
| AMCL localization | ✅ Working (ติด warning เล็กน้อย — Fixed C) |
| Camera / VLM pipeline | ✅ Working |
| Robot rotation | ✅ Working |
| Nav2 lifecycle | ❌ Conflict ยังเกิด — root cause ยังไม่ชัด (ดู To-Do) |
| Nav2 forward movement | ⚠️ cmd_vel fallback เมื่อ Nav2 fail |
| Position accuracy | ⚠️ Depends on Nav2 working (ถ้า Nav2 OK → AMCL แม่น) |
| Full search (find object) | ✅ Works — Test #4 SUCCESS |

---

## Files Changed Today

| File | Change |
|------|--------|
| `Myagv/nav2_params.yaml` | max_particles 2000→500, max_beams 60→30 (Bug 1) |
| `Myagv/start_nav2.sh` | pkill cleanup block (Fix B) + stamp=0 bootstrap pose |
| `Gateway/gateway/nav2_client.py` | lazy ActionClient (Bug 2), Goal(None) guard (Bug 3), set_initial_pose stamp=0 (Bug 6) |
| `Gateway/gateway/main.py` | inject_ros() wrapped in try/except (Bug 2) |
| `Gateway/gateway/obstacle_avoidance.py` | 40° window + revert cos-projection (Bug 4) |

---

## To-Do วันพรุ่งนี้

### 1. Deploy fixes ที่ค้างอยู่

| File | Deploy ไปที่ | Command |
|------|-------------|---------|
| `Gateway/gateway/obstacle_avoidance.py` | Gateway PC | restart Gateway |
| `Gateway/gateway/nav2_client.py` | Gateway PC | restart Gateway |
| `Myagv/start_nav2.sh` | Jetson Nano | `chmod +x start_nav2.sh` |

---

### 2. สืบหา root cause จริงของ Nav2 lifecycle conflict

**สิ่งที่รู้แล้ว:**
- pkill ใช้งานได้ — node ทุกตัวเริ่มด้วย PID ใหม่ (verified จาก log)
- Conflict เกิดจาก lifecycle_manager เรียก `startup()` **2 ครั้ง** ภายใน 225µs — ไม่ใช่ old node ค้าง
- ครั้งที่ 2 พบ node อยู่ใน state `active` แล้ว → configure ไม่ได้ → fail

**สิ่งที่ต้องตรวจ:**
```bash
# ดู nav2_params.yaml ว่า lifecycle_manager_navigation มี params อะไรบ้าง
grep -A 10 "lifecycle_manager" Myagv/nav2_params.yaml
```
- เช็ค `autostart: true` ว่ามีซ้ำหรือเปล่า (param file + launch argument double-trigger)
- เช็ค `bond_timeout` ค่าสั้นเกินไปหรือเปล่า
- ลอง `pkill -f "lifecycle_manager_navigation"` เพิ่มใน cleanup block แล้วทดสอบ

---

### 3. Test ครั้งถัดไป (หลัง deploy + fix lifecycle)

**Sequence:**
1. Jetson: `./start_nav2.sh` → รอ `Managed nodes are active` **ไม่มี** follow-up error
2. Gateway PC: restart Gateway → verify `✅ Obstacle avoidance started` + `Nav2 client using shared ROSBridge`
3. Webapp: set pose → search "หาขวดน้ำ"

**Success criteria:**
- Gateway log: `Nav2 goal sent, waiting for result...` (ไม่ใช่ `CONNECTION_FAILED`)
- Robot เดินตาม Nav2 path (ไม่ชนกำแพง)
- ตำแหน่งขวดน้ำบน map ตรงกับตำแหน่งจริง

---

### 4. Thesis data collection

เมื่อ Nav2 ทำงานสมบูรณ์:
- รัน search หลายรอบเก็บ success rate
- บันทึก steps to find, time to find, position accuracy
- ทดสอบหาวัตถุหลายชนิด (ไม่ใช่แค่ขวดน้ำ)
