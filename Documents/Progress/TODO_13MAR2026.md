# VORA TODO — 13 มีนาคม 2026

> **Deadline:** < 1 เดือน (ต้องเสร็จ + ทำ Experiment)
> **Advisor Feedback:** รีบทำให้ระบบเสร็จ แล้วเริ่ม Experiment

---

## สถานะปัจจุบัน (สรุปจาก Log + Codebase)

| Component | สถานะ | หมายเหตุ |
|-----------|--------|----------|
| 3-Tier Deploy (Server/Gateway/Robot) | ✅ Done | ทำงานได้ |
| ROSBridge (Gateway↔Robot) | ✅ Done | Connected, LiDAR + Camera OK |
| LiDAR 360° Sector | ✅ Done | LIDAR_MIRROR=ON fix แล้ว |
| Camera Stream | ⚠️ ไม่เสถียร | USB หลุดบ่อย, Camera Blind Guard ป้องกันชน |
| LLM Agent Loop | ✅ Done | 12 steps, force-forward, anti-spin |
| VLM Object Detection | ✅ Done | qwen3-vl:32b per step |
| **STT (เสียง→ข้อความ)** | ❌ ไม่ได้ใช้ | delay หนักมาก ~5-10s, Server 502 บ่อย |
| **TTS (ข้อความ→เสียง)** | ❌ ไม่ได้ใช้ | ไม่มีลำโพงต่อ |
| Nav2 (Path Planning) | ❌ ไม่ได้ใช้ | ใช้ cmd_vel ตรงแทน |
| SLAM Map | ❌ ยังไม่ทำ | อาจารย์อยากได้ map ที่ environment fix |
| **องศาการหมุน** | 🔧 กำลังจูน | LIDAR_MIRROR fix แล้ว แต่ยังไม่ได้ทดสอบจริง |

---

## 🔴 PRIORITY 1 — ต้องเสร็จก่อน Demo / Experiment

### 1.1 จูนองศาการหมุนหุ่น
- [ ] ทดสอบ LIDAR_MIRROR=ON ว่าหันถูกทิศหรือยัง
- [ ] Calibrate `SCAN_ROTATION_CAL` — สั่งหมุน 90° แล้ววัดจริง
- [ ] ทดสอบ `LIDAR_ANGLE_OFFSET_DEG` ถ้า sector 0 ไม่ตรงหน้าหุ่น
- [ ] End-to-end: สั่ง "หาขวดน้ำ" → หุ่นต้องหันไปทิศที่โล่ง → เดินถูก

### 1.2 Camera Stream ให้เสถียร
- [ ] เช็ค USB connection (ใช้ USB hub มีไฟเลี้ยง?)
- [ ] ทดสอบ `ros2 topic hz /camera/compressed` ว่า stable กี่ fps
- [ ] ถ้ายังหลุด → ลอง USB อื่น / ลด resolution

### 1.3 Server Connection (502 Error)
- [ ] เช็ค VORA Server ว่า running (`ollama list` + `systemctl status`)
- [ ] Tailscale ping → ดู latency
- [ ] ถ้า Server ไม่พร้อม → Gateway ต้อง fallback ได้ (ไม่ crash)

---

## 🟡 PRIORITY 2 — ต้องมีสำหรับ Experiment (ตาม Advisor)

### 2.1 สร้าง SLAM Map (Advisor Task #2)
- [ ] ssh เข้า MyAGV → run SLAM (start_slam.sh)
- [ ] ขับหุ่นสร้าง map ห้องทดลอง
- [ ] save map → `maps/` folder
- [ ] จัด environment ให้ fix (วางของตำแหน่งเดิมทุกรอบ)

### 2.2 VLM Experiment (Advisor Task #4.2)
- [ ] เตรียมวัตถุทดสอบ 5-10 ชิ้น (ขวดน้ำ, แก้ว, กล่อง, ...)
- [ ] ถ่ายรูปจากกล้องหุ่น ส่ง VLM → บันทึก accuracy
- [ ] ทดสอบ edge case: ของซ้อนกัน, ของคล้ายกัน
- [ ] คำนวณ detection accuracy (TP/FP/FN)

### 2.3 End-to-End Experiment (Advisor Task #4.3)
- [ ] ออกแบบ test scenario: วางของ → สั่งหา → วัดผล
- [ ] วัด Latency ทั้ง pipeline (voice→action หรือ text→action)
- [ ] วัด Task Success Rate (หาเจอ/ไม่เจอ ใน 12 steps)
- [ ] ทำ 10+ trials → สรุปผล

---

## 🟢 PRIORITY 3 — Nice to Have / ถ้ามีเวลา

### 3.1 STT Experiment (Advisor Task #4.1)
- [ ] STT delay หนัก → ลอง local whisper บน Gateway (ถ้า GPU พอ)
- [ ] หรือใช้ text input แทนเสียง สำหรับ experiment (skip STT)
- [ ] ถ้าทำ STT exp → เตรียม test set 20-30 ประโยคไทย → วัด WER

### 3.2 TTS
- [ ] ต่อลำโพง USB กับ MyAGV
- [ ] ทดสอบ gTTS output
- [ ] ถ้าไม่มีลำโพง → ใช้ Webapp แสดงข้อความแทน (มีอยู่แล้ว)

### 3.3 Nav2 Integration (Advisor Task #1, #3, #6)
- [ ] Nav2 stack setup (ถ้ามีเวลา — ตอนนี้ใช้ cmd_vel ตรง)
- [ ] Waypoint following experiment
- [ ] LLM→Nav2 communication protocol
- **หมายเหตุ:** ระบบปัจจุบันใช้ LLM + LiDAR sector → cmd_vel ได้โดยไม่ต้อง Nav2 สำหรับ object search task. Nav2 จะสำคัญถ้าต้อง navigate ไปจุดเฉพาะบน map

---

## 📋 Experiment Plan สรุป (ตามที่อาจารย์ต้องการ)

| Experiment | ต้องเสร็จก่อน | Metric | Priority |
|------------|---------------|--------|----------|
| **VLM Accuracy** | Camera stable, VLM working | Detection Accuracy % | 🔴 สูง |
| **End-to-End Search** | หมุนถูกทิศ, camera, VLM | Success Rate, Latency | 🔴 สูง |
| **STT WER** | STT delay fix หรือ skip | Word Error Rate | 🟡 กลาง |
| **Navigation Accuracy** | SLAM map, Nav2 (optional) | Position error m | 🟢 ต่ำ |

---

## ⚡ ขั้นตอนถัดไป (วันนี้)

1. **จูนองศาการหมุน** — ทดสอบ LIDAR_MIRROR + calibrate rotation
2. แก้ Server 502 → ให้ VLM + LLM ใช้งานได้
3. End-to-end test: สั่งหาของ → ดู log ว่าหมุนถูกทิศ

---

## 💡 Decision Log

| คำถาม | ตัดสินใจ | เหตุผล |
|--------|----------|--------|
| ใช้ Nav2 ไหม? | ยังไม่ใช้ | cmd_vel + LiDAR sector พอสำหรับ search task |
| STT delay? | Skip STT → ใช้ text/webapp สั่ง | delay 5-10s ไม่ practical, ทำ STT exp ทีหลัง |
| TTS? | ใช้ webapp text แทน | ไม่มีลำโพง |
| Image capture vs streaming? | Capture per step | stable กว่า, VLM inference ต้องการ 1 frame |

---

*สร้าง: 13 มีนาคม 2026 | อัพเดตหลังคุยกับอาจารย์*
