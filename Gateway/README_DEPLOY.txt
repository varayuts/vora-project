# 🤖 VORA Gateway & MyAGV Deployment Guide
# Updated: 29 มกราคม 2026
# Static IPs: Gateway=192.168.0.60, MyAGV=192.168.0.111 ✅

================================================================================
                        📦 FOLDER STRUCTURE
================================================================================

VORA/
├── Gateway/                    # รันบน Windows Notebook (192.168.0.60)
│   ├── gateway/
│   │   ├── main.py            # FastAPI Gateway Server
│   │   ├── intent_parser.py   # แปลงคำสั่งภาษาไทย → Motion
│   │   ├── ros_cmd.py         # ส่งคำสั่งไป ROSBridge
│   │   ├── waypoint.py        # สร้าง PoseStamped
│   │   └── audio_proxy.py     # Proxy เสียงไป VORA Server
│   ├── .env                   # Configuration
│   ├── start_gateway.sh       # Startup script
│   └── README_DEPLOY.txt      # คู่มือนี้
│
└── Myagv/                      # รันบน Jetson Nano (192.168.0.111)
    ├── vora_robot_bridge/      # ROS2 Package
    │   └── command_executor.py # รับคำสั่ง JSON → ควบคุมหุ่น
    ├── send_audio_to_gateway.py # Stream audio to Gateway
    └── start_myagv.sh          # Automated startup script


================================================================================
                    🖥️ PART 1: GATEWAY (บน Windows Notebook)
================================================================================

📍 Static IP: 192.168.0.60 (Configured ✅)
📍 Tailscale: 100.73.232.94

1. Copy folder Gateway ไปยัง Notebook

2. ติดตั้ง Dependencies:
   cd Gateway
   pip install -r gateway/requirements.txt

3. ตรวจสอบ .env (ควรถูกต้องแล้ว):
   -----------------------------------------------------------------
   # VORA Server (A6000) - ใช้ Tailscale
   SERVER_BASE=https://user.tail87d9fe.ts.net
   SERVER_WS=wss://user.tail87d9fe.ts.net/ws/stt
   
   # Robot ROSBridge - MyAGV Static IP
   ROSBRIDGE=ws://192.168.0.111:9090
   CMD_VEL=/cmd_vel
   -----------------------------------------------------------------

4. รัน Gateway:
   # Method 1: ใช้ script (แนะนำ)
   cd Gateway
   bash start_gateway.sh
   
   # Method 2: รันเอง
   cd Gateway
   uvicorn gateway.main:app --host 0.0.0.0 --port 9001

5. ทดสอบ:
   # Check health (บน Gateway เอง)
   curl http://localhost:9001/health
   
   # Check จาก MyAGV
   curl http://192.168.0.60:9001/health
   
   # Test intent parser
   curl -X POST http://localhost:9001/test/intent \
     -H "Content-Type: application/json" \
     -d '{"text": "โวร่า เดินหน้า 50 เซน"}'


================================================================================
                    🤖 PART 2: MYAGV (บน Jetson Nano)
================================================================================

📍 Static IP: 192.168.0.111 (Configured ✅)
📍 Gateway IP: 192.168.0.60

1. Copy folder Myagv ไปยัง Jetson Nano

2. Build ROS2 Package:
   cd ~/ros2_ws/src   # หรือ ROS2 workspace path
   cp -r Myagv/vora_robot_bridge .
   cd ~/ros2_ws
   source /opt/ros/galactic/setup.bash
   colcon build --packages-select vora_robot_bridge
   source install/setup.bash

3. รัน All Services:
   # Method 1: ใช้ script อัตโนมัติ (แนะนำ!)
   cd Myagv
   ./start_myagv.sh 192.168.0.60
   
   # Method 2: รันเอง (3 terminals)
   # Terminal 1: ROSBridge
   ros2 launch rosbridge_server rosbridge_websocket_launch.xml
   
   # Terminal 2: Command Executor
   ros2 run vora_robot_bridge command_executor
   
   # Terminal 3: Audio Stream
   python3 send_audio_to_gateway.py \
     --gateway-ws ws://192.168.0.60:9001/gw/audio \
     --device "ReSpeaker" \
     --rate 16000

4. ทดสอบส่งคำสั่ง:
   ros2 topic pub --once /vora/command std_msgs/String \
     '{"data":"{\"intent\": \"stop\", \"query_id\": \"test-001\"}"}'
   
   # Check result
   ros2 topic echo /vora/result --once


================================================================================
                    🔊 PART 3: AUDIO STREAMING
================================================================================

บน MyAGV (ถ้ามี ReSpeaker หรือ Mic):
--------------------------------------
1. Install dependencies:
   pip3 install websockets sounddevice numpy

2. รัน Audio Streamer (ระบุ Gateway IP):
   python3 send_audio_to_gateway.py \
     --gateway-ws ws://192.168.0.60:9001/gw/audio \
     --device "ReSpeaker" \
     --rate 16000

   (หรือใช้ start_myagv.sh ซึ่งจะเปิดให้อัตโนมัติ)

   python -c "
   import asyncio, websockets, sounddevice as sd, numpy as np, json

   GATEWAY_WS = 'ws://NOTEBOOK_IP:9001/gw/audio'
   RATE = 16000

   async def main():
       async with websockets.connect(GATEWAY_WS) as ws:
           await ws.send(json.dumps({'rate': RATE, 'lang': 'th'}))
           
           def callback(indata, frames, time, status):
               asyncio.run_coroutine_threadsafe(
                   ws.send(indata.tobytes()), 
                   asyncio.get_event_loop()
               )
           
           with sd.InputStream(samplerate=RATE, channels=1, dtype='int16', callback=callback):
               async for msg in ws:
                   print('Server:', msg)
   
   asyncio.run(main())
   "


================================================================================
                    🧪 TESTING WITHOUT ROBOT
================================================================================

1. บน Server (A6000) - ตรวจสอบ VORA Server:
   curl https://user.tail87d9fe.ts.net/health

2. บน Notebook - รัน Gateway ใน MOCK mode:
   export MOCK_ROBOT=1
   uvicorn gateway.main:app --host 0.0.0.0 --port 9001

3. ทดสอบ Pipeline ผ่าน Text:
   curl -X POST http://localhost:9001/gw/text \
     -H "Content-Type: application/json" \
     -d '{"text": "เดินหน้า 30 เซน"}'

   Expected output:
   {"ok": true, "mode": "motion", "motion": {...}}


================================================================================
                    📝 SUPPORTED COMMANDS
================================================================================

| คำสั่งภาษาไทย              | Intent                          |
|---------------------------|----------------------------------|
| โวร่า หยุด                 | stop                            |
| โวร่า เดินหน้า             | move forward 20cm               |
| โวร่า เดินหน้า 50 เซน      | move forward 50cm               |
| โวร่า ถอยหลัง              | move backward 20cm              |
| โวร่า ถอยหลัง 30 เซน       | move backward 30cm              |
| โวร่า หันซ้าย              | turn left 30 degrees            |
| โวร่า หันซ้าย 90 องศา      | turn left 90 degrees            |
| โวร่า เลี้ยวขวา 45 องศา    | turn right 45 degrees           |

Wake Words ที่รองรับ:
- โวร่า, โวรา, vora
- วอล่า, โวล่า, โอร่า
- ฮัลโหล (Hello)


================================================================================
                    🔧 TROUBLESHOOTING
================================================================================

1. Gateway ไม่เชื่อมต่อ Server:
   - ตรวจสอบ Tailscale: tailscale status
   - ตรวจสอบ VORA Server: curl https://user.tail87d9fe.ts.net/health

2. Robot ไม่ขยับ:
   - ตรวจสอบ ROSBridge: ros2 topic list
   - ตรวจสอบ /cmd_vel: ros2 topic echo /cmd_vel

3. Microphone ไม่ทำงาน:
   - ตรวจสอบ ALSA: arecord -l
   - ทดสอบบันทึกเสียง: arecord -d 5 test.wav && aplay test.wav

4. Wake word ไม่ทำงาน:
   - พูดชัดๆ: "โวร่า เดินหน้า"
   - ดู log ใน Gateway terminal


================================================================================
                              END
================================================================================
