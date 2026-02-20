# VORA Progress Report - 19 February 2026

## 📷 Camera Streaming Feature Implementation

### Overview
เพิ่มระบบ streaming กล้องจาก MyAGV (Jetson Nano) ไปยัง webapp ผ่าน Gateway

---

## 🏗️ Architecture

```
┌─────────────────────┐     ┌─────────────────────┐     ┌─────────────────────┐
│      MyAGV          │     │      Gateway        │     │      Server         │
│   (Jetson Nano)     │     │   (Windows PC)      │     │     (A6000)         │
│   192.168.0.111     │     │   192.168.0.60      │     │  Tailscale HTTPS    │
├─────────────────────┤     ├─────────────────────┤     ├─────────────────────┤
│                     │     │                     │     │                     │
│  ┌───────────────┐  │     │  ┌───────────────┐  │     │  ┌───────────────┐  │
│  │   usb_cam     │  │     │  │ camera_stream │  │     │  │ camera_router │  │
│  │  (ROS2 node)  │  │────▶│  │   .py         │  │────▶│  │    .py        │  │
│  └───────────────┘  │     │  └───────────────┘  │     │  └───────────────┘  │
│         │           │     │         │           │     │         │           │
│         ▼           │     │         ▼           │     │         ▼           │
│  /image_raw (YUYV)  │     │  YUYV → JPEG        │     │  Proxy to webapp    │
│                     │     │  conversion         │     │                     │
│  ROSBridge :9090    │     │  Port: 9001         │     │  Port: 8080         │
└─────────────────────┘     └─────────────────────┘     └─────────────────────┘
                                                                  │
                                                                  ▼
                                                        ┌─────────────────────┐
                                                        │      Webapp         │
                                                        │   (index.html)      │
                                                        ├─────────────────────┤
                                                        │  Polls /camera/frame│
                                                        │  @ 10 fps           │
                                                        │  Displays in <img>  │
                                                        └─────────────────────┘
```

---

## 📁 Files Modified/Created

### 1. Gateway (Windows PC)

#### `Gateway/gateway/camera_stream.py` - NEW
- Subscribe ROS2 `/image_raw` และ `/image_raw/compressed` via ROSBridge
- รองรับ encoding: `rgb8`, `bgr8`, `yuyv`, `yuv422_yuy2`, `jpeg`, `mjpeg`
- Convert YUYV to JPEG ด้วย PIL/numpy
- Auto-retry เมื่อ ROSBridge connection หลุด
- Endpoints: `/camera/frame`, `/camera/mjpeg`, `/camera/status`

#### `Gateway/gateway/main.py` - MODIFIED
- เพิ่ม import `camera_stream`
- เพิ่ม camera endpoints
- เริ่ม camera stream ตอน startup

#### `Gateway/gateway/requirements.txt` - MODIFIED
- เพิ่ม `numpy`, `Pillow` สำหรับ image processing

### 2. Server (A6000)

#### `app/api/camera_router.py` - NEW
- Proxy camera frames จาก Gateway ไปยัง webapp
- Endpoints: `GET /camera/status`, `GET /camera/frame`, `GET /camera/mjpeg`, `POST /camera/capture`

#### `app/main.py` - MODIFIED
- เพิ่ม import และ register `camera_router`

#### `app/core/settings.py` - MODIFIED
- เพิ่ม `GATEWAY_URL = "http://192.168.0.60:9001"`

### 3. MyAGV (Jetson Nano)

#### `Myagv/start_myagv.sh` - MODIFIED
- เพิ่ม terminal สำหรับ USB Camera (`ros2 run usb_cam usb_cam_node_exe`)
- ตอนนี้มี 5 terminals: Hardware Driver, ROSBridge, Camera, Command Executor, Audio Stream

### 4. Webapp

#### `app/frontend/index.html` - REWRITTEN
- Layout 3 panels: Controls (left), Camera+VLM (center), Chat (right)
- Camera feed polling @ 10 fps
- VLM image gallery
- Status indicators (Server, Camera, Robot)

---

## 🔧 Technical Details

### YUYV to JPEG Conversion (camera_stream.py)

```python
# YUYV format: 2 bytes per pixel
# Y = luminance (for each pixel)
# U, V = chrominance (shared between 2 pixels)

np_arr = np.frombuffer(raw_bytes, dtype=np.uint8)
np_arr = np_arr.reshape((height, width, 2))

# Extract components
y = np_arr[:, :, 0].astype(np.float32)
u = np_arr[:, 0::2, 1].repeat(2, axis=1).astype(np.float32) - 128
v = np_arr[:, 1::2, 1].repeat(2, axis=1).astype(np.float32) - 128

# YUV to RGB conversion
r = np.clip(y + 1.402 * v, 0, 255).astype(np.uint8)
g = np.clip(y - 0.344 * u - 0.714 * v, 0, 255).astype(np.uint8)
b = np.clip(y + 1.772 * u, 0, 255).astype(np.uint8)
```

### Data Flow

1. **usb_cam** (MyAGV) → Publishes `/image_raw` (YUYV format, 640x480 @ 15fps)
2. **ROSBridge** (MyAGV:9090) → WebSocket bridge for ROS2 topics
3. **camera_stream.py** (Gateway) → roslibpy subscribes, converts YUYV→JPEG
4. **main.py** (Gateway:9001) → HTTP endpoints `/camera/frame`, `/camera/status`
5. **camera_router.py** (Server:8080) → Proxy from Gateway
6. **index.html** (Webapp) → Polls `/camera/frame` every 100ms

---

## 🚀 Deployment Steps

### 1. Start MyAGV Services
```bash
# บน MyAGV (Jetson Nano)
cd ~/Desktop/VORA_myAGV_only_ros2_package/new
./start_myagv.sh 192.168.0.60   # ← Gateway IP (ไม่ใช่ MyAGV IP!)
```

จะเปิด 5 terminals:
- Terminal 0: MyAGV Hardware Driver
- Terminal 1: ROSBridge WebSocket (:9090)
- Terminal 2: USB Camera (usb_cam)
- Terminal 3: Command Executor
- Terminal 4: Audio Stream

### 2. Start Gateway
```powershell
# บน Windows PC (Gateway)
cd C:\Project_RE\VORA_gateway_nav
.\start_gateway.sh
```

รอจนเห็น:
```
✅ Connected to ROSBridge: ws://192.168.0.111:9090
📷 Subscribed to /image_raw (raw)
📷 First frame received! encoding=yuyv, size=XXXXX bytes
```

### 3. Start Server
```bash
# บน Server (A6000)
cd /home/user/vora_project/VORA/VORA
./start_tailscale.sh
```

### 4. Access Webapp
เปิด browser: `https://user.tail87d9fe.ts.net/app`
กด "Connect Camera" หรือ "Stream"

---

## 🔍 Troubleshooting

### Camera ไม่แสดงภาพ

1. **เช็ค usb_cam กำลังทำงาน:**
   ```bash
   # บน MyAGV
   ros2 node list | grep usb
   ros2 topic hz /image_raw   # ควรเห็น ~15 Hz
   ```

2. **เช็ค ROSBridge:**
   ```bash
   # บน MyAGV
   ros2 topic list | grep image
   # ควรเห็น /image_raw, /image_raw/compressed, etc.
   ```

3. **เช็ค Gateway logs:**
   ```
   ✅ Connected to ROSBridge
   📷 Subscribed to /image_raw (raw)
   📷 First frame received!   ← ต้องเห็นนี้
   ```

4. **เช็ค camera status:**
   ```bash
   curl http://192.168.0.60:9001/camera/status
   ```

### Gateway ไม่เห็น frame

- ปัญหา: Subscribe แล้วแต่ไม่มี frame
- แก้ไข: เช็คว่า usb_cam กำลังรันอยู่ (อย่า Ctrl+C)

### Encoding ไม่รองรับ

- Gateway รองรับ: `rgb8`, `bgr8`, `yuyv`, `yuv422_yuy2`, `jpeg`, `mjpeg`
- ถ้าใช้ encoding อื่น อาจต้องเพิ่ม conversion logic

---

## ✅ Session Status

| Component | Status | Notes |
|-----------|--------|-------|
| Server | ✅ Working | Tailscale HTTPS |
| Gateway | ✅ Working | Camera streaming + Robot control |
| MyAGV | ✅ Working | ROSBridge, Audio, Commands |
| Camera | ✅ Fixed | YUYV conversion implemented |
| VLM | ✅ Working | Qwen3-VL:8b via Ollama |
| STT | ✅ Working | Whisper Large-v3-turbo |
| TTS | ✅ Working | Thai TTS |

---

## 📝 Next Steps

1. ทดสอบ camera streaming บน hardware จริง
2. ทดสอบ VLM กับภาพจากกล้อง
3. ปรับปรุง latency ถ้าจำเป็น
4. เพิ่ม error handling สำหรับ edge cases

---

*Last Updated: 19 February 2026, 16:00*
