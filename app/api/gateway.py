# gateway.py
import os, asyncio, json
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import websockets, httpx
from dotenv import load_dotenv
load_dotenv()

SERVER_WS = os.getenv("SERVER_WS", "ws://<SERVER_IP>:8000/ws/stt")  # ของโปรเจกต์บน server
app = FastAPI(title="Notebook Gateway")

# ========== AUDIO UPSTREAM: myAGV -> Notebook -> Server ==========
@app.websocket("/ws/audio")
async def ws_audio(websocket: WebSocket, robot_id: str, sr: int = 16000):
    await websocket.accept()
    server_url = f"{SERVER_WS}?robot_id={robot_id}&sr={sr}"
    try:
        async with websockets.connect(server_url, ping_interval=None) as sws:
            await sws.send(json.dumps({"type":"hello","robot_id":robot_id,"sr":sr}))
            async def up():
                while True:
                    data = await websocket.receive()
                    if data.get("bytes") is not None:
                        await sws.send(data["bytes"])
                    elif data.get("text") is not None:
                        await sws.send(data["text"])
            async def down():
                async for msg in sws:
                    # Server อาจส่งข้อความ/partial/final/intent/cmd_vel กลับมาทาง WS นี้
                    # ถ้าต้องการ log ก็ส่งต่อให้ client ด้วย:
                    await websocket.send_text(msg)
            await asyncio.gather(up(), down())
    except WebSocketDisconnect:
        pass
    except Exception:
        await asyncio.sleep(1)

# ========== CMD DOWNSTREAM: Notebook -> myAGV ==========
# เก็บ connection ของ myAGV ที่มารอรับคำสั่ง
CMD_PEERS = {}  # robot_id -> WebSocket

@app.websocket("/ws/cmd")
async def ws_cmd(websocket: WebSocket, robot_id: str):
    await websocket.accept()
    CMD_PEERS[robot_id] = websocket
    try:
        while True:
            # โดยปกติ myAGV จะไม่ส่งอะไรขึ้นมา นอกจาก hello/heartbeat
            _ = await websocket.receive_text()
    except WebSocketDisconnect:
        CMD_PEERS.pop(robot_id, None)

# Helper: เรียกใช้จากโค้ดอื่นเพื่อส่งคำสั่งลงหุ่น
async def send_cmd(robot_id: str, cmd: dict):
    ws = CMD_PEERS.get(robot_id)
    if ws:
        await ws.send_text(json.dumps({"cmd_vel": cmd}))

# ตัวอย่าง endpoint สำหรับทดสอบสั่งหุ่นจาก Notebook เอง (ไม่ผ่าน Server)
from fastapi import Body
@app.post("/test/move_forward")
async def move_forward(robot_id: str = "myagv-jetson", duration_s: float = 0.4, speed: float = 0.05):
    cmd = {"linear_x": speed, "angular_z": 0.0, "duration_s": duration_s}
    await send_cmd(robot_id, cmd)
    return {"ok": True, "sent": cmd}
