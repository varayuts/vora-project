import os, asyncio, json, re, logging
from typing import Optional, List, Dict
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import Response, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import httpx
import websockets

from gateway.audio_proxy import upstream_stt_proxy
from gateway.intent_parser import (
    parse_intent, parse_find_intent, parse_multi_intent, 
    normalize_search_target, parse_find_multi_objects, parse_find_with_description
)
from gateway.ros_cmd import MotionPublisher, WaypointSender, ensure_ros, GOAL_FRAME, USE_ACTION
from gateway.obstacle_avoidance import ObstacleAvoidance
from gateway.camera_stream import get_camera, start_camera, stop_camera
from gateway.object_memory import object_memory

# Setup logging with colors
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger("gateway")

# Suppress verbose httpx request logging (floods console during camera push)
logging.getLogger("httpx").setLevel(logging.WARNING)

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
    # คำเพี้ยนที่ STT มักฟังผิด (จาก logs จริง)
    r"วอร่า",    # "วอร่าตรงไปข้างหน้า" ← very common
    r"วอลล่า",   # "วอลล่าเลี้ยวซ้าย" (double ล)
    r"งอล่า",   # "งอล่า เลี้ยวซ้าย"
    r"กัวล่า",   # "กัวล่าเลี้ยวขวา"
    r"โหล่า",    # "ฮัลโหล่า" → ตัด โหล่า
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


# ============ TTS Helper (Call Server gTTS) ============

async def speak_tts(text: str, play_on_robot: bool = True) -> bool:
    """
    Speak Thai text via Server's gTTS service.
    
    1. POST to /server/tts/speak to get WAV audio
    2. Optionally play on robot (future: send to MyAGV speaker)
    3. Broadcast to webapp for frontend playback
    
    Returns: True if successful
    """
    if not text or not text.strip():
        return False
    
    tts_url = f"{SERVER_BASE}/api/server/tts/speak"
    
    try:
        logger.info(f"🔊 TTS: '{text[:40]}...'")
        
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(tts_url, json={"text": text, "voice": "default", "speed": 1.0})
        
        if resp.status_code != 200:
            logger.warning(f"⚠️ TTS failed: HTTP {resp.status_code}")
            return False
        
        # Get WAV bytes
        wav_bytes = resp.content
        
        # Broadcast to webapp (base64 encoded for JSON transport)
        import base64
        wav_b64 = base64.b64encode(wav_bytes).decode()
        
        await manager.broadcast(json.dumps({
            "type": "tts_audio",
            "text": text,
            "audio_b64": wav_b64,
            "format": "wav",
        }))
        
        logger.info(f"✅ TTS broadcast ({len(wav_bytes)} bytes)")
        return True
        
    except Exception as e:
        logger.error(f"❌ TTS error: {e}")
        return False


# ============ Visual Search (Find Object) ============
# State: allows cancellation via "หยุด" command during search
_search_active = False
_search_cancel = False
_last_search_result: Optional[Dict] = None   # cached for polling from webapp (mixed-content WS fallback)


async def _broadcast_and_cache(msg_dict: dict):
    """Broadcast search_status to local dashboard WS AND cache for HTTP polling.
    Also forward to Server pipeline WS so webapp (on HTTPS) can get events."""
    global _last_search_result
    _last_search_result = msg_dict
    msg_str = json.dumps(msg_dict)
    await manager.broadcast(msg_str)
    # Forward via Server pipeline WS (webapp connects to Server, not Gateway WS)
    if server_gateway_ws:
        try:
            await server_gateway_ws.send(msg_str)
        except Exception:
            pass

async def cancel_search():
    """Cancel any active visual search"""
    global _search_cancel
    if _search_active:
        _search_cancel = True
        logger.info("🛑 Visual search cancel requested")

async def visual_search(
    target_object: str,
    max_move_cycles: int = 4,            # max move-then-scan cycles
    move_duration: float = 2.5,          # seconds to move forward per step
    move_speed: float = 0.10,            # m/s (slow and safe)
    vlm_timeout: float = 60.0,           # seconds for VLM call
    scan_directions: int = 4,            # 4 directions = 360° (90° × 4)
):
    """
    🔍 Visual Search — Rotate-Scan-First Strategy
    
    New improved flow (much faster than old move-first approach):
    1. Announce "กำลังค้นหา {target}..."
    2. Phase 0: Check current frame (maybe visible already)
    3. Phase 1: 360° IN-PLACE SCAN — rotate 90° × 3 more times, check each direction
       (Covers all 4 directions without moving — finds nearby objects fast)
    4. Phase 2: Move forward, then do another 360° scan — repeat up to max_move_cycles
    5. Give up if not found after all cycles
    
    This is much better than the old approach because:
    - Scans all directions BEFORE wasting time moving forward
    - Finds objects that are behind/beside the robot immediately
    - Each 360° scan = 4 VLM checks (~80s), but covers every angle
    """
    global _search_active, _search_cancel
    _search_active = True
    _search_cancel = False
    
    total_checks = 0
    import math
    SCAN_ROTATION_CAL = 0.87  # Match ROTATION_CALIBRATION
    
    logger.info(f"═" * 50)
    logger.info(f"🔍 VISUAL SEARCH START: '{target_object}'")
    logger.info(f"   Strategy: Rotate-Scan-First (360° scan → move → repeat)")
    logger.info(f"   max_move_cycles={max_move_cycles}, scan_dirs={scan_directions}")
    logger.info(f"   Pipeline: VLM Describe → LLM Reasoning")
    logger.info(f"═" * 50)
    
    # Normalize target name to official item if possible
    normalized_target = normalize_search_target(target_object)
    
    # Check memory for previous location
    memory_hint = object_memory.get_search_hint(normalized_target)
    if memory_hint:
        logger.info(f"📚 Memory hint: {memory_hint}")
    
    # Broadcast search start to dashboard + cache for polling
    await _broadcast_and_cache({
        "type": "search_status",
        "status": "started",
        "target": target_object,
        "normalized_target": normalized_target,
        "memory_hint": memory_hint,
    })
    
    # 🔊 TTS: Announce search start + memory hint
    announce = f"กำลังค้นหา {target_object} ครับ"
    if memory_hint:
        announce += f" {memory_hint}"
    await speak_tts(announce)
    
    stale_count = 0  # track consecutive stale/empty VLM checks
    
    async def _do_vlm_and_check(phase_label: str) -> bool:
        """Helper: run VLM check, handle stale frames, broadcast progress. Returns True if found."""
        nonlocal total_checks, stale_count
        
        found, location, description = await _vlm_check(target_object, vlm_timeout)
        total_checks += 1
        
        # Track stale frames — abort if camera is dead
        if not description:
            stale_count += 1
            logger.warning(f"⚠️ Empty VLM result ({stale_count} consecutive) — camera may be dead")
            if stale_count >= 3:
                logger.error(f"🛑 Aborting search: {stale_count} consecutive empty VLM checks (camera dead?)")
                await speak_tts("กล้องขัดข้อง ไม่สามารถค้นหาต่อได้ครับ")
                await _search_not_found(target_object, total_checks)
                return True  # Return True to exit (handled)
        else:
            stale_count = 0
        
        # Broadcast progress
        await _broadcast_and_cache({
            "type": "search_status",
            "status": "scanning",
            "target": target_object,
            "phase": phase_label,
            "total_checks": total_checks,
        })
        
        if found:
            logger.info(f"🎉 Object found! ({phase_label}) Location: {location}")
            await _search_found(target_object, location, description)
            return True
        
        return False
    
    async def _rotate_90():
        """Rotate 90° with calibration applied."""
        if not MOCK_ROBOT:
            angle_rad = math.radians(90)
            duration = (angle_rad / 0.50) * SCAN_ROTATION_CAL
            rotate_cmd = {
                "type": "move",
                "linear_x": 0.0,
                "angular_z": 0.50,  # rad/s (positive = turn left)
                "duration": duration,
            }
            logger.info(f"🔄 Rotating 90° (duration={duration:.2f}s, cal={SCAN_ROTATION_CAL})")
            await motion.exec_motion(rotate_cmd)
        else:
            logger.info(f"🤖 [MOCK] Would rotate 90°")
            await asyncio.sleep(0.5)
        await asyncio.sleep(0.5)  # Let camera frame settle
    
    try:
        # === Phase 0: Check current frame BEFORE anything ===
        logger.info(f"👁️ Phase 0: Checking current camera frame...")
        if await _do_vlm_and_check("phase0_current"):
            return
        if _search_cancel:
            await _search_cancelled(target_object)
            return
        
        # === Phase 1: 360° IN-PLACE SCAN ===
        # Already checked direction 0 (current), now rotate 90° and check 3 more directions
        logger.info(f"🔄 Phase 1: 360° in-place scan ({scan_directions - 1} more directions)...")
        await speak_tts("กำลังมองรอบๆ ครับ")
        
        for d in range(1, scan_directions):
            if _search_cancel:
                await _search_cancelled(target_object)
                return
            
            logger.info(f"🔄 Scan direction {d + 1}/{scan_directions}: Rotating 90°...")
            await _rotate_90()
            
            if await _do_vlm_and_check(f"phase1_scan_dir{d + 1}"):
                return
            if _search_cancel:
                await _search_cancelled(target_object)
                return
        
        # === Phase 2: Move forward + 360° scan cycles ===
        for cycle in range(max_move_cycles):
            if _search_cancel:
                await _search_cancelled(target_object)
                return
            
            logger.info(f"🚶 Phase 2 cycle {cycle + 1}/{max_move_cycles}: Moving forward {move_duration}s...")
            
            # Check for obstacles before moving
            if not MOCK_ROBOT:
                obs = await _obstacle_avoidance.check_and_avoid()
                if obs and obs.get("obstacle_detected"):
                    dist = obs.get("distance", 0)
                    strategy = obs.get("strategy", "stop")
                    obs_type = obs.get("obstacle_type", "สิ่งกีดขวาง")
                    
                    logger.warning(f"🚧 Obstacle detected during search! Distance: {dist:.2f}m")
                    await speak_tts(f"เจอ{obs_type}ข้างหน้า กำลังหลบครับ")
                    
                    backup_cmd = {
                        "type": "move",
                        "linear_x": -0.10,
                        "angular_z": 0.0,
                        "duration": 1.0,
                    }
                    await motion.exec_motion(backup_cmd)
                    await _obstacle_avoidance.execute_avoidance(strategy)
                    await speak_tts("หลบสิ่งกีดขวางเสร็จแล้ว กำลังค้นหาต่อครับ")
                    
                    await manager.broadcast(json.dumps({
                        "type": "search_obstacle",
                        "target": target_object,
                        "obstacle_type": obs_type,
                        "distance": dist,
                        "strategy": strategy,
                    }))
                    continue  # Skip this move, re-loop
            
            # Move forward
            if not MOCK_ROBOT:
                move_cmd = {
                    "type": "move",
                    "linear_x": move_speed,
                    "angular_z": 0.0,
                    "duration": move_duration,
                }
                await motion.exec_motion(move_cmd)
            else:
                logger.info(f"🤖 [MOCK] Would move forward {move_duration}s")
                await asyncio.sleep(0.5)
            
            if _search_cancel:
                await _search_cancelled(target_object)
                return
            
            await asyncio.sleep(0.5)
            
            # Check current direction after moving
            logger.info(f"👁️ Checking after move (cycle {cycle + 1})...")
            if await _do_vlm_and_check(f"phase2_cycle{cycle + 1}_fwd"):
                return
            if _search_cancel:
                await _search_cancelled(target_object)
                return
            
            # Mini 360° scan: rotate and check 3 more directions
            for d in range(1, scan_directions):
                if _search_cancel:
                    await _search_cancelled(target_object)
                    return
                
                logger.info(f"🔄 Cycle {cycle + 1} scan dir {d + 1}/{scan_directions}")
                await _rotate_90()
                
                if await _do_vlm_and_check(f"phase2_cycle{cycle + 1}_dir{d + 1}"):
                    return
                if _search_cancel:
                    await _search_cancelled(target_object)
                    return
        
        # === Phase 3: Not found ===
        logger.warning(f"😔 Object '{target_object}' not found after {total_checks} VLM checks")
        await _search_not_found(target_object, total_checks)
        
    except Exception as e:
        logger.error(f"❌ Visual search error: {e}")
        await _broadcast_and_cache({
            "type": "search_status",
            "status": "error",
            "target": target_object,
            "error": str(e),
        })
    finally:
        _search_active = False
        _search_cancel = False


async def _vlm_check(target: str, timeout: float) -> tuple:
    """
    🧠 VLM Describe + LLM Reasoning Pipeline  (task-triggered, 1 frame per action)

    Step 1: Capture the CURRENT frame from local CameraStream (fresh, right after action)
    Step 2: POST raw JPEG bytes directly to /vlm/describe-bytes on Server
            → no stale cached frame; frame is tied to this exact moment
    Step 3: LLM reasons about the description to decide if target is present (/generate)

    Returns (found: bool, location: str, description: str)
    """
    llm_url = f"{SERVER_BASE}/generate"

    # ─── Step 0: Get fresh frame from local camera ───
    cam = get_camera()
    frame = cam.get_frame() if cam else None
    if not frame:
        logger.warning("⚠️ No local camera frame — cannot run VLM check")
        return False, "", ""

    # ─── Check frame freshness — reject stale frames ───
    # If camera died (USB disconnect), get_frame() returns the last cached frame.
    # VLM analyzing a stale frame = false positives (hallucination).
    frame_age = cam.get_frame_age()
    MAX_FRAME_AGE = 5.0  # seconds — frame older than this is stale
    if frame_age > MAX_FRAME_AGE:
        logger.warning(
            f"⚠️ Frame too old ({frame_age:.1f}s > {MAX_FRAME_AGE}s) — camera may be dead. "
            f"Skipping VLM check to avoid false positive."
        )
        return False, "", ""

    # ─── Resize frame to 480x360 for balanced speed/quality ───
    # 640x480 (~80KB) is too large for Qwen3-VL (takes >30s).
    # 320x240 is too small — misses floor objects like keys.
    # 480x360 is a good balance: ~25-35KB, keeps small objects visible.
    try:
        import io as _io
        from PIL import Image as _Image
        _img = _Image.open(_io.BytesIO(frame))
        _img = _img.resize((480, 360), _Image.LANCZOS)
        _buf = _io.BytesIO()
        _img.save(_buf, format="JPEG", quality=80)
        frame_small = _buf.getvalue()
        logger.info(f"📷 VLM frame: {len(frame)}→{len(frame_small)} bytes (480x360), age={frame_age:.1f}s")
    except Exception as _e:
        logger.warning(f"⚠️ Frame resize failed ({_e}), using original")
        frame_small = frame

    try:
        # ─── Step 1: VLM Describe (send resized frame directly) ───
        logger.info(f"👁️ VLM Describe: sending fresh frame to server...")

        # Normalize the target name for the VLM prompt — strip grammar/sentences,
        # keep only the object name (e.g., "กุญแจให้หน่อยครับ เริ่มจาก..." → "กุญแจ")
        vlm_target = normalize_search_target(target)

        # Target-aware prompt: tells VLM specifically what to search for.
        # Keep prompt SHORT and DIRECT — long/complex prompts trigger Qwen3-VL chain-of-thought.
        # Do NOT mention "thinking" or "ห้ามคิด" — it paradoxically triggers more thinking.
        # Server-side qwen_vlm.py appends /no_think automatically.
        vlm_prompt = (
            f"ภาพนี้มีอะไรบ้าง? มี '{vlm_target}' ไหม? "
            f"บอกวัตถุและตำแหน่ง (ซ้าย/ขวา/กลาง/บน/ล่าง) ตอบภาษาไทยสั้นๆ"
        )

        vlm_url = f"{SERVER_BASE}/vlm/describe-bytes"
        async with httpx.AsyncClient(timeout=timeout) as client:
            vlm_resp = await client.post(
                vlm_url,
                content=frame_small,
                headers={"Content-Type": "image/jpeg"},
                params={"prompt": vlm_prompt, "lang": "th", "max_tokens": "300"},
            )

        if vlm_resp.status_code != 200:
            logger.warning(f"⚠️ VLM Describe failed: HTTP {vlm_resp.status_code} — body: {vlm_resp.text[:200]}")
            return False, "", ""
        
        vlm_data = vlm_resp.json()
        scene_description = vlm_data.get("text", "")
        logger.info(f"📨 VLM raw response keys: {list(vlm_data.keys())}, text_len={len(scene_description)}, error={vlm_data.get('error','none')}")
        
        if not scene_description:
            # Log full response body to diagnose
            logger.warning(f"⚠️ VLM returned empty description — full response: {vlm_resp.text[:400]}")
            return False, "", ""
        
        logger.info(f"👁️ VLM Scene: {scene_description[:120]}...")
        
        # ─── Step 2: LLM Reasoning ───
        # Use full target context for reasoning — user's full description gives
        # important hints (e.g. "กุญแจที่วางบนกระดาษสีขาว" = look for key ON white paper)
        # Also provide normalized English name as a reference for multilingual matching
        llm_target_display = target
        if vlm_target and vlm_target != target:
            llm_target_display = f"{target} (English: {vlm_target})"
        
        logger.info(f"🧠 LLM Reasoning: Is '{llm_target_display}' in the scene?")
        
        llm_system = (
            "คุณเป็น AI Reasoning Engine ของหุ่นยนต์ VORA ที่ช่วยวิเคราะห์ว่าวัตถุเป้าหมายอยู่ในภาพหรือไม่ "
            "คุณจะได้รับคำอธิบายฉากจาก VLM และชื่อวัตถุเป้าหมาย "
            "กฎสำคัญ: ตอบ found=true เฉพาะเมื่อคำอธิบายระบุชื่อวัตถุเป้าหมาย (หรือชื่ออื่นของมัน) ไว้อย่างชัดเจนเท่านั้น "
            "คำอธิบายอาจเป็นภาษาไทยหรือภาษาอังกฤษ — ต้องเข้าใจทั้งสองภาษา "
            "ถ้าคำอธิบายไม่ได้กล่าวถึงวัตถุนั้นเลย ให้ตอบ found=false เสมอ "
            "ห้ามเดา ห้ามสมมุติ — ต้องมีหลักฐานจากคำอธิบายเท่านั้น "
            "ตอบเป็น JSON ที่ถูกต้องเท่านั้น ห้ามตอบอย่างอื่นนอกจาก JSON ห้ามมี markdown"
        )
        
        llm_prompt = (
            f"คำอธิบายภาพจากกล้องหุ่นยนต์:\n\"{scene_description}\"\n\n"
            f"สิ่งของที่กำลังหา: \"{target}\"\n"
            f"ชื่อภาษาอังกฤษ: \"{vlm_target}\"\n\n"
            f"วิเคราะห์ว่าวัตถุเป้าหมายอยู่ในภาพหรือไม่ "
            f"คิดถึงชื่อเรียกอื่นด้วย เช่น กุญแจ=key=ดอกกุญแจ, ปากกา=pen, กระเป๋าสตางค์=wallet, ดินสอ=pencil\n"
            f"สำคัญ: คำอธิบายภาพอาจเป็นภาษาอังกฤษ — ให้ matching ข้ามภาษาได้\n\n"
            f"ตอบเป็น JSON เท่านั้น:\n"
            f'{{"found": true/false, "location": "top_left/top_right/top_center/center_left/center/center_right/bottom_left/bottom_right/bottom_center/unknown", '
            f'"reason": "เหตุผลสั้นๆ", "confidence": 0.0-1.0}}'
        )
        
        async with httpx.AsyncClient(timeout=timeout) as client:
            llm_resp = await client.post(llm_url, json={
                "prompt": llm_prompt,
                "system": llm_system,
                "temperature": 0.2,
                "max_tokens": 256,
            })
        
        if llm_resp.status_code != 200:
            logger.warning(f"⚠️ LLM Reasoning failed: HTTP {llm_resp.status_code}")
            # Fallback: simple keyword match in VLM description
            return _fallback_keyword_check(target, scene_description), "unknown", scene_description
        
        llm_data = llm_resp.json()
        llm_text = llm_data.get("response", "")
        
        logger.info(f"🧠 LLM Response: {llm_text[:150]}...")
        
        # ─── Parse LLM JSON Response ───
        found, location, reason, confidence = _parse_llm_reasoning(llm_text)
        
        logger.info(f"   ✅ Result: found={found}, location={location}, confidence={confidence}")
        if reason:
            logger.info(f"   💭 Reason: {reason}")
        
        # ─── Sanity check: if VLM response is very long (>2000 chars), it's likely
        # English chain-of-thought hallucination — be more skeptical ───
        vlm_suspicious = len(scene_description) > 2000
        if vlm_suspicious:
            logger.warning(f"⚠️ VLM response suspiciously long ({len(scene_description)} chars) — likely CoT hallucination")
        
        # Only count as found if confidence is reasonably high
        if found and confidence < 0.7:
            logger.info(f"   ⚠️ Low confidence ({confidence}), treating as not found")
            found = False
        
        # Extra skepticism for long VLM responses
        if found and vlm_suspicious and confidence < 0.9:
            logger.info(f"   ⚠️ VLM was long CoT + confidence < 0.9 ({confidence}), treating as not found")
            found = False
        
        return found, location, scene_description
        
    except httpx.TimeoutException:
        logger.error(f"❌ VLM/LLM check timeout ({timeout}s)")
        return False, "", ""
    except Exception as e:
        logger.error(f"❌ VLM+LLM check error: {e}")
        return False, "", ""


def _parse_llm_reasoning(llm_text: str) -> tuple:
    """Parse LLM JSON response for found/location/reason/confidence.
    Returns (found, location, reason, confidence)"""
    import json as _json
    
    # Try to extract JSON from the response
    # LLM might wrap it in markdown code blocks
    text = llm_text.strip()
    
    # Remove markdown code fences
    if "```json" in text:
        text = text.split("```json", 1)[1]
        text = text.split("```", 1)[0]
    elif "```" in text:
        text = text.split("```", 1)[1]
        text = text.split("```", 1)[0]
    
    # Try to find JSON object in the text
    text = text.strip()
    
    # Find first { and last }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1:
        text = text[start:end+1]
    
    try:
        data = _json.loads(text)
        found = bool(data.get("found", False))
        location = str(data.get("location", "unknown"))
        reason = str(data.get("reason", ""))
        confidence = float(data.get("confidence", 0.5))
        return found, location, reason, confidence
    except (_json.JSONDecodeError, ValueError, TypeError) as e:
        logger.warning(f"⚠️ Failed to parse LLM JSON: {e}, text={llm_text[:100]}")
        # Fallback: look for keywords in raw text
        txt = llm_text.lower()
        found = any(w in txt for w in ["\"found\": true", "\"found\":true", "พบ", "เจอ", "found"])
        not_found = any(w in txt for w in ["\"found\": false", "\"found\":false", "ไม่พบ", "ไม่เจอ", "not found"])
        if not_found:
            found = False
        return found, "unknown", "", 0.5


def _fallback_keyword_check(target: str, description: str) -> bool:
    """Simple keyword matching fallback when LLM reasoning fails."""
    desc_lower = description.lower()
    target_lower = target.lower()
    
    # Direct match
    if target_lower in desc_lower:
        return True
    
    # Common Thai ↔ English mappings for the 5 official items
    mappings = {
        "กุญแจ": ["key", "กุญแจ", "ลูกกุญแจ"],
        "ปากกา": ["pen", "ปากกา", "ปากกาลูกลื่น"],
        "ดินสอ": ["pencil", "ดินสอ"],
        "กระเป๋า": ["wallet", "กระเป๋า", "กระเป๋าสตางค์", "กระเป๋าตังค์"],
        "การ์ด": ["card", "การ์ด", "บัตร", "นามบัตร"],
        "คอยล์": ["coil", "คอยล์", "ขดลวด"],
        "key": ["key", "กุญแจ", "ลูกกุญแจ"],
        "pen": ["pen", "ปากกา"],
        "pencil": ["pencil", "ดินสอ"],
        "wallet": ["wallet", "กระเป๋า", "กระเป๋าสตางค์"],
        "card": ["card", "การ์ด", "บัตร"],
        "coil": ["coil", "คอยล์", "ขดลวด"],
    }
    
    keywords = mappings.get(target_lower, [target_lower])
    return any(kw in desc_lower for kw in keywords)


async def _search_found(target: str, location: str, description: str):
    """Handle object found — announce, APPROACH the object, then confirm arrival."""
    global _search_active
    import math
    
    loc_thai = _location_to_thai(location)
    loc_text = f" อยู่ทาง{loc_thai}" if loc_thai else ""
    announce = f"เจอ{target}แล้วครับ!{loc_text} กำลังเคลื่อนที่เข้าไปครับ"
    
    logger.info(f"═" * 50)
    logger.info(f"🎉 OBJECT DETECTED: '{target}'")
    logger.info(f"   Location: {location} ({loc_thai})")
    logger.info(f"   → Starting APPROACH phase")
    logger.info(f"═" * 50)
    
    # 🔊 TTS: Announce found + approaching
    await speak_tts(announce)
    
    await _broadcast_and_cache({
        "type": "search_status",
        "status": "approaching",
        "target": target,
        "location": location,
        "location_thai": loc_thai,
        "description": description,
    })
    
    # ═══════════════════════════════════════════════════════
    # 🚶 APPROACH PHASE — Navigate toward the detected object
    # ═══════════════════════════════════════════════════════
    MAX_APPROACH_STEPS = 6
    APPROACH_SPEED = 0.10       # m/s (cautious)
    ROTATION_CAL = 0.87         # match ROTATION_CALIBRATION
    current_location = location
    
    for step in range(MAX_APPROACH_STEPS):
        if _search_cancel:
            logger.info("🛑 Approach cancelled")
            break
        
        logger.info(f"🚶 Approach step {step + 1}/{MAX_APPROACH_STEPS}: object at '{current_location}'")
        
        # ── Step A: Turn toward object if off-center ──
        turn_angle = 0
        if "left" in current_location:
            turn_angle = 30     # degrees left
        elif "right" in current_location:
            turn_angle = -30    # degrees right (negative = right)
        
        if turn_angle != 0 and not MOCK_ROBOT:
            direction_text = "ซ้าย" if turn_angle > 0 else "ขวา"
            logger.info(f"   ↪️ Turning {abs(turn_angle)}° {direction_text} toward object")
            angle_rad = abs(turn_angle) * (math.pi / 180)
            duration = (angle_rad / 0.50) * ROTATION_CAL
            turn_cmd = {
                "type": "move",
                "linear_x": 0.0,
                "angular_z": 0.50 if turn_angle > 0 else -0.50,
                "duration": duration,
            }
            await motion.exec_motion(turn_cmd)
            await asyncio.sleep(0.3)
        
        # ── Step B: Decide move distance based on proximity ──
        if "bottom" in current_location or "near" in current_location:
            # Close! Small final step (object is at bottom of frame = close to robot)
            move_dur = 0.8
            logger.info(f"   📍 Object is NEAR (bottom/near) — small step ({move_dur}s)")
        elif "top" in current_location or "far" in current_location:
            # Far away (object at top of frame = far from robot), bigger step
            move_dur = 3.0
            logger.info(f"   📍 Object is FAR (top/far) — bigger step ({move_dur}s)")
        else:
            # Default medium step
            move_dur = 1.8
            logger.info(f"   📍 Moving forward ({move_dur}s)")
        
        # ── Step C: Move forward ──
        if not MOCK_ROBOT:
            move_cmd = {
                "type": "move",
                "linear_x": APPROACH_SPEED,
                "angular_z": 0.0,
                "duration": move_dur,
            }
            await motion.exec_motion(move_cmd)
        else:
            logger.info(f"   🤖 [MOCK] Would move forward {move_dur}s")
            await asyncio.sleep(0.3)
        
        await asyncio.sleep(0.5)  # let camera settle
        
        # Broadcast approach progress
        await _broadcast_and_cache({
            "type": "search_status",
            "status": "approaching",
            "target": target,
            "step": step + 1,
            "max_steps": MAX_APPROACH_STEPS,
            "location": current_location,
        })
        
        # ── Step D: Re-check with VLM — is the object still visible + how close? ──
        found, new_location, new_desc = await _vlm_check(target, 30.0)
        
        if found:
            current_location = new_location
            logger.info(f"   👁️ Still visible at '{new_location}'")
            
            # Check if we're close enough (object is near/bottom + center-ish)
            is_close = any(k in new_location for k in ["near", "bottom"])
            is_center = "center" in new_location or "middle" in new_location or ("left" not in new_location and "right" not in new_location)
            if is_close and is_center:
                logger.info(f"   ✅ Close enough! Object is center/near — stopping approach")
                break
        else:
            # Object not visible anymore — might be directly below camera or passed it
            logger.info(f"   👀 Object no longer visible — likely very close or passed. Stopping.")
            break
    
    # ── Final: Stop and announce arrival ──
    if not MOCK_ROBOT:
        stop_cmd = {"type": "move", "linear_x": 0.0, "angular_z": 0.0, "duration": 0.1}
        await motion.exec_motion(stop_cmd)
    
    _search_active = False
    
    # Final announce
    final_loc_thai = _location_to_thai(current_location)
    final_announce = f"ถึงแล้วครับ {target} อยู่ตรงนี้"
    
    logger.info(f"═" * 50)
    logger.info(f"🏁 APPROACH COMPLETE: Arrived at '{target}'")
    logger.info(f"   Final location: {current_location} ({final_loc_thai})")
    logger.info(f"═" * 50)
    
    await speak_tts(final_announce)
    
    # 📸 Capture image and save for webapp
    capture_url = None
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(f"{SERVER_BASE}/camera/capture")
            if resp.status_code == 200:
                data = resp.json()
                capture_url = data.get("url")
                logger.info(f"📸 Captured: {capture_url}")
    except Exception as e:
        logger.warning(f"⚠️ Capture failed: {e}")
    
    # 📚 Remember location in memory
    normalized = normalize_search_target(target)
    object_memory.remember(
        object_name=normalized,
        display_name=target,
        location=current_location,
        location_description=final_loc_thai,
        confidence=0.9,
        image_url=capture_url,
    )
    
    await _broadcast_and_cache({
        "type": "search_status",
        "status": "found",
        "target": target,
        "location": current_location,
        "location_thai": final_loc_thai,
        "description": description,
        "announce": final_announce,
        "capture_url": capture_url,
    })


def _location_to_thai(location: str) -> str:
    """Convert location code to Thai text."""
    mapping = {
        "left": "ด้านซ้าย",
        "right": "ด้านขวา",
        "center": "ตรงกลาง",
        "middle": "ตรงกลาง",
        "near": "ใกล้ๆ",
        "far": "ไกลออกไป",
        "top_left": "ซ้ายบน",
        "top_right": "ขวาบน",
        "top_center": "ด้านบน",
        "center_left": "ด้านซ้าย",
        "center_right": "ด้านขวา",
        "bottom_left": "ซ้ายล่าง",
        "bottom_right": "ขวาล่าง",
        "bottom_center": "ด้านล่าง",
        "center_near": "ตรงกลาง ใกล้ๆ",
        "left_near": "ด้านซ้าย ใกล้ๆ",
        "right_near": "ด้านขวา ใกล้ๆ",
        "left_far": "ด้านซ้าย ไกล",
        "right_far": "ด้านขวา ไกล",
        "unknown": "",
    }
    return mapping.get(location, "")


async def _search_not_found(target: str, total_checks: int):
    """Handle object not found after full search"""
    global _search_active
    _search_active = False
    
    announce = f"ขออภัยครับ ค้นหา{target}แล้วแต่ยังไม่พบ"
    
    logger.info(f"═" * 50)
    logger.info(f"😔 SEARCH COMPLETE: '{target}' NOT FOUND")
    logger.info(f"   Total VLM checks: {total_checks}")
    
    # 🔊 TTS: Announce not found
    await speak_tts(announce)
    logger.info(f"═" * 50)
    
    await _broadcast_and_cache({
        "type": "search_status",
        "status": "not_found",
        "target": target,
        "total_checks": total_checks,
        "announce": announce,
    })


async def _search_cancelled(target: str):
    """Handle search cancellation"""
    global _search_active, _search_cancel
    _search_active = False
    _search_cancel = False
    
    # Stop the robot
    if not MOCK_ROBOT:
        stop_cmd = {"type": "move", "linear_x": 0.0, "angular_z": 0.0, "duration": 0.1}
        await motion.exec_motion(stop_cmd)
    
    logger.info(f"🛑 SEARCH CANCELLED: '{target}'")
    
    await _broadcast_and_cache({
        "type": "search_status",
        "status": "cancelled",
        "target": target,
    })


async def visual_search_multi(targets: List[str]):
    """
    🔍 Multi-Object Visual Search — search for multiple objects sequentially.
    
    Example: "หาปากกากับดินสอ" → search for pen, then pencil.
    
    Args:
        targets: List of object names to find (e.g., ["ปากกา", "ดินสอ"])
    """
    global _search_active, _search_cancel
    _search_active = True
    _search_cancel = False
    
    found_objects = []
    not_found_objects = []
    
    logger.info(f"═" * 50)
    logger.info(f"🔍 MULTI-OBJECT SEARCH: {targets}")
    logger.info(f"═" * 50)
    
    # Announce start
    targets_text = " กับ ".join(targets)
    await speak_tts(f"กำลังค้นหา {targets_text} ครับ")
    
    await _broadcast_and_cache({
        "type": "search_status",
        "status": "multi_started",
        "targets": targets,
    })
    
    for idx, target in enumerate(targets):
        if _search_cancel:
            await _search_cancelled(targets_text)
            return
        
        logger.info(f"🔍 Searching for object {idx+1}/{len(targets)}: '{target}'")
        await speak_tts(f"กำลังหา {target}")
        
        # Quick check current frame using VLM Describe + LLM Reasoning
        try:
            found, location, description = await _vlm_check(target, 30.0)
            
            if found:
                    # Found!
                    logger.info(f"🎉 Found '{target}' at {location}")
                    found_objects.append({
                        "name": target,
                        "location": location,
                        "description": description,
                    })
                    
                    # Remember in memory
                    normalized = normalize_search_target(target)
                    object_memory.remember(
                        object_name=normalized,
                        display_name=target,
                        location=location,
                        location_description="",
                        confidence=0.8,
                    )
                    
                    await speak_tts(f"เจอ {target} แล้ว")
            else:
                    not_found_objects.append(target)
                    logger.info(f"❌ '{target}' not found in current frame")
        
        except Exception as e:
            logger.error(f"Error searching for {target}: {e}")
            not_found_objects.append(target)
        
        await asyncio.sleep(0.5)
    
    # Summary
    _search_active = False
    
    summary_parts = []
    if found_objects:
        found_names = [f["name"] for f in found_objects]
        summary_parts.append(f"เจอ {' กับ '.join(found_names)} แล้วครับ")
    if not_found_objects:
        summary_parts.append(f"ไม่เจอ {' กับ '.join(not_found_objects)}")
    
    summary = " ".join(summary_parts) if summary_parts else "ค้นหาเสร็จแล้วครับ"
    
    logger.info(f"═" * 50)
    logger.info(f"🏁 MULTI-SEARCH COMPLETE: found={len(found_objects)}, not_found={len(not_found_objects)}")
    logger.info(f"═" * 50)
    
    await speak_tts(summary)
    
    await _broadcast_and_cache({
        "type": "search_status",
        "status": "multi_complete",
        "targets": targets,
        "found": found_objects,
        "not_found": not_found_objects,
        "summary": summary,
    })


async def execute_server_command(cmd: str, params: dict):
    """Execute command received from Server"""
    logger.info(f"🎮 Executing: {cmd} with {params}")
    
    try:
        if cmd == "move":
            # Convert to motion command format
            direction = params.get("direction", "forward")
            distance = params.get("distance", 1)
            
            motion_map = {
                "forward": {"type": "move", "linear_x": 0.15, "angular_z": 0.0, "duration": 2.0},
                "backward": {"type": "move", "linear_x": -0.15, "angular_z": 0.0, "duration": 2.0},
            }
            
            motion_cmd = motion_map.get(direction)
            if motion_cmd and not MOCK_ROBOT:
                await motion.exec_motion(motion_cmd)
                logger.info(f"✅ Motion executed: {direction}")
            else:
                logger.info(f"🤖 [MOCK] Would move {direction}")
                
        elif cmd == "rotate":
            angle = params.get("angle", 90)
            
            # Elephant MyAGV 2023 (Jetson Nano) Default Rotation Config:
            # - Angular velocity: 0.50 rad/s (factory default สำหรับ Mecanum wheel)
            # - Calibration: 0.85 (ชดเชย inertia ของ 4-wheel Mecanum drive)
            # - Formula: duration = (angle_rad / angular_z) * calibration
            import math
            ROTATION_CALIBRATION = 0.87   # จูนจริง: 1.0 เกิน 30°, 0.90 เกินอีกนิด → 0.87
            
            angle_rad = abs(angle) * (math.pi / 180)  # Convert degrees to radians
            angular_z = 0.50  # rad/s (Elephant MyAGV 2023 factory default)
            duration = (angle_rad / angular_z) * ROTATION_CALIBRATION
            
            motion_cmd = {
                "type": "move",
                "linear_x": 0.0,
                "angular_z": angular_z if angle > 0 else -angular_z,
                "duration": duration
            }
            
            logger.info(f"🔄 Rotate {angle}° = {angle_rad:.2f} rad, duration={duration:.2f}s @ {angular_z} rad/s (cal={ROTATION_CALIBRATION})")
            
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

        elif cmd == "search":
            # Visual search: move + VLM detection loop
            target = params.get("target", "")
            if target:
                logger.info(f"🔍 Starting visual search for: {target}")
                asyncio.create_task(visual_search(target))
            else:
                logger.warning("⚠️ Search command with no target")
                
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
_obstacle_avoidance = ObstacleAvoidance(motion_publisher=motion, rosbridge_url=ROSBRIDGE)


# ── Camera Frame Push (Gateway → Server) ─────────────────────────────
async def _push_frames_to_server():
    """
    Background task: push camera frames to Server every ~200ms (~5 fps).
    Server stores them in memory for webapp to fetch.
    This is needed because Server (Tailscale) cannot reach Gateway (LAN).
    """
    push_url = f"{SERVER_BASE}/camera/push"
    last_count = 0
    fail_count = 0
    first_success = False

    logger.info(f"📤 Frame push target: {push_url}")

    # Wait a bit for camera to start receiving frames
    await asyncio.sleep(3)

    async with httpx.AsyncClient(timeout=3.0) as client:
        while True:
            try:
                cam = get_camera()
                frame = cam.get_frame()
                count = cam.frame_count

                if frame and count > last_count:
                    resp = await client.post(
                        push_url,
                        content=frame,
                        headers={"Content-Type": "image/jpeg"},
                    )
                    if resp.status_code == 200:
                        last_count = count
                        fail_count = 0
                        if not first_success:
                            first_success = True
                            logger.info(f"📤 First frame pushed to Server! ({len(frame)} bytes)")
                    else:
                        fail_count += 1
                        if fail_count <= 3 or fail_count % 50 == 0:
                            logger.warning(f"📤 Push failed: HTTP {resp.status_code} (fail #{fail_count})")

            except Exception as e:
                fail_count += 1
                if fail_count <= 3 or fail_count % 50 == 0:
                    logger.debug(f"📤 Push error (#{fail_count}): {e}")

            await asyncio.sleep(3.0)  # ~0.33 fps push rate (low bandwidth — VLM uses direct frame upload)


@app.on_event("startup")
async def startup_event():
    """Start Gateway → Server connection when app starts"""
    asyncio.create_task(connect_to_server())
    logger.info("🚀 Gateway startup: Server connection task started")
    
    # Start camera stream (ROSBridge subscriber)
    try:
        start_camera()
        logger.info("📷 Camera stream started")
    except Exception as e:
        logger.warning(f"⚠️ Camera not started: {e}")
    
    # Start pushing camera frames to Server
    asyncio.create_task(_push_frames_to_server())
    logger.info("📤 Camera frame push task started")
    
    # Start obstacle avoidance (LiDAR subscriber)
    if not MOCK_ROBOT:
        try:
            ros = await ensure_ros(ROSBRIDGE)
            await _obstacle_avoidance.start(ros)
            logger.info("✅ Obstacle avoidance active")
        except Exception as e:
            logger.warning(f"⚠️ Obstacle avoidance not started (ROSBridge unavailable): {e}")

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

@app.get("/obstacle/status")
async def obstacle_status():
    """Get current obstacle avoidance status (LiDAR + VLM)"""
    return _obstacle_avoidance.get_status()

@app.post("/obstacle/check")
async def obstacle_check():
    """Manually trigger obstacle check and get avoidance strategy"""
    result = await _obstacle_avoidance.check_and_avoid()
    if result is None:
        return {"obstacle_detected": False, "message": "Path is clear"}
    return result

@app.post("/obstacle/enable")
async def obstacle_enable():
    """Enable obstacle avoidance"""
    _obstacle_avoidance._enabled = True
    return {"enabled": True}

@app.post("/obstacle/disable")
async def obstacle_disable():
    """Disable obstacle avoidance"""
    _obstacle_avoidance._enabled = False
    return {"enabled": False}

# ===== Visual Search Status =====

@app.get("/search/status")
async def search_status():
    """Get current visual search status + last result (for HTTP polling fallback).
    Webapp polls this when Dashboard WS can't connect (mixed-content blocking)."""
    result = {
        "active": _search_active,
        "cancel_requested": _search_cancel,
    }
    if _last_search_result:
        result["last_event"] = _last_search_result
    return result

@app.post("/search/cancel")
async def search_cancel_endpoint():
    """Cancel any active visual search"""
    if _search_active:
        await cancel_search()
        return {"ok": True, "message": "Search cancel requested"}
    return {"ok": False, "message": "No active search"}

# ===== Object Memory Endpoints =====

@app.get("/memory/objects")
async def memory_objects():
    """Get all remembered objects"""
    objects = object_memory.get_all_objects()
    result = {}
    for obj in objects:
        history = object_memory.get_history(obj, limit=3)
        result[obj] = [{
            "location": h.location,
            "location_description": h.location_description,
            "timestamp": h.timestamp,
            "age_hours": round(h.age_hours(), 1),
            "image_url": h.image_url,
        } for h in history]
    return {"objects": result, "count": len(objects)}

@app.get("/memory/{object_name}")
async def memory_object(object_name: str):
    """Get memory for a specific object"""
    normalized = normalize_search_target(object_name)
    history = object_memory.get_history(normalized, limit=10)
    hint = object_memory.get_search_hint(normalized)
    priorities = object_memory.get_priority_zones(normalized)
    
    return {
        "object": object_name,
        "normalized": normalized,
        "hint": hint,
        "priorities": priorities,
        "history": [{
            "location": h.location,
            "location_description": h.location_description,
            "timestamp": h.timestamp,
            "age_hours": round(h.age_hours(), 1),
            "image_url": h.image_url,
        } for h in history],
    }

@app.delete("/memory/{object_name}")
async def memory_clear_object(object_name: str):
    """Clear memory for a specific object"""
    normalized = normalize_search_target(object_name)
    object_memory.clear(normalized)
    return {"ok": True, "cleared": normalized}

@app.delete("/memory")
async def memory_clear_all():
    """Clear all object memory"""
    object_memory.clear()
    return {"ok": True, "message": "All object memory cleared"}

# ===== Camera Endpoints =====

@app.get("/camera/status")
async def camera_status():
    """Get camera status"""
    cam = get_camera()
    return cam.get_status()

@app.get("/camera/frame")
async def camera_frame():
    """Get latest camera frame as JPEG"""
    cam = get_camera()
    frame = cam.get_frame()
    
    if frame is None:
        return Response(content=b"", status_code=204)
    
    return Response(
        content=frame,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "X-Frame-Age-Ms": str(round(cam.get_frame_age() * 1000, 1)),
            "X-Frame-Count": str(cam.frame_count),
        }
    )

@app.get("/camera/mjpeg")
async def camera_mjpeg():
    """Get MJPEG stream (continuous multipart/x-mixed-replace)"""
    
    async def generate():
        cam = get_camera()
        last_count = 0
        
        while True:
            frame = cam.get_frame()
            
            if frame and cam.frame_count > last_count:
                last_count = cam.frame_count
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n" + 
                    frame + 
                    b"\r\n"
                )
            
            await asyncio.sleep(0.033)  # ~30 FPS
    
    return StreamingResponse(
        generate(),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )

@app.post("/camera/start")
async def camera_start():
    """Start camera stream"""
    start_camera()
    return {"status": "started"}

@app.post("/camera/stop")
async def camera_stop():
    """Stop camera stream"""
    stop_camera()
    return {"status": "stopped"}

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

            # ============================================================
            # ลำดับการทำงาน:
            # 1. Motion Parser (Regex) ก่อน → ส่ง /cmd_vel ตรง (เร็ว, ใช้ได้เลย)
            # 2. LLM Planner ทีหลัง → ส่ง /move_base (Nav2, ต้องมี Nav2 stack)
            # ============================================================

            # 1. ลอง Motion Parser (Regex → /cmd_vel) ก่อน
            logger.info(f"🎮 Trying Motion Parser...")
            motion_cmd = parse_intent(text)
            if motion_cmd:
                logger.info(f"✅ Motion Intent Detected: {motion_cmd}")
                
                # If "stop" while searching → cancel the search too
                if motion_cmd.get("type") == "stop" and _search_active:
                    await cancel_search()
                
                # Check for obstacles before moving forward
                if not MOCK_ROBOT and motion_cmd.get("linear_x", 0) > 0:
                    obs = await _obstacle_avoidance.check_and_avoid()
                    if obs and obs.get("obstacle_detected"):
                        logger.warning(f"🚧 Obstacle blocking forward motion! Strategy: {obs.get('strategy')}")
                        await manager.broadcast(json.dumps({
                            "type": "obstacle",
                            "distance": obs.get("distance"),
                            "strategy": obs.get("strategy"),
                            "obstacle_type": obs.get("obstacle_type", "unknown"),
                        }))
                        await _obstacle_avoidance.execute_avoidance(obs["strategy"])
                        return
                
                if not MOCK_ROBOT:
                    await motion.exec_motion(motion_cmd)
                    logger.info("✅ Motion command executed via /cmd_vel")
                else:
                    logger.info(f"🤖 [MOCK] Would execute: {motion_cmd}")
                return
            
            # 1.2 ลอง Multi-Step Parser (เดินหน้าแล้วเลี้ยวซ้าย, หมุนกลับ, เดินรูป U)
            multi_cmds = parse_multi_intent(text)
            if multi_cmds:
                logger.info(f"✅ Multi-Step Intent Detected: {len(multi_cmds)} commands")
                for i, cmd in enumerate(multi_cmds, 1):
                    logger.info(f"   Step {i}: {cmd}")
                    if not MOCK_ROBOT:
                        await motion.exec_motion(cmd)
                    else:
                        logger.info(f"🤖 [MOCK] Would execute step {i}: {cmd}")
                        await asyncio.sleep(0.5)
                logger.info(f"✅ All {len(multi_cmds)} steps executed")
                return
            
            # 1.5 ลอง Find Object Parser (หา... → Visual Search Loop)
            # Check for multi-object search first: "หาปากกากับดินสอ"
            multi_objects = parse_find_multi_objects(text)
            if multi_objects:
                logger.info(f"🔍 Multi-Object Search Detected: {multi_objects}")
                
                # Cancel any existing search
                if _search_active:
                    await cancel_search()
                    await asyncio.sleep(1)
                
                # Search for multiple objects sequentially
                asyncio.create_task(visual_search_multi(multi_objects))
                return
            
            # Check for description-matching search: "หาปากกาสีน้ำเงิน"
            desc_query = parse_find_with_description(text)
            if desc_query:
                logger.info(f"🔍 Description Search: {desc_query}")
                target_with_desc = f"{desc_query['object']} {desc_query['description']}"
                
                if _search_active:
                    await cancel_search()
                    await asyncio.sleep(1)
                
                # Use full description in VLM query
                asyncio.create_task(visual_search(target_with_desc))
                return
            
            # Standard single object search
            find_target = parse_find_intent(text)
            if find_target:
                logger.info(f"🔍 Find Object Detected: '{find_target}'")
                
                # Cancel any existing search
                if _search_active:
                    await cancel_search()
                    await asyncio.sleep(1)  # Wait for previous search to clean up
                
                # Start visual search as background task
                asyncio.create_task(visual_search(find_target))
                return
            
            # 2. ถ้า Regex ไม่จับ → ลอง LLM Planner (สำหรับคำสั่งยากๆ เช่น "หาของ", "ไปที่โต๊ะ")
            if not MOCK_ROBOT:
                logger.info(f"🧠 Trying LLM Planner...")
                if await try_plan_and_execute(text, lang):
                    logger.info("✅ Executed via LLM Planner")
                    return
                else:
                    logger.info("⏭️  LLM Planner: No plan generated")
            else:
                logger.info(f"🤖 [MOCK] Would call LLM Planner for: {text}")
            
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
    
    # 1. ลอง Motion Parser ก่อน (Regex → /cmd_vel ตรง, เร็ว)
    motion_cmd = parse_intent(text)
    if motion_cmd:
        # If stop while searching → cancel
        if motion_cmd.get("type") == "stop" and _search_active:
            await cancel_search()
        
        if not MOCK_ROBOT:
            await motion.exec_motion(motion_cmd)
            return {"ok": True, "mode": "motion", "motion": motion_cmd}
        else:
            logger.info(f"🤖 [MOCK] Would execute motion: {motion_cmd}")
            return {"ok": True, "mode": "motion", "motion": motion_cmd, "mock": True}
    
    # 1.2 ลอง Multi-Step Parser (เดินหน้าแล้วเลี้ยวซ้าย, หมุนกลับ, เดินรูป U)
    multi_cmds = parse_multi_intent(text)
    if multi_cmds:
        executed = []
        for i, cmd in enumerate(multi_cmds, 1):
            if not MOCK_ROBOT:
                await motion.exec_motion(cmd)
            else:
                logger.info(f"🤖 [MOCK] Would execute step {i}: {cmd}")
            executed.append(cmd)
        return {"ok": True, "mode": "multi_step", "steps": len(executed), "commands": executed}
    
    # 1.5 ลอง Find Object Parser (หา... → Visual Search)
    find_target = parse_find_intent(text)
    if find_target:
        if _search_active:
            await cancel_search()
            await asyncio.sleep(1)
        asyncio.create_task(visual_search(find_target))
        return {"ok": True, "mode": "visual_search", "target": find_target}
    
    # 2. ถ้า Regex ไม่จับ → ลอง LLM Planner
    if not MOCK_ROBOT:
        if await try_plan_and_execute(text, lang):
            return {"ok": True, "mode": "planner"}
    else:
        logger.info(f"🤖 [MOCK] Would call LLM Planner for: {text}")
    
    # 3. Fallback → LLM ตอบคำถาม (chitchat/question) ผ่าน Server
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                f"{SERVER_BASE}/pipeline/command",
                json={"text": text, "session_id": "gateway", "lang": lang},
            )
        if r.status_code == 200:
            data = r.json()
            reply = data.get("response_text", "")
            if reply:
                await speak_tts(reply)
                return {"ok": True, "mode": "chitchat", "reply": reply}
    except Exception as e:
        logger.warning(f"⚠️ Chitchat fallback error: {e}")
    
    return {"ok": False, "reason": "no_motion_or_plan"}