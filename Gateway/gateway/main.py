import os, asyncio, json, re, logging, math, time
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
from gateway.spatial_memory import spatial_memory
from gateway.nav2_client import Nav2Client

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
USE_NAV2 = os.getenv("USE_NAV2", "0") == "1"  # ถ้าเปิด Phase 2 จะใช้ Nav2 แทน LiDAR heuristic

logger.info("═" * 60)
logger.info("🚀 VORA GATEWAY STARTING")
logger.info("═" * 60)
logger.info(f"📡 SERVER_BASE:  {SERVER_BASE}")
logger.info(f"🔌 SERVER_WS:    {SERVER_WS}")
logger.info(f"🤖 ROSBRIDGE:    {ROSBRIDGE}")
logger.info(f"🎮 CMD_VEL:      {CMD_VEL}")
logger.info(f"🐛 DEBUG:        {DEBUG}")
logger.info(f"🧪 MOCK_ROBOT:   {MOCK_ROBOT}")
logger.info(f"🧭 USE_NAV2:     {USE_NAV2}")
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
    move_duration: float = 4.0,          # seconds to move forward per step (~0.40m)
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
    SCAN_ROTATION_CAL = 0.95  # Increased from 0.87 — was undershooting 10-20°
    
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
    
    # ── Spatial memory: keep cross-search history for co-location ──
    # Don't clear() — observations from past searches enable:
    #   - Co-location: "saw desk at X → pen likely near X"
    #   - Scene memory: LLM knows what was already seen where (DAAAM-inspired)
    # Skip-duplicate (was_recently_observed) uses 2min TTL, so it auto-expires
    logger.info(f"🧠 Spatial memory: {len(spatial_memory._observations)} observations from past searches")
    
    # ── Semantic Spatial Memory: exploration summary + co-location ──
    spatial_summary = spatial_memory.get_exploration_summary(max_age_min=30)
    if spatial_summary:
        logger.info(f"🧠 Spatial memory: {len(spatial_summary.splitlines())} past observations")
    
    colocation_context = spatial_memory.build_colocation_context(target_object, max_age_min=60)
    if colocation_context:
        logger.info(f"📌 Co-location hints:\n{colocation_context}")
    
    # ── LLM Co-location Advisor: classify target → likely locations ──
    colocation_llm_hint = ""
    if spatial_summary:
        try:
            _coloc_system = (
                "คุณเป็น AI ช่วยค้นหาวัตถุ วิเคราะห์ว่าวัตถุเป้าหมายน่าจะอยู่ใกล้สิ่งของชนิดใด "
                "จากข้อมูลที่สำรวจมาแล้ว ตอบสั้นๆ 1-2 ประโยค ระบุตำแหน่ง (x,y) ที่น่าไปก่อน "
                "ถ้าไม่แน่ใจ ตอบ 'ไม่มีข้อมูลเพียงพอ'"
            )
            _coloc_prompt = (
                f"เป้าหมาย: ค้นหา \"{target_object}\"\n\n"
                f"สิ่งที่เคยเห็นตามจุดต่างๆ:\n{spatial_summary}\n\n"
            )
            if colocation_context:
                _coloc_prompt += f"ข้อมูล co-location:\n{colocation_context}\n\n"
            _coloc_prompt += (
                f"วิเคราะห์: วัตถุชนิดนี้น่าจะอยู่ใกล้อะไร? "
                f"ตำแหน่งไหนที่เคยสำรวจแล้วน่าจะมีมากที่สุด?"
            )
            async with httpx.AsyncClient(timeout=httpx.Timeout(12.0)) as _cc:
                _coloc_resp = await _cc.post(f"{SERVER_BASE}/generate", json={
                    "prompt": _coloc_prompt,
                    "system": _coloc_system,
                    "temperature": 0.3,
                    "max_tokens": 128,
                })
            if _coloc_resp.status_code == 200:
                colocation_llm_hint = _coloc_resp.json().get("response", "")[:200]
                if colocation_llm_hint and "ไม่มีข้อมูล" not in colocation_llm_hint:
                    logger.info(f"🧠 LLM Co-location: {colocation_llm_hint}")
                else:
                    colocation_llm_hint = ""
        except Exception as _e:
            logger.debug(f"LLM co-location skipped: {_e}")
    
    # Broadcast search start to dashboard + cache for polling
    await _broadcast_and_cache({
        "type": "search_status",
        "status": "started",
        "target": target_object,
        "normalized_target": normalized_target,
        "memory_hint": memory_hint,
        "colocation_hint": colocation_llm_hint,
    })
    
    # 🔊 TTS: Announce search start + memory hint
    announce = f"กำลังค้นหา {target_object} ครับ"
    if memory_hint:
        announce += f" {memory_hint}"
    elif colocation_llm_hint:
        announce += f" {colocation_llm_hint[:60]}"
    await speak_tts(announce)
    
    stale_count = 0  # track consecutive stale/empty VLM checks (camera/VLM truly dead)
    vlm_reject_count = 0  # track consecutive VLM quality-gate rejections (VLM working but bad output)
    wall_empty_count = 0  # consecutive "wall/empty/floor-only" scenes → stop moving
    good_vlm_count = 0  # total VLM checks that passed quality gate (non-fragment)
    consecutive_found = 0  # consecutive found=true results (for confirmation)
    partial_approach_count = 0  # how many times we've approached a partial match (limit)
    MAX_PARTIAL_APPROACHES = 3  # don't chase partial matches forever
    exploration_log = []     # [{"phase": str, "desc": str}] — VLM descriptions per step
    prev_move_angles = []    # directions we moved in Phase 2 (for LLM to avoid repeating)
    _WALL_KEYWORDS = ["ผนัง", "แผ่นผนัง", "แผงกั้น", "ฉากเรียบ", "ไม่มีวัตถุ", "พื้นหลังสีเทา",
                      "พื้นหลังสีขาว", "ไม่มีสิ่งของ", "ไม่มีป้าย", "ทางตัน",
                      # English (VLM now outputs English descriptions)
                      "white panel", "white wall", "partition wall", "plain surface",
                      "no object", "no distinct", "uniform gray", "solid gray",
                      "blank wall", "empty room", "no visible object"]
    
    async def _is_wall_or_empty(desc: str) -> bool:
        """Check if VLM description indicates wall/empty/dead-end scene."""
        if not desc:
            return True  # no data = treat as empty
        d = desc.lower()
        return any(kw in d for kw in _WALL_KEYWORDS)
    
    async def _do_vlm_and_check(phase_label: str, force: bool = False, broadcast_extra: dict = None) -> bool:
        """Helper: run VLM check, handle stale frames, broadcast progress.
        Returns True if found.  Set force=True after forward moves to never skip.
        broadcast_extra: optional dict merged into scanning broadcast (step, action, etc.)"""
        nonlocal total_checks, stale_count, vlm_reject_count, wall_empty_count, good_vlm_count, consecutive_found, partial_approach_count
        
        # ── Skip-duplicate: don't re-scan same position+heading ──
        if not force:
            _rx = _robot_pose.get("x", 0.0)
            _ry = _robot_pose.get("y", 0.0)
            _rtheta = _robot_pose.get("theta", 0.0)
            _rheading = math.degrees(_rtheta) % 360
            if spatial_memory.was_recently_observed(_rx, _ry, _rheading):
                logger.info(f"🧠 SKIP [{phase_label}] — same position+heading observed recently")
                total_checks += 1  # count it but don't waste VLM time
                return False
        
        found, location, description, _conf = await _vlm_check(target_object, vlm_timeout)
        total_checks += 1
        
        # ── Auto-retry once on prompt_echo with alternate prompt ──
        is_rejected = description.startswith("__REJECTED__:") if description else False
        if is_rejected and "prompt_echo" in description:
            logger.info(f"🔄 [{phase_label}] Prompt echo — retrying VLM with alternate prompt...")
            await asyncio.sleep(0.5)  # brief pause before retry
            found, location, description, _conf = await _vlm_check(target_object, vlm_timeout, retry=True)
            total_checks += 1
            is_rejected = description.startswith("__REJECTED__:") if description else False
        
        # ── Classify the VLM result into 3 categories ──
        is_empty = not description  # No frame, stale frame, or VLM HTTP error
        
        if is_empty:
            # REAL camera/VLM failure — no frame captured or VLM completely failed
            stale_count += 1
            vlm_reject_count = 0  # not a quality issue
            consecutive_found = 0
            logger.warning(f"⚠️ No VLM data ({stale_count} consecutive) — camera/VLM may be dead")
            if stale_count >= 8:
                logger.error(f"🛑 Aborting search: {stale_count} consecutive NO DATA from camera/VLM")
                await speak_tts("กล้องขัดข้อง ไม่สามารถค้นหาต่อได้ครับ")
                await _search_not_found(target_object, total_checks)
                return True
        elif is_rejected:
            # VLM produced output but quality gate rejected it — VLM is working, just unreliable
            vlm_reject_count += 1
            stale_count = 0  # camera IS alive (VLM received a frame)
            consecutive_found = 0
            reject_parts = description.split(":", 2)
            reject_reason = reject_parts[1] if len(reject_parts) > 1 else "?"
            logger.info(f"📊 VLM rejected ({vlm_reject_count} consec, reason={reject_reason}) — camera OK, VLM unreliable")
            
            # Prompt echoes indicate uninformative scene (model echoes when it sees a wall/blank).
            # Count these toward wall_empty_count so the wall-guard can activate.
            if reject_reason == "prompt_echo" or reject_reason == "too_short":
                wall_empty_count += 1
                logger.info(f"🧱 Prompt echo/short → treating as wall/empty scene #{wall_empty_count}")
            
            # After many rejections, lower the quality gate temporarily
            if vlm_reject_count >= 6:
                logger.warning(
                    f"⚠️ VLM UNRELIABLE: {vlm_reject_count} consecutive rejections. "
                    f"Camera is streaming but VLM output quality is poor. Continuing search with LiDAR."
                )
                vlm_reject_count = 0
                description = ""
        else:
            # VLM produced a valid, accepted description
            stale_count = 0
            vlm_reject_count = 0
            good_vlm_count += 1
        
        # Track wall/empty scenes — abort forward moves if stuck in dead-end
        # IMPORTANT: Only count as wall when VLM actually SAW a wall, not camera failure
        effective_desc = "" if is_rejected else description
        if is_empty:
            pass  # Camera dead → do NOT inflate wall_empty_count (would trigger blind forward)
        elif await _is_wall_or_empty(effective_desc):
            wall_empty_count += 1
            if effective_desc:
                logger.info(f"🧱 Wall/empty scene #{wall_empty_count}: '{effective_desc[:60]}'")
        else:
            wall_empty_count = 0  # reset when we see interesting content
        
        # Accumulate exploration memory for LLM navigator
        if effective_desc and not is_rejected and not is_empty:
            exploration_log.append({"phase": phase_label, "desc": effective_desc[:500]})
            # ── Persist to Semantic Spatial Memory ──
            spatial_memory.record(
                x=_robot_pose.get("x", 0.0),
                y=_robot_pose.get("y", 0.0),
                theta=_robot_pose.get("theta", 0.0),
                description=effective_desc,
                phase=phase_label,
                search_target=target_object,
            )
        
        # Broadcast progress
        _broadcast_data = {
            "type": "search_status",
            "status": "scanning",
            "target": target_object,
            "phase": phase_label,
            "total_checks": total_checks,
            "description": (effective_desc[:500] if effective_desc else ""),
        }
        if broadcast_extra:
            _broadcast_data.update(broadcast_extra)
        await _broadcast_and_cache(_broadcast_data)
        
        if found:
            consecutive_found += 1
            logger.info(f"🎉 Object detected! ({phase_label}) Location: {location} [confirm {consecutive_found}/2]")
            
            # ── Confirmation gate: require 2 consecutive found=true before approach ──
            # Single detection can be VLM hallucination or seeing through a gap.
            # Re-check immediately (same position) to confirm.
            if consecutive_found < 2:
                logger.info("🔁 Confirming detection — re-checking same angle...")
                await asyncio.sleep(0.5)  # let camera settle
                found2, loc2, desc2, _conf2 = await _vlm_check(target_object, vlm_timeout)
                total_checks += 1
                if found2:
                    consecutive_found += 1
                    location = loc2  # use latest location
                    description = desc2
                    logger.info(f"✅ Confirmed! ({consecutive_found}/2) at '{loc2}'")
                else:
                    consecutive_found = 0
                    logger.warning(f"❌ Confirmation failed — first detection was likely false positive")
                    return False
            
            if consecutive_found >= 2:
                logger.info(f"🎉 CONFIRMED: Object found! ({phase_label}) Location: {location}")
                await _search_found(target_object, location, description)
                return True
        else:
            consecutive_found = 0  # reset confirmation chain
            
            # ── Partial match → approach: object category matches but can't confirm details ──
            # Example: searching for "bottle with Avias label" and VLM sees "bottle" but
            # can't read label → confidence 0.3-0.6 → approach closer and re-check
            if (
                _conf >= 0.3
                and not is_empty
                and not is_rejected
                and partial_approach_count < MAX_PARTIAL_APPROACHES
                and not await _is_wall_or_empty(effective_desc)
            ):
                partial_approach_count += 1
                logger.info(
                    f"🔍 PARTIAL MATCH [{phase_label}] conf={_conf:.2f} — "
                    f"approaching to verify ({partial_approach_count}/{MAX_PARTIAL_APPROACHES})"
                )
                await speak_tts("เห็นสิ่งที่คล้ายเป้าหมาย กำลังเข้าไปดูใกล้ๆ ครับ")
                
                # Move forward a small step to get closer view
                if not MOCK_ROBOT:
                    approach_cmd = {
                        "type": "move",
                        "linear_x": 0.10,
                        "angular_z": 0.0,
                        "duration": 1.5,  # ~15cm closer
                    }
                    await motion.exec_motion(approach_cmd, obstacle_checker=_lidar_obstacle_check)
                await asyncio.sleep(0.5)
                
                # Re-check with VLM after approaching
                found_retry, loc_retry, desc_retry, conf_retry = await _vlm_check(target_object, vlm_timeout)
                total_checks += 1
                
                if found_retry and conf_retry >= 0.7:
                    consecutive_found = 2  # bypass confirmation gate — we just approached + confirmed
                    logger.info(f"🎉 PARTIAL→CONFIRMED! conf={conf_retry:.2f} at '{loc_retry}' after approach")
                    await _search_found(target_object, loc_retry, desc_retry)
                    return True
                elif conf_retry >= 0.3:
                    # Still partial — log but don't approach again immediately
                    logger.info(f"🔍 Still partial after approach: conf={conf_retry:.2f} — continuing search")
                else:
                    logger.info(f"❌ Partial match not confirmed after approach (conf={conf_retry:.2f})")
        
        return False
    
    async def _rotate_deg(degrees: float = 90.0):
        """Rotate by given degrees with calibration applied."""
        if not MOCK_ROBOT:
            angle_rad = math.radians(abs(degrees))
            duration = (angle_rad / 0.50) * SCAN_ROTATION_CAL
            direction = 0.50 if degrees > 0 else -0.50  # positive = left
            rotate_cmd = {
                "type": "move",
                "linear_x": 0.0,
                "angular_z": direction,
                "duration": duration,
            }
            phys_dir = "LEFT(CCW)" if degrees > 0 else "RIGHT(CW)"
            logger.info(f"🔄 Rotating {degrees}° → {phys_dir} (duration={duration:.2f}s, cal={SCAN_ROTATION_CAL})")
            await motion.exec_motion(rotate_cmd)
        else:
            logger.info(f"🤖 [MOCK] Would rotate {degrees}°")
            await asyncio.sleep(0.3)
        await asyncio.sleep(0.5)  # Let camera frame settle
    
    async def _rotate_90():
        """Rotate 90° (backward compatible)."""
        await _rotate_deg(90.0)
    
    try:
        # === LiDAR Pre-Scan: understand environment before searching ===
        if not MOCK_ROBOT:
            await asyncio.sleep(0.5)  # let LiDAR accumulate fresh data
            lidar_prescan = _obstacle_avoidance.find_best_direction()
            lidar_prescan_text = _obstacle_avoidance.get_sector_summary()
            open_count = len(lidar_prescan.get("open_directions", []))
            logger.info(f"📡 LiDAR PRE-SCAN (initial orientation):\n{lidar_prescan_text}")
            logger.info(f"   {open_count} passable directions at start position")
        
        # === Phase 0: Check current frame BEFORE anything ===
        logger.info(f"👁️ Phase 0: Checking current camera frame...")
        if await _do_vlm_and_check("phase0_current"):
            return
        if _search_cancel:
            await _search_cancelled(target_object)
            return
        
        # ════════════════════════════════════════════════════════════
        # Agent Loop: LLM/VLM-driven search
        # VLM+LLM are the CORE decision-makers (replaces fixed Phase 1→1.5→2)
        # Each step: LLM sees LiDAR + VLM history → decides action
        # ════════════════════════════════════════════════════════════
        
        # Nav2 fast-path
        if USE_NAV2 and _nav2_client and not MOCK_ROBOT:
            # ─── Nav2 MODE: Use explore_lite frontiers or random goals ───
            # _phase2_nav2 returns True if search concluded (found/not-found/cancelled)
            # Returns False if Nav2 connection failed → fall through to legacy
            nav2_handled = await _phase2_nav2(
                target_object=target_object,
                max_move_cycles=max_move_cycles,
                vlm_timeout=vlm_timeout,
                do_vlm_and_check=_do_vlm_and_check,
                rotate_deg=_rotate_deg,
                speak=speak_tts,
                broadcast=_broadcast_and_cache,
                search_not_found=_search_not_found,
                search_cancelled=_search_cancelled,
                total_checks=total_checks,
            )
            if nav2_handled:
                return
        
        # ─── Agent Loop: LLM+LiDAR driven exploration ───
        MAX_AGENT_STEPS = 12
        MAX_FORWARD_MOVES = max_move_cycles
        
        logger.info(
            f"🧠 Agent Loop: LLM+LiDAR driven search "
            f"(max {MAX_AGENT_STEPS} steps, {MAX_FORWARD_MOVES} moves)"
        )
        await speak_tts("กำลังมองรอบๆ ครับ")
        
        checked_dirs = []   # [{"angle_abs": float, "desc": str}]
        cumulative_rotation = 0.0   # heading relative to search start (degrees)
        forward_move_count = 0
        turns_at_position = 0  # turns since last forward move — force forward after 4
        
        # Record Phase 0 observation
        if exploration_log:
            checked_dirs.append({
                "angle_abs": 0.0,
                "desc": exploration_log[-1]["desc"][:150],
            })
        
        for step in range(MAX_AGENT_STEPS):
            if _search_cancel:
                await _search_cancelled(target_object)
                return
            
            # Perception warning after a few steps
            if step == 3 and good_vlm_count < 2:
                logger.warning(
                    f"⚠️ PERCEPTION UNSTABLE: only {good_vlm_count}/{total_checks} good VLM. "
                    f"Continuing with LiDAR guidance."
                )
                await speak_tts("กล้องไม่เสถียร เคลื่อนที่ด้วยเลเซอร์อย่างเดียวครับ")
            
            # ── Fresh LiDAR scan ──
            lidar_text = ""
            open_dirs_list = []
            if not MOCK_ROBOT:
                lidar_result = _obstacle_avoidance.find_best_direction()
                lidar_text = _obstacle_avoidance.get_sector_summary()
                open_dirs_list = lidar_result.get("open_directions", [])
                if step == 0 or step % 3 == 0:
                    logger.info(f"📡 LiDAR scan:\n{lidar_text}")
            
            # ── Safety: block forward/force-forward when camera is dead ──
            camera_blind = stale_count >= 2
            
            # ── Force forward if stuck spinning at same position (only with camera) ──
            if turns_at_position >= 4 and forward_move_count < MAX_FORWARD_MOVES and not camera_blind:
                if open_dirs_list:
                    best_fwd = open_dirs_list[0]
                    plan = {
                        "action": "forward",
                        "angle": best_fwd["angle_deg"],
                        "reason": f"force: {turns_at_position} turns at same spot, moving to {best_fwd.get('avg_dist_m', 0):.1f}m open"
                    }
                    logger.info(f"🔄 FORCE FORWARD: {turns_at_position} turns without moving → forwarding to {best_fwd['angle_deg']:+.0f}°")
                else:
                    plan = {"action": "done", "angle": 0, "reason": f"stuck: {turns_at_position} turns, no open direction"}
            else:
                # ── LLM decides next action ──
                plan = await _llm_plan_action(
                    target=target_object,
                    lidar_summary=lidar_text,
                    open_directions=open_dirs_list,
                    checked_dirs=checked_dirs,
                    cumulative_rotation=cumulative_rotation,
                    step=step,
                    max_steps=MAX_AGENT_STEPS,
                    move_count=forward_move_count,
                    max_moves=MAX_FORWARD_MOVES,
                    exploration_log=exploration_log,
                    wall_streak=wall_empty_count if not camera_blind else 0,
                    turns_at_position=turns_at_position,
                )
            
            action = plan.get("action", "done")
            angle = plan.get("angle", 0.0)
            reason = plan.get("reason", "")
            
            logger.info(
                f"🧠 Step {step + 1}/{MAX_AGENT_STEPS}: "
                f"action={action}, angle={angle:+.0f}°"
                + (f" — {reason}" if reason else "")
            )
            
            # ═══════════ Execute action ═══════════
            
            if action == "turn":
                # ── Turn to check a new direction ──
                if abs(angle) > 5:
                    await _rotate_deg(angle)
                    cumulative_rotation += angle
                
                label = f"agent_s{step + 1}_t{angle:+.0f}"
                if await _do_vlm_and_check(label, force=True, broadcast_extra={
                    "step": step + 1,
                    "max_steps": MAX_AGENT_STEPS,
                    "action": "turn",
                    "angle": angle,
                    "reason": reason,
                }):
                    return
                
                desc = exploration_log[-1]["desc"][:150] if exploration_log else ""
                checked_dirs.append({
                    "angle_abs": round(cumulative_rotation % 360, 1),
                    "desc": desc,
                })
                turns_at_position += 1
            
            elif action == "forward":
                # ── Safety: never move forward blind ──
                if camera_blind:
                    logger.warning(f"🛑 BLOCKING forward — camera dead ({stale_count} stale). Turning instead.")
                    # Try to turn to an open direction to get camera working
                    if open_dirs_list:
                        await _rotate_deg(open_dirs_list[0]["angle_deg"])
                        cumulative_rotation += open_dirs_list[0]["angle_deg"]
                    continue
                
                # ── Move to new vantage point ──
                if forward_move_count >= MAX_FORWARD_MOVES:
                    logger.info(f"🚶 Max forward moves reached ({MAX_FORWARD_MOVES}).")
                    break
                
                forward_move_count += 1
                prev_move_angles.append(angle)
                turns_at_position = 0  # reset turn counter at new position
                
                if abs(angle) > 20:
                    await speak_tts(f"เลี้ยว{int(abs(angle))}องศาไปทางที่โล่งครับ")
                    await _rotate_deg(angle)
                    cumulative_rotation += angle
                
                if wall_empty_count >= 3:
                    wall_empty_count = 0
                
                # Check clearance before moving
                if not MOCK_ROBOT:
                    can_fit, clearance = _obstacle_avoidance.can_robot_fit(0.0)
                    if not can_fit:
                        logger.warning(f"🚫 Cannot fit ({clearance}m). Backing up.")
                        await motion.exec_motion(
                            {"type": "move", "linear_x": -0.10, "angular_z": 0.0, "duration": 1.5}
                        )
                        await asyncio.sleep(0.3)
                        continue
                    
                    obs = await _obstacle_avoidance.check_and_avoid()
                    if obs and obs.get("obstacle_detected"):
                        logger.warning(f"🚧 Obstacle at {obs.get('distance', 0):.2f}m!")
                        await speak_tts("เจอสิ่งกีดขวาง กำลังหลบครับ")
                        await motion.exec_motion(
                            {"type": "move", "linear_x": -0.10, "angular_z": 0.0, "duration": 1.0}
                        )
                        await _obstacle_avoidance.execute_avoidance(obs.get("strategy", "stop"))
                        continue
                
                logger.info(f"🚶 Moving forward ({forward_move_count}/{MAX_FORWARD_MOVES})...")
                
                if not MOCK_ROBOT:
                    move_cmd = {
                        "type": "move",
                        "linear_x": move_speed,
                        "angular_z": 0.0,
                        "duration": move_duration,
                    }
                    completed = await motion.exec_motion(
                        move_cmd, obstacle_checker=_lidar_obstacle_check
                    )
                    if not completed:
                        logger.warning("🛑 Forward interrupted by LiDAR!")
                        await speak_tts("ตรวจพบสิ่งกีดขวาง หยุดเดินครับ")
                else:
                    await asyncio.sleep(0.5)
                
                await asyncio.sleep(0.5)
                
                # VLM check at new position
                if await _do_vlm_and_check(f"agent_mv{forward_move_count}_fwd", force=True, broadcast_extra={
                    "step": step + 1,
                    "max_steps": MAX_AGENT_STEPS,
                    "action": "forward",
                    "angle": angle,
                    "reason": reason,
                }):
                    return
                
                # New position → reset checked directions
                checked_dirs.clear()
                cumulative_rotation = 0.0
                desc = exploration_log[-1]["desc"][:150] if exploration_log else ""
                checked_dirs.append({"angle_abs": 0.0, "desc": desc})
            
            elif action == "done":
                logger.info(f"🧠 Search exhausted: {reason}")
                break
        
        # === Not found ===
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


async def _phase2_nav2(
    target_object: str,
    max_move_cycles: int,
    vlm_timeout: float,
    do_vlm_and_check,
    rotate_deg,
    speak,
    broadcast,
    search_not_found,
    search_cancelled,
    total_checks: int,
):
    """
    Phase 2 using Nav2 navigate_to_pose instead of manual LiDAR heuristics.

    Strategy:
    1. Pick exploration goals (spread outward from current position)
    2. Send goal to Nav2 → it handles path planning + obstacle avoidance + recovery
    3. At each goal, do a VLM look-around check
    4. Repeat until found or max_move_cycles exhausted

    Nav2 handles: path planning, dynamic obstacle avoidance, stuck recovery.
    Gateway handles: VLM checks at each waypoint, goal selection strategy.
    """
    global _search_cancel

    logger.info("═" * 50)
    logger.info("🧭 Phase 2 (Nav2): Navigate to explore new viewpoints")
    logger.info("═" * 50)
    await speak("เปลี่ยนเป็นโหมดนำทางอัตโนมัติครับ")

    # Connect Nav2 client
    if not _nav2_client.connected:
        connected = await _nav2_client.connect()
        if not connected:
            logger.error("❌ Nav2 client failed to connect — falling back to legacy")
            await speak("ระบบนำทางไม่พร้อม ใช้โหมดเดิมครับ")
            return False  # Signal caller to fall through to legacy Phase 2

    # Generate exploration goals as a radial pattern from current position
    rx = _robot_pose.get("x", 0.0)
    ry = _robot_pose.get("y", 0.0)
    rtheta = _robot_pose.get("theta", 0.0)

    # Exploration goals: forward, left, right, diag-left, diag-right, behind
    EXPLORE_DIST = 1.0  # meters — Nav2 will plan the actual path
    explore_angles = [
        rtheta,                     # forward
        rtheta + math.pi / 2,      # left
        rtheta - math.pi / 2,      # right
        rtheta + math.pi / 4,      # front-left diagonal
        rtheta - math.pi / 4,      # front-right diagonal
        rtheta + math.pi,          # behind (last resort)
    ]

    goals_tried = 0
    for cycle in range(min(max_move_cycles, len(explore_angles))):
        if _search_cancel:
            await search_cancelled(target_object)
            return True

        angle = explore_angles[cycle]
        goal_x = rx + EXPLORE_DIST * math.cos(angle)
        goal_y = ry + EXPLORE_DIST * math.sin(angle)

        logger.info(
            f"🧭 Nav2 cycle {cycle + 1}/{max_move_cycles}: "
            f"goal=({goal_x:.2f}, {goal_y:.2f}) from ({rx:.2f}, {ry:.2f})"
        )
        await speak(f"เคลื่อนที่ไปจุดสำรวจที่ {cycle + 1} ครับ")

        await broadcast({
            "type": "search_status",
            "status": "nav2_navigating",
            "target": target_object,
            "goal": {"x": round(goal_x, 2), "y": round(goal_y, 2)},
            "cycle": cycle + 1,
        })

        # Feedback callback — log progress
        def _on_nav_feedback(fb):
            dist = fb.get("distance_remaining", -1)
            if dist >= 0:
                logger.debug(f"  Nav2 progress: {dist:.2f}m remaining")

        # Navigate to exploration point
        result = await _nav2_client.navigate_to_pose(
            x=goal_x,
            y=goal_y,
            theta=angle,
            timeout=45.0,
            on_feedback=_on_nav_feedback,
        )

        goals_tried += 1
        status = result.get("status", "UNKNOWN")
        duration = result.get("duration", 0)

        if status == "SUCCEEDED":
            logger.info(f"✅ Nav2 reached goal in {duration:.1f}s")
        elif status == "ABORTED":
            logger.warning(f"⚠️ Nav2 aborted (unreachable goal) after {duration:.1f}s")
            continue
        elif status == "TIMEOUT":
            logger.warning(f"⚠️ Nav2 timed out after {duration:.1f}s")
            await _nav2_client.cancel_navigation()
            continue
        elif status == "CANCELED":
            logger.info("Nav2 goal was cancelled")
            continue
        else:
            logger.warning(f"Nav2 unexpected status: {status}")
            continue

        # Update position from odom
        rx = _robot_pose.get("x", rx)
        ry = _robot_pose.get("y", ry)
        rtheta = _robot_pose.get("theta", rtheta)

        if _search_cancel:
            await search_cancelled(target_object)
            return True

        # VLM look-around at new position: forward → left → right → back to forward
        logger.info(f"👁️ VLM check at new position (cycle {cycle + 1})...")
        if await do_vlm_and_check(f"nav2_cycle{cycle + 1}_fwd"):
            return True

        if _search_cancel:
            await search_cancelled(target_object)
            return True

        await rotate_deg(90.0)
        if await do_vlm_and_check(f"nav2_cycle{cycle + 1}_left"):
            return True

        await rotate_deg(-180.0)
        if await do_vlm_and_check(f"nav2_cycle{cycle + 1}_right"):
            return True

        await rotate_deg(90.0)  # return to forward

    # Not found after all Nav2 goals
    logger.warning(f"😔 Object '{target_object}' not found after Nav2 exploration ({goals_tried} goals)")
    await search_not_found(target_object, total_checks)
    return True  # Search concluded (not found)


async def _ask_llm_navigate(target: str, exploration_log: list, lidar_summary: str,
                            prev_move_angles: list, open_directions: list) -> Optional[float]:
    """
    🧠 LLM Navigation Advisor — choose the best exploration direction.
    
    Uses exploration history (VLM descriptions) + LiDAR sectors + spatial memory
    to decide which direction the robot should move next for efficient search.
    
    Returns recommended angle (degrees, + = left, - = right) or None on failure.
    """
    if not exploration_log:
        return None

    # Build exploration summary (last 12 entries from current search)
    history_lines = []
    for entry in exploration_log[-12:]:
        history_lines.append(f"  [{entry['phase']}] {entry['desc'][:150]}")
    history_text = "\n".join(history_lines)

    # Spatial memory — past observations from all searches
    spatial_context = spatial_memory.get_exploration_summary(max_age_min=15)
    coloc_hits = spatial_memory.find_related_locations(target, max_age_min=30)
    coloc_text = ""
    if coloc_hits:
        coloc_text = "🔍 Co-location hints:\n" + "\n".join(
            f"  {h['type']}: {h['landmark']} at ({h['x']:.2f},{h['y']:.2f}) [{h['age_min']:.0f}m ago]"
            for h in coloc_hits[:5]
        )

    # Format passable directions from LiDAR
    open_text = ", ".join(
        f"{d['angle_deg']:+.0f}°({d.get('avg_dist_m','?')}m)"
        for d in open_directions[:8]
    )
    moved_text = ", ".join(f"{a:+.0f}°" for a in prev_move_angles) if prev_move_angles else "ยังไม่ได้เดิน"

    system = (
        "คุณเป็น AI นำทางหุ่นยนต์ค้นหาวัตถุในห้อง วิเคราะห์ข้อมูล LiDAR + คำอธิบายกล้อง "
        "แล้วเลือกทิศทางที่ดีที่สุดในการสำรวจ "
        "ตอบเป็น JSON เท่านั้น ห้ามมีข้อความอื่น: "
        "{\"angle_deg\": <number>, \"reason\": \"เหตุผลสั้นๆ\"} "
        "angle_deg = มุมหมุนจากทิศหน้า (+ซ้าย, -ขวา, 0=ตรงไป) "
        "ต้องเลือกจากทิศที่ LiDAR บอกว่าโล่ง(✅) เท่านั้น"
    )

    prompt = (
        f"🎯 เป้าหมาย: ค้นหา \"{target}\"\n\n"
        f"📷 กล้องเห็นอะไรบ้าง (รอบปัจจุบัน):\n{history_text}\n\n"
    )
    if spatial_context:
        prompt += f"🧠 ความทรงจำจากรอบก่อนหน้า:\n{spatial_context}\n\n"
    if coloc_text:
        prompt += f"{coloc_text}\n\n"
    prompt += (
        f"📡 LiDAR รอบตัว:\n{lidar_summary}\n\n"
        f"🟢 ทิศที่โล่ง: {open_text}\n"
        f"🚶 ทิศที่เดินไปแล้ว: {moved_text}\n\n"
        f"กฎเลือกทิศ:\n"
        f"1. ห้ามไปทิศที่เดินซ้ำ (เสียเวลา)\n"
        f"2. ห้ามไปทิศที่เห็นกำแพง/ผนัง/ทางตัน\n"
        f"3. ไปทิศที่โล่งและยังไม่ได้สำรวจ\n"
        f"4. ถ้าเห็นช่องทางหรือพื้นที่เปิดกว้าง ให้ไปทางนั้น\n"
        f"5. ถ้ามี co-location hint ให้มุ่งไปทิศนั้นก่อน\n"
        f"ตอบ JSON:"
    )

    try:
        llm_url = f"{SERVER_BASE}/generate"
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            resp = await client.post(llm_url, json={
                "prompt": prompt,
                "system": system,
                "temperature": 0.3,
                "max_tokens": 128,
            })

        if resp.status_code != 200:
            logger.warning(f"⚠️ LLM Navigator: HTTP {resp.status_code}")
            return None

        data = resp.json()
        text = data.get("response", "")
        logger.info(f"🧠 LLM Navigator raw: {text[:200]}")

        # Parse JSON — extract angle_deg
        json_match = re.search(r'\{[^}]*"angle_deg"\s*:\s*(-?\d+(?:\.\d+)?)[^}]*\}', text)
        if not json_match:
            # Try alternate format: angle_deg first
            json_match = re.search(r'"angle_deg"\s*:\s*(-?\d+(?:\.\d+)?)', text)
        if json_match:
            angle = float(json_match.group(1))
            reason_match = re.search(r'"reason"\s*:\s*"([^"]*)"', text)
            reason = reason_match.group(1) if reason_match else ""
            logger.info(f"🧠 LLM Navigator: go {angle:+.0f}° — {reason}")
            if -180 <= angle <= 180:
                return angle
            else:
                logger.warning(f"⚠️ LLM Navigator angle out of range: {angle}")
        else:
            logger.warning(f"⚠️ LLM Navigator: could not parse angle from response")
    except Exception as e:
        logger.warning(f"⚠️ LLM Navigator error: {e}")

    return None


async def _llm_plan_action(
    target: str,
    lidar_summary: str,
    open_directions: list,
    checked_dirs: list,
    cumulative_rotation: float,
    step: int,
    max_steps: int,
    move_count: int,
    max_moves: int,
    exploration_log: list,
    wall_streak: int = 0,
    turns_at_position: int = 0,
) -> dict:
    """
    🧠 LLM Agent Brain: decide next action for visual search.

    Replaces fixed Phase 1→1.5→2 with intelligent per-step decisions.
    LLM sees pre-filtered open directions + VLM history → picks action.
    Raw LiDAR table is NOT shown (LLM misinterprets ❌/✅ symbols).

    Returns {"action": "turn"|"forward"|"done", "angle": float, "reason": str}
    """
    # ── Build checked/unchecked context ──
    checked_abs_angles = set()
    checked_text_lines = []
    for cd in checked_dirs:
        rel = ((cd["angle_abs"] - cumulative_rotation + 180) % 360) - 180
        checked_text_lines.append(f"  {rel:+.0f}°: \"{cd['desc'][:80]}\"")
        checked_abs_angles.add(round(cd["angle_abs"]))
    checked_text = "\n".join(checked_text_lines) if checked_text_lines else "  (none yet)"

    # Build unchecked open directions list (sorted by distance, widest first)
    unchecked_open = []
    for od in open_directions:
        lidar_angle = od["angle_deg"]
        abs_angle = round((cumulative_rotation + lidar_angle) % 360)
        already = any(
            abs((abs_angle - ca + 180) % 360 - 180) < 25
            for ca in checked_abs_angles
        )
        if not already:
            dist = od.get("avg_dist_m")
            dist_val = dist if isinstance(dist, (int, float)) and dist else 0
            unchecked_open.append({"angle": lidar_angle, "dist": dist_val})
    unchecked_open.sort(key=lambda x: x["dist"], reverse=True)  # widest first

    if unchecked_open:
        unchecked_lines = []
        for i, u in enumerate(unchecked_open):
            tag = " ← widest" if i == 0 else ""
            unchecked_lines.append(f"  {u['angle']:+.0f}° ({u['dist']:.1f}m){tag}")
        unchecked_text = "\n".join(unchecked_lines)
    else:
        unchecked_text = "  (none — all directions checked)"

    # Build forward options (all open directions for forward move)
    if open_directions:
        best_fwd = open_directions[0]  # already sorted by score
        forward_hint = f"Best forward direction: {best_fwd['angle_deg']:+.0f}° ({best_fwd.get('avg_dist_m', 0):.1f}m)"
    else:
        forward_hint = "No passable forward direction"

    system = (
        "You are a robot search AI. Choose the best action to find the target object.\n"
        "RESPOND JSON ONLY: {\"action\": \"turn\"|\"forward\"|\"done\", \"angle\": <degrees>, \"reason\": \"brief\"}\n\n"
        "ACTIONS:\n"
        "- turn: Rotate to look at an unchecked direction. angle = degrees to turn (+left, -right).\n"
        "- forward: Move to a new area. angle = turn before moving (0=straight). Use when current area explored.\n"
        "- done: No passable directions left.\n\n"
        "DECISION RULES (follow strictly in order):\n"
        "1. If UNCHECKED directions exist → turn to the WIDEST one (largest distance = most space to explore).\n"
        "2. If all directions checked → forward to widest open direction to reach a new area.\n"
        "3. If wall streak is high (3+) → PREFER forward even if some directions unchecked.\n"
        "4. ONLY use angles from the lists below. Do NOT invent angles.\n"
        "5. If no passable directions and no moves left → done.\n"
    )

    # ── Scene history from exploration_log ──
    scene_history = ""
    if exploration_log:
        recent_scenes = exploration_log[-5:]
        scene_lines = []
        for i, entry in enumerate(recent_scenes):
            desc = entry.get("desc", "")[:80]
            scene_lines.append(f"  [{i+1}] \"{desc}\"")
        scene_history = "\nRECENT CAMERA VIEWS:\n" + "\n".join(scene_lines) + "\n"

    # ── Wall streak warning ──
    wall_warning = ""
    if wall_streak >= 2:
        wall_warning = f"\n⚠️ WALL STREAK: Last {wall_streak} views were walls/empty. You should FORWARD to change position.\n"

    prompt = (
        f"TARGET: \"{target}\"\n\n"
        f"UNCHECKED OPEN directions (turn to check):\n{unchecked_text}\n\n"
        f"ALREADY CHECKED:\n{checked_text}\n\n"
        f"{forward_hint}\n"
        f"{scene_history}"
        f"{wall_warning}"
        f"MOVES: {move_count}/{max_moves} | STEP: {step + 1}/{max_steps} | TURNS HERE: {turns_at_position}\n"
        f"Reply JSON:"
    )

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(12.0)) as client:
            resp = await client.post(f"{SERVER_BASE}/generate", json={
                "prompt": prompt,
                "system": system,
                "temperature": 0.3,
                "max_tokens": 128,
            })

        if resp.status_code == 200:
            text = resp.json().get("response", "")
            logger.info(f"🧠 Plan raw: {text[:200]}")

            # Parse JSON response
            action_m = re.search(r'"action"\s*:\s*"(turn|forward|done)"', text)
            angle_m = re.search(r'"angle"\s*:\s*(-?\d+(?:\.\d+)?)', text)
            reason_m = re.search(r'"reason"\s*:\s*"([^"]*)"', text)

            if action_m:
                action = action_m.group(1)
                angle = float(angle_m.group(1)) if angle_m else 0.0
                angle = max(-180, min(180, angle))
                reason = reason_m.group(1) if reason_m else ""

                # ── Post-validate: ensure turn angle is near an actual open direction ──
                if action == "turn" and open_directions:
                    valid = any(
                        abs((angle - od["angle_deg"] + 180) % 360 - 180) < 30
                        for od in open_directions
                    )
                    if not valid:
                        # LLM picked a blocked/invalid direction → override to best unchecked
                        if unchecked_open:
                            old_angle = angle
                            angle = unchecked_open[0]["angle"]
                            reason = f"corrected {old_angle:+.0f}°→{angle:+.0f}° (was blocked)"
                            logger.warning(f"🔧 LLM turn angle corrected: {old_angle:+.0f}° → {angle:+.0f}° (not near any open dir)")
                        elif open_directions:
                            # All open dirs checked → switch to forward
                            action = "forward"
                            angle = open_directions[0]["angle_deg"]
                            reason = "corrected: all open dirs checked, forwarding"
                            logger.warning(f"🔧 LLM turn corrected → forward {angle:+.0f}° (no unchecked left)")

                return {"action": action, "angle": angle, "reason": reason}
    except Exception as e:
        logger.warning(f"⚠️ LLM plan error: {e}")

    # ── Fallback: heuristic (no LLM) ──
    logger.info("🔄 LLM plan unavailable → heuristic fallback")
    return _heuristic_plan_action(
        open_directions, checked_dirs, cumulative_rotation,
        move_count, max_moves, wall_streak
    )


def _heuristic_plan_action(open_directions, checked_dirs, cumulative_rotation,
                           move_count, max_moves, wall_streak=0):
    """Fallback when LLM is unavailable: pick next action by simple heuristic."""
    checked_abs = set(round(cd["angle_abs"]) for cd in checked_dirs)

    # If wall streak is high, prefer forward to escape dead area
    if wall_streak >= 3 and move_count < max_moves and open_directions:
        best = open_directions[0]
        return {"action": "forward", "angle": best["angle_deg"],
                "reason": f"heuristic: wall streak {wall_streak}, moving to new area"}

    for od in open_directions:
        lidar_angle = od["angle_deg"]
        abs_angle = round((cumulative_rotation + lidar_angle) % 360)
        already = any(
            abs((abs_angle - ca + 180) % 360 - 180) < 25
            for ca in checked_abs
        )
        if not already:
            return {"action": "turn", "angle": lidar_angle,
                    "reason": "heuristic: unchecked open direction"}

    if move_count < max_moves and open_directions:
        best = open_directions[0]
        return {"action": "forward", "angle": best["angle_deg"],
                "reason": "heuristic: all checked, move to best open"}

    return {"action": "done", "angle": 0, "reason": "heuristic: no options left"}


async def _vlm_describe_only(timeout: float = 30.0, retry: bool = False) -> str:
    """
    📷 VLM Describe Only — capture frame and get scene description.
    Returns scene_description string (empty string on failure).
    This is the expensive step (~15-20s). Reuse the result for multiple targets.
    """
    cam = get_camera()
    frame = cam.get_frame() if cam else None
    if not frame:
        logger.warning("⚠️ No local camera frame — cannot run VLM describe")
        return ""

    frame_age = cam.get_frame_age()
    MAX_FRAME_AGE = 5.0
    if frame_age > MAX_FRAME_AGE:
        logger.warning(f"⚠️ Frame too old ({frame_age:.1f}s > {MAX_FRAME_AGE}s) — skipping VLM")
        return ""

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
        if retry:
            vlm_prompt = "What do you see? Name each object, its color, and whether it is on the left, center, or right."
        else:
            vlm_prompt = "Describe what you see. List every object with its color and position (left/center/right)."

        vlm_url = f"{SERVER_BASE}/vlm/describe-bytes"
        async with httpx.AsyncClient(timeout=timeout) as client:
            vlm_resp = await client.post(
                vlm_url,
                content=frame_small,
                headers={"Content-Type": "image/jpeg"},
                params={"prompt": vlm_prompt, "lang": "th", "max_tokens": "300"},
            )

        if vlm_resp.status_code != 200:
            logger.warning(f"⚠️ VLM Describe failed: HTTP {vlm_resp.status_code}")
            return ""

        vlm_data = vlm_resp.json()
        scene_description = vlm_data.get("text", "")
        logger.info(f"📨 VLM raw response keys: {list(vlm_data.keys())}, text_len={len(scene_description)}")

        if not scene_description:
            return ""

        # Strip English CoT prefix
        import re as _re
        _cot_prefixes = ("okay", "the user", "let me", "i need to", "first,",
                         "looking at", "the image shows", "so,", "so ", "alright",
                         "let's", "let me", "hmm", "well,")
        _first_line = scene_description.split("\n")[0].strip().lower()
        if any(_first_line.startswith(p) for p in _cot_prefixes):
            _thai_match = _re.search(r'[\u0E00-\u0E7F]', scene_description)
            if _thai_match:
                scene_description = scene_description[_thai_match.start():].strip()
            else:
                _stripped = _re.sub(
                    r'^(?:so,?\s*)?let\'?s\s+(?:look at|analyze|examine|check|see)\s+(?:the\s+)?(?:image|photo|picture|frame)[.\s]*',
                    '', scene_description, count=1, flags=_re.IGNORECASE
                ).strip()
                _stripped = _re.sub(
                    r'^(?:first,?\s*)?(?:identify|let\'?s\s+(?:check|identify|see))\s+(?:the\s+)?(?:objects|items|things)[.\s]*',
                    '', _stripped, count=1, flags=_re.IGNORECASE
                ).strip()
                if _stripped and len(_stripped) > 20:
                    scene_description = _stripped

        # Quality gate
        _VLM_MIN_USEFUL_LEN = 15
        _prompt_fragments = [
            "บอกวัตถุและตำแหน่ง", "ภาพนี้...", "บอกสีและรูปร่าง",
            "แต่ละอย่างอยู่ตำแหน่งไหน", "ภาพนี้มีสิ่งของ",
            "ตอบภาษาไทยสั้นๆ", "ซ้าย/ขวา/กลาง",
            "List all objects", "For each object", "Answer in Thai",
            "Describe what you see", "What do you see", "Name each object",
        ]
        _is_fragment = (
            len(scene_description) < _VLM_MIN_USEFUL_LEN
            or scene_description.rstrip('.').endswith('...')
            or (len(scene_description) < 100 and any(scene_description.startswith(frag) for frag in _prompt_fragments))
        )
        if _is_fragment:
            return ""

        logger.info(f"👁️ VLM Scene: {scene_description[:120]}...")
        return scene_description

    except httpx.TimeoutException:
        logger.error(f"❌ VLM describe timeout ({timeout}s)")
        return ""
    except Exception as e:
        logger.error(f"❌ VLM describe error: {e}")
        return ""


async def _llm_reason_target(target: str, scene_description: str, timeout: float = 30.0) -> tuple:
    """
    🧠 LLM Reasoning — given a scene description, check if target is present.
    Returns (found: bool, location: str, confidence: float, reason: str).
    This is the cheap step (~3-8s, text only). Can run in parallel for multiple targets.
    """
    llm_url = f"{SERVER_BASE}/generate"
    vlm_target = normalize_search_target(target)
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
        "เรื่อง confidence: ถ้าเจอวัตถุประเภทเดียวกัน (เช่น หาขวดน้ำยี่ห้อ A แต่เห็นขวดน้ำไม่ทราบยี่ห้อ) "
        "ให้ตอบ found=false แต่ให้ confidence=0.3-0.5 เพื่อบอกว่าน่าสนใจ หุ่นจะเข้าไปดูใกล้ๆ "
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

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            llm_resp = await client.post(llm_url, json={
                "prompt": llm_prompt,
                "system": llm_system,
                "temperature": 0.2,
                "max_tokens": 256,
            })

        if llm_resp.status_code != 200:
            logger.warning(f"⚠️ LLM Reasoning failed for '{target}': HTTP {llm_resp.status_code}")
            return _fallback_keyword_check(target, scene_description), "unknown", 0.5, "LLM failed, keyword fallback"

        llm_data = llm_resp.json()
        llm_text = llm_data.get("response", "")
        logger.info(f"🧠 LLM [{target}]: {llm_text[:120]}...")

        found, location, reason, confidence = _parse_llm_reasoning(llm_text)

        # Sanity checks
        vlm_suspicious = len(scene_description) > 2000
        if found and confidence < 0.7:
            found = False
        if found and vlm_suspicious and confidence < 0.9:
            found = False

        return found, location, confidence, reason

    except httpx.TimeoutException:
        logger.error(f"❌ LLM reasoning timeout for '{target}'")
        return False, "unknown", 0.0, "timeout"
    except Exception as e:
        logger.error(f"❌ LLM reasoning error for '{target}': {e}")
        return False, "unknown", 0.0, str(e)


async def _vlm_check(target: str, timeout: float, retry: bool = False) -> tuple:
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
        return False, "", "", 0.0

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
        return False, "", "", 0.0

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

        # Pure scene description prompt — do NOT mention the target.
        # If we tell VLM what we're looking for, it echoes the target name back
        # ("ป้ายที่มีตัวอักษรสีส้มตัว...") instead of describing what it actually sees.
        # LLM Reasoning (Step 2) does the matching between description and target.
        # Do NOT mention "thinking" or "ห้ามคิด" — it paradoxically triggers more thinking.
        # Server-side qwen_vlm.py appends /no_think automatically.
        if retry:
            # Alternate prompt on retry — break echo pattern with a completely different phrasing
            vlm_prompt = "What do you see? Name each object, its color, and whether it is on the left, center, or right."
        else:
            vlm_prompt = (
                "Describe what you see. List every object with its color and position (left/center/right)."
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
            return False, "", "", 0.0
        
        vlm_data = vlm_resp.json()
        scene_description = vlm_data.get("text", "")
        logger.info(f"📨 VLM raw response keys: {list(vlm_data.keys())}, text_len={len(scene_description)}, error={vlm_data.get('error','none')}")
        
        if not scene_description:
            # Log full response body to diagnose
            logger.warning(f"⚠️ VLM returned empty description — full response: {vlm_resp.text[:400]}")
            return False, "", "", 0.0
        
        # Strip English chain-of-thought prefix at Gateway level.
        # Qwen3-VL with /no_think STILL sometimes produces reasoning preamble like:
        #   "So, let's look at the image. First, identify the objects."
        # Strip these before sending to LLM to save tokens and reduce confusion.
        import re as _re
        _cot_prefixes = ("okay", "the user", "let me", "i need to", "first,",
                         "looking at", "the image shows", "so,", "so ", "alright",
                         "let's", "let me", "hmm", "well,")
        _first_line = scene_description.split("\n")[0].strip().lower()
        if any(_first_line.startswith(p) for p in _cot_prefixes):
            # Strategy A: If there's Thai content, jump to it
            _thai_match = _re.search(r'[\u0E00-\u0E7F]', scene_description)
            if _thai_match:
                scene_description = scene_description[_thai_match.start():].strip()
                logger.info(f"🔧 Gateway: stripped English CoT prefix, Thai starts at char {_thai_match.start()}")
            else:
                # Strategy B: English-only output — strip CoT sentences
                # Remove "So, let's look at the image." and similar opening sentences
                _stripped = _re.sub(
                    r'^(?:so,?\s*)?let\'?s\s+(?:look at|analyze|examine|check|see)\s+(?:the\s+)?(?:image|photo|picture|frame)[.\s]*',
                    '', scene_description, count=1, flags=_re.IGNORECASE
                ).strip()
                # Also strip "First, identify the objects." type sentences
                _stripped = _re.sub(
                    r'^(?:first,?\s*)?(?:identify|let\'?s\s+(?:check|identify|see))\s+(?:the\s+)?(?:objects|items|things)[.\s]*',
                    '', _stripped, count=1, flags=_re.IGNORECASE
                ).strip()
                if _stripped and len(_stripped) > 20:
                    logger.info(f"🔧 Gateway: stripped English CoT prefix ({len(scene_description)}→{len(_stripped)} chars)")
                    scene_description = _stripped
        
        # ─── VLM quality gate: reject garbage outputs only ───
        # Only reject: prompt echoes, truncated fragments, too-short outputs.
        # ACCEPT English output — VLM (Qwen3-VL) naturally produces English and
        # the LLM Reasoning engine is configured to handle both Thai and English.
        # Previous gate rejected english(0%thai) which killed ~50% of valid outputs.
        _VLM_MIN_USEFUL_LEN = 15  # Real scene descriptions need ~15+ chars
        _prompt_fragments = [
            "บอกวัตถุและตำแหน่ง", "ภาพนี้...", "บอกสีและรูปร่าง",
            "แต่ละอย่างอยู่ตำแหน่งไหน", "ภาพนี้มีสิ่งของ",
            "ตอบภาษาไทยสั้นๆ", "ซ้าย/ขวา/กลาง",
            # English prompt fragments (current + alternate prompts)
            "List all objects", "For each object", "Answer in Thai",
            "Describe what you see", "What do you see", "Name each object",
        ]
        
        # Only reject short outputs as prompt_echo. Long outputs (>100 chars) that
        # happen to start with a prompt fragment contain real content after the fragment.
        _is_fragment = (
            len(scene_description) < _VLM_MIN_USEFUL_LEN
            or scene_description.rstrip('.').endswith('...')
            or (len(scene_description) < 100 and any(scene_description.startswith(frag) for frag in _prompt_fragments))
        )
        if _is_fragment:
            reason = "too_short" if len(scene_description) < _VLM_MIN_USEFUL_LEN else \
                     "trailing_dots" if scene_description.rstrip('.').endswith('...') else \
                     "prompt_echo" if any(scene_description.startswith(f) for f in _prompt_fragments) else "unknown"
            logger.warning(f"⚠️ VLM rejected [{reason}] ({len(scene_description)} chars): '{scene_description[:80]}'")
            return False, "", f"__REJECTED__:{reason}:{scene_description[:100]}", 0.0
        
        # Log language mix for debugging (but don't reject)
        _thai_chars = len(_re.findall(r'[\u0E00-\u0E7F]', scene_description))
        _total_alpha = len(_re.findall(r'[a-zA-Z\u0E00-\u0E7F]', scene_description))
        _thai_ratio = _thai_chars / max(_total_alpha, 1)
        if _thai_ratio < 0.3:
            logger.info(f"📝 VLM output is English ({_thai_ratio:.0%} Thai) — passing to LLM for reasoning")
        
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
            "เรื่อง confidence: ถ้าเจอวัตถุประเภทเดียวกัน (เช่น หาขวดน้ำยี่ห้อ A แต่เห็นขวดน้ำไม่ทราบยี่ห้อ) "
            "ให้ตอบ found=false แต่ให้ confidence=0.3-0.5 เพื่อบอกว่าน่าสนใจ หุ่นจะเข้าไปดูใกล้ๆ "
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
            return _fallback_keyword_check(target, scene_description), "unknown", scene_description, 0.5
        
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
        
        return found, location, scene_description, confidence
        
    except httpx.TimeoutException:
        logger.error(f"❌ VLM/LLM check timeout ({timeout}s)")
        return False, "", "", 0.0
    except Exception as e:
        logger.error(f"❌ VLM+LLM check error: {e}")
        return False, "", "", 0.0


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
    ROTATION_CAL = 0.95         # Increased from 0.87 — was undershooting 10-20°
    current_location = location
    
    for step in range(MAX_APPROACH_STEPS):
        if _search_cancel:
            logger.info("🛑 Approach cancelled")
            break
        
        logger.info(f"🚶 Approach step {step + 1}/{MAX_APPROACH_STEPS}: object at '{current_location}'")
        
        # ── LiDAR proximity check: stop if too close to anything ahead ──
        if not MOCK_ROBOT:
            can_fit, front_clearance = _obstacle_avoidance.can_robot_fit(0.0)
            if front_clearance < 0.25:
                logger.info(
                    f"   📏 LiDAR: front clearance {front_clearance:.2f}m < 0.25m — "
                    f"object is right in front! Stopping approach."
                )
                await speak_tts(f"ถึงแล้วครับ {target} อยู่ตรงหน้า")
                break
        
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
        
        # ── Step C: Move forward (with LiDAR obstacle protection) ──
        if not MOCK_ROBOT:
            # Pre-move LiDAR check — don't drive into obstacles
            obs = await _obstacle_avoidance.check_and_avoid()
            if obs and obs.get("obstacle_detected"):
                obs_dist = obs.get("distance", 0)
                logger.warning(
                    f"   🚧 APPROACH BLOCKED: obstacle at {obs_dist:.2f}m! "
                    f"Object may be behind an obstacle. Stopping approach."
                )
                await speak_tts("มีสิ่งกีดขวางขวางทาง ไม่สามารถเข้าถึงได้ครับ")
                break
            
            move_cmd = {
                "type": "move",
                "linear_x": APPROACH_SPEED,
                "angular_z": 0.0,
                "duration": move_dur,
            }
            completed = await motion.exec_motion(move_cmd, obstacle_checker=_lidar_obstacle_check)
            if not completed:
                logger.warning("   🛑 Approach move interrupted by LiDAR obstacle!")
                await speak_tts("ตรวจพบสิ่งกีดขวาง หยุดเข้าใกล้ครับ")
                break
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
            "description": description[:200] if description else "",
        })
        
        # ── Step D: Re-check with VLM — is the object still visible + how close? ──
        found, new_location, new_desc, _approach_conf = await _vlm_check(target, 30.0)
        
        if found:
            current_location = new_location
            description = new_desc  # update for next broadcast
            logger.info(f"   👁️ Still visible at '{new_location}' (conf={_approach_conf:.2f})")
            
            # Check if we're close enough:
            # 1. VLM says bottom/near + center-ish
            # 2. OR high confidence + step >= 2 (we've been approaching for a while)
            # 3. OR step >= 3 regardless (safety: don't keep walking forever)
            is_close = any(k in new_location for k in ["near", "bottom"])
            is_center = "center" in new_location or "middle" in new_location or ("left" not in new_location and "right" not in new_location)
            
            if (is_close and is_center) or (step >= 2 and _approach_conf >= 0.8 and is_center) or step >= 4:
                reason_text = "center/near" if (is_close and is_center) else f"conf={_approach_conf:.1f},step={step+1}" if step >= 2 else f"max_steps"
                logger.info(f"   ✅ Close enough! ({reason_text}) — stopping approach")
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
        robot_x=_robot_pose.get("x", 0.0),
        robot_y=_robot_pose.get("y", 0.0),
        robot_theta=_robot_pose.get("theta", 0.0),
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
        "robot_x": round(_robot_pose.get("x", 0.0), 3),
        "robot_y": round(_robot_pose.get("y", 0.0), 3),
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
    🔍 Multi-Object Visual Search — One VLM, Many LLM (parallel).
    
    Optimization: VLM describe (~15-20s) runs ONCE per frame,
    then LLM reasoning (~3-8s each) runs in PARALLEL for ALL targets.
    
    N targets now take: 1×VLM + 1×LLM (parallel) instead of N×(VLM+LLM).
    Example: 3 targets: ~25s instead of ~75s.
    
    Args:
        targets: List of object names to find (e.g., ["ขวดน้ำ", "หมวก"])
    """
    global _search_active, _search_cancel
    _search_active = True
    _search_cancel = False
    
    found_objects = []
    not_found_objects = []
    
    logger.info(f"═" * 50)
    logger.info(f"🔍 MULTI-OBJECT SEARCH (One VLM, Many LLM): {targets}")
    logger.info(f"═" * 50)
    
    # Announce start
    targets_text = " กับ ".join(targets)
    await speak_tts(f"กำลังค้นหา {targets_text} ครับ")
    
    await _broadcast_and_cache({
        "type": "search_status",
        "status": "multi_started",
        "targets": targets,
    })
    
    if _search_cancel:
        await _search_cancelled(targets_text)
        return
    
    # ── Step 1: ONE VLM describe call (the expensive step) ──
    logger.info(f"👁️ VLM Describe: single call for all {len(targets)} targets...")
    scene_description = await _vlm_describe_only(timeout=30.0)
    
    if not scene_description:
        # Retry with alternate prompt
        logger.info(f"🔄 VLM empty — retrying with alternate prompt...")
        scene_description = await _vlm_describe_only(timeout=30.0, retry=True)
    
    if not scene_description:
        logger.warning(f"⚠️ VLM describe failed — cannot check any targets")
        not_found_objects = list(targets)
    else:
        # ── Step 2: PARALLEL LLM reasoning for ALL targets ──
        remaining_targets = [t for t in targets if not _search_cancel]
        logger.info(f"🧠 LLM Reasoning: checking {len(remaining_targets)} targets in parallel...")
        
        async def _check_one_target(t):
            """LLM reason for one target against shared scene description."""
            found, location, confidence, reason = await _llm_reason_target(t, scene_description, timeout=30.0)
            return t, found, location, confidence, reason, scene_description
        
        # Run all LLM calls in parallel
        results = await asyncio.gather(
            *[_check_one_target(t) for t in remaining_targets],
            return_exceptions=True,
        )
        
        for result in results:
            if isinstance(result, Exception):
                logger.error(f"❌ LLM parallel error: {result}")
                continue
            
            t, found, location, confidence, reason, desc = result
            logger.info(f"   {'✅' if found else '❌'} {t}: found={found}, location={location}, conf={confidence}")
            if reason:
                logger.info(f"      💭 {reason}")
            
            if found:
                found_objects.append({
                    "name": t,
                    "location": location,
                    "description": desc,
                })
                
                # Remember in memory with current robot pose
                normalized = normalize_search_target(t)
                object_memory.remember(
                    object_name=normalized,
                    display_name=t,
                    location=location,
                    location_description="",
                    confidence=confidence,
                    robot_x=_robot_pose.get("x", 0.0),
                    robot_y=_robot_pose.get("y", 0.0),
                    robot_theta=_robot_pose.get("theta", 0.0),
                )
                
                await _broadcast_and_cache({
                    "type": "search_status",
                    "status": "found",
                    "target": t,
                    "location": location,
                    "description": desc,
                    "robot_x": _robot_pose.get("x", 0.0),
                    "robot_y": _robot_pose.get("y", 0.0),
                })
            else:
                not_found_objects.append(t)
    
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
            ROTATION_CALIBRATION = 0.95   # Increased from 0.87 — was undershooting 10-20°
            
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
_nav2_client = Nav2Client(rosbridge_url=ROSBRIDGE) if USE_NAV2 else None


def _lidar_obstacle_check() -> bool:
    """Synchronous check for LiDAR obstacle — passed as callback to exec_motion."""
    return _obstacle_avoidance.is_obstacle_detected and _obstacle_avoidance.min_distance < 0.30


# ── Camera Frame Push (Gateway → Server) ─────────────────────────────

# ── Robot Pose Tracking (from /odom via ROSBridge) ────────────────────
_robot_pose = {"x": 0.0, "y": 0.0, "theta": 0.0}
_odom_subscribed = False
_odom_msg_count = 0
_odom_source = "none"  # "odom_hybrid" | "dead_reckoning" | "none"
_odom_xy_moving = False  # True if /odom x,y actually changes over time

# Track whether /odom provides real x,y or just yaw
_odom_first_pos = None  # (x, y) from first odom message
_odom_xy_drift = 0.0    # cumulative x,y drift from first position

def _odom_callback(msg):
    """Extract theta from /odom. MyAGV odom provides reliable yaw but
    x,y is often stuck near 0 due to Mecanum wheel encoder limitations.
    We use odom ONLY for theta; dead reckoning handles x,y."""
    global _odom_msg_count, _odom_source, _odom_first_pos, _odom_xy_drift, _odom_xy_moving
    try:
        pos = msg["pose"]["pose"]["position"]
        ori = msg["pose"]["pose"]["orientation"]
        # Quaternion → yaw
        siny = 2.0 * (ori["w"] * ori["z"] + ori["x"] * ori["y"])
        cosy = 1.0 - 2.0 * (ori["y"] ** 2 + ori["z"] ** 2)
        theta = math.atan2(siny, cosy)

        # Always use odom theta — it's reliable from IMU/encoders
        _robot_pose["theta"] = theta

        # Track whether /odom x,y actually changes
        ox, oy = pos["x"], pos["y"]
        if _odom_first_pos is None:
            _odom_first_pos = (ox, oy)
        else:
            drift = math.sqrt((ox - _odom_first_pos[0])**2 + (oy - _odom_first_pos[1])**2)
            if drift > _odom_xy_drift:
                _odom_xy_drift = drift
            # If odom x,y has moved >5cm total, it's working — use it for position too
            if _odom_xy_drift > 0.05:
                _odom_xy_moving = True
                _robot_pose["x"] = ox
                _robot_pose["y"] = oy

        _odom_source = "odom_hybrid"
        _odom_msg_count += 1
        if _odom_msg_count <= 3 or _odom_msg_count % 200 == 0:
            logger.info(
                f"📍 Odom #{_odom_msg_count}: x={ox:.3f} y={oy:.3f} θ={math.degrees(theta):.1f}° "
                f"[xy_drift={_odom_xy_drift:.3f}m, xy_live={'YES' if _odom_xy_moving else 'NO→DR'}]"
            )
    except (KeyError, TypeError) as e:
        _odom_msg_count += 1
        if _odom_msg_count <= 5:
            logger.warning(f"⚠️ Odom parse error #{_odom_msg_count}: {e} — keys={list(msg.keys()) if isinstance(msg, dict) else type(msg)}")

def _update_dead_reckoning(linear_x: float, angular_z: float, duration: float):
    """Update robot pose x,y via dead reckoning from cmd_vel commands.
    Always updates x,y unless /odom x,y has been verified as working.
    Theta is always from /odom when available (more accurate than DR)."""
    global _odom_source
    # If /odom x,y is verified working, skip DR for position
    if _odom_xy_moving:
        return
    if _odom_source == "none":
        _odom_source = "dead_reckoning"
    # If odom is connected, theta comes from odom — only update x,y here
    # If odom is NOT connected, also update theta
    if _odom_msg_count == 0:
        # No odom at all — DR handles everything
        _robot_pose["theta"] += angular_z * duration
        while _robot_pose["theta"] > math.pi:
            _robot_pose["theta"] -= 2 * math.pi
        while _robot_pose["theta"] < -math.pi:
            _robot_pose["theta"] += 2 * math.pi
    # Always update x,y from dead reckoning (odom x,y is broken)
    if abs(linear_x) > 0.001:
        dist = linear_x * duration
        _robot_pose["x"] += dist * math.cos(_robot_pose["theta"])
        _robot_pose["y"] += dist * math.sin(_robot_pose["theta"])

async def _subscribe_odom():
    """Subscribe to /odom via ROSBridge for robot position tracking.
    Retries up to 5 times with 10s delay if ROSBridge isn't ready at boot."""
    global _odom_subscribed
    if MOCK_ROBOT:
        logger.info("📍 MOCK_ROBOT=1 — skipping odom subscription")
        return
    if _odom_subscribed:
        return
    MAX_RETRIES = 5
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            import roslibpy
            logger.info(f"📍 Odom subscription attempt {attempt}/{MAX_RETRIES} via {ROSBRIDGE}...")
            ros = await ensure_ros(ROSBRIDGE)
            logger.info(f"📍 ROSBridge connected (is_connected={ros.is_connected})")
            topic = roslibpy.Topic(ros, "/odom", "nav_msgs/Odometry")
            topic.subscribe(_odom_callback)
            _odom_subscribed = True
            logger.info("📍 Subscribed to /odom — robot pose tracking active")
            return
        except Exception as e:
            logger.warning(f"⚠️ Odom attempt {attempt}/{MAX_RETRIES} failed: {e}")
            if attempt < MAX_RETRIES:
                await asyncio.sleep(10)

async def _push_pose_to_server():
    """Background task: push robot pose to Server every ~500ms."""
    push_url = f"{SERVER_BASE}/map/pose"
    await asyncio.sleep(5)  # Wait for odom to start
    push_count = 0
    async with httpx.AsyncClient(timeout=3.0) as client:
        fail_count = 0
        while True:
            try:
                pose_data = {**_robot_pose, "source": _odom_source}
                resp = await client.post(push_url, json=pose_data)
                if resp.status_code == 200:
                    fail_count = 0
                    push_count += 1
                    if push_count <= 3 or push_count % 200 == 0:
                        logger.debug(f"📍 Pose push #{push_count}: x={_robot_pose['x']:.3f} y={_robot_pose['y']:.3f} → {push_url}")
                else:
                    fail_count += 1
            except Exception:
                fail_count += 1
                if fail_count <= 3 or fail_count % 100 == 0:
                    logger.debug(f"📍 Pose push failed (#{fail_count})")
            await asyncio.sleep(0.5)

async def _push_objects_to_server():
    """Background task: push object memory to Server every ~5s."""
    push_url = f"{SERVER_BASE}/map/objects"
    await asyncio.sleep(8)
    async with httpx.AsyncClient(timeout=3.0) as client:
        while True:
            try:
                # Build marker list from object memory
                markers = []
                for obj_name in object_memory.get_all_objects():
                    loc = object_memory.recall(obj_name)
                    if loc:
                        markers.append({
                            "name": loc.display_name or obj_name,
                            "location": loc.location,
                            "location_description": loc.location_description,
                            "confidence": loc.confidence,
                            "age_hours": round(loc.age_hours(), 1),
                            "robot_x": loc.robot_x,
                            "robot_y": loc.robot_y,
                        })
                await client.post(push_url, json=markers)
            except Exception:
                pass
            await asyncio.sleep(5.0)

# ── Gateway endpoint for robot pose (webapp can also poll this) ───────
@app.get("/robot/pose")
async def get_robot_pose():
    return _robot_pose

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
    
    # Start robot pose tracking + push
    motion.post_motion_hook = _update_dead_reckoning  # dead-reckoning fallback
    asyncio.create_task(_subscribe_odom())
    asyncio.create_task(_push_pose_to_server())
    asyncio.create_task(_push_objects_to_server())
    logger.info("📍 Robot pose + object memory push tasks started")
    
    # Start obstacle avoidance (LiDAR subscriber) — with retry on failure
    if not MOCK_ROBOT:
        asyncio.create_task(_start_lidar_with_retry())

async def _start_lidar_with_retry():
    """Try to start LiDAR/obstacle avoidance, retry up to 5 times if ROSBridge isn't ready."""
    MAX_RETRIES = 5
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            ros = await ensure_ros(ROSBRIDGE)
            await _obstacle_avoidance.start(ros)
            logger.info(f"✅ Obstacle avoidance active (attempt {attempt})")
            return
        except Exception as e:
            logger.warning(f"⚠️ LiDAR attempt {attempt}/{MAX_RETRIES} failed: {e}")
            if attempt < MAX_RETRIES:
                await asyncio.sleep(10)
    logger.error("❌ LiDAR/obstacle avoidance failed after all retries — running without LiDAR")

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
    status = _obstacle_avoidance.get_status()
    # Add robot body model info
    from gateway.obstacle_avoidance import (
        ROBOT_WIDTH_M, ROBOT_LENGTH_M, ROBOT_MIN_PASSAGE_M,
        CAMERA_FOV_H_DEG, CAMERA_MOUNT_HEIGHT_M,
    )
    status["robot_body"] = {
        "width_m": ROBOT_WIDTH_M,
        "length_m": ROBOT_LENGTH_M,
        "min_passage_m": ROBOT_MIN_PASSAGE_M,
        "camera_fov_deg": CAMERA_FOV_H_DEG,
        "camera_height_m": CAMERA_MOUNT_HEIGHT_M,
    }
    return status

@app.get("/nav2/status")
async def nav2_status():
    """Get Nav2 navigation status."""
    if not USE_NAV2 or not _nav2_client:
        return {"enabled": False, "reason": "USE_NAV2=0"}
    return {
        "enabled": True,
        "connected": _nav2_client.connected,
        "navigating": _nav2_client.is_navigating,
        "status": _nav2_client.nav_status_name,
        "feedback": _nav2_client.feedback,
    }

@app.get("/obstacle/directions")
async def obstacle_directions():
    """Get LiDAR 360° sector analysis — best direction to travel."""
    return _obstacle_avoidance.find_best_direction()

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
            "robot_x": round(h.robot_x, 3),
            "robot_y": round(h.robot_y, 3),
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
            "robot_x": round(h.robot_x, 3),
            "robot_y": round(h.robot_y, 3),
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
    # Also clear server-side map markers immediately
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            await client.post(f"{SERVER_BASE}/map/objects", json=[])
    except Exception:
        pass
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
                    await motion.exec_motion(motion_cmd, obstacle_checker=_lidar_obstacle_check)
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
                        await motion.exec_motion(cmd, obstacle_checker=_lidar_obstacle_check)
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