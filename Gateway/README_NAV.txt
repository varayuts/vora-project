VORA/gateway_nav — Notebook (Gateway + Planner Executor)
=======================================================
บทบาท: รับเสียงจาก myAGV ผ่าน WS → ส่งต่อไป Server /ws/stt →
เมื่อได้ final transcript → เรียก /plan/plan_from_text → ส่ง goals เข้า ROS ผ่าน rosbridge
ถ้าไม่มี waypoints → fallback เป็น intent_parser (เดิน/ถอย/หัน/หยุด)

วิธีรัน
1) ตั้งค่า .env (ตัวอย่าง):
   SERVER_BASE=http://100.102.217.45:8000
   SERVER_WS=ws://100.102.217.45:8000/ws/stt?lang=th
   ROSBRIDGE=ws://192.168.0.22:9090
   CMD_VEL=/robot3/cmd_vel
   GOAL_FRAME=map
   USE_ACTION=0         # 1=ใช้ /move_base action, 0=ใช้ /move_base_simple/goal

2) ติดตั้งไลบรารี
   cd gateway
   pip install -r requirements.txt

3) รัน
   uvicorn main:app --host 0.0.0.0 --port 9001 --reload


