# VORA Progress Report — 7 March 2026

## 📋 Summary

วันนี้โฟกัส 2 งานหลัก:
1. **Pipeline Debug & Fix** — วิเคราะห์ debug logs จริงจากการทดสอบค้นหาวัตถุ, พบ root causes 3 ข้อ, แก้โค้ดแล้ว
2. **Nav2 Stack Installation** — ลง Navigation2, SLAM Toolbox, robot_localization, explore_lite บน MyAGV สำเร็จ

**ผลลัพธ์:** แก้ bug หุ่นหมุนอยู่กับที่ 3 จุด, Nav2 full stack พร้อมใช้บนหุ่น, วางแผน integration 5 เฟส

---

## 🐛 Bug Analysis — Robot Spin-in-Place

### Debug Logs Analyzed
- **Log 1:** Target "ป้ายตัวอักษร E สีแดง" — 15 VLM checks, not found
- **Log 2:** Target "ขวดน้ำ" — 14-18 VLM checks, not found

### Observed Behavior
หุ่นไม่เคย `linear_x > 0` สำเร็จสักครั้ง ติดอยู่ใน loop:
```
LiDAR เลือก +165° → หมุนไป → can_robot_fit fail (0.107m)
  → หมุน -330° กลับ → MAP BLOCK → หมุน 90°
  → LiDAR เลือก +165° อีก → วนซ้ำ...
```

### Motion Sequence (จาก log จริง)
```
+90° / -180° / -90° / +45° / +165° / -330° / +90° / +165° / +90°
```
ไม่มี forward motion เลย — หมุนอยู่กับที่ตลอด 3 นาที

---

## 🔧 Fixes Applied (3 Changes)

### Fix 1: LiDAR 180° Offset Inversion ⭐ CRITICAL
| | Before | After |
|---|---|---|
| **File** | `Gateway/gateway/obstacle_avoidance.py` line ~59 | same |
| **Value** | `LIDAR_ANGLE_OFFSET_RAD = math.pi` (180° hardcoded) | `LIDAR_ANGLE_OFFSET_RAD = 0°` (configurable) |
| **Config** | ไม่มี | `LIDAR_OFFSET_DEG` env var |

**Root Cause:**
YDLidar G2 บน MyAGV sensor 0° ชี้ไปข้างหน้า (ปกติ) แต่โค้ดเดิมบวก 180° ทำให้:
- ด้านหน้าจริง (โล่ง) → ถูกรายงานเป็น +165° (ด้านหลัง)
- ด้านหลังจริง (กำแพง) → ถูกรายงานเป็น +15° (ด้านหน้า)

| LiDAR Sector | Distance | เดิม (ผิด) | แก้แล้ว (ถูก) |
|---|---|---|---|
| +15° (front) | 0.22m ❌ | คิดว่าหน้าตัน | จริงๆ = หลังตัน |
| +165° (rear) | ∞ ✅ | คิดว่าหลังโล่ง | จริงๆ = หน้าโล่ง |

หุ่นเลยหมุนไปหา +165° ทุกครั้ง (คิดว่าโล่ง) → หมุนกลับไปชนกำแพง

### Fix 2: Backup on Fit Fail (แทน -330° Spin)
| | Before | After |
|---|---|---|
| **File** | `Gateway/gateway/main.py` line ~574 | same |
| **Logic** | rotate to alt_angle - turn_angle (-330°) | back up 1.5s → re-check → if fail, rotate 90° |

**Root Cause:**
เมื่อ `can_robot_fit` ล้มเหลว (clearance=0.107m < 0.21m), โค้ดเดิมหมุนไปทิศที่ 2:
`alt_angle - turn_angle = -165° - 165° = -330°` ≈ หมุนเกือบครบรอบ กลับมาที่เดิม

### Fix 3: Prompt Echo Length Check
| | Before | After |
|---|---|---|
| **File** | `Gateway/gateway/main.py` line ~838 | same |
| **Logic** | reject ถ้าขึ้นต้นด้วย prompt fragment (ทุกความยาว) | reject เฉพาะ output < 100 chars |

**Root Cause:**
VLM output 909 chars ถูก strip English CoT แล้วเหลือ Thai ที่ขึ้นต้นด้วย "แต่ละอย่างอยู่ตำแหน่งไหน"
→ match prompt_fragment → ถูก reject ทั้งที่มีเนื้อหาจริงข้างหลัง

---

## 📦 Nav2 Stack Installed on MyAGV

### Packages Installed
| Package | Source | Purpose |
|---|---|---|
| nav2_bringup | ~/myagv_ros2/install/ | Nav2 launch files + configs |
| nav2_costmap_2d | /opt/ros/galactic/ | LiDAR → costmap |
| nav2_bt_navigator | /opt/ros/galactic/ | Behavior tree navigator |
| nav2_planner | /opt/ros/galactic/ | Path planning (A*, Smac) |
| nav2_controller | /opt/ros/galactic/ | DWB/MPPI controller |
| nav2_recoveries | /opt/ros/galactic/ | Spin, backup, wait behaviors |
| nav2_lifecycle_manager | /opt/ros/galactic/ | Node lifecycle management |
| nav2_map_server | /opt/ros/galactic/ | Static map serving |
| nav2_amcl | /opt/ros/galactic/ | Localization (AMCL) |
| slam_toolbox | /opt/ros/galactic/ | Online SLAM (replaces gmapping) |
| robot_localization | ~/myagv_ros2/install/ | EKF sensor fusion |
| explore_lite | ~/colcon_ws/install/ | Frontier exploration |

### Source Order (ต้อง source ตามลำดับ)
```bash
source /opt/ros/galactic/setup.bash
source ~/myagv_ros2/install/setup.bash
source ~/colcon_ws/install/setup.bash   # explore_lite
```

### Note
- explore_lite pinned to commit d75bb07 (July 2022) — last galactic-compatible
- vora_robot_bridge ยังไม่ได้ build via colcon (source package only)

---

## 📐 Target Architecture (Nav2 Integration)

```
┌─────────────────────────────────────────────┐
│                  Gateway                     │
│                                             │
│  VLM Check → found/not found               │
│       ↓ not found                           │
│  explore_lite → pick frontier point          │
│       ↓                                     │
│  Nav2 navigate_to_pose → path plan + avoid   │
│       ↓ arrived                             │
│  VLM Check again → loop                     │
│                                             │
│  (Phase 0 + Phase 1 look-around = KEEP)     │
│  (Phase 2 movement logic = REPLACE by Nav2) │
└─────────────────────────────────────────────┘
```

### Benefits vs Current System
| Current (custom) | Nav2 Stack |
|---|---|
| 12-sector LiDAR heuristic | Full costmap + global planner |
| Dead reckoning → drift | AMCL + EKF → accurate pose |
| Wall guard + MAP BLOCK | Recovery behaviors (spin, backup, wait) |
| Forward bias → stuck in corners | A*/Dijkstra → optimal path |
| No exploration memory | explore_lite → frontier-based coverage |

---

## 📝 Integration Plan (5 Phases)

### Phase A: Nav2 Standalone Test
- [ ] Create nav2_params.yaml for MyAGV (robot_radius=0.15, inflation=0.25)
- [ ] Create launch file with correct topic remappings
- [ ] Test navigate_to_pose with manual goal
- [ ] Verify costmap sees LiDAR obstacles
- [ ] Verify AMCL on maps/new5_map5.yaml

### Phase B: SLAM Toolbox Online
- [ ] Switch gmapping → slam_toolbox online mode
- [ ] Verify real-time map update
- [ ] Test save_map.sh compatibility

### Phase C: explore_lite Integration
- [ ] Launch explore_lite + Nav2
- [ ] Robot explores unseen areas autonomously
- [ ] Tune min_frontier_size for room scale

### Phase D: Gateway Code Changes
- [ ] Gateway sends navigate_to_pose via ROSBridge action
- [ ] Replace Phase 2 loop with explore → navigate → VLM check
- [ ] Keep Phase 0 + Phase 1 (look-around)
- [ ] Keep obstacle_avoidance.py as emergency fallback
- [ ] Listen to Nav2 feedback for progress/stuck

### Phase E: EKF Localization
- [ ] Configure robot_localization EKF (odom + IMU)
- [ ] Publish fused odom → Nav2 + webapp
- [ ] Remove dead reckoning from Gateway

---

## 🔑 Key Config Reference

| Parameter | Value |
|---|---|
| Robot width | 0.21m |
| Robot length | 0.26m |
| Min passage | 0.31m |
| LiDAR | YDLidar G2, /scan |
| Odom topic | /odom |
| Camera topic | /image_raw/compressed |
| CMD_VEL topic | /cmd_vel |
| LIDAR_OFFSET_DEG | 0 (env var, set 180 if mounted backward) |
| Map file | ~/Desktop/.../maps/new5_map5.yaml |
| MyAGV IP | 192.168.0.111 |
| Gateway IP | 192.168.0.60 |
| Server | user.tail87d9fe.ts.net:8080 |



=== VORA MyAGV — ROS2 Environment Context (for server-side debugging) ===

HARDWARE
  Device   : NVIDIA Jetson Nano 4 GB — aarch64 (Ubuntu 20.04.6 LTS)
  RAM      : ~3.9 GB + 6 GB swap
  ROS dist : ROS2 Galactic

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
KEY PATHS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ROS2 base
  /opt/ros/galactic/                          ← ros2 core, slam_toolbox

myagv_ros2 workspace  (pre-built, robot-specific)
  ~/myagv_ros2/install/                       ← nav2_bringup, robot_localization,
                                                myagv_description, cv_bridge, etc.
  Nav2 launch files:
    ~/myagv_ros2/install/nav2_bringup/share/nav2_bringup/launch/
      bringup_launch.py
      localization_launch.py
      navigation_launch.py
      slam_launch.py

colcon workspace  (our custom build)
  ~/colcon_ws/
  ~/colcon_ws/src/m-explore-ros2/            ← explore_lite source (pinned commit d75bb07)
  ~/colcon_ws/install/explore_lite/          ← explore_lite binary

VORA project folder
  ~/Desktop/VORA_myAGV_only_ros2_package/new5/

  Shell scripts:
    start_myagv.sh        ← main robot startup
    start_slam.sh         ← launch SLAM Toolbox
    start_camera.sh       ← camera node
    save_map.sh           ← save current SLAM map
    teleop_map.sh         ← keyboard teleop + map
    debug_camera.sh       ← camera debug
    install_nav2_stack.sh ← installer script (idempotent)

  Config:
    slam_toolbox_params.yaml   ← SLAM Toolbox parameters

  Maps (PGM + YAML pairs):
    maps/new5_map5.yaml        ← current working map
    maps/new5_map5_backup.yaml ← backup
    maps/lab_room.yaml         ← lab room map

  ROS2 Python package — vora_robot_bridge:
    vora_robot_bridge/vora_robot_bridge/
      command_executor.py      ← ros2 run vora_robot_bridge command_executor
      camera_bridge.py         ← ros2 run vora_robot_bridge camera_bridge
      __init__.py

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
INSTALLED PACKAGES (verified ros2 pkg list)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  explore_lite          → ~/colcon_ws/install/explore_lite/
  nav2_bringup          → ~/myagv_ros2/install/nav2_bringup/
  nav2_costmap_2d       → /opt/ros/galactic/
  nav2_bt_navigator     → /opt/ros/galactic/
  nav2_planner          → /opt/ros/galactic/
  nav2_controller       → /opt/ros/galactic/
  nav2_recoveries       → /opt/ros/galactic/
  nav2_lifecycle_manager→ /opt/ros/galactic/
  nav2_map_server       → /opt/ros/galactic/
  nav2_amcl             → /opt/ros/galactic/
  slam_toolbox          → /opt/ros/galactic/
  robot_localization    → ~/myagv_ros2/install/robot_localization/

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SOURCE ORDER (must source in this order)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  source /opt/ros/galactic/setup.bash
  source ~/myagv_ros2/install/setup.bash
  source ~/colcon_ws/install/setup.bash   ← for explore_lite only

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
NOTES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- explore_lite pinned to commit d75bb07 (July 2022) — last galactic-compatible
  commit of robo-friends/m-explore-ros2. main branch requires Humble headers.
- ROS apt repo GPG key expired — apt uses cached index; packages install fine.
- vora_robot_bridge is NOT installed via colcon yet (source package only).
  To install: cd ~/Desktop/VORA_myAGV_only_ros2_package/new5 &&

---

# 🔧 Afternoon Session — Nav2 TF Chain Debug + Visual Search Live Test

## 📋 Summary (บ่าย)

ทดสอบ Visual Search Pipeline แบบ end-to-end จริงกับหุ่น + debug Nav2 TF chain ที่หายไป

**ผลการทดสอบ Visual Search:**
- ✅ ค้นหา "ป้ายที่มีตัวอักษรตัว E สีแดง" สำเร็จ — Phase 1.5 เจอป้าย "ENC" สีแดง, confirm 2/2, approach ถึง
- ❌ ค้นหา "ขวดน้ำ" — Phase 0-1.5 ไม่เจอ → Phase 2 (Nav2) ล้มเหลว → fallback legacy
- ❌ Nav2 stack ยัง**ไม่**ทำงาน — TF tree ไม่เชื่อมต่อกัน, AMCL รับ /scan ไม่ได้

---

## 🌳 TF Chain ที่ Nav2 ต้องการ

```
map ──→ odom ──→ base_link ──→ laser_frame
 │        │          │              │
AMCL   odom_tf_    (shared      static_transform_
       broadcaster   parent)     publisher
       .py                       (start_nav2.sh)
```

---

## 🔍 ปัญหาที่พบ + แก้ไข (Session บ่าย)

### Issue A: ❌ Missing TF `base_link → laser_frame` — FIXED ✅

**อาการ:**
```
[amcl] Message Filter dropping message: frame 'laser_frame'
       for reason 'discarding message because the queue is full'
```

**สาเหตุ:** ไม่มี TF เชื่อม base_link → laser_frame → AMCL ไม่สามารถ transform laser scan เข้า robot frame

**แก้ไข:** เพิ่ม static_transform_publisher ใน start_nav2.sh

**ค่า transform (MyAGV 2023, Elephant Robotics):**
| Parameter | Value | เหตุผล |
|-----------|-------|--------|
| x | -0.0475m | LiDAR อยู่ด้านหลัง chassis |
| y | 0m | อยู่กลาง |
| z | 0.068m | อยู่บนฝาด้านบน |
| yaw | π (3.14159) | LiDAR หันหลัง |

**ยืนยัน:**
```bash
$ ros2 run tf2_ros tf2_echo base_link laser_frame
Translation: [-0.048, 0.000, 0.068]
Rotation: in Quaternion [0.000, 0.000, 1.000, 0.000]  ← yaw=π ✅
```

---

### Issue B: ❌ waypoint_follower FATAL crash — FIXED ✅

**อาการ:** `[FATAL] Can not get 'plugin' param value for wait_at_waypoint`

**สาเหตุ:** nav2_params.yaml ไม่มี waypoint_follower section + ไม่อยู่ใน lifecycle_manager

**แก้ไข:** เพิ่มใน nav2_params.yaml:
```yaml
waypoint_follower:
  ros__parameters:
    use_sim_time: false
    loop_rate: 20
    stop_on_failure: false
    waypoint_task_executor_plugin: "wait_at_waypoint"
    wait_at_waypoint:
      plugin: "nav2_waypoint_follower::WaitAtWaypoint"
      enabled: true
      waypoint_pause_duration: 0
```

**ยืนยัน:** `Created waypoint_task_executor : wait_at_waypoint` ✅

---

### Issue C: ❌ Missing TF `odom → base_link` — FIXED (code) ⚠️

**อาการ:**
```
[controller_server] Timed out waiting for transform from base_link to odom
Invalid frame ID "odom" passed to canTransform — frame does not exist
```

**สาเหตุ:** `myagv_odometry_node` publishes /odom เป็น **topic** แต่ไม่ broadcast TF

**แก้ไข:** สร้าง `odom_tf_broadcaster.py` (ROS2 node ใหม่)
- Subscribe /odom → broadcast TF odom→base_link
- เพิ่ม launch ใน start_nav2.sh

**สถานะ:** ⚠️ Deploy แล้ว — ต้อง verify

---

### Issue D: ⚠️ TF Tree Disconnected (ล่าสุด)

**อาการ (log ล่าสุดที่ user ส่งมา):**
```
Could not find a connection between 'base_link' and 'laser_frame'
because they are not part of the same tree.
Tf has two or more unconnected trees.
```

**วิเคราะห์:** ถึงแม้ทุก TF จะ publish อยู่ แต่ TF tree ยังแยกกัน:
- Tree 1: `odom → base_link` (odom_tf_broadcaster)
- Tree 2: `base_link → laser_frame` (static_transform_publisher)

**สาเหตุที่เป็นไปได้:**
1. odom_tf_broadcaster.py ยังไม่ได้ deploy version ล่าสุด — base_link ยังไม่ถูก publish เป็น child ของ odom
2. Static TF publisher เริ่มก่อน odom broadcaster → TF tree ยังไม่เชื่อม
3. Timing issue — odom TF ยัง publish ไม่ทัน

**สิ่งที่ต้องทำ:**
```bash
# ตรวจ TF tree ทั้งหมด
ros2 run tf2_ros tf2_monitor

# ถ้า odom→base_link ไม่เห็น → odom_tf_broadcaster ไม่ทำงาน
# ตรวจ process:
ps aux | grep odom_tf

# ตรวจว่า /odom topic มี data:
ros2 topic echo /odom --once
```

---

## ✅ Visual Search Live Test Results

### Search #1: "ป้ายที่มีตัวอักษรตัว E สีแดง" — ✅ สำเร็จ!

| Phase | Direction | VLM Response | Result |
|-------|-----------|-------------|--------|
| Phase 0 | Current | prompt_echo (23 chars) | ❌ Reject |
| Phase 1 | Left 90° | English CoT → furniture (no E) | ❌ Not found |
| Phase 1 | Right 180° | เก้าอี้สีขาว (39 chars) | ❌ Not found |
| Phase 1 | Behind 90° | prompt_echo (30 chars) | ❌ Reject |
| Phase 1.5 | Diag-Left +45° | Curtain with zipper (1285 chars) | ❌ Not found |
| Phase 1.5 | Diag-Right -90° | **"ENC" sign in red** (1298 chars) | ✅ **FOUND!** (conf=0.9) |
| Confirm | Same angle | "white partition with ENC in red" | ✅ **Confirmed 2/2** |

**Approach Phase:**
- หมุนขวา 30° → เดินตรง 3.0s (object at top = far)
- VLM re-check: object not visible → ถือว่าใกล้มากแล้ว → STOP
- 📝 Object memory saved: "ป้ายที่มีตัวอักษรตัว E สีแดง" at top_right
- ⏱️ เวลาทั้งหมด: ~1.5 นาที (15:47:42 → 15:49:27)

### Search #2: "ขวดน้ำ" — ❌ ไม่เจอ → Nav2 Fallback

| Phase | Result |
|-------|--------|
| Phase 0 | ไม่เจอ |
| Phase 1 (L/R/Behind) | ไม่เจอ (เก้าอี้, ฉากกั้น, ป้ายขาว) |
| Phase 1.5 (±45°) | ไม่เจอ (คน, ผ้าม่าน) |
| Phase 2 (Nav2) | ❌ `Action client failed to connect, no status received` |
| Fallback (Legacy) | เดินตามทิศทาง... (ongoing) |

---

## 📁 ไฟล์ที่สร้าง/แก้ไข (Session บ่าย)

| ไฟล์ | Action | Description |
|------|--------|-------------|
| `Myagv/odom_tf_broadcaster.py` | **NEW** | ROS2 node: /odom topic → odom→base_link TF |
| `Myagv/start_nav2.sh` | **MODIFIED ×3** | เพิ่ม static TF, LiDAR position fix, odom broadcaster |
| `Myagv/nav2_params.yaml` | **MODIFIED** | waypoint_follower + transform_tolerance 2.0 + lifecycle_manager |

---

## 📊 สถานะรวม End-of-Day

| Component | Status | Notes |
|-----------|--------|-------|
| Gateway ↔ ROSBridge | ✅ | Odom, Camera, LiDAR all working |
| Gateway ↔ Server | ✅ | VLM + LLM pipeline working |
| Visual Search Phase 0-1.5 | ✅ | เจอ + approach ป้าย E สีแดงสำเร็จ |
| Visual Search Phase 2 (Nav2) | ❌ | Nav2 action server ยังไม่ active |
| TF: base_link → laser_frame | ✅ | Verified via tf2_echo |
| TF: odom → base_link | ⚠️ | Code deployed, needs verification |
| TF: map → odom (AMCL) | ❌ | AMCL ยังรับ scan ไม่ได้ → ไม่ localize |
| Nav2 lifecycle | ❌ | controller_server timeout on TF |
| waypoint_follower config | ✅ | Confirmed working |
| Obstacle avoidance (legacy) | ✅ | LiDAR ±30° cone |
| Object memory | ✅ | Saved "ป้าย E สีแดง" |
| Dead reckoning | ✅ | xy_drift 0.37m tracked |
| VLM prompt_echo | ⚠️ | 3/11 VLM calls rejected (27%) |

---

## 🔮 Next Steps (Priority)

### 🔴 ด่วน — Fix TF Tree Connectivity
1. ตรวจว่า odom_tf_broadcaster.py ทำงาน: `ros2 run tf2_ros tf2_monitor`
2. ถ้า TF tree ยังแยก → ตรวจ process, restart start_nav2.sh
3. TF chain ต้อง: `odom → base_link → laser_frame` อยู่ใน tree เดียว

### 🟡 ต่อไป — Verify Nav2 Full Stack
4. AMCL ต้อง localize: ดูจาก "queue is full" หยุด + map→odom TF ปรากฏ
5. Nav2 lifecycle ทุก node ต้อง ACTIVE
6. Gateway Nav2Client ต้องเชื่อม /navigate_to_pose action ได้
7. set_initial_pose ก่อน navigate (AMCL ต้องรู้ตำแหน่งเริ่มต้น)

### 🟢 Optimization
8. VLM prompt_echo reduction (ปรับ system prompt / temperature)
9. explore_lite integration (`start_nav2.sh --explore`)
10. EKF localization (robot_localization + IMU fusion)

---

## 🔑 คำสั่ง Deploy + Debug บน MyAGV

```bash
# Deploy files
scp Myagv/odom_tf_broadcaster.py er@192.168.0.111:~/Desktop/VORA_myAGV_only_ros2_package/new5/
scp Myagv/start_nav2.sh er@192.168.0.111:~/Desktop/VORA_myAGV_only_ros2_package/new5/
scp Myagv/nav2_params.yaml er@192.168.0.111:~/Desktop/VORA_myAGV_only_ros2_package/new5/

# On MyAGV — verify TF tree
ros2 run tf2_ros tf2_monitor              # ดูทุก TF
ros2 run tf2_ros tf2_echo odom base_link  # ต้องเห็น translation
ros2 run tf2_ros tf2_echo base_link laser_frame  # ต้องเห็น (-0.048, 0, 0.068)
ros2 run tf2_ros tf2_echo map odom        # AMCL ต้องทำงานก่อน

# Save TF tree ดูเป็น PDF
ros2 run tf2_tools view_frames            # → frames.pdf
```

---

---

# Session 3: Evening — TF Tree Root Cause + Critical Fix

## 🔍 Root Cause Discovery: Conflicting TF Publishers

### ปัญหาที่ค้นพบ
เมื่อตรวจสอบ TF tree จริงจาก `start_myagv.sh` (driver เดิมของ MyAGV):

```
odom
 └── base_footprint   (EKF node — robot_localization)
      ├── base_link        (robot_state_publisher + URDF)
      ├── laser_frame      [0.065, 0, 0.08, yaw=π]  (ydlidar static TF)
      ├── camera_link      (static TF)
      └── imu_link         (static TF)
```

**Root cause:** `start_nav2.sh` เพิ่ม TF publisher ที่ **ชนกับ** driver เดิม:
- ❌ `odom_tf_broadcaster.py` — publish `odom → base_link` (ชนกับ EKF ที่ publish `odom → base_footprint`)
- ❌ `static_transform_publisher base_link → laser_frame` (ชนกับ ydlidar static TF `base_footprint → laser_frame`)

**ผล:** TF tree แยกเป็น 2 ต้นไม้ → "two unconnected trees" error → AMCL ทำงานไม่ได้

### การแก้ไข
1. **ลบ** odom_tf_broadcaster.py + static_transform_publisher ออกจาก start_nav2.sh
2. **เปลี่ยน** `nav2_params.yaml` ทุกจุดที่ใช้ `base_link` → `base_footprint` (4 จุด: AMCL, bt_navigator, local_costmap, global_costmap)
3. **เพิ่ม** TF chain check ก่อนเปิด Nav2 (ใช้ `timeout 2 ros2 run tf2_ros tf2_echo`)
4. **เพิ่ม** fallback: ถ้า `odom → base_footprint` ไม่เจอ จะลอง `odom → base_link`

### Bug เพิ่มเติมที่พบ
- `--wait-for-transform 1.0` flag ไม่มีใน ROS2 Galactic `tf2_echo` → TF check ล้มเหลว 30s timeout
- แก้เป็น: `timeout 2 ros2 run tf2_ros tf2_echo odom base_footprint 2>&1 | grep -q 'Translation'`

---

## 📊 Visual Search Results (Evening Test)

| Search Target | Phase 0 | Phase 1 (L/R/Behind) | Phase 1.5 (±45°) | Phase 2 (Nav2) | ผลลัพธ์ |
|---|---|---|---|---|---|
| ป้าย E สีแดง | ✅ Found | - | - | - | **สำเร็จ** (approach OK) |
| ขวดน้ำ | ❌ | ❌ ผนัง/สายไฟ/ว่าง | ❌ เงา/prompt_echo | Nav2 fail → legacy | **ไม่พบ** (ถูกต้อง) |

- **Prompt echo rate**: ~27% (3/11 VLM calls rejected, คำตอบ ≤30 chars = echo prompt กลับ)
- **Phase 2 fallback**: Nav2 Action client fail → legacy rotation ทำงานแทน (graceful degradation)

---

# 🚨 Problems & Errors Summary

## Critical Issues (ยังไม่ได้ทดสอบหลังแก้)

### 1. Nav2 Stack ยังไม่ Active
- **อาการ**: `Action client failed to connect, no status received`
- **สาเหตุ**: TF tree แยก 2 ต้นไม้ → AMCL "queue is full" loop → lifecycle ไม่ผ่าน ACTIVE
- **แก้แล้ว**: ลบ conflicting TF publishers, เปลี่ยนเป็น `base_footprint`
- **สถานะ**: ⚠️ แก้โค้ดแล้ว, ยังไม่ได้ deploy + ทดสอบบน MyAGV

### 2. TF Wait Check Fails
- **อาการ**: `TF odom → base_footprint not available after 30s`
- **สาเหตุ**: `--wait-for-transform` flag ไม่มีใน Galactic
- **แก้แล้ว**: ใช้ `timeout 2 ros2 run tf2_ros tf2_echo ... 2>&1 | grep -q 'Translation'`
- **สถานะ**: ⚠️ แก้โค้ดแล้ว, ยังไม่ได้ deploy + ทดสอบ

### 3. base_footprint vs base_link Uncertainty
- **ปัญหา**: TF tree diagram แสดง `odom → base_footprint` แต่ TF check timeout 30s
- **ความเป็นไปได้**: EKF node อาจใช้ `base_link` โดยตรง (ขึ้นกับ firmware version)
- **แก้แล้ว**: เพิ่ม fallback ลอง `base_link` + แจ้งเตือนให้แก้ nav2_params.yaml
- **ต้องตรวจ**: `ros2 run tf2_ros tf2_monitor` บน MyAGV ขณะ `start_myagv.sh` ทำงาน

## Known Issues (ยังไม่ได้แก้)

### 4. VLM Prompt Echo (~27% rejection rate)
- **อาการ**: VLM ตอบกลับด้วย prompt เดิม (≤30 chars) แทนที่จะวิเคราะห์ภาพ
- **สาเหตุ**: Qwen3-VL:8B บางครั้ง echo prompt กลับ โดยเฉพาะเมื่อภาพมืดหรือไม่ชัด
- **Workaround**: text_preprocessor.py ตรวจจับ (len ≤ 30 AND prefix match) → reject → re-query
- **แก้ถาวร**: ปรับ system prompt / temperature / ลอง Qwen2.5-VL

### 5. General Commands ไม่ทำงาน
- **อาการ**: พิมพ์คำสั่งทั่วไป (ไม่ใช่ visual search) → "ไม่รู้จักคำนี้" ทุกครั้ง
- **สาเหตุ**: ระบบ intent parser รองรับแค่ visual search commands
- **แก้ถาวร**: เพิ่ม LLM-based intent classification → general conversation, navigation, status query etc.

### 6. waypoint_follower FATAL Config (แก้แล้ว ✅)
- เพิ่ม config section + lifecycle_manager entry ใน nav2_params.yaml

---

# 🔮 Future Planning

## Phase 1: Nav2 Integration Complete (ความสำคัญสูงสุด)
1. **Deploy + Test** updated `start_nav2.sh` + `nav2_params.yaml` บน MyAGV
2. ตรวจสอบ TF tree จริง: `ros2 run tf2_ros tf2_monitor` → ยืนยัน `base_footprint` หรือ `base_link`
3. ถ้าใช้ `base_link` → update nav2_params.yaml กลับ (4 จุด)
4. ยืนยัน Nav2 lifecycle ทุก node = ACTIVE
5. ทดสอบ `set_initial_pose` + `navigate_to_pose` จาก Gateway

## Phase 2: Visual Search Phase 2 with Nav2
6. Gateway `nav2_client.py` ต้องเชื่อม /navigate_to_pose ได้
7. ทดสอบ Visual Search full pipeline: Phase 0 → 1 → 1.5 → **Phase 2 (Nav2)** → Phase 3
8. ปรับ explore goals ให้เหมาะกับห้อง (radial pattern from nav2 costmap)

## Phase 3: Intelligence Improvement
9. **Intent Parser**: LLM-based classification → รองรับ general commands
10. **VLM Prompt Echo Fix**: ปรับ prompt / temperature / model
11. **Memory Integration**: วัตถุที่เคยเจอ → ใช้ Nav2 navigate กลับไปหาได้

## Phase 4: Advanced Navigation
12. **Explore Mode**: `start_nav2.sh --explore` (SLAM + explore_lite)
13. **Dynamic Obstacle Avoidance**: ปรับ local costmap + DWB controller
14. **Multi-room SLAM**: สร้าง map ทั้งชั้น → save → reload

## Phase 5: Polish & Demo
15. **Dashboard UI**: แสดง Nav2 path + costmap บน web
16. **Voice Control**: STT → Intent → Nav2/Search/General
17. **Paper Draft**: methodology + results

---

# 🔑 คำสั่ง Deploy ล่าสุด

```bash
# Deploy updated files (ไม่ต้อง deploy odom_tf_broadcaster.py แล้ว — ลบออกจาก script แล้ว)
scp Myagv/start_nav2.sh er@192.168.0.111:~/Desktop/VORA_myAGV_only_ros2_package/new5/
scp Myagv/nav2_params.yaml er@192.168.0.111:~/Desktop/VORA_myAGV_only_ros2_package/new5/

# On MyAGV — ตรวจ TF tree ก่อนเปิด Nav2
ros2 run tf2_ros tf2_monitor              # ดูทุก TF pair
ros2 run tf2_ros tf2_echo odom base_footprint  # ✅ ต้องเห็น translation
ros2 run tf2_ros tf2_echo odom base_link       # สำรอง — ถ้า base_footprint ไม่เจอ

# ดู TF tree เป็น PDF
ros2 run tf2_tools view_frames            # → frames.pdf

# เปิด Nav2 (หลัง start_myagv.sh ทำงานแล้ว)
./start_nav2.sh                           # nav mode (AMCL + map)
./start_nav2.sh --slam                    # SLAM mode

# ตรวจ Nav2 lifecycle
ros2 lifecycle list /amcl
ros2 lifecycle list /controller_server
ros2 lifecycle list /planner_server
```

---

*Updated 7 March 2026 (evening session) — TF Root Cause Discovery + Critical Fix + Problems/Future Planning*
              colcon build --packages-select vora_robot_bridge