import os, asyncio, json, re, logging
from typing import Optional, List, Dict
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import httpx
import websockets

from gateway.audio_proxy import upstream_stt_proxy
from gateway.intent_parser import parse_intent
from gateway.ros_cmd import MotionPublisher, WaypointSender, ensure_ros, GOAL_FRAME, USE_ACTION

# Setup logging with colors
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger("gateway")

load_dotenv()
SERVER_BASE = os.getenv("SERVER_BASE", "https://user.tail87d9fe.ts.net")
SERVER_WS = os.getenv("SERVER_WS", "wss://user.tail87d9fe.ts.net/ws/stt")
ROSBRIDGE = os.getenv("ROSBRIDGE", "ws://192.168.0.111:9090")
CMD_VEL = os.getenv("CMD_VEL", "/cmd_vel")
DEBUG = os.getenv("DEBUG", "1") == "1"
MOCK_ROBOT = os.getenv("MOCK_ROBOT", "0") == "1"  # ถ้าเปิด จะไม่ส่งคำสั่งไปหุ่นจริง

logger.info("═" * 60)
logger.info("🚀 VORA GATEWAY STARTING")
logger.info("═" * 60)
logger.info(f"📡 SERVER_BASE:  {SERVER_BASE}")
logger.info(f"🔌 SERVER_WS:    {SERVER_WS}")
logger.info(f"🤖 ROSBRIDGE:    {ROSBRIDGE}")
logger.info(f"🎮 CMD_VEL:      {CMD_VEL}")
logger.info(f"🐛 DEBUG:        {DEBUG}")
logger.info(f"🧪 MOCK_ROBOT:   {MOCK_ROBOT}")
logger.info("═" * 60)

# --- 1. Wake Words Configuration ---
# เพิ่มคำเพี้ยนต่างๆ ที่ Whisper อาจฟังผิดเป็นชื่อหุ่น + คำนำหน้าทั่วไป
WAKE_WORDS = [
    r"vora", r"โวร่า", r"โวรา", 
    r"วัวล่า", r"วอล่า", r"โบรา", 
    r"โรล่า", r"โวล่า", r"โอร่า", r"โอรา",
    r"วอร์รา", r"โมว",
    r"ล่า", r"ล่ะ", r"ฮัลโหล"  # เพิ่มคำกลุ่มนี้เพื่อให้ดักจับง่ายขึ้น
]

def extract_after_wake_word(t: str):
    """
    ตรวจสอบว่าประโยค *มี* Wake Word หรือไม่ (อนุญาตให้มีคำนำหน้าได้)
    เช่น "ฮัลโหล โวร่า ช่วยหน่อย" -> คืนค่า "ช่วยหน่อย"
    รองรับ: "โวร่าหยุด" (ไม่มีช่องว่าง) ด้วย
    """
    if not t:
        return None
    t = t.strip()
    
    for w in WAKE_WORDS:
        # Pattern 1: มีช่องว่างหรือเครื่องหมายคั่น
        # เช่น "โวร่า เดินหน้า", "โวร่า, หยุด"
        pattern1 = rf"(.*)({w})[\s,.:;-]+(.+)$"
        m = re.search(pattern1, t, flags=re.IGNORECASE)
        if m:
            cmd = (m.group(3) or "").strip()
            if cmd:
                logger.debug(f"Wake word (pattern1): '{m.group(2)}' → Command: '{cmd}'")
                return cmd
        
        # Pattern 2: ไม่มีช่องว่าง (ติดกัน) 
        # เช่น "โวร่าหยุด", "โวร่าเดินหน้า"
        pattern2 = rf"(.*)({w})([ก-๙a-zA-Z].*)$"
        m = re.search(pattern2, t, flags=re.IGNORECASE)
        if m:
            cmd = (m.group(3) or "").strip()
            if cmd:
                logger.debug(f"Wake word (pattern2): '{m.group(2)}' → Command: '{cmd}'")
                return cmd
    
    logger.debug(f"No wake word found in: '{t}'")
    return None

# --- 2. Connection Manager สำหรับ Dashboard ---
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        # ส่งข้อความไปหาทุก Web App (Dashboard) ที่เปิดอยู่
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except:
                pass

manager = ConnectionManager()

# --- Gateway → Server Connection ---
server_gateway_ws: Optional[websockets.WebSocketClientProtocol] = None
ROBOT_ID = os.getenv("ROBOT_ID", "myagv")

async def connect_to_server():
    """🔌 Connect Gateway to Server (/pipeline/gateway) to receive commands"""
    global server_gateway_ws
    
    # แปลง https → wss
    server_ws_url = SERVER_BASE.replace("https://", "wss://").replace("http://", "ws://")
    gateway_endpoint = f"{server_ws_url}/pipeline/gateway"
    
    logger.info(f"🔌 Connecting to Server Gateway: {gateway_endpoint}")
    
    while True:
        try:
            async with websockets.connect(gateway_endpoint, max_size=8*1024*1024) as ws:
                server_gateway_ws = ws
                
                # Register this Gateway as a Robot
                await ws.send(json.dumps({
                    "type": "register",
                    "robot_id": ROBOT_ID
                }))
                
                # Wait for confirmation
                response = await ws.recv()
                data = json.loads(response)
                if data.get("type") == "registered":
                    logger.info(f"✅ Gateway registered as Robot: {data.get('robot_id')}")
                
                # Listen for commands from Server
                while True:
                    message = await ws.recv()
                    data = json.loads(message)
                    
                    if data.get("type") == "command":
                        cmd = data.get("cmd")
                        params = data.get("params", {})
                        logger.info(f"📥 Command from Server: {cmd} {params}")
                        
                        # Execute command
                        await execute_server_command(cmd, params)
                        
        except Exception as e:
            logger.error(f"❌ Server Gateway connection error: {e}")
            server_gateway_ws = None
            await asyncio.sleep(5)  # Retry after 5s


async def execute_server_command(cmd: str, params: dict):
    """Execute command received from Server"""
    logger.info(f"🎮 Executing: {cmd} with {params}")
    
    try:
        if cmd == "move":
            # Convert to motion command format
            direction = params.get("direction", "forward")
            distance = params.get("distance", 1)
            
            motion_map = {
                "forward": {"type": "move", "linear_x": 0.1, "angular_z": 0.0, "duration": 2.0},
                "backward": {"type": "move", "linear_x": -0.1, "angular_z": 0.0, "duration": 2.0},
            }
            
            motion_cmd = motion_map.get(direction)
            if motion_cmd and not MOCK_ROBOT:
                await motion.exec_motion(motion_cmd)
                logger.info(f"✅ Motion executed: {direction}")
            else:
                logger.info(f"🤖 [MOCK] Would move {direction}")
                
        elif cmd == "rotate":
            angle = params.get("angle", 90)
            
            # Elephant myAGV 2023 specs:
            # - Angular velocity: 0.3 rad/s (ช้าแต่แม่นกว่า)
            # - Formula: duration = angle_rad / angular_z
            import math
            angle_rad = abs(angle) * (math.pi / 180)  # Convert degrees to radians
            angular_z = 0.3  # rad/s (optimized for myAGV)
            duration = angle_rad / angular_z
            
            motion_cmd = {
                "type": "move",
                "linear_x": 0.0,
                "angular_z": angular_z if angle > 0 else -angular_z,
                "duration": duration
            }
            
            logger.info(f"🔄 Rotate {angle}° = {angle_rad:.2f} rad, duration={duration:.2f}s @ {angular_z} rad/s")
            
            if not MOCK_ROBOT:
                await motion.exec_motion(motion_cmd)
                logger.info(f"✅ Rotated: {angle} degrees")
            else:
                logger.info(f"🤖 [MOCK] Would rotate {angle} degrees")
                
        elif cmd == "stop":
            motion_cmd = {"type": "move", "linear_x": 0.0, "angular_z": 0.0, "duration": 0.1}
            if not MOCK_ROBOT:
                await motion.exec_motion(motion_cmd)
                logger.info("✅ Stopped")
            else:
                logger.info("🤖 [MOCK] Would stop")
                
        elif cmd == "speak":
            text = params.get("text", "")
            logger.info(f"🔊 [TTS] {text}")
            # TODO: Add TTS playback on robot
            
        elif cmd == "goto":
            target = params.get("target", "")
            logger.info(f"📍 [GOTO] {target}")
            # TODO: Add waypoint navigation
            
        else:
            logger.warning(f"⚠️ Unknown command: {cmd}")
            
    except Exception as e:
        logger.error(f"❌ Command execution error: {e}")

# ---------------------------------------

app = FastAPI(title="VORA Gateway (planner + waypoints)")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

motion = MotionPublisher(rosbridge_url=ROSBRIDGE, cmd_vel_topic=CMD_VEL)
_way_sender = WaypointSender()

@app.on_event("startup")
async def startup_event():
    """Start Gateway → Server connection when app starts"""
    asyncio.create_task(connect_to_server())
    logger.info("🚀 Gateway startup: Server connection task started")

@app.get("/")
async def root():
    return {"app": "VORA Gateway", "status": "running"}

@app.get("/health")
async def health():
    # ทดสอบ connection ไปยัง Server
    server_ok = False
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{SERVER_BASE}/health")
            server_ok = r.status_code == 200
    except:
        pass
    
    return {
        "status": "ok",
        "server_base": SERVER_BASE,
        "server_ws": SERVER_WS,
        "server_connected": server_ok,
        "rosbridge": ROSBRIDGE,
        "cmd_vel": CMD_VEL,
        "goal_frame": GOAL_FRAME,
        "use_action": USE_ACTION,
        "mock_robot": MOCK_ROBOT,
        "debug": DEBUG
    }

@app.get("/test/server")
async def test_server():
    """ทดสอบ connection ไปยัง VORA Server"""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{SERVER_BASE}/health")
            return {"ok": True, "status": r.status_code, "data": r.json()}
    except Exception as e:
        return {"ok": False, "error": str(e)}

from pydantic import BaseModel

class TestIntentRequest(BaseModel):
    text: str

@app.post("/test/intent")
async def test_intent(req: TestIntentRequest):
    """ทดสอบ intent parser โดยไม่ส่งคำสั่งไปหุ่นจริง"""
    text = req.text
    cmd = extract_after_wake_word(text)
    if cmd is None:
        return {"wake_word_found": False, "raw_text": text}
    
    motion_cmd = parse_intent(cmd)
    return {
        "wake_word_found": True,
        "command_after_wake": cmd,
        "motion_intent": motion_cmd,
        "raw_text": text
    }

async def try_plan_and_execute(text: str, lang: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            # ยิงไปหา LLM Planner ที่ Server
            r = await client.post(f"{SERVER_BASE}/plan/plan_from_text", json={"text": text, "lang": lang, "max_waypoints": 8})
        
        if r.status_code != 200:
            return False
        
        data = r.json()
        wps: List[Dict] = data.get("waypoints", [])
        
        if not wps:
            return False
            
        print(f"--- Planner Executing: {len(wps)} waypoints ---")
    except Exception as e:
        print(f"Planner Error: {e}")
        return False

    ros = await ensure_ros(ROSBRIDGE)
    if USE_ACTION:
        await _way_sender.send_via_action(ros, wps)
    else:
        await _way_sender.send_via_topic(ros, wps)
    return True

@app.websocket("/gw/audio")
async def gw_audio(ws: WebSocket):
    await ws.accept()
    try:
        cfg = json.loads(await ws.receive_text())
        rate = int(cfg.get("rate", 16000))
        lang = cfg.get("lang", "th")
    except Exception:
        await ws.close(code=1002)
        return

    async def on_server_text(msg: str):
        try:
            # 1. Debug Log: ดูข้อความที่ Server ส่งกลับมา
            if DEBUG:
                logger.debug(f"[SERVER → GATEWAY]: {msg[:100]}...")
            
            # 2. ส่งกลับไปหา Client (ReSpeaker/Robot) เพื่อให้ loop ไม่ขาด
            await ws.send_text(msg)
            
            # 3. Broadcast ไปแสดงผลที่หน้า Web App (Dashboard)
            await manager.broadcast(msg)

            data = json.loads(msg)
        except json.JSONDecodeError as e:
            logger.error(f"❌ Invalid JSON from server: {e}")
            return
        except Exception as e:
            logger.error(f"❌ Error in on_server_text: {e}")
            return
        
        kind = (data.get("type") or data.get("event") or "").lower()
        text = data.get("text") or data.get("final") or ""
        
        # เฉพาะเมื่อเป็น Final Result (จบประโยค) ถึงจะเริ่มประมวลผลคำสั่ง
        if kind in ["final","result","transcript_final"] and text:
            logger.info(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            logger.info(f"📝 RAW TRANSCRIPT: '{text}'")

            # --- เช็ค Wake Word ---
            cmd = extract_after_wake_word(text)
            
            if cmd is None:
                # ไม่เจอคำว่า VORA หรือคำสั่งหลังชื่อว่างเปล่า -> ไม่ทำอะไร
                logger.info(f"⏭️  No wake word detected - Ignored")
                logger.info(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                return
            
            # ตัดคำว่า VORA ออก เหลือแต่คำสั่งจริง เช่น "เดินไปหน้าห้อง"
            text = cmd 
            logger.info(f"🎯 COMMAND EXTRACTED: '{text}'")
            logger.info(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            # --------------------------------

            # 1. ลองให้ LLM Planner (Server) คิดก่อน (เผื่อเป็นคำสั่งยากๆ เช่น "หาของ")
            if not MOCK_ROBOT:
                logger.info(f"🧠 Trying LLM Planner...")
                if await try_plan_and_execute(text, lang):
                    logger.info("✅ Executed via LLM Planner")
                    return
                else:
                    logger.info("⏭️  LLM Planner: No plan generated")
            else:
                logger.info(f"🤖 [MOCK] Would call LLM Planner for: {text}")
            
            # 2. ถ้า LLM ไม่รับ ให้ลองดูว่าเป็นคำสั่งเคลื่อนที่พื้นฐานไหม (Regex)
            logger.info(f"🎮 Trying Motion Parser...")
            motion_cmd = parse_intent(text)
            if motion_cmd:
                logger.info(f"✅ Motion Intent Detected: {motion_cmd}")
                if not MOCK_ROBOT:
                    await motion.exec_motion(motion_cmd)
                    logger.info("✅ Motion command executed")
                else:
                    logger.info(f"🤖 [MOCK] Would execute: {motion_cmd}")
            else:
                logger.warning(f"❓ No matching command found for: '{text}'")
                logger.warning(f"   Try: 'VORA เดินหน้า', 'VORA หยุด', 'VORA หาไขควง'")

    try:
        await upstream_stt_proxy(server_ws=f"{SERVER_WS}", client_ws=ws, upstream_init={"rate": rate, "lang": lang}, on_text=on_server_text)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await ws.send_text(json.dumps({"type":"error","detail":str(e)}))
        except Exception:
            pass
        await ws.close()

# --- 3. Endpoint ใหม่สำหรับ Dashboard ---
@app.websocket("/gw/dashboard")
async def gw_dashboard(ws: WebSocket):
    await manager.connect(ws)
    try:
        while True:
            # รอรับคำสั่งจากปุ่มบนหน้าเว็บ (เช่น Ask LLM Now)
            data = await ws.receive_text()
            print(f"Dashboard Command: {data}")
            # อนาคต: ใส่ logic เพื่อส่ง command ไปบอก server ได้ตรงนี้
    except WebSocketDisconnect:
        manager.disconnect(ws)

# TextIn class สำหรับ /gw/text endpoint
class TextIn(BaseModel):
    text: str
    lang: Optional[str] = "th"

@app.post("/gw/text")
async def gw_text(inp: TextIn):
    """รองรับการยิง Text เข้ามาเทสตรงๆ (ผ่าน Postman/Curl)"""
    text = inp.text or ""
    lang = inp.lang or "th"
    
    # 1. ลอง LLM Planner ก่อน
    if not MOCK_ROBOT:
        if await try_plan_and_execute(text, lang):
            return {"ok": True, "mode": "planner"}
    else:
        logger.info(f"🤖 [MOCK] Would call LLM Planner for: {text}")
    
    # 2. ลอง Motion Parser
    motion_cmd = parse_intent(text)
    if motion_cmd:
        if not MOCK_ROBOT:
            await motion.exec_motion(motion_cmd)
            return {"ok": True, "mode": "motion", "motion": motion_cmd}
        else:
            logger.info(f"🤖 [MOCK] Would execute motion: {motion_cmd}")
            return {"ok": True, "mode": "motion", "motion": motion_cmd, "mock": True}
    
    return {"ok": False, "reason": "no_motion_or_plan"}