# 🗺️ ดู SLAM Map บน Gateway (Windows 11 + WSL2)

**Updated:** 6 มีนาคม 2026

---

## สถานะไฟล์ในโฟลเดอร์ `maps/`

| ไฟล์ | คำอธิบาย |
|---|---|
| `lab_room.pgm` | ภาพ occupancy grid (map จาก SLAM) |
| `lab_room.yaml` | metadata — **แก้ path แล้ว** ให้ชี้ `./lab_room.pgm` |
| `rviz_slam.rviz` | RViz config ต้นฉบับจากเพื่อน (มี RobotModel, LaserScan, TF) |
| `rviz_map_only.rviz` | **RViz config ใหม่** — แสดงแค่ Map + Grid (สำหรับดูอย่างเดียว) |
| `install_ros2_minimal.sh` | Script ติดตั้ง ROS2 Humble บน WSL2 |
| `view_map.sh` | Script รัน map_server + RViz2 |

---

## Step 1: เตรียม WSL2 (ทำครั้งเดียว)

### 1.1 เปิด WSL2 Terminal

บน Windows PowerShell:
```powershell
wsl
```

ถ้ายังไม่มี Ubuntu:
```powershell
wsl --install -d Ubuntu-22.04
```

### 1.2 ติดตั้ง ROS2 Humble

```bash
cd /mnt/c/Project_RE/VORA_myAGV_only_ros2_package/maps
chmod +x install_ros2_minimal.sh
bash install_ros2_minimal.sh
```

ใช้เวลาประมาณ 5–15 นาที (ขึ้นกับเน็ต) ติดตั้งแค่:
- `ros-humble-ros-base` — ROS2 core
- `ros-humble-rviz2` — visualization
- `ros-humble-nav2-map-server` — อ่าน .pgm/.yaml แล้ว publish `/map`
- `ros-humble-nav2-lifecycle-manager` — จัดการ lifecycle ของ map_server

### 1.3 ทดสอบ

```bash
source ~/.bashrc
ros2 --help
rviz2 --help
```

ถ้า `rviz2` เปิดหน้าจอมาได้ = WSLg ทำงานปกติ (Windows 11 มี built-in)

---

## Step 2: ดู Map

```bash
cd /mnt/c/Project_RE/VORA_myAGV_only_ros2_package/maps
chmod +x view_map.sh
bash view_map.sh
```

สิ่งที่เกิดขึ้น:
1. **lifecycle_manager** — จัดการ activate map_server
2. **map_server** — อ่าน `lab_room.yaml` + `lab_room.pgm` แล้ว publish ไปที่ topic `/map`
3. **rviz2** — เปิดพร้อม config `rviz_map_only.rviz` ที่แสดงเฉพาะ Map layer

กด **Ctrl+C** เพื่อปิดทั้งหมด

### ใช้ RViz config ต้นฉบับ (ที่มี TF, LaserScan, RobotModel)

```bash
bash view_map.sh lab_room.yaml rviz_slam.rviz
```

> ⚠️ Display ที่ต้องการ live topics (LaserScan, RobotModel, TF) จะขึ้น error สีแดง เพราะไม่มี robot ต่ออยู่ — ไม่มีผลกับการดู map

---

## Step 3: ROS2 Version Mismatch (Galactic vs Humble)

### ไม่มีปัญหาสำหรับการดู map

| รายการ | Galactic (myAGV) | Humble (WSL2) |
|---|---|---|
| Ubuntu | 20.04 | 22.04 |
| EOL | ธ.ค. 2022 | พ.ค. 2027 |
| Map format (.pgm + .yaml) | เหมือนกัน ✅ | เหมือนกัน ✅ |
| RViz plugin API | rviz_default_plugins | rviz_default_plugins ✅ |

**map file format เป็น standard เดียวกันทุก ROS2 distro** — ไฟล์ `.pgm` + `.yaml` ที่สร้างจาก Galactic เปิดใน Humble ได้ทันทีไม่ต้องแปลง

### ถ้าต้องการต่อ live กับ myAGV (อนาคต)

ถ้าจะเชื่อม ROS2 topics ระหว่าง myAGV (Galactic) กับ Gateway (Humble) แบบ real-time จะมีปัญหา **DDS incompatibility** ระหว่าง distro ต่างกัน วิธีแก้:

1. **ใช้ ros1_bridge / topic_tools** — ไม่เหมาะ ซับซ้อน
2. **ใช้ ROSBridge (WebSocket)** — ✅ **แนะนำ** เพราะโปรเจค VORA ใช้อยู่แล้ว
3. **Upgrade myAGV เป็น Humble** — ดีที่สุดระยะยาว แต่ต้องเปลี่ยน Ubuntu เป็น 22.04

---

## Troubleshooting

### RViz2 เปิดไม่ได้ / ไม่มีหน้าจอ

```bash
# ตรวจสอบ WSLg
echo $DISPLAY
# ควรเห็น :0 หรือค่าอะไรบางอย่าง

# ถ้าไม่มี ลอง
export DISPLAY=:0

# ทดสอบด้วย app ง่ายๆ
sudo apt install -y x11-apps
xclock
```

ถ้า `xclock` เปิดได้ = WSLg ปกติ, ถ้าไม่ได้:
```powershell
# บน PowerShell (ไม่ใช่ WSL)
wsl --update
wsl --shutdown
# แล้วเปิด wsl ใหม่
```

### Map ไม่แสดงใน RViz

1. ตรวจว่า map_server ทำงาน:
   ```bash
   ros2 topic list
   # ต้องเห็น /map
   ros2 topic echo /map --once
   ```

2. ตรวจ RViz — ใน Displays panel ดู Map → Topic → Durability Policy ต้องเป็น **Transient Local**

3. ลอง subscribe ใหม่ — ใน RViz uncheck แล้ว check กลับที่ Map display

### map_server ไม่ activate

lifecycle_manager ต้อง start ก่อน map_server — script `view_map.sh` จัดลำดับไว้แล้ว ถ้ามีปัญหา ลอง activate manual:
```bash
ros2 lifecycle set /map_server configure
ros2 lifecycle set /map_server activate
```


