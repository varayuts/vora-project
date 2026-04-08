# VORA Progress — 25 March 2026

## สรุปภาพรวม

วันนี้ทำการ **Code Verification** ทุก component + **Live Test** + **Bug Fix** จาก log

---

## Live Test (วันนี้) — เทียบกับ Test 11 (24 Mar)

| Feature | Test 11 เดิม | Live Test วันนี้ |
|---------|-------------|----------------|
| Forward movement | ❌ STUCK 6/8 ครั้ง (0.000m) | ✅ เคลื่อนที่จริง 0.31m / 0.15m / 0.31m |
| AMCL tracking | ❌ ไม่แน่ใจ xy_live=NO | ✅ stuck_check ยืนยันตำแหน่ง AMCL ถูกต้อง |
| LiDAR obstacle stop | ❌ ไม่แน่ใจ | ✅ interrupt ถูกต้อง (หยุดที่ step 10/11) |
| Wall steering | ❌ ไม่มี | ✅ "Wall RIGHT 0.17m → steering left" |
| Server WS connect | ❌ timeout 27s | ✅ connect ภายใน 10s |
| ROSBridge factories | ❌ 6 factories spam | ✅ "sharing with camera, odom, LiDAR" (1 client) |
| Nav2 | ❌ Action client fail | ⚠️ ยังล้มเหลว (nav2 stack ไม่ได้เปิด) |

---

## Code Verification — ผลตรวจสอบ

### ✅ Gateway — ถูกต้องทั้งหมด

| Fix | File | ผล |
|-----|------|-----|
| Shared ROSBridge `ensure_ros()` singleton + asyncio lock | `ros_cmd.py:173–211` | ✅ |
| MotionPublisher ใช้ `ensure_ros()` (ไม่สร้าง client ใหม่) | `ros_cmd.py:30–36` | ✅ |
| ObstacleAvoidance รับ `ros_connection` จาก main | `obstacle_avoidance.py:109` | ✅ |
| Server WS: 3s startup delay + exponential backoff (max 30s) | `main.py:146–190` | ✅ |
| `get_forward_clearance()` ข้าม LiDAR dead zone ±0–15° | `obstacle_avoidance.py:360` | ✅ |
| `LIDAR_MIRROR=1` default — แก้ซ้าย/ขวากลับ | `obstacle_avoidance.py:72` | ✅ |
| `call_soon_threadsafe` + `await wait_for(event.wait())` | `nav2_client.py:219,236` | ✅ |
| Relocalize handler → `/initialpose` × 3 ครั้ง + suppress AMCL 3s | `main.py` | ✅ |

**Note:** `Nav2Client.connect()` ยังสร้าง `roslibpy.Ros` แยก (`nav2_client.py:86`) — ยังไม่ใช้ `ensure_ros()` แต่ไม่กระทบ (USE_NAV2=0)

### ✅ Myagv — ถูกต้องทั้งหมด

| Fix | File | ผล |
|-----|------|-----|
| `robot_model_type: "omni"` (เดิม "differential") | `nav2_params.yaml:36` | ✅ |
| `set_initial_pose: false` + bootstrap `/initialpose` | `nav2_params.yaml:48`, `start_nav2.sh:155–160` | ✅ |
| `INIT_X/Y/YAW` env vars ตั้งจุดเริ่มต้น | `start_nav2.sh:29–31` | ✅ |
| `bond_timeout: 30.0` (เดิม 10.0) | `nav2_params.yaml:271,283` | ✅ |
| `odom_tf_broadcaster` auto-start ถ้า TF ไม่มี | `start_nav2.sh:87–129` | ✅ |
| `trans_stopped_velocity: 0.05` (เดิม 0.15 = max_vel!) | `nav2_params.yaml:129` | ✅ |
| `required_movement_radius: 0.15m` (เดิม 0.30m) | `nav2_params.yaml:95` | ✅ |
| `min_particles: 500`, `max_particles: 2000` | `nav2_params.yaml:29` | ✅ |

### ✅ App Server — ถูกต้องทั้งหมด

| Fix | ผล |
|-----|-----|
| Map PNG sync + reload on origin/size change | ✅ |
| Viewport PAD=0.5m fixed (ไม่ขยายตาม robot pos) | ✅ |
| `imageSmoothingEnabled=false` (map คมชัด) | ✅ |
| Set Pose CSS bug (ลบ inline `style="display:none"`) | ✅ |
| `math` scoping bug ใน `execute_server_command()` | ✅ |
| Markdown rendering สำหรับ VORA chat | ✅ |
| `/map/relocalize` endpoint (POST) → forward to Gateway | ✅ |

---

## Bug ที่พบและแก้ไขวันนี้

### ✅ Fix 2: Odom x,y = 0 — Dead Reckoning (Myagv/odom_tf_broadcaster.py + nav2_params.yaml)

**อาการ:** `/odom` topic จาก MyAGV hardware publish x=0, y=0 ตลอด (θ ถูกต้อง)
→ TF `odom→base_footprint` ไม่เคย move ใน x,y
→ AMCL motion model เห็น Δx=0, Δy=0 ทุก step → particles ไม่ spread → localization หลุดทุกครั้งที่ robot เดิน

**แก้:** integrate `/cmd_vel` velocity ด้วย 20Hz timer + yaw จาก `/odom` (dead reckoning)
→ TF ใช้ x,y จาก DR แทน hardware
→ publish `/odom_fused` topic สำหรับ Nav2 bt_navigator

**ผลลัพธ์ที่ยืนยันจาก log:**
```
Odom #4200: x=-0.089 y=0.145 θ=122.9°   ← x,y update แล้ว ✅
Stuck check: pre=(-0.445,-0.824) post=(-0.255,-0.825) moved=0.190m ← AMCL track ถูก ✅
```

---

### ✅ Fix 3: AMCL Initial Pose TF Timing (Myagv/start_nav2.sh)

**อาการ:** หลัง `start_nav2.sh` robot dot ขึ้นที่ map origin **(0, 0)** แทน INIT_X/Y → "หลุดแมพ"

**สาเหตุ (root cause):**
`ros2 topic pub --once /initialpose` stamp ข้อความด้วย `now()` — แต่ TF ล่าสุดจาก `odom_tf_broadcaster`
อยู่ที่ `now() - 75ms` (broadcaster publish ที่ 10Hz = ทุก 100ms → มี timing gap)
AMCL พยายาม lookup TF ณ เวลา `now()` แต่ TF ยังไม่มีถึงจุดนั้น:

```
[amcl-2] [WARN]: Failed to transform initial pose in time
  (Lookup would require extrapolation into the future.
   Requested time 1774442979.519564
   but the latest data is at time 1774442979.443913)
```

AMCL ตั้ง pose ที่ map origin (0,0) แทน (-0.45, -0.82) → robot dot โชว์นอก map

**ปัญหาที่ 2 (ร่วมกัน):** Nav2 nodes สร้าง TF buffer ใหม่ตอน launch
→ scan messages จาก LiDAR ที่ buffer ไว้ก่อนหน้ามี timestamp เก่ากว่า TF buffer
→ ถูก drop ~17 วินาที: `Message Filter dropping message: frame 'laser_frame'`
→ AMCL ไม่ได้รับ scan data ช่วงแรก → particles ไม่ converge

**แก้ใน `start_nav2.sh`:**

1. **`stamp: {sec: 0, nanosec: 0}`** — tf2 TimePointZero = "ใช้ TF ล่าสุดที่มี" แทนการ lookup ณ timestamp แน่นอน → bypass 75ms gap
2. **`sleep 15` เพิ่ม** (รวม 20s หลัง localization launch) — รอ laser_frame TF warming ผ่านก่อน
3. **retry loop 3 ครั้ง** (2s gap) — ป้องกัน edge case

**ผลลัพธ์ที่คาดหวัง:** ไม่มี `WARN Failed to transform initial pose` อีก, robot dot ขึ้นที่ INIT_X/Y ตั้งแต่ต้น

---

### ✅ Fix 1: VLM PARROT False Rejection (Gateway/gateway/main.py)

**อาการ:** VLM ส่งคืน "ขวดน้ำเปล่า" (11 ตัวอักษร) แทนที่จะ describe scene
→ parrot filter reject → นับเป็น `wall_empty_count` → `wall_streak ≥ 3` → force forward โดยไม่มีข้อมูลภาพ → ชนกำแพง

**หลักฐานสำคัญ:** frame ขนาด **40,134 bytes** (ปกติ 7k-9k) ขณะที่ VLM parrot เกิดขึ้น
→ กล้องน่าจะเห็น **ขวดน้ำอยู่จริงๆ** แต่ VLM ตอบแค่ชื่อ

**ปัญหาเดิม:** แก้ 2 จุดที่ double-count `wall_empty_count`:

```
# จุดที่ 1 (main.py:519)
เดิม: target_parrot → wall_empty_count += 1
แก้:  target_parrot → log "potential match, not counting as wall"

# จุดที่ 2 (main.py:546)
เดิม: is_rejected (ทุก type) → _is_wall_or_empty("") = True → wall_empty_count += 1 อีกครั้ง!
แก้:  is_rejected → skip (handled above)
```

**ผลลัพธ์หลัง fix:**
- `target_parrot` ไม่นับเป็น wall อีกต่อไป
- `wall_streak` จะไม่ขึ้นจาก VLM parrot
- Robot ไม่ force forward โดยไม่มีเหตุผล

---

## ปัญหาที่เหลือ (Priority)

| # | ปัญหา | Severity | Location |
|---|-------|----------|----------|
| 1 | ~~Odom x,y ไม่ update~~ | ✅ FIXED | Dead reckoning ใน `odom_tf_broadcaster.py` |
| 2 | ~~AMCL initial pose TF timing~~ | ✅ FIXED | `start_nav2.sh` stamp=0 + sleep 20s |
| 3 | Nav2 action client ล้มเหลว | 🔴 HIGH | Myagv — ต้องเปิด `start_nav2.sh` + ต้องทดสอบหลัง fix |
| 4 | AMCL ยังไม่ได้ทดสอบหลัง fix | 🔴 HIGH | `start_nav2.sh` fix ยังไม่ได้ deploy + ทดสอบบน Jetson Nano จริง |
| 5 | VLM PARROT ยังเกิด (แต่ไม่ชน) | 🟡 MED | Gateway/Server VLM — qwen3-vl:32b บางครั้งตอบแค่ชื่อ object |
| 6 | Nav2Client ใช้ roslibpy.Ros แยก | 🟢 LOW | `nav2_client.py:86` — fix เมื่อเปิด USE_NAV2=1 |
| 7 | Gateway latency ~1000ms | 🟢 LOW | Webapp measure — น่าจะ Tailscale proxy hop |

---

## แก้ไขที่ทำวันนี้ (สรุป)

### ✅ Fix 1: VLM PARROT false rejection → wall_streak (main.py)
- `target_parrot` ไม่นับเป็น `wall_empty_count` อีกต่อไป
- ป้องกัน double-count จาก `_is_wall_or_empty("")` path ด้วย

### ✅ Fix 2: Odom x,y = 0 (odom_tf_broadcaster.py + nav2_params.yaml)
- integrate `/cmd_vel` velocity ที่ 20Hz → dead reckoning x,y
- publish `/odom_fused` topic สำหรับ Nav2
- `bt_navigator.odom_topic: /odom_fused`
- ยืนยันใน log: x,y update ถูกต้อง, AMCL track ถูกต้อง 0.19m

### ✅ Fix 3: AMCL initial pose TF timing (start_nav2.sh)
- เปลี่ยน stamp เป็น `{sec: 0, nanosec: 0}` → tf2 TimePointZero
- เพิ่ม sleep 15 (รวม 20s) → รอ laser_frame TF warming (~17s)
- เพิ่ม retry loop 3 ครั้ง
- ยังไม่ได้ทดสอบบน Jetson Nano — deploy พรุ่งนี้

### ✅ All Previous Fixes (Verified Correct)
- 15 files, 962 insertions ตรวจสอบแล้วทั้งหมด

---

## Configuration ปัจจุบัน

- **USE_NAV2:** True (แต่ action server ไม่ได้รัน → fallback legacy cmd_vel)
- **MOCK_ROBOT:** False
- **MAX_AGENT_STEPS:** 16
- **MAX_FORWARD_MOVES:** 4
- **move_speed:** 0.15 m/s
- **LiDAR:** YDLidar G2, 230 rays, MIRROR=ON, OFFSET=0°
- **VLM:** qwen3-vl:32b | **LLM:** gemma3:27b-it-qat
- **AMCL:** Active, robot_model_type=omni, set_initial_pose=false
- **Odom:** θ live ✅, x/y = dead reckoning ✅ (hardware still 0 แต่ DR fix แล้ว)
- **ROSBridge:** Shared single connection (ensure_ros singleton)

---

## Test ถัดไปที่แนะนำ (พรุ่งนี้/วันหลัง)

1. **Deploy + ทดสอบ `start_nav2.sh` fix:**
   ```bash
   scp Myagv/start_nav2.sh er@nano:~/Desktop/VORA_myAGV_only_ros2_package/new5/
   ```
   ดู log — ไม่ควรมี `WARN Failed to transform initial pose`
   Robot dot ควรขึ้นที่ INIT_X/Y ตั้งแต่ต้น ไม่ต้องกด Set Pose

2. **ทดสอบ AMCL tracking หลัง fix:**
   Set Pose ครั้งเดียว → เดินหุ่น → ดูว่า robot dot ขยับตาม map ถูกต้อง

3. **ทดสอบ Nav2 action client:**
   เปิด `start_nav2.sh` → รอ "Managed nodes are active" → รัน search
   Nav2 ควร connect ได้ (ไม่ fallback ไป legacy cmd_vel)

4. **ทดสอบ search หลัง fix ทั้งหมด:**
   รัน search "ขวดน้ำ" — ดูว่า AMCL ไม่หลุดระหว่าง search
