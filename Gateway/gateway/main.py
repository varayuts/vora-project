import os, asyncio, json, re, logging, math, time
from typing import Optional, List, Dict
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import Response, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import httpx
import websockets
from typing import Any, Dict

from gateway.audio_proxy import upstream_stt_proxy
from gateway.intent_parser import (
    parse_intent, parse_find_intent, parse_multi_intent, 
    normalize_search_target, parse_find_multi_objects, parse_find_with_description
)
from gateway.ros_cmd import MotionPublisher, WaypointSender, ensure_ros, GOAL_FRAME, USE_ACTION
from gateway.obstacle_avoidance import ObstacleAvoidance
from gateway.camera_stream import get_camera, start_camera, stop_camera
from gateway.object_memory import (
    object_memory, get_section, location_to_bearing,
    set_server_base as _set_object_memory_server_base,
)
from gateway.spatial_memory import spatial_memory
from gateway.semantic_map import semantic_map, Zone, Landmark
from gateway.search_planner import SearchPlanner, extract_zone_intent, find_memory_anchor
# Legacy Gateway-side Nav2 roslibpy ActionClient path is disabled.
# See gateway/robot_nav_bridge.py for the authoritative /vora/command route.
from gateway.robot_nav_bridge import RobotNavBridge as Nav2Client

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
logger.info(f"🧭 USE_NAV2:     {USE_NAV2}" + (" ← Nav2 handles forward movement" if USE_NAV2 else " ← Legacy cmd_vel mode"))
logger.info("═" * 60)

# Shared httpx client — reuses TCP connections across all VLM/LLM calls.
# Default timeout is generous; individual calls override with timeout= kwarg.
_http: httpx.AsyncClient = httpx.AsyncClient(timeout=httpx.Timeout(30.0))

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
        dead: List[WebSocket] = []
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except Exception:
                dead.append(connection)
        for d in dead:
            try:
                self.active_connections.remove(d)
            except ValueError:
                pass

manager = ConnectionManager()

# --- Gateway → Server Connection ---
server_gateway_ws: Optional[websockets.WebSocketClientProtocol] = None
ROBOT_ID = os.getenv("ROBOT_ID", "myagv")

async def connect_to_server():
    """🔌 Connect Gateway to Server (/pipeline/gateway) to receive commands.

    Handles: initial VPN delay, exponential backoff, graceful reconnect on
    service restart (close code 1012), and clean state reset between sessions.
    """
    global server_gateway_ws

    # แปลง https → wss
    server_ws_url = SERVER_BASE.replace("https://", "wss://").replace("http://", "ws://")
    gateway_endpoint = f"{server_ws_url}/pipeline/gateway"

    # Brief initial wait — let Tailscale establish the VPN tunnel and
    # let the local ROSBridge connection start before hitting the Server.
    await asyncio.sleep(3)
    logger.info(f"🔌 Connecting to Server Gateway: {gateway_endpoint}")

    retry_delay = 3   # seconds — start short, grow with each failure
    attempt = 0

    while True:
        try:
            attempt += 1
            async with websockets.connect(
                gateway_endpoint,
                max_size=8*1024*1024,
                ping_interval=20,        # send ping every 20s to detect dead connections
                ping_timeout=10,          # wait 10s for pong before closing
                close_timeout=5,          # don't hang on close handshake
            ) as ws:
                server_gateway_ws = ws
                retry_delay = 3   # reset backoff on successful connect
                attempt = 0

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

        except websockets.ConnectionClosedError as e:
            server_gateway_ws = None
            if e.rcvd and e.rcvd.code == 1012:
                # 1012 = Service Restart — server is restarting, reconnect quickly
                logger.info(f"🔄 Server restarting (close 1012) — reconnecting in 2s...")
                await asyncio.sleep(2)
                retry_delay = 3  # reset backoff — this is expected
                continue
            elif e.rcvd and e.rcvd.code == 1001:
                # 1001 = Going Away — server shutting down gracefully
                logger.info(f"🔌 Server going away (close 1001) — reconnecting in {retry_delay}s...")
            else:
                close_code = e.rcvd.code if e.rcvd else "none"
                logger.warning(f"⚠️ Server WS closed (code={close_code}, attempt {attempt}): {e}")
        except websockets.ConnectionClosed as e:
            server_gateway_ws = None
            logger.info(f"🔌 Server WS closed normally — reconnecting in {retry_delay}s...")
        except (ConnectionRefusedError, OSError) as e:
            server_gateway_ws = None
            logger.warning(f"⚠️ Server unreachable (attempt {attempt}): {type(e).__name__}: {e}")
        except Exception as e:
            server_gateway_ws = None
            logger.error(f"❌ Server Gateway error (attempt {attempt}) [{type(e).__name__}]: {e}")

        logger.info(f"⏳ Retrying Server connection in {retry_delay}s...")
        await asyncio.sleep(retry_delay)
        retry_delay = min(retry_delay * 2, 30)  # exponential backoff, cap at 30s


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

        resp = await _http.post(tts_url, json={"text": text, "voice": "default", "speed": 1.0}, timeout=15.0)

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


def speak_tts_bg(text: str):
    """Fire-and-forget TTS — launch speak_tts as a background task.
    Use inside visual_search loop where TTS must NOT block the search."""
    asyncio.create_task(speak_tts(text))


# ============ Visual Search (Find Object) ============
# State: allows cancellation via "หยุด" command during search
_search_active = False
_search_cancel = False
_search_cancel_event: asyncio.Event = asyncio.Event()   # instant wakeup for _cancellable_vlm_check
_last_search_result: Optional[Dict] = None   # cached for polling from webapp (mixed-content WS fallback)
_current_search_target: str = ""              # tracks active search target for duplicate-query dedup
_memory_block_until: float = 0.0              # epoch-secs; remember() writes are suppressed until this time (set by relocalize)


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
        except Exception as _fwd_e:
            logger.debug(f"WS forward failed [{type(_fwd_e).__name__}] — server may be restarting")

async def cancel_search():
    """Cancel any active visual search"""
    global _search_cancel
    if _search_active:
        _search_cancel = True
        _search_cancel_event.set()
        logger.info("🛑 Visual search cancel requested")


async def _llm_enrich_search_query(raw_query: str, _retries: int = 1) -> str:
    """
    🧠 LLM-based query enrichment for detailed search commands.

    Extracts compact keyword-style English description from Thai search command.
    Falls back to raw_query on failure. Retries once on transport error.
    Timeout: 10s (enrichment is non-critical, must not delay search start).
    """
    system = (
        "Extract search keywords from the Thai command. "
        "Output ONLY comma-separated English keywords: object type, brand, color, features, direction. "
        "Example: 'water bottle, Avias, white label, bird logo, right side'. "
        "No sentences. No explanation. Keywords only."
    )
    prompt = f"Command: \"{raw_query}\"\nKeywords:"

    for attempt in range(_retries + 1):
        try:
            resp = await _http.post(f"{SERVER_BASE}/generate", json={
                    "prompt": prompt,
                    "system": system,
                    "temperature": 0.2,
                    "max_tokens": 80,
                }, timeout=10.0)

            if resp.status_code == 200:
                enriched = _strip_think_blocks(resp.json().get("response", "")).strip()
                if enriched and 5 < len(enriched) < 200:
                    logger.info(f"🧠 LLM enriched: '{raw_query}' → '{enriched[:100]}'")
                    return enriched
                else:
                    logger.warning(f"⚠️ LLM enrichment bad result ({len(enriched)} chars): '{(enriched or '')[:80]}'")
                    return raw_query  # bad output, no retry needed
            else:
                logger.warning(f"⚠️ LLM enrichment HTTP {resp.status_code}")
                return raw_query  # server error, no retry

        except httpx.TimeoutException:
            logger.warning(f"⚠️ LLM enrichment timeout (10s) attempt {attempt + 1}/{_retries + 1}")
        except httpx.ConnectError as e:
            logger.warning(f"⚠️ LLM enrichment ConnectError attempt {attempt + 1}/{_retries + 1}: {e}")
        except Exception as e:
            logger.warning(f"⚠️ LLM enrichment [{type(e).__name__}] attempt {attempt + 1}/{_retries + 1}: '{e}'")

        if attempt < _retries:
            await asyncio.sleep(1.0)  # brief pause before retry

    logger.warning("⚠️ LLM enrichment failed after retries — using original query")
    return raw_query


async def visual_search(
    target_object: str,
    max_move_cycles: int = 4,            # max move-then-scan cycles
    move_duration: float = 2.7,          # seconds to move forward per step (~0.40m at 0.15 m/s)
    move_speed: float = 0.15,            # m/s (increased from 0.10 — still safe with LiDAR check every 0.1s)
    vlm_timeout: float = 75.0,           # total budget per VLM+LLM check (split: 45s VLM + 30s LLM)
    scan_directions: int = 4,            # 4 directions = 360° (90° × 4)
    display_name: str = "",              # Thai-friendly name for TTS/UI (target_object used for VLM)
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
    global _search_active, _search_cancel, _current_zone, _current_search_target
    _search_active = True
    _search_cancel = False
    _search_cancel_event.clear()
    _current_search_target = target_object

    # Display name for TTS/UI — use original Thai target, not enriched English
    _display = display_name or target_object
    
    # Set cancel checker on motion publisher so it can interrupt mid-motion
    motion._cancel_checker = lambda: _search_cancel
    
    total_checks = 0
    SCAN_ROTATION_CAL = 0.95  # Increased from 0.87 — was undershooting 10-20°
    
    logger.info(f"═" * 50)
    logger.info(f"🔍 VISUAL SEARCH START: '{target_object}'")
    logger.info(f"   Strategy: Rotate-Scan-First (360° scan → move → repeat)")
    logger.info(f"   max_move_cycles={max_move_cycles}, scan_dirs={scan_directions}")
    logger.info(f"   Pipeline: VLM Describe → LLM Reasoning")
    logger.info(f"═" * 50)
    
    # Normalize target name to official item if possible
    normalized_target = normalize_search_target(_display)

    # [SAFETY] Reset Nav2 abort counter at the start of each new search so
    # stale failures from a previous attempt can't permanently block nav.
    try:
        if _nav2_client is not None and hasattr(_nav2_client, "reset_failure_guard"):
            _nav2_client.reset_failure_guard()
    except Exception:
        pass

    # ── [MEM] Authoritative reload before search ──
    # Fetch the authoritative object memory from the Server so anything
    # deleted/edited via the UI takes effect *now*. Local Gateway disk
    # is no longer consulted — see object_memory.set_server_base().
    try:
        _mem_count = object_memory.reload()
        logger.info(
            f"[MEM] loaded objects: {len(object_memory.get_all_objects())} "
            f"records: {_mem_count}"
        )
    except Exception as _e:
        logger.warning(f"[MEM] reload failed: {_e}")

    # Check memory for previous location.
    # [MEM] Zone-filter: if the user's query explicitly names a target room
    # and the robot is currently in a DIFFERENT zone, memory is untrusted
    # for this search — stale cross-zone hints were biasing TTS and the
    # decision layer into wrong-room wandering. Ignore the hint entirely.
    memory_hint = object_memory.get_search_hint(normalized_target)
    try:
        _early_tgt_zone, _ = extract_zone_intent(
            f"{_display} {normalized_target}", semantic_map
        )
    except Exception:
        _early_tgt_zone = None
    if memory_hint and _early_tgt_zone:
        _cz_obj = semantic_map.get_zone_at(
            _robot_pose.get("x", 0.0), _robot_pose.get("y", 0.0)
        )
        _cz_id = _cz_obj.id if _cz_obj else _current_zone
        if _cz_id != _early_tgt_zone:
            logger.info(
                f"[MEM] ignoring memory_hint — target_zone='{_early_tgt_zone}' "
                f"!= current_zone='{_cz_id}' (cross-zone memory is untrusted)"
            )
            memory_hint = None
    if memory_hint:
        logger.info(f"📚 Memory hint: {memory_hint}")

    # ── Split OBJECT from ZONE for VLM/LLM matching ──
    # User queries like "ขวดน้ำที่นั่งเล่น" have an object part ("ขวดน้ำ") and a
    # zone part ("ที่นั่งเล่น"). The zone decides WHERE to search; once the
    # robot is in the right zone, confirmation must focus on the OBJECT only,
    # not require the VLM scene description to literally repeat the room name.
    # Otherwise correct sightings get rejected because "ที่นั่งเล่น" is missing
    # from the scene text. We strip zone-phrase tokens here and pass the
    # cleaned target to all VLM/LLM checks. The original `target_object` is
    # left untouched for TTS/UI/memory paths.
    try:
        from gateway.search_planner import _ZONE_ALIASES as _SP_ZONE_ALIASES
    except Exception:
        _SP_ZONE_ALIASES = {}

    def _strip_zone_phrase(text: str, zone_id) -> str:
        if not text or not zone_id:
            return text
        z = semantic_map.get_zone(zone_id)
        toks = set()
        if z:
            for s in (z.id, getattr(z, "label_en", None), getattr(z, "label_th", None)):
                if s:
                    toks.add(s.strip().lower())
        for alias, zid in _SP_ZONE_ALIASES.items():
            if zid == zone_id and alias:
                toks.add(alias.strip().lower())
        out = text
        for t in sorted(toks, key=len, reverse=True):
            matched = False
            for prefix in ("ที่ห้อง", "ที่", "ห้อง", "in the ", "in ", "at the ", "at "):
                needle = prefix + t
                idx = out.lower().find(needle)
                if idx >= 0:
                    out = out[:idx] + " " + out[idx + len(needle):]
                    matched = True
                    break
            if not matched:
                idx = out.lower().find(t)
                if idx >= 0:
                    out = out[:idx] + " " + out[idx + len(t):]
        return " ".join(out.split()).strip(" ,.")

    _vlm_query_target = _strip_zone_phrase(target_object, _early_tgt_zone) or target_object
    if _vlm_query_target != target_object:
        logger.info(
            f"[ZONE] split target: object='{_vlm_query_target}' "
            f"zone='{_early_tgt_zone}' (was '{target_object}')"
        )
    
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
    
    colocation_context = spatial_memory.build_colocation_context(_display, max_age_min=20)
    if colocation_context:
        logger.info(f"📌 Co-location hints:\n{colocation_context}")
    
    # ── LLM Co-location Advisor: classify target → likely locations ──
    colocation_llm_hint = ""
    if spatial_summary:
        try:
            _coloc_system = (
                "คุณเป็น AI ช่วยค้นหาวัตถุ วิเคราะห์ว่าวัตถุเป้าหมายน่าจะอยู่ใกล้สิ่งของชนิดใด "
                "จากข้อมูลที่สำรวจมาแล้ว ตอบสั้นๆ 1-2 ประโยค ระบุบริเวณหรือจุดสังเกตที่น่าไปก่อน "
                "หมายเหตุ: พิกัด (x,y) คือตำแหน่งหุ่นตอนเห็น ไม่ใช่ตำแหน่งวัตถุ "
                "ถ้าไม่แน่ใจ ตอบ 'ไม่มีข้อมูลเพียงพอ'"
            )
            _coloc_prompt = (
                f"เป้าหมาย: ค้นหา \"{target_object}\"\n\n"
                f"สิ่งที่เคยเห็นตามจุดต่างๆ (พิกัดคือตำแหน่งหุ่นตอนสำรวจ ไม่ใช่ตำแหน่งของ):\n{spatial_summary}\n\n"
            )
            if colocation_context:
                _coloc_prompt += f"ข้อมูล co-location:\n{colocation_context}\n\n"
            _coloc_prompt += (
                f"วิเคราะห์: วัตถุชนิดนี้น่าจะอยู่ใกล้อะไร? "
                f"บริเวณไหนที่เคยสำรวจแล้วน่าจะมีมากที่สุด?"
            )
            _coloc_resp = await _http.post(f"{SERVER_BASE}/generate", json={
                    "prompt": _coloc_prompt,
                    "system": _coloc_system,
                    "temperature": 0.3,
                    "max_tokens": 128,
                }, timeout=5.0)
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
        "target": _display,
        "normalized_target": normalized_target,
        "memory_hint": memory_hint,
        "colocation_hint": colocation_llm_hint,
    })
    
    # 🔊 TTS: Announce search start + memory hint
    announce = f"กำลังค้นหา {_display} ครับ"
    if memory_hint:
        announce += f" {memory_hint}"
    elif colocation_llm_hint:
        announce += f" {colocation_llm_hint[:60]}"
    speak_tts_bg(announce)

    arrived_zone = False  # GO-TO-ZONE state: True after successful navigate_to_zone (locks LOCAL mode)
    stale_count = 0  # track consecutive stale/empty VLM checks (camera/VLM truly dead)
    vlm_reject_count = 0  # track consecutive VLM quality-gate rejections (VLM working but bad output)
    wall_empty_count = 0  # consecutive "wall/empty/floor-only" scenes → stop moving
    good_vlm_count = 0  # total VLM checks that passed quality gate (non-fragment)
    consecutive_found = 0  # consecutive found=true results (for confirmation)
    partial_approach_count = 0  # how many times we've approached a partial match (limit)
    _last_vlm_reject: Optional[str] = None  # last reject reason: 'target_parrot' | 'prompt_echo' | 'too_short' | None
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
    
    async def _cancellable_vlm_check(target: str, timeout: float, retry: bool = False, skip_cancel: bool = False):
        """Wrap _vlm_check so _search_cancel can interrupt it mid-flight.
        skip_cancel=True: run VLM to completion regardless of cancel event (used by
        LOCAL SEARCH MODE so a mid-nav cancel doesn't suppress the in-room scan)."""
        vlm_task = asyncio.create_task(_vlm_check(target, timeout, retry=retry))
        if skip_cancel:
            return await vlm_task
        cancel_wait = asyncio.create_task(_search_cancel_event.wait())
        done, _pending = await asyncio.wait(
            {vlm_task, cancel_wait}, return_when=asyncio.FIRST_COMPLETED,
        )
        if cancel_wait in done or _search_cancel:
            vlm_task.cancel()
            cancel_wait.cancel()
            logger.info("🛑 VLM check cancelled by search cancel")
            return False, "", "", 0.0, "cancelled"
        cancel_wait.cancel()
        return vlm_task.result()

    async def _do_vlm_and_check(phase_label: str, force: bool = False, broadcast_extra: dict = None) -> bool:
        """Helper: run VLM check, handle stale frames, broadcast progress.
        Returns True if found.  Set force=True after forward moves to never skip.
        broadcast_extra: optional dict merged into scanning broadcast (step, action, etc.)
        Side effect: sets _last_vlm_reject (None / 'target_parrot' / 'prompt_echo' / ...)."""
        nonlocal total_checks, stale_count, vlm_reject_count, wall_empty_count, good_vlm_count, consecutive_found, partial_approach_count, _last_vlm_reject

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

        found, location, description, _conf, _reason = await _cancellable_vlm_check(_vlm_query_target, vlm_timeout, skip_cancel=force)
        total_checks += 1

        # ── Auto-retry once on prompt_echo or target_parrot with alternate prompt ──
        is_rejected = description.startswith("__REJECTED__:") if description else False
        if is_rejected and ("prompt_echo" in description or "target_parrot" in description):
            logger.info(f"🔄 [{phase_label}] VLM echoed/parroted — retrying with alternate prompt...")
            await asyncio.sleep(0.5)  # brief pause before retry
            found, location, description, _conf, _reason = await _cancellable_vlm_check(_vlm_query_target, vlm_timeout, retry=True, skip_cancel=force)
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
                speak_tts_bg("กล้องขัดข้อง ไม่สามารถค้นหาต่อได้ครับ")
                await _search_not_found(_display, total_checks)
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
            # target_parrot = VLM returned only the target name (e.g. "ขวดน้ำเปล่า") — this may
            # mean the object IS in frame but VLM responded poorly. Do NOT count as wall/empty.
            _last_vlm_reject = reject_reason
            if reject_reason in ("prompt_echo", "too_short"):
                wall_empty_count += 1
                logger.info(f"🧱 Prompt echo/short → treating as wall/empty scene #{wall_empty_count}")
            elif reject_reason == "target_parrot":
                logger.info(f"🤔 VLM returned target name only — potential match, not counting as wall")
            
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
            _last_vlm_reject = None
        
        # Track wall/empty scenes — abort forward moves if stuck in dead-end
        # IMPORTANT: Only count as wall when VLM actually SAW a wall, not camera failure
        effective_desc = "" if is_rejected else description
        if is_empty:
            pass  # Camera dead → do NOT inflate wall_empty_count (would trigger blind forward)
        elif is_rejected:
            pass  # Already handled above (prompt_echo/too_short counted once) — don't double-count
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
                search_target=_display,
            )
        
        # Broadcast progress
        _broadcast_data = {
            "type": "search_status",
            "status": "scanning",
            "target": _display,
            "phase": phase_label,
            "total_checks": total_checks,
            "description": (effective_desc[:500] if effective_desc else ""),
            "vlm_scene": (effective_desc[:800] if effective_desc and not is_rejected else ""),
            "llm_reason": (_reason or ""),
            "llm_found": found,
            "llm_confidence": _conf,
        }
        if broadcast_extra:
            _broadcast_data.update(broadcast_extra)
        await _broadcast_and_cache(_broadcast_data)
        
        if found:
            # ── WRONG-ZONE GUARD ──
            # User specified a target zone but robot isn't there yet → never
            # confirm a sighting from the wrong room. e.g. "ขวดน้ำที่นั่งเล่น"
            # while still in toilet must NOT short-circuit on a toilet bottle.
            # arrived_zone=True means we've already entered the target zone,
            # so confirmation proceeds normally.
            if _early_tgt_zone and not arrived_zone:
                try:
                    _cz_now = semantic_map.get_zone_at(
                        _robot_pose.get("x", 0.0),
                        _robot_pose.get("y", 0.0),
                    )
                    _cz_now_id = _cz_now.id if _cz_now else None
                except Exception:
                    _cz_now_id = None
                if _cz_now_id != _early_tgt_zone:
                    logger.warning(
                        f"🚫 [{phase_label}] WRONG-ZONE: VLM saw '{_vlm_query_target}' "
                        f"but robot is in '{_cz_now_id}' (target='{_early_tgt_zone}') "
                        f"— refusing to confirm; will navigate to target zone first"
                    )
                    consecutive_found = 0
                    return False
            consecutive_found += 1
            logger.info(f"🎉 Object detected! ({phase_label}) Location: {location} [confirm {consecutive_found}/2]")
            
            # ── Confirmation gate: require 2 consecutive found=true before approach ──
            # Single detection can be VLM hallucination or seeing through a gap.
            # Re-check immediately (same position) to confirm.
            if consecutive_found < 2:
                logger.info("🔁 Confirming detection — re-checking same angle...")
                await asyncio.sleep(0.5)  # let camera settle
                found2, loc2, desc2, _conf2, _reason2 = await _cancellable_vlm_check(_vlm_query_target, vlm_timeout)
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
                await _search_found(_display, location, description)
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
                speak_tts_bg("เห็นสิ่งที่คล้ายเป้าหมาย กำลังเข้าไปดูใกล้ๆ ครับ")
                
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
                found_retry, loc_retry, desc_retry, conf_retry, _reason_retry = await _cancellable_vlm_check(_vlm_query_target, vlm_timeout)
                total_checks += 1
                
                if found_retry and conf_retry >= 0.7:
                    consecutive_found = 2  # bypass confirmation gate — we just approached + confirmed
                    logger.info(f"🎉 PARTIAL→CONFIRMED! conf={conf_retry:.2f} at '{loc_retry}' after approach")
                    await _search_found(_display, loc_retry, desc_retry)
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
            await motion.exec_motion(rotate_cmd, rear_obstacle_checker=_obstacle_avoidance.get_rear_obstacle_checker())
        else:
            logger.info(f"🤖 [MOCK] Would rotate {degrees}°")
            await asyncio.sleep(0.3)
        await asyncio.sleep(0.5)  # Let camera frame settle
    
    async def _rotate_90():
        """Rotate 90° (backward compatible)."""
        await _rotate_deg(90.0)
    
    _nav2_failed_this_session = False  # cache Nav2 failure to avoid retry timeout
    _last_nav2_result: dict = {}      # rich result of the most recent Nav2 move (feeds LLM context)

    async def _nav2_forward(angle_deg: float, distance_m: float) -> bool:
        """
        Move forward using Nav2 path planning (lower brain).
        Converts relative angle + distance to a map-frame goal.
        Nav2 handles obstacle avoidance, costmap, and recovery.
        
        Returns True if navigation succeeded, False if failed/aborted.
        """
        if not _nav2_client:
            return False

        nonlocal _nav2_failed_this_session, _last_nav2_result
        if _nav2_failed_this_session:
            return False
        
        # Connect Nav2 if needed
        if not _nav2_client.connected:
            connected = await _nav2_client.connect()
            if not connected:
                logger.warning("🧭 Nav2 connection failed — disabling Nav2 for this search session")
                _nav2_failed_this_session = True
                return False
        
        # Get current robot pose in map frame
        rx = _robot_pose.get("x", 0.0)
        ry = _robot_pose.get("y", 0.0)
        rtheta = _robot_pose.get("theta", 0.0)  # radians
        
        # The robot already rotated to face the desired direction (angle applied before this)
        # So we just move forward from current heading
        goal_x = rx + distance_m * math.cos(rtheta)
        goal_y = ry + distance_m * math.sin(rtheta)
        
        logger.info(
            f"🧭 Nav2 forward: ({rx:.2f},{ry:.2f}) → ({goal_x:.2f},{goal_y:.2f}) "
            f"dist={distance_m:.2f}m heading={math.degrees(rtheta):.0f}°"
        )
        
        await _broadcast_and_cache({
            "type": "search_status",
            "status": "nav2_navigating",
            "goal": {"x": round(goal_x, 2), "y": round(goal_y, 2)},
        })
        
        def _on_feedback(fb):
            dist = fb.get("distance_remaining", -1)
            if dist >= 0:
                logger.debug(f"  Nav2: {dist:.2f}m remaining")
        
        result = await _nav2_client.navigate_to_pose(
            x=goal_x,
            y=goal_y,
            theta=rtheta,
            timeout=30.0,
            on_feedback=_on_feedback,
        )
        
        status = result.get("status", "UNKNOWN")
        duration = result.get("duration", 0)
        dist_rem = result.get("distance_remaining", -1)

        # Store rich result so LLM can reason about what Nav2 did last step
        _last_nav2_result = {
            "status": status,
            "goal_x": round(goal_x, 2),
            "goal_y": round(goal_y, 2),
            "start_x": round(rx, 2),
            "start_y": round(ry, 2),
            "distance_remaining": round(dist_rem, 2) if dist_rem >= 0 else -1,
            "duration": round(duration, 1),
        }

        if status == "SUCCEEDED":
            logger.info(f"✅ Nav2 reached goal in {duration:.1f}s")
            return True
        elif status == "ABORTED":
            logger.warning(f"⚠️ Nav2 aborted (obstacle/unreachable) after {duration:.1f}s")
            return False
        elif status == "TIMEOUT":
            logger.warning(f"⚠️ Nav2 timed out after {duration:.1f}s")
            await _nav2_client.cancel_navigation()
            return False
        else:
            logger.warning(f"Nav2 status: {status} after {duration:.1f}s")
            return False
    
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
            await _search_cancelled(_display)
            return

        # ════════════════════════════════════════════════════════════
        # Phase 0.4: Direct Zone-Name Navigation (GO-TO-ZONE)
        # If the LLM target itself names a zone (e.g. "bedroom"),
        # navigate to that zone's anchor BEFORE searching. After arrival,
        # normal search continues. On failure, fall through to agent loop.
        # ════════════════════════════════════════════════════════════
        def _match_zone_by_name(name: str):
            if not name:
                return None
            n = name.strip().lower()
            for z in semantic_map.get_all_zones():
                if n == z.id.lower() \
                   or n == (z.label_en or "").strip().lower() \
                   or n == (z.label_th or "").strip().lower():
                    return z
            return None

        # Explicit zone intent — substring/alias/exit-pattern aware.
        # Used by both Phase 0.4 (GO-TO-ZONE) and Phase 0.5 (planner override).
        _explicit_zone_id, _excluded_zone_id = extract_zone_intent(
            f"{_display} {normalized_target}", semantic_map
        )
        _current_zone = semantic_map.get_zone_at(
            _robot_pose.get("x", 0.0), _robot_pose.get("y", 0.0)
        )
        _current_zone_id = _current_zone.id if _current_zone else None
        if _explicit_zone_id or _excluded_zone_id:
            logger.info(
                f"🎯 Zone intent extracted: target={_explicit_zone_id} "
                f"excluded={_excluded_zone_id} current={_current_zone_id}"
            )

        if not _search_cancel:
            # Prefer explicit zone intent (handles compound queries like
            # "water bottle, bedroom"). Fall back to legacy exact-match.
            _zone_match = (
                semantic_map.get_zone(_explicit_zone_id) if _explicit_zone_id else None
            ) or _match_zone_by_name(normalized_target) or _match_zone_by_name(_display)

            # If the target itself NAMES a zone (e.g. user said "bathroom"
            # and LLM passed it through as the target), treat it as explicit
            # so the safety stop below fires on nav failure and Phase 0.5
            # / SearchPlanner is skipped. Without this, a failed go-to-room
            # silently degrades into planner wandering.
            if _zone_match and not _explicit_zone_id:
                _explicit_zone_id = _zone_match.id
                logger.info(
                    f"[ZONE] target is a zone name → promoting to "
                    f"explicit_zone_id='{_explicit_zone_id}'"
                )

            # ── SAME-ZONE HARD SKIP ──
            # If the explicit/matched target zone is the zone the robot is
            # already standing in, NEVER call go_to_zone again. Force entry
            # to LOCAL SEARCH MODE by setting arrived_zone=True. This
            # supersedes the older parrot-only gate (which fired only after a
            # specific VLM reject) — same-zone redundant nav must be blocked
            # unconditionally, regardless of why Phase 0 missed.
            if (
                _zone_match is not None
                and _current_zone_id is not None
                and _current_zone_id == _zone_match.id
            ):
                logger.warning(
                    f"[ZONE] SKIP go_to_zone — already in target zone "
                    f"current='{_current_zone_id}' target='{_zone_match.id}' "
                    f"last_vlm_reject={_last_vlm_reject!r} "
                    f"→ entering LOCAL SEARCH MODE directly (no nav)"
                )
                arrived_zone = True
                _zone_match = None  # disable Phase 0.4 nav for this iteration

            if _zone_match and USE_NAV2 and _nav2_client and not MOCK_ROBOT:
                _zlabel = _zone_match.label_th or _zone_match.label_en or _zone_match.id
                logger.info(
                    f"[NAV] go_to_zone target='{_display}' zone='{_zone_match.id}' "
                    f"@ ({_zone_match.center_x:.2f}, {_zone_match.center_y:.2f}) "
                    f"current_zone='{_current_zone_id}' last_vlm_reject={_last_vlm_reject!r}"
                )

                # ── Preflight: refuse to announce a move we can't actually
                # dispatch. The old code spoke "กำลังไปห้องนอนครับ" even when
                # the Nav2 path was broken — that made the system lie to the
                # user. Now TTS + status only fire AFTER we know the /vora/command
                # channel is up.
                _nav_available = False
                try:
                    _nav_available = await _nav2_client.connect()
                except Exception as _pe:
                    logger.warning(f"[NAV] preflight connect error: {_pe}")
                    _nav_available = False
                _zone_nav_ok = False
                if not _nav_available:
                    logger.error(
                        f"[NAV] navigation channel unavailable — NOT announcing "
                        f"movement to zone '{_zone_match.id}'"
                    )
                    await _broadcast_and_cache({
                        "type": "search_status",
                        "status": "nav_unavailable",
                        "target": _display,
                        "zone": _zone_match.id,
                        "zone_label": _zlabel,
                        "reason": "robot_nav_bridge_offline",
                    })
                    # do NOT speak, do NOT pretend to move; the explicit-zone
                    # safety stop below will abort truthfully.
                else:
                    # Broadcast intent BEFORE gates so UI shows the zone immediately,
                    # but TTS fires AFTER gates so robot never lies "กำลังไป" then aborts.
                    await _broadcast_and_cache({
                        "type": "search_status",
                        "status": "navigating_to_zone",
                        "target": _display,
                        "zone": _zone_match.id,
                        "zone_label": _zlabel,
                        "goal_x": _zone_match.center_x,
                        "goal_y": _zone_match.center_y,
                    })

                    # [SAFETY] Block Nav2 launch until AMCL is stable and
                    # the forward cone is physically clear. Without these
                    # two gates Nav2 starts against stale pose + wall in
                    # front, grinds through recovery, and hits the wall.
                    if not await wait_for_localization_ready(timeout=10.0):
                        _zone_nav_ok = False
                        logger.error("[NAV] aborting zone nav — localization not ready")
                    else:
                        # Preflight is ADVISORY only — may rotate once to a better
                        # heading but NEVER blocks navigation. Nav2 owns the path.
                        try:
                            await _preflight_forward_clearance(min_clearance=_SAFE_FORWARD_START)
                        except Exception as _pe:
                            logger.warning(f"[NAV] preflight advisory error: {_pe}")
                        logger.info("[NAV] letting Nav2 handle path")
                        speak_tts_bg(f"กำลังไป{_zlabel}ครับ")
                        # Bounded attempt loop: first try + exactly ONE replan from
                        # current pose after stall. No infinite retry storms.
                        _MAX_ATTEMPTS = 2
                        for _attempt in range(_MAX_ATTEMPTS):
                            if _search_cancel:
                                break
                            try:
                                _zone_nav_ok = await _navigate_with_watchdog(
                                    x=_zone_match.center_x,
                                    y=_zone_match.center_y,
                                    timeout=45.0,
                                )
                            except Exception as _e_inner:
                                logger.warning(f"[NAV] go_to_zone error attempt {_attempt+1}: {_e_inner}")
                                _zone_nav_ok = False
                            if _zone_nav_ok:
                                break
                            logger.warning(
                                f"[NAV] failed zone {_zone_match.id} "
                                f"(attempt {_attempt+1}/{_MAX_ATTEMPTS})"
                            )
                            if _attempt + 1 < _MAX_ATTEMPTS and not _search_cancel:
                                # Near-wall escape: rotate 25° to clear costmap
                                # shadow, nudge forward 10cm, then resend goal ONCE.
                                logger.info("[NAV] soft-replan triggered — wall escape nudge")
                                try:
                                    await _rotate_deg(25.0)
                                except Exception as _re:
                                    logger.warning(f"[NAV] recovery rotate error: {_re}")
                                # Small forward nudge to physically leave the
                                # inflated zone. Obstacle checker still active.
                                try:
                                    await motion.exec_motion(
                                        {"type": "move", "linear_x": 0.10,
                                         "angular_z": 0.0, "duration": 1.0},
                                        obstacle_checker=_lidar_obstacle_check,
                                    )
                                except Exception as _me:
                                    logger.warning(f"[NAV] escape nudge error: {_me}")
                        if not _zone_nav_ok:
                            logger.warning(
                                f"[NAV] final failure after retry — stopping zone nav for {_zone_match.id}"
                            )

                # Post-nav verification: trust the actual robot pose.
                # Nav2 sometimes returns ABORTED/TIMEOUT after the robot has
                # already physically entered the target zone (watchdog races,
                # final-approach micro-stalls). If pose-vs-zone agrees, treat
                # the trip as a success and proceed to LOCAL SEARCH MODE so
                # the search ALWAYS continues automatically after arrival.
                if not _zone_nav_ok and _nav_available:
                    try:
                        _pz = semantic_map.get_zone_at(
                            _robot_pose.get("x", 0.0),
                            _robot_pose.get("y", 0.0),
                        )
                        _pz_id = _pz.id if _pz else None
                    except Exception:
                        _pz_id = None
                    if _pz_id == _zone_match.id:
                        logger.warning(
                            f"[NAV] Nav2 reported failure but robot pose is "
                            f"inside target zone '{_zone_match.id}' — "
                            f"treating as arrived (continuing in-room search)"
                        )
                        _zone_nav_ok = True

                if _zone_nav_ok:
                    arrived_zone = True
                    _current_zone_id = _zone_match.id
                    # Persist across searches — decision layer of future
                    # requests reads this to zone-filter memory.
                    _current_zone = _zone_match.id
                    logger.info(f"[ZONE] current_zone updated -> {_current_zone_id}")
                    logger.info(
                        f"✅ [NAV] room navigation complete → "
                        f"starting in-room scan at '{_zone_match.id}' "
                        f"({_zone_match.center_x:.2f}, {_zone_match.center_y:.2f})"
                    )
                elif _nav_available:
                    logger.warning(
                        f"[NAV] go_to_zone permanently failed for {_zone_match.id}"
                    )

        # If zone nav just succeeded, proceed to LOCAL SEARCH MODE even if
        # cancel was set during the transit — the cancel checks inside the
        # scan loop still honor it between rotations.
        if _search_cancel and not arrived_zone:
            await _search_cancelled(_display)
            return

        # ════════════════════════════════════════════════════════════
        # Phase 0.5: Semantic Zone Navigation
        # Before scanning randomly, check if we know WHERE to look.
        # Uses: zone expected_objects, object memory sightings, co-location.
        # If a strong zone match exists → navigate there → compact 3-dir scan.
        # Falls through to Agent Loop if no match or zone scan fails.
        # ════════════════════════════════════════════════════════════
        # Phase 0.5 only runs when there is NO explicit zone intent.
        # If the user named a room, Phase 0.4 is authoritative — we must not
        # let the heuristic planner pick another zone first.
        if not _search_cancel and not arrived_zone and not _explicit_zone_id:
            # [MEM] Source-of-truth = object_memory (disk-authoritative).
            # spatial_memory is deliberately NOT passed — its stale cross-session
            # observations were still influencing zone ranking even after the
            # user deleted object memory via the UI. Planner now ignores it.
            _planner = SearchPlanner(semantic_map, object_memory, None)
            _plan = _planner.plan(
                target=normalized_target,
                robot_x=_robot_pose.get("x", 0.0),
                robot_y=_robot_pose.get("y", 0.0),
                explicit_zone_id=_explicit_zone_id,
                excluded_zone_id=_excluded_zone_id,
                current_zone_id=_current_zone_id,
            )

            if _plan.approach_zone_id:
                _zone = semantic_map.get_zone(_plan.approach_zone_id)
                _zone_label = _zone.label_th if _zone else _plan.approach_zone_id

                # Memory-anchor refinement: if we have a recent sighting of
                # the target inside this zone, hop to that exact spot instead
                # of the zone center. Makes "I remember where I saw it" real.
                _mem_anchor = find_memory_anchor(
                    object_memory, semantic_map,
                    normalized_target, _plan.approach_zone_id,
                )
                if _mem_anchor:
                    _max, _may, _maage = _mem_anchor
                    logger.info(
                        f"🧠 memory_anchor in {_plan.approach_zone_id}: "
                        f"({_max:.2f},{_may:.2f}) age={_maage:.1f}h "
                        f"→ overriding zone center"
                    )
                    _plan.approach_x = _max
                    _plan.approach_y = _may

                logger.info(
                    f"🗺️ Semantic: go to {_zone_label} ({_plan.approach_zone_id}) "
                    f"at ({_plan.approach_x:.2f}, {_plan.approach_y:.2f}) "
                    f"score={_plan.ranked_zones[0][1]:.1f}"
                )
                speak_tts_bg(f"น่าจะอยู่{_zone_label} กำลังไปดูครับ")

                await manager.broadcast(json.dumps({
                    "type": "search_status",
                    "status": "navigating_to_zone",
                    "target": _display,
                    "zone": _plan.approach_zone_id,
                    "zone_label": _zone_label,
                    "goal_x": _plan.approach_x,
                    "goal_y": _plan.approach_y,
                }))

                # ── Navigate to zone anchor ──
                _nav_ok = False
                if USE_NAV2 and _nav2_client and not MOCK_ROBOT:
                    try:
                        _nav_result = await _nav2_client.navigate_to_pose(
                            x=_plan.approach_x, y=_plan.approach_y,
                            theta=_robot_pose.get("theta", 0.0),
                            timeout=45.0,
                        )
                        _nav_ok = _nav_result.get("success", False)
                        logger.info(f"🧭 Nav2 zone nav: {'OK' if _nav_ok else 'FAIL'}")
                    except Exception as e:
                        logger.warning(f"🧭 Nav2 zone nav error: {e}")

                if not _nav_ok and not MOCK_ROBOT:
                    # cmd_vel fallback: rotate toward zone anchor, drive forward.
                    # Only drive forward if LiDAR confirms the path is clear —
                    # otherwise we'd just slam into a wall and rotate-loop.
                    _dx = _plan.approach_x - _robot_pose.get("x", 0.0)
                    _dy = _plan.approach_y - _robot_pose.get("y", 0.0)
                    _target_angle = math.atan2(_dy, _dx)
                    _turn_deg = math.degrees(_target_angle - _robot_pose.get("theta", 0.0))
                    _turn_deg = ((_turn_deg + 180) % 360) - 180  # normalize to [-180, 180]
                    if abs(_turn_deg) > 10:
                        await _rotate_deg(_turn_deg)
                    _dist = math.sqrt(_dx * _dx + _dy * _dy)
                    if _dist > 0.15:
                        try:
                            _fwd_clear = _obstacle_avoidance.get_forward_clearance()
                        except Exception:
                            _fwd_clear = float("inf")
                        if _fwd_clear < 0.35:
                            logger.warning(
                                f"[NAV] forward blocked ({_fwd_clear:.2f}m < 0.35m) "
                                f"— skipping cmd_vel fallback drive"
                            )
                        else:
                            _move_cmd = {
                                "type": "move", "linear_x": 0.15, "angular_z": 0.0,
                                "duration": min(_dist / 0.15, 8.0),
                            }
                            await motion.exec_motion(_move_cmd, obstacle_checker=_lidar_obstacle_check)

                if _search_cancel:
                    await _search_cancelled(_display)
                    return

                # ── Compact local scan at zone: 3 positions ──
                logger.info(f"🔍 Zone local scan: {_plan.approach_zone_id} (3 directions)")
                _scan_angles = SearchPlanner.local_scan_angles()
                for _si, _sa in enumerate(_scan_angles):
                    if _search_cancel:
                        await _search_cancelled(_display)
                        return
                    if _si > 0:
                        await _rotate_deg(_sa)
                    if await _do_vlm_and_check(
                        f"zone_{_plan.approach_zone_id}_{_si}", force=True
                    ):
                        return  # Found!

                _planner.mark_searched(_plan.approach_zone_id)
                logger.info(
                    f"🔍 Zone {_plan.approach_zone_id} scanned — not found"
                )

                # ── Memory-backed fallback chain ──
                # Before escalating to the reactive agent loop, try the next
                # 1-2 zones that have memory or static-prior backing for this
                # target. Skipped entirely when the user named an explicit
                # zone (forced priority) — that case has only one valid room.
                _try_fallbacks = (
                    _explicit_zone_id is None
                    and not MOCK_ROBOT
                    and not _search_cancel
                )
                if _try_fallbacks:
                    _excl = {_excluded_zone_id} if _excluded_zone_id else set()
                    _fallbacks = _planner.memory_backed_fallback_zones(
                        _plan, exclude_ids=_excl, max_n=2,
                    )
                    if _fallbacks:
                        logger.info(
                            f"🔁 memory_fallback candidates for "
                            f"'{normalized_target}': "
                            f"{[fz[0] for fz in _fallbacks]}"
                        )
                    for _fzid, _fx, _fy in _fallbacks:
                        if _search_cancel:
                            await _search_cancelled(_display)
                            return
                        # Refine fallback target with its own memory anchor
                        _fanchor = find_memory_anchor(
                            object_memory, semantic_map,
                            normalized_target, _fzid,
                        )
                        if _fanchor:
                            _fx, _fy, _fage = _fanchor
                            logger.info(
                                f"🧠 fallback memory_anchor in {_fzid}: "
                                f"({_fx:.2f},{_fy:.2f}) age={_fage:.1f}h"
                            )
                        _fzone = semantic_map.get_zone(_fzid)
                        _flabel = (_fzone.label_th if _fzone else _fzid)
                        logger.info(
                            f"🔁 escalate → memory-backed fallback zone "
                            f"{_fzid} at ({_fx:.2f},{_fy:.2f})"
                        )
                        speak_tts_bg(f"ลองดู{_flabel}อีกที่ครับ")
                        await manager.broadcast(json.dumps({
                            "type": "search_status",
                            "status": "navigating_to_zone",
                            "target": _display,
                            "zone": _fzid,
                            "zone_label": _flabel,
                            "goal_x": _fx,
                            "goal_y": _fy,
                        }))
                        _fb_ok = False
                        if USE_NAV2 and _nav2_client:
                            try:
                                _fb_ok = await _nav2_client.navigate_to_zone(
                                    x=_fx, y=_fy, timeout=40.0,
                                )
                            except Exception as _e:
                                logger.warning(f"🔁 fallback nav error: {_e}")
                        if not _fb_ok:
                            logger.info(
                                f"🔁 fallback nav to {_fzid} failed — "
                                f"trying next candidate"
                            )
                            continue
                        # Local scan at fallback zone
                        for _fsi, _fsa in enumerate(SearchPlanner.local_scan_angles()):
                            if _search_cancel:
                                await _search_cancelled(_display)
                                return
                            if _fsi > 0:
                                await _rotate_deg(_fsa)
                            if await _do_vlm_and_check(
                                f"fallback_{_fzid}_{_fsi}", force=True
                            ):
                                return
                        _planner.mark_searched(_fzid)
                        logger.info(
                            f"🔍 Fallback zone {_fzid} scanned — not found"
                        )

                logger.info(
                    "🔍 memory-backed plan exhausted — "
                    "falling through to agent loop"
                )

        if _search_cancel and not arrived_zone:
            await _search_cancelled(_display)
            return

        # ── Explicit-zone safety stop ──
        # The user named a specific room and we could not reach it. Do NOT
        # silently degrade into the global wander loop — that would search
        # other rooms the user explicitly didn't ask about.
        if _explicit_zone_id and not arrived_zone:
            logger.warning(
                f"[NAV] explicit zone '{_explicit_zone_id}' unreachable — "
                f"aborting search (no wander)"
            )
            speak_tts_bg("ไปไม่ถึงห้องที่ต้องการครับ ขออภัย")
            await _search_not_found(_display, total_checks)
            return

        # ════════════════════════════════════════════════════════════
        # LOCAL SEARCH MODE — only runs if GO-TO-ZONE arrived successfully.
        # Robot stays at the zone anchor: rotate-in-place + VLM, no forward
        # moves, no SearchPlanner, no global wandering. Always returns.
        # ════════════════════════════════════════════════════════════
        if arrived_zone:
            logger.info(
                f"🔒 [SCAN] LOCAL SEARCH MODE: in-room scan starting at "
                f"'{_current_zone_id}' — {SearchPlanner.local_scan_angles().__len__()} "
                f"scan directions, no global wander"
            )
            speak_tts_bg("ถึงแล้วครับ กำลังมองรอบๆ")

            # Memory-anchor hop inside the arrived zone: if we remember
            # exactly where this object lives here, walk to that spot before
            # rotating. Skipped if anchor is < 0.4 m away (already there).
            _arrived_zid = _zone_match.id if _zone_match else None
            if _arrived_zid and not MOCK_ROBOT:
                _local_anchor = find_memory_anchor(
                    object_memory, semantic_map,
                    normalized_target, _arrived_zid,
                )
                if _local_anchor:
                    _lax, _lay, _laage = _local_anchor
                    _ldx = _lax - _robot_pose.get("x", 0.0)
                    _ldy = _lay - _robot_pose.get("y", 0.0)
                    _ldist = math.sqrt(_ldx * _ldx + _ldy * _ldy)
                    if _ldist > 0.4:
                        logger.info(
                            f"🧠 local memory_anchor in {_arrived_zid}: "
                            f"({_lax:.2f},{_lay:.2f}) age={_laage:.1f}h "
                            f"dist={_ldist:.2f}m → hopping"
                        )
                        if USE_NAV2 and _nav2_client:
                            try:
                                await _nav2_client.navigate_to_zone(
                                    x=_lax, y=_lay, timeout=25.0,
                                )
                            except Exception as _e:
                                logger.warning(f"🧠 local anchor nav error: {_e}")
                    else:
                        logger.info(
                            f"🧠 local memory_anchor in {_arrived_zid}: "
                            f"already within {_ldist:.2f}m — scan in place"
                        )

            _local_angles = SearchPlanner.local_scan_angles()  # e.g. [0, 120, -120]
            logger.info(f"🏠 room reached → starting in-room search ({len(_local_angles)} directions)")
            for _li, _la in enumerate(_local_angles):
                if _li > 0:
                    if _search_cancel:
                        await _search_cancelled(_display)
                        return
                    await _rotate_deg(_la)
                if await _do_vlm_and_check(f"local_zone_{_li}", force=True):
                    return  # found

            # ── In-room expansion: after 3 rotations failed, take up to 2
            # short forward steps (each followed by a 3-direction scan) so
            # we don't give up on the room from a single vantage point.
            # Stop expanding the moment the robot pose leaves the target
            # zone — we promised the user we'd stay in their named room.
            _expand_zone_id = _arrived_zid
            _MAX_EXPANSION = 2
            for _ei in range(_MAX_EXPANSION):
                if _search_cancel:
                    await _search_cancelled(_display)
                    return
                if MOCK_ROBOT:
                    break
                # Verify we are still inside the target zone
                try:
                    _ez = semantic_map.get_zone_at(
                        _robot_pose.get("x", 0.0), _robot_pose.get("y", 0.0),
                    )
                    _ez_id = _ez.id if _ez else None
                except Exception:
                    _ez_id = None
                if _expand_zone_id and _ez_id != _expand_zone_id:
                    logger.info(
                        f"🔒 LOCAL EXPANSION: robot drifted out of "
                        f"target zone ({_ez_id} != {_expand_zone_id}) — stopping expansion"
                    )
                    break
                # LiDAR clearance gate — never ram a wall
                try:
                    _fwd_clear = _obstacle_avoidance.get_forward_clearance()
                except Exception:
                    _fwd_clear = 0.0
                if _fwd_clear < 0.35:
                    logger.info(
                        f"🔒 LOCAL EXPANSION step {_ei+1}: clearance "
                        f"{_fwd_clear:.2f}m blocked — scanning in place before stopping"
                    )
                    for _eli, _ela in enumerate(_local_angles):
                        if _eli > 0:
                            if _search_cancel:
                                await _search_cancelled(_display)
                                return
                            await _rotate_deg(_ela)
                        if await _do_vlm_and_check(
                            f"local_blocked_{_ei}_{_eli}", force=True,
                        ):
                            return
                    break
                logger.info(
                    f"🔒 LOCAL EXPANSION step {_ei+1}/{_MAX_EXPANSION}: "
                    f"nudging forward 0.30m inside '{_expand_zone_id}'"
                )
                try:
                    await motion.exec_motion(
                        {"type": "move", "linear_x": 0.15, "angular_z": 0.0,
                         "duration": 2.0},
                        obstacle_checker=_lidar_obstacle_check,
                    )
                except Exception as _ee:
                    logger.warning(f"🔒 LOCAL EXPANSION nudge error: {_ee}")
                    break
                await asyncio.sleep(0.4)
                for _eli, _ela in enumerate(_local_angles):
                    if _eli > 0:
                        if _search_cancel:
                            await _search_cancelled(_display)
                            return
                        await _rotate_deg(_ela)
                    if await _do_vlm_and_check(
                        f"local_expand_{_ei}_{_eli}", force=True,
                    ):
                        return

            logger.info("🔒 LOCAL SEARCH MODE: target not found in room — ending search")
            await _search_not_found(_display, total_checks)
            return

        # ════════════════════════════════════════════════════════════
        # Agent Loop: LLM/VLM-driven search (fallback)
        # VLM+LLM are the CORE decision-makers
        # Each step: LLM sees LiDAR + VLM history → decides action
        # ════════════════════════════════════════════════════════════

        # ─── Agent Loop: LLM+LiDAR driven exploration ───
        # When USE_NAV2=True: "forward" uses Nav2 for safe path planning (lower brain)
        # When USE_NAV2=False: "forward" uses direct cmd_vel + LiDAR interrupt (legacy)
        MAX_AGENT_STEPS = 16
        MAX_FORWARD_MOVES = max_move_cycles
        
        logger.info(
            f"🧠 Agent Loop: LLM+LiDAR driven search "
            f"(max {MAX_AGENT_STEPS} steps, {MAX_FORWARD_MOVES} moves)"
        )
        speak_tts_bg("กำลังมองรอบๆ ครับ")
        
        checked_dirs = []   # [{"angle_abs": float, "desc": str}]
        cumulative_rotation = 0.0   # heading relative to search start (degrees)
        forward_move_count = 0
        turns_at_position = 0  # turns since last forward move — force forward after 4
        consecutive_blocked = 0  # front blocked counter — turn away after 2
        
        # Record Phase 0 observation
        if exploration_log:
            checked_dirs.append({
                "angle_abs": 0.0,
                "desc": exploration_log[-1]["desc"][:150],
            })
        
        for step in range(MAX_AGENT_STEPS):
            if _search_cancel:
                await _search_cancelled(_display)
                return
            
            # Perception warning after a few steps
            if step == 3 and good_vlm_count < 2:
                logger.warning(
                    f"⚠️ PERCEPTION UNSTABLE: only {good_vlm_count}/{total_checks} good VLM. "
                    f"Continuing with LiDAR guidance."
                )
                speak_tts_bg("กล้องไม่เสถียร เคลื่อนที่ด้วยเลเซอร์อย่างเดียวครับ")
            
            # ── Fresh LiDAR scan ──
            lidar_text = ""
            open_dirs_list = []
            blocked_dirs = []
            if not MOCK_ROBOT:
                lidar_result = _obstacle_avoidance.find_best_direction()
                lidar_text = _obstacle_avoidance.get_sector_summary()
                open_dirs_list = lidar_result.get("open_directions", [])
                # Extract blocked directions for LLM safety input
                for sec in lidar_result.get("all_sectors", []):
                    md = sec.get("min_dist_m")
                    if md is not None and md < 0.30:
                        blocked_dirs.append({"angle": sec["angle_deg"], "dist": md})
                logger.info(f"📡 LiDAR scan (step {step+1}, {len(open_dirs_list)} open, {len(blocked_dirs)} blocked):\n{lidar_text}")
            
            # ── Safety: block forward/force-forward when camera is dead ──
            camera_blind = stale_count >= 2
            
            # ── Force forward if stuck spinning at same position (only with camera) ──
            if turns_at_position >= 4 and forward_move_count < MAX_FORWARD_MOVES and not camera_blind:
                if open_dirs_list:
                    # Prefer forward-ish directions (within ±90° of front)
                    _front_dirs = [d for d in open_dirs_list if abs(d["angle_deg"]) <= 90]
                    best_fwd = _front_dirs[0] if _front_dirs else min(open_dirs_list, key=lambda d: abs(d["angle_deg"]))
                    plan = {
                        "action": "forward",
                        "angle": best_fwd["angle_deg"],
                        "reason": f"force: {turns_at_position} turns at same spot, moving to {best_fwd.get('avg_dist_m', 0):.1f}m open"
                    }
                    logger.info(f"🔄 FORCE FORWARD: {turns_at_position} turns without moving → forwarding to {best_fwd['angle_deg']:+.0f}°")
                else:
                    plan = {"action": "done", "angle": 0, "reason": f"stuck: {turns_at_position} turns, no open direction"}
            else:
                # ── Camera corridor shortcut: VLM sees path → go forward immediately ──
                # Prevents LLM from ignoring visible corridors to check unchecked dirs
                plan = None
                if (checked_dirs and not camera_blind
                        and forward_move_count < MAX_FORWARD_MOVES
                        and turns_at_position >= 1):
                    _last_cd = checked_dirs[-1]
                    _last_rel = ((_last_cd["angle_abs"] - cumulative_rotation + 180) % 360) - 180
                    if abs(_last_rel) < 20:  # current facing direction
                        _desc_lower = _last_cd["desc"].lower()
                        _neg_kw = ["no path", "no corridor", "no open", "dead end",
                                   "blocked", "no doorway", "no passage"]
                        _has_neg = any(neg in _desc_lower for neg in _neg_kw)
                        if not _has_neg:
                            _path_kw = ["corridor", "hallway", "open path", "doorway",
                                        "passage", "walkway", "open space", "open area"]
                            _has_path = any(kw in _desc_lower for kw in _path_kw)
                            if _has_path:
                                # Verify forward is not blocked by LiDAR
                                _fwd_blocked = any(
                                    abs(bd["angle"]) < 30 and bd["dist"] < 0.30
                                    for bd in blocked_dirs
                                )
                                # Also check real forward clearance (±16°-60° rays)
                                # Robot is 21cm wide — need at least 30cm to pass safely
                                _fwd_clearance = float('inf')
                                if not MOCK_ROBOT:
                                    _fwd_clearance = _obstacle_avoidance.get_forward_clearance()
                                _MIN_SHORTCUT_CLEARANCE = 0.30
                                if _fwd_blocked:
                                    logger.info(f"👁️ Camera sees path but LiDAR blocked < 0.30m — skipping shortcut")
                                elif _fwd_clearance < _MIN_SHORTCUT_CLEARANCE:
                                    logger.info(
                                        f"👁️ Camera sees path but forward clearance only {_fwd_clearance:.2f}m "
                                        f"< {_MIN_SHORTCUT_CLEARANCE}m — too narrow, skipping shortcut"
                                    )
                                else:
                                    plan = {
                                        "action": "forward",
                                        "angle": 0,
                                        "reason": f"camera sees corridor/path, clearance {_fwd_clearance:.2f}m → forward",
                                    }
                                    logger.info(
                                        f"👁️ CAMERA PATH SHORTCUT: VLM sees path + clearance "
                                        f"{_fwd_clearance:.2f}m ≥ {_MIN_SHORTCUT_CLEARANCE}m → go forward"
                                    )

                if plan is None:
                    if _search_cancel:
                        await _search_cancelled(_display)
                        return
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
                        blocked_dirs=blocked_dirs,
                        nav2_last_result=_last_nav2_result if USE_NAV2 else None,
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
                    if not MOCK_ROBOT:
                        refined = _obstacle_avoidance.refine_direction(angle)
                        if refined != angle:
                            logger.info(f"🔍 Refined angle: {angle:+.0f}° → {refined:+.0f}°")
                            angle = refined
                    await _rotate_deg(angle)
                    cumulative_rotation += angle
                
                label = f"agent_s{step + 1}_t{angle:+.0f}"
                if _search_cancel:
                    await _search_cancelled(_display)
                    return
                if await _do_vlm_and_check(label, force=True, broadcast_extra={
                    "step": step + 1,
                    "max_steps": MAX_AGENT_STEPS,
                    "action": "turn",
                    "angle": angle,
                    "reason": reason,
                }):
                    return
                if _search_cancel:
                    await _search_cancelled(_display)
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
                    if not MOCK_ROBOT:
                        refined = _obstacle_avoidance.refine_direction(angle)
                        if refined != angle:
                            logger.info(f"🔍 Refined angle: {angle:+.0f}° → {refined:+.0f}°")
                            angle = refined
                    speak_tts_bg(f"เลี้ยว{int(abs(angle))}องศาไปทางที่โล่งครับ")
                    await _rotate_deg(angle)
                    cumulative_rotation += angle
                
                if wall_empty_count >= 3:
                    wall_empty_count = 0
                
                # ══════ Try Nav2 first ══════
                _nav2_moved = False
                if USE_NAV2 and _nav2_client and not MOCK_ROBOT:
                    _nav2_moved = await _nav2_forward(
                        angle_deg=angle,
                        distance_m=move_speed * move_duration,  # ~0.40m
                    )
                    if not _nav2_moved:
                        # DO NOT fall back to legacy cmd_vel when USE_NAV2=True.
                        # Legacy cmd_vel after Nav2 ERROR causes a dead loop:
                        #   Nav2 fail → cmd_vel → LiDAR interrupt at step 0 → no movement.
                        # Let the agent loop pick a different action (turn, scan, etc.).
                        logger.warning(
                            "🧭 Nav2 forward failed — skipping legacy cmd_vel "
                            "(USE_NAV2=True). Agent loop will choose next action."
                        )

                # ══════ Legacy MODE: direct cmd_vel + LiDAR interrupt ══════
                # Only runs when Nav2 is disabled (USE_NAV2=False) or in MOCK mode.
                # Never runs as a fallback after Nav2 failure — that creates broken loops.
                if not _nav2_moved and not (USE_NAV2 and not MOCK_ROBOT):
                    # Check clearance AFTER rotation using non-dead-zone rays (±16°~60°)
                    # can_robot_fit(0.0) is broken: sector 0 is in the dead zone, so it
                    # returns side clearance instead of front clearance.
                    if not MOCK_ROBOT:
                        await asyncio.sleep(0.3)  # let LiDAR update after rotation
                        clearance = _obstacle_avoidance.get_forward_clearance()
                        logger.info(f"🧭 Post-rotation front clearance: {clearance:.2f}m (±16°-60° rays)")
                        
                        # Minimum clearance guard: don't ram walls
                        MIN_FWD_CLEARANCE = 0.20  # 20cm absolute minimum
                        if clearance < MIN_FWD_CLEARANCE:
                            logger.warning(
                                f"⛔ Front blocked: {clearance:.2f}m < {MIN_FWD_CLEARANCE}m! "
                                f"Turning to open direction."
                            )
                            forward_move_count -= 1
                            consecutive_blocked += 1
                            # Turn to best open direction instead of dead-looping
                            if open_dirs_list:
                                _best_open = open_dirs_list[0]
                                _turn_angle = _best_open["angle_deg"]
                                logger.info(f"🔄 Blocked → turning {_turn_angle:+.0f}° to open space ({_best_open.get('avg_dist_m', 0):.1f}m)")
                                await _rotate_deg(_turn_angle)
                                cumulative_rotation += _turn_angle
                                turns_at_position += 1
                            else:
                                logger.warning("⚠️ No open direction found — all blocked!")
                            if consecutive_blocked >= 3:
                                logger.warning(f"🚧 Blocked {consecutive_blocked}x — robot is trapped, breaking forward loop")
                                break
                            continue
                        
                        obs = await _obstacle_avoidance.check_and_avoid()
                        if obs and obs.get("obstacle_detected"):
                            logger.warning(f"🚧 Obstacle at {obs.get('distance', 0):.2f}m!")
                            speak_tts_bg("เจอสิ่งกีดขวางครับ")
                            # No blind reverse — rear unknown. Stop + fall through to strategy.
                            await _safe_reverse(speed=0.10, duration=1.0)
                            await _obstacle_avoidance.execute_avoidance(obs.get("strategy", "stop"))
                            forward_move_count -= 1
                            continue
                        
                        # Limit forward distance to clearance minus safety margin
                        safety_margin = 0.15  # 15cm buffer from walls
                        max_safe_distance = max(0.0, clearance - safety_margin)
                        commanded_distance = move_speed * move_duration  # normally 0.40m
                        if max_safe_distance < commanded_distance:
                            safe_duration = max(0.5, max_safe_distance / move_speed)
                            logger.info(f"📏 Limiting forward: {clearance:.2f}m clearance → {max_safe_distance:.2f}m safe → {safe_duration:.1f}s")
                        else:
                            safe_duration = move_duration
                    else:
                        safe_duration = move_duration
                    
                    logger.info(f"🚶 Moving forward ({forward_move_count}/{MAX_FORWARD_MOVES})...")
                    consecutive_blocked = 0  # reset — we got past the clearance check
                    
                    if not MOCK_ROBOT:
                        # Record pose before moving for stuck detection
                        _pre_x = _robot_pose.get("x", 0.0)
                        _pre_y = _robot_pose.get("y", 0.0)
                        
                        # ── Side-wall avoidance during search forward ──
                        await asyncio.sleep(0.2)
                        _left_c, _right_c = _obstacle_avoidance.get_side_clearance()
                        _SIDE_WARN = 0.20
                        _SIDE_STEER = 0.12
                        _steer_z = 0.0
                        if _left_c < _SIDE_WARN and _right_c >= _SIDE_WARN:
                            _steer_z = -_SIDE_STEER
                            logger.info(f"🧱 Wall LEFT {_left_c:.2f}m → steering right")
                        elif _right_c < _SIDE_WARN and _left_c >= _SIDE_WARN:
                            _steer_z = _SIDE_STEER
                            logger.info(f"🧱 Wall RIGHT {_right_c:.2f}m → steering left")
                        
                        move_cmd = {
                            "type": "move",
                            "linear_x": move_speed,
                            "angular_z": _steer_z,
                            "duration": safe_duration,
                        }
                        completed = await motion.exec_motion(
                            move_cmd,
                            obstacle_checker=_lidar_obstacle_check,
                            rear_obstacle_checker=_obstacle_avoidance.get_rear_obstacle_checker(),
                        )
                        if not completed:
                            logger.warning("🛑 Forward interrupted by LiDAR!")
                            # Rear may be unknown — only reverse if side-rear band is clear
                            if await _safe_reverse(speed=0.10, duration=1.0):
                                speak_tts_bg("ตรวจพบสิ่งกีดขวาง ถอยหลังครับ")
                            else:
                                speak_tts_bg("ตรวจพบสิ่งกีดขวาง หยุดรอครับ")
                        
                        # Stuck detection: if odom didn't change after forward command,
                        # robot is stuck (wheels spinning but not moving)
                        await asyncio.sleep(0.3)  # let odom update
                        _post_x = _robot_pose.get("x", 0.0)
                        _post_y = _robot_pose.get("y", 0.0)
                        _moved_dist = math.sqrt((_post_x - _pre_x)**2 + (_post_y - _pre_y)**2)
                        _expected_dist = move_speed * safe_duration * 0.3  # at least 30% of expected
                        logger.info(
                            f"📏 Stuck check: pre=({_pre_x:.3f},{_pre_y:.3f}) post=({_post_x:.3f},{_post_y:.3f}) "
                            f"moved={_moved_dist:.3f}m expect≥{_expected_dist:.3f}m "
                            f"[src={_odom_source}, amcl={_amcl_pose_active}, odom_xy={_odom_xy_moving}]"
                        )
                        if completed and _moved_dist < _expected_dist and _expected_dist > 0.05:
                            logger.warning(
                                f"🚨 STUCK DETECTED: moved {_moved_dist:.3f}m but expected ≥{_expected_dist:.3f}m."
                            )
                            # Only reverse if side-rear band is explicitly clear.
                            # Otherwise escape by rotating to an open direction below.
                            await _safe_reverse(speed=0.10, duration=1.5)
                            await asyncio.sleep(0.3)
                            forward_move_count -= 1  # don't count stuck move
                            consecutive_blocked += 1
                            # After stuck, turn to open direction to escape
                            if open_dirs_list:
                                _escape_angle = open_dirs_list[0]["angle_deg"]
                                logger.info(f"🔄 Stuck → turning {_escape_angle:+.0f}° to escape")
                                await _rotate_deg(_escape_angle)
                                cumulative_rotation += _escape_angle
                                turns_at_position += 1
                            if consecutive_blocked >= 3:
                                logger.warning(f"🚧 Stuck/blocked {consecutive_blocked}x — robot is trapped")
                                break
                            continue
                    else:
                        await asyncio.sleep(0.5)
                
                await asyncio.sleep(0.5)
                
                if _search_cancel:
                    await _search_cancelled(_display)
                    return
                
                # VLM check at new position
                if await _do_vlm_and_check(f"agent_mv{forward_move_count}_fwd", force=True, broadcast_extra={
                    "step": step + 1,
                    "max_steps": MAX_AGENT_STEPS,
                    "action": "forward",
                    "angle": angle,
                    "reason": reason,
                }):
                    return
                if _search_cancel:
                    await _search_cancelled(_display)
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
        await _search_not_found(_display, total_checks)
        
    except Exception as e:
        logger.error(f"❌ Visual search error: {e}")
        await _broadcast_and_cache({
            "type": "search_status",
            "status": "error",
            "target": _display,
            "error": str(e),
        })
    finally:
        # Safety: always stop the robot when search ends
        if not MOCK_ROBOT:
            try:
                stop_cmd = {"type": "move", "linear_x": 0.0, "angular_z": 0.0, "duration": 0.1}
                await motion.exec_motion(stop_cmd)
            except Exception:
                pass
        _search_active = False
        _search_cancel = False
        _search_cancel_event.clear()
        _current_search_target = ""
        motion._cancel_checker = None


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
        resp = await _http.post(llm_url, json={
                "prompt": prompt,
                "system": system,
                "temperature": 0.3,
                "max_tokens": 128,
            }, timeout=15.0)

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
    blocked_dirs: list = None,
    nav2_last_result: dict = None,
) -> dict:
    """
    🧠 LLM Agent Brain: decide next action for visual search.

    VLM-PRIMARY approach: camera scene descriptions drive direction choice.
    LiDAR is used ONLY for safety (blocked directions < 0.3m).
    The YDLidar G2 has a 30° front dead zone — LiDAR distances cannot
    reliably predict forward drivability after rotation.

    Returns {"action": "turn"|"forward"|"done", "angle": float, "reason": str}
    """
    if blocked_dirs is None:
        blocked_dirs = []

    # ── Build checked/unchecked context ──
    checked_abs_angles = set()
    checked_text_lines = []
    for cd in checked_dirs:
        rel = ((cd["angle_abs"] - cumulative_rotation + 180) % 360) - 180
        checked_text_lines.append(f"  {rel:+.0f}°: \"{cd['desc'][:80]}\"")
        checked_abs_angles.add(round(cd["angle_abs"]))
    checked_text = "\n".join(checked_text_lines) if checked_text_lines else "  (none yet)"

    # Build unchecked open directions list
    unchecked_open = []
    for od in open_directions:
        lidar_angle = od["angle_deg"]
        abs_angle = round((cumulative_rotation + lidar_angle) % 360)
        already = any(
            abs((abs_angle - ca + 180) % 360 - 180) < 25
            for ca in checked_abs_angles
        )
        if not already:
            unchecked_open.append({"angle": lidar_angle})
    # Don't sort by distance — LiDAR distance is unreliable for direction choice

    if unchecked_open:
        unchecked_lines = [f"  {u['angle']:+.0f}°" for u in unchecked_open]
        unchecked_text = "\n".join(unchecked_lines)
    else:
        unchecked_text = "  (none — all directions checked)"

    # ── Camera scene observations (PRIMARY navigation input) ──
    camera_text = ""
    if checked_dirs:
        camera_lines = []
        for cd in checked_dirs[-5:]:
            rel = ((cd["angle_abs"] - cumulative_rotation + 180) % 360) - 180
            desc = cd["desc"][:120]
            camera_lines.append(f"  [facing {rel:+.0f}°] \"{desc}\"")
        camera_text = "\n".join(camera_lines)
    if not camera_text:
        camera_text = "  (no observations yet)"

    # ── Blocked directions from LiDAR (safety only) ──
    if blocked_dirs:
        blocked_lines = [f"  {bd['angle']:+.0f}° ({bd['dist']:.2f}m)" for bd in blocked_dirs]
        blocked_text = "\n".join(blocked_lines)
    else:
        blocked_text = "  (none blocked)"

    # Available angles for forward move
    if open_directions:
        fwd_angles = ", ".join(f"{od['angle_deg']:+.0f}°" for od in open_directions[:5])
        forward_hint = f"Available forward directions: {fwd_angles}"
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
        "1. HIGHEST PRIORITY — GO FORWARD THROUGH VISIBLE PATHS:\n"
        "   If the LATEST camera observation (the most recent one, facing ≈0°) describes an open\n"
        "   corridor, hallway, doorway, path, or open space → you MUST choose \"forward\" with angle=0.\n"
        "   DO NOT turn to check unchecked directions when you already see a navigable path ahead.\n"
        "   Exploring through passages is more valuable than checking every angle at one spot.\n"
        "2. If the current camera view shows ONLY walls or dead ends, and UNCHECKED directions exist\n"
        "   → turn to the nearest unchecked direction. Prefer ones near camera-observed openings.\n"
        "3. If wall streak ≥ 3 → PREFER forward to escape the dead-end area.\n"
        "4. For forward: choose direction where camera SHOWED open space, corridor, or doorway.\n"
        "5. SAFETY: Do NOT choose BLOCKED directions (confirmed wall < 0.3m).\n"
        "6. ONLY use angles from the UNCHECKED or forward directions lists. Do NOT invent angles.\n"
        "7. If no passable directions and no moves left → done.\n"
    )

    # ── Wall streak warning ──
    wall_warning = ""
    if wall_streak >= 2:
        wall_warning = f"\n⚠️ WALL STREAK: Last {wall_streak} views were walls/empty. You should FORWARD to escape this area.\n"

    # ── Nav2 last result context (only when Nav2 is active) ──
    nav2_text = ""
    if nav2_last_result:
        n2s = nav2_last_result.get("status", "")
        n2d = nav2_last_result.get("duration", 0)
        n2gx = nav2_last_result.get("goal_x", 0)
        n2gy = nav2_last_result.get("goal_y", 0)
        n2dr = nav2_last_result.get("distance_remaining", -1)
        if n2s == "SUCCEEDED":
            nav2_text = f"\nNAV2 LAST MOVE: SUCCEEDED — reached ({n2gx}, {n2gy}) in {n2d}s\n"
        elif n2s == "ABORTED":
            dr_info = f", stopped {n2dr:.2f}m from goal" if n2dr >= 0 else ""
            nav2_text = (
                f"\nNAV2 LAST MOVE: ABORTED after {n2d}s — goal ({n2gx}, {n2gy}){dr_info}\n"
                f"  Nav2 could not reach goal (obstacle in planned path or goal unreachable).\n"
                f"  → Consider turning to a DIFFERENT direction before forwarding.\n"
            )
        elif n2s == "TIMEOUT":
            nav2_text = (
                f"\nNAV2 LAST MOVE: TIMED OUT after {n2d}s — goal ({n2gx}, {n2gy})\n"
                f"  Robot moved but did not reach goal in time. Try a closer or different goal.\n"
            )
        elif n2s == "CONNECTION_FAILED":
            nav2_text = "\nNAV2: Offline — using direct cmd_vel (no path planning).\n"

    prompt = (
        f"TARGET: \"{target}\"\n\n"
        f"CAMERA SCENE OBSERVATIONS (use these to choose direction):\n{camera_text}\n\n"
        f"UNCHECKED directions (turn to look):\n{unchecked_text}\n\n"
        f"ALREADY CHECKED:\n{checked_text}\n\n"
        f"{forward_hint}\n\n"
        f"BLOCKED by LiDAR (DO NOT go here):\n{blocked_text}\n"
        f"⚠️ LiDAR has 30° front dead zone — distances are unreliable for choosing direction.\n"
        f"{nav2_text}"
        f"{wall_warning}\n"
        f"MOVES: {move_count}/{max_moves} | STEP: {step + 1}/{max_steps} | TURNS HERE: {turns_at_position}\n"
        f"Reply JSON:"
    )

    try:
        resp = await _http.post(f"{SERVER_BASE}/generate", json={
                "prompt": prompt,
                "system": system,
                "temperature": 0.3,
                "max_tokens": 200,
            }, timeout=30.0)

        if resp.status_code == 200:
            text = resp.json().get("response", "")
            # Strip <think> blocks before parsing (gemma4/qwen3 chain-of-thought)
            text = _strip_think_blocks(text)
            logger.info(f"🧠 Plan raw: {text[:200]}")

            # Parse JSON response
            action_m = re.search(r'"action"\s*:\s*"(turn|forward|done)"', text)
            angle_m = re.search(r'"angle"\s*:\s*([+-]?\d+(?:\.\d+)?)', text)
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

                # ── Post-validate: prevent premature "done" ──
                if action == "done":
                    if unchecked_open:
                        action = "turn"
                        angle = unchecked_open[0]["angle"]
                        reason = f"overridden: {len(unchecked_open)} unchecked directions remain"
                        logger.warning(f"🔧 LLM 'done' overridden → turn {angle:+.0f}° ({len(unchecked_open)} unchecked dirs)")
                    elif open_directions and move_count < max_moves:
                        action = "forward"
                        angle = open_directions[0]["angle_deg"]
                        reason = f"overridden: forward available ({max_moves - move_count} moves left)"
                        logger.warning(f"🔧 LLM 'done' overridden → forward {angle:+.0f}° ({max_moves - move_count} moves left)")

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
            vlm_prompt = (
                "What do you see? List ALL objects — including small, far, or partially visible ones. "
                "For each: name, color, position (left/center/right). "
                "Also: Is there an open path, corridor, or doorway? Which direction (LEFT/CENTER/RIGHT)?"
            )
        else:
            vlm_prompt = (
                "Describe ALL objects you see — near and far, big and small. "
                "For each: name, color, shape, position (left/center/right). "
                "Include small or distant objects too. "
                "Also: Is there an open path, corridor, or doorway visible? Which direction (LEFT/CENTER/RIGHT)?"
            )

        vlm_url = f"{SERVER_BASE}/vlm/describe-bytes"
        try:
            vlm_resp = await _http.post(
                    vlm_url,
                    content=frame_small,
                    headers={"Content-Type": "image/jpeg"},
                    params={"prompt": vlm_prompt, "lang": "th", "max_tokens": "500"},
                    timeout=timeout,
                )
        except httpx.TimeoutException:
            logger.error(f"❌ VLM describe-only timeout ({timeout}s)")
            return ""
        except httpx.ConnectError as e:
            logger.error(f"❌ VLM describe-only ConnectError: {e}")
            return ""

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

    except Exception as e:
        logger.error(f"❌ VLM describe-only error [{type(e).__name__}]: {e}")
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
        llm_resp = await _http.post(llm_url, json={
                "prompt": llm_prompt,
                "system": llm_system,
                "temperature": 0.2,
                "max_tokens": 256,
            }, timeout=timeout)

        if llm_resp.status_code != 200:
            logger.warning(f"⚠️ LLM Reasoning failed for '{target}': HTTP {llm_resp.status_code}")
            return _fallback_keyword_check(target, scene_description), "unknown", 0.5, "LLM failed, keyword fallback"

        llm_data = llm_resp.json()
        llm_text = llm_data.get("response", "")
        logger.info(f"🧠 LLM [{target}]: {llm_text[:120]}...")

        found, location, reason, confidence = _parse_llm_reasoning(llm_text)

        # Sanity checks
        if found and confidence < 0.7:
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
        return False, "", "", 0.0, ""

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
        return False, "", "", 0.0, ""

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

    # ─── Timeout budget: split between VLM (expensive) and LLM (cheap) ───
    # Total timeout param is a budget; VLM gets 60% capped at 45s, LLM gets 30s.
    vlm_budget = min(timeout * 0.6, 45.0)
    llm_budget = min(timeout * 0.4, 30.0)

    try:
        # ─── Step 1: VLM Describe (send resized frame directly) ───
        logger.info(f"👁️ VLM Describe: sending fresh frame to server... (timeout={vlm_budget:.0f}s)")

        # Normalize the target name for the VLM prompt — strip grammar/sentences,
        # keep only the object name (e.g., "กุญแจให้หน่อยครับ เริ่มจาก..." → "กุญแจ")
        vlm_target = normalize_search_target(target)

        # Target-aware VLM prompt: tell VLM what we're looking for so it
        # pays attention to small/distant objects that match.  LLM Reasoning
        # (Step 2) still verifies — so false positives from VLM get caught,
        # but missing the target entirely is the worse failure mode.
        # Server-side qwen_vlm.py appends /no_think automatically.
        if retry:
            vlm_prompt = (
                f"Look carefully for: {vlm_target}. "
                "Scan the ENTIRE image — left to right, near to far. "
                "List ALL objects, even small, distant, or partially visible ones. "
                "For each: name, color, shape, position (left/center/right). "
                "If anything resembles the target, describe it in detail. "
                "Also describe: Is there an open path/corridor/doorway to move through? Which direction (LEFT/CENTER/RIGHT)? "
                "Be honest — only describe what is actually visible."
            )
        else:
            vlm_prompt = (
                f"A robot is searching for: {vlm_target}. "
                "Describe ALL objects you see — near and far, big and small. "
                "For each object: name, color, shape, position (left/center/right). "
                "Pay special attention to small or distant objects. "
                "Also describe: Is there an open path, corridor, or doorway visible? Which direction (LEFT/CENTER/RIGHT)? "
                "IMPORTANT: Only describe what you ACTUALLY see. Do NOT imagine objects."
            )

        vlm_url = f"{SERVER_BASE}/vlm/describe-bytes"
        try:
            vlm_resp = await _http.post(
                    vlm_url,
                    content=frame_small,
                    headers={"Content-Type": "image/jpeg"},
                    params={"prompt": vlm_prompt, "lang": "th", "max_tokens": "500"},
                    timeout=vlm_budget,
                )
        except httpx.TimeoutException:
            logger.error(f"❌ VLM describe timeout ({vlm_budget:.0f}s) — skipping frame")
            return False, "", "", 0.0, ""
        except httpx.ConnectError as e:
            logger.error(f"❌ VLM describe ConnectError: {e}")
            return False, "", "", 0.0, ""

        if vlm_resp.status_code != 200:
            logger.warning(f"⚠️ VLM Describe HTTP {vlm_resp.status_code} — body: {vlm_resp.text[:200]}")
            return False, "", "", 0.0, ""

        vlm_data = vlm_resp.json()
        scene_description = vlm_data.get("text", "")
        logger.info(f"📨 VLM raw response keys: {list(vlm_data.keys())}, text_len={len(scene_description)}, error={vlm_data.get('error','none')}")

        if not scene_description:
            # Log full response body to diagnose
            logger.warning(f"⚠️ VLM returned empty description — full response: {vlm_resp.text[:400]}")
            return False, "", "", 0.0, ""
        
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
        
        # ── TARGET PARROT GUARD ──
        # VLM sometimes echoes the target name instead of describing the scene.
        # e.g. searching for "ขวดน้ำฝาสีน้ำเงิน" → VLM returns "ขวดน้ำฝาสีน้ำเงิน" (17 chars)
        # This causes LLM to say found=True with 1.0 confidence — false positive!
        # Guard: if VLM output is short (<100 chars) and mostly matches the target, reject.
        _target_norm = target.strip().lower()
        _vlm_norm = scene_description.strip().lower()
        _is_parrot = (
            len(scene_description) < 100
            and (_target_norm in _vlm_norm or _vlm_norm in _target_norm
                 or (vlm_target and vlm_target.lower() in _vlm_norm))
        )
        if _is_parrot:
            logger.warning(f"⚠️ VLM PARROT detected: output '{scene_description[:80]}' echoes target '{target[:40]}' — rejecting")
            return False, "", f"__REJECTED__:target_parrot:{scene_description[:100]}", 0.0, ""
        
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
            return False, "", f"__REJECTED__:{reason}:{scene_description[:100]}", 0.0, ""
        
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
            f'"reason": "เหตุผลไม่ยาวมาก แต่ไม่สั้น", "confidence": 0.0-1.0}}'
        )
        
        try:
            llm_resp = await _http.post(llm_url, json={
                    "prompt": llm_prompt,
                    "system": llm_system,
                    "temperature": 0.2,
                    "max_tokens": 256,
                }, timeout=llm_budget)
        except httpx.TimeoutException:
            logger.error(f"❌ LLM reasoning timeout ({llm_budget:.0f}s) — keyword fallback")
            return _fallback_keyword_check(target, scene_description), "unknown", scene_description, 0.3, "LLM timeout, keyword fallback"
        except httpx.ConnectError as e:
            logger.error(f"❌ LLM reasoning ConnectError: {e}")
            return _fallback_keyword_check(target, scene_description), "unknown", scene_description, 0.3, "LLM connect error, keyword fallback"

        if llm_resp.status_code != 200:
            logger.warning(f"⚠️ LLM Reasoning HTTP {llm_resp.status_code}")
            return _fallback_keyword_check(target, scene_description), "unknown", scene_description, 0.5, "LLM failed, keyword fallback"

        llm_data = llm_resp.json()
        llm_text = llm_data.get("response", "")

        logger.info(f"🧠 LLM Response: {llm_text[:150]}...")

        # ─── Parse LLM JSON Response ───
        found, location, reason, confidence = _parse_llm_reasoning(llm_text)

        logger.info(f"   ✅ Result: found={found}, location={location}, confidence={confidence}")
        if reason:
            logger.info(f"   💭 Reason: {reason}")

        # Only count as found if confidence is reasonably high
        if found and confidence < 0.7:
            logger.info(f"   ⚠️ Low confidence ({confidence}), treating as not found")
            found = False

        return found, location, scene_description, confidence, reason

    except Exception as e:
        logger.error(f"❌ VLM+LLM check error [{type(e).__name__}]: {e}")
        return False, "", "", 0.0, ""


def _strip_think_blocks(text: str) -> str:
    """Remove <think>...</think> chain-of-thought blocks from LLM output.
    gemma4/qwen3 models produce these even when not requested."""
    if not text or "<think>" not in text:
        return text
    import re as _re
    cleaned = _re.sub(r"<think>.*?</think>", "", text, flags=_re.DOTALL)
    if "<think>" in cleaned:
        cleaned = cleaned.split("<think>")[0]
    return cleaned.strip()


def _parse_llm_reasoning(llm_text: str) -> tuple:
    """Parse LLM JSON response for found/location/reason/confidence.
    Strips <think> blocks and markdown fences before extraction.
    Returns (found, location, reason, confidence)"""
    import json as _json

    # Strip <think> blocks first (gemma4/qwen3 chain-of-thought)
    text = _strip_think_blocks(llm_text).strip()

    # Remove markdown code fences
    if "```json" in text:
        text = text.split("```json", 1)[1]
        text = text.split("```", 1)[0]
    elif "```" in text:
        text = text.split("```", 1)[1]
        text = text.split("```", 1)[0]

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
    global _search_active, _current_search_target

    loc_thai = _location_to_thai(location)
    loc_text = f" อยู่ทาง{loc_thai}" if loc_thai else ""
    announce = f"เจอ{target}แล้วครับ!{loc_text} กำลังเคลื่อนที่เข้าไปครับ"
    
    logger.info(f"═" * 50)
    logger.info(f"🎉 OBJECT DETECTED: '{target}'")
    logger.info(f"   Location: {location} ({loc_thai})")
    logger.info(f"   → Starting APPROACH phase")
    logger.info(f"═" * 50)
    
    # 🔊 TTS: Announce found + approaching (fire-and-forget — don't block approach)
    speak_tts_bg(announce)
    
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
                speak_tts_bg(f"ถึงแล้วครับ {target} อยู่ตรงหน้า")
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
                speak_tts_bg("มีสิ่งกีดขวางขวางทาง ไม่สามารถเข้าถึงได้ครับ")
                break
            
            # ── Side-wall avoidance: steer away from close walls ──
            await asyncio.sleep(0.2)  # let LiDAR settle
            left_clear, right_clear = _obstacle_avoidance.get_side_clearance()
            SIDE_WARN = 0.20  # 20cm — wall is dangerously close
            SIDE_STEER = 0.15  # angular_z correction (rad/s)
            steer_z = 0.0
            
            if left_clear < SIDE_WARN and right_clear >= SIDE_WARN:
                # Wall on left → steer right
                steer_z = -SIDE_STEER
                logger.info(f"   🧱 Wall LEFT {left_clear:.2f}m → steering right")
            elif right_clear < SIDE_WARN and left_clear >= SIDE_WARN:
                # Wall on right → steer left
                steer_z = SIDE_STEER
                logger.info(f"   🧱 Wall RIGHT {right_clear:.2f}m → steering left")
            elif left_clear < SIDE_WARN and right_clear < SIDE_WARN:
                # Walls both sides — narrow corridor, go straight but slow
                move_dur = min(move_dur, 1.0)
                logger.info(f"   🧱 Narrow corridor (L={left_clear:.2f}m R={right_clear:.2f}m) — slow straight")
            else:
                logger.info(f"   📐 Side clearance OK (L={left_clear:.2f}m R={right_clear:.2f}m)")
            
            move_cmd = {
                "type": "move",
                "linear_x": APPROACH_SPEED,
                "angular_z": steer_z,
                "duration": move_dur,
            }
            completed = await motion.exec_motion(move_cmd, obstacle_checker=_lidar_obstacle_check)
            if not completed:
                logger.warning("   🛑 Approach move interrupted by LiDAR obstacle!")
                speak_tts_bg("ตรวจพบสิ่งกีดขวาง หยุดเข้าใกล้ครับ")
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
        found, new_location, new_desc, _approach_conf, _approach_reason = await _vlm_check(target, 30.0)
        
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
    _current_search_target = ""

    # Final announce
    final_loc_thai = _location_to_thai(current_location)
    final_announce = f"ถึงแล้วครับ {target} อยู่ตรงนี้"
    
    logger.info(f"═" * 50)
    logger.info(f"🏁 APPROACH COMPLETE: Arrived at '{target}'")
    logger.info(f"   Final location: {current_location} ({final_loc_thai})")
    logger.info(f"═" * 50)
    
    speak_tts_bg(final_announce)

    # 📸 Capture image and save for webapp
    capture_url = None
    try:
        resp = await _http.post(f"{SERVER_BASE}/camera/capture", timeout=5.0)
        if resp.status_code == 200:
                data = resp.json()
                capture_url = data.get("url")
                logger.info(f"📸 Captured: {capture_url}")
    except Exception as e:
        logger.warning(f"⚠️ Capture failed: {e}")
    
    # 📚 Remember observation in memory (observer pose, not object pose).
    # Suppressed when localization is untrustworthy: no source, post-relocalize
    # window, or pose still at default (0,0).
    normalized = normalize_search_target(target)
    _ox = _robot_pose.get("x", 0.0)
    _oy = _robot_pose.get("y", 0.0)
    _oth = _robot_pose.get("theta", 0.0)
    _trust_ok = (
        _odom_source not in ("", "none")
        and time.time() >= _memory_block_until
        and not (abs(_ox) < 0.01 and abs(_oy) < 0.01)
    )
    if _trust_ok:
        object_memory.remember(
            object_name=normalized,
            display_name=target,
            location=current_location,
            location_description=final_loc_thai,
            confidence=0.9,
            image_url=capture_url,
            observer_x=_ox,
            observer_y=_oy,
            observer_theta=_oth,
            localization_source=_odom_source,
        )
    else:
        logger.warning(
            f"📕 SKIP remember('{normalized}'): pose untrusted "
            f"src={_odom_source!r} pose=({_ox:.2f},{_oy:.2f},{_oth:.2f}) "
            f"block_left={max(0.0, _memory_block_until - time.time()):.1f}s"
        )
    
    # NOTE: robot_x/robot_y kept for webapp backward compat — these are OBSERVER pose, not object pose
    _obs_x = round(_robot_pose.get("x", 0.0), 3)
    _obs_y = round(_robot_pose.get("y", 0.0), 3)
    _obs_theta = round(_robot_pose.get("theta", 0.0), 3)
    await _broadcast_and_cache({
        "type": "search_status",
        "status": "found",
        "target": target,
        "location": current_location,
        "location_thai": final_loc_thai,
        "description": description,
        "announce": final_announce,
        "capture_url": capture_url,
        "robot_x": _obs_x,             # DEPRECATED — use observer_x
        "robot_y": _obs_y,             # DEPRECATED — use observer_y
        "observer_x": _obs_x,
        "observer_y": _obs_y,
        "observer_theta": _obs_theta,
        "section": get_section(_obs_x, _obs_y),
        "bearing_deg": location_to_bearing(current_location),
        "object_pose_estimated": None,
        "pose_semantics": "observer_pose_not_object_pose",
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
    global _search_active, _current_search_target
    _search_active = False
    _current_search_target = ""
    
    # Stop the robot
    if not MOCK_ROBOT:
        stop_cmd = {"type": "move", "linear_x": 0.0, "angular_z": 0.0, "duration": 0.1}
        await motion.exec_motion(stop_cmd)
    
    announce = f"ขออภัยครับ ค้นหา{target}แล้วแต่ยังไม่พบ"
    
    logger.info(f"═" * 50)
    logger.info(f"😔 SEARCH COMPLETE: '{target}' NOT FOUND")
    logger.info(f"   Total VLM checks: {total_checks}")
    
    # 🔊 TTS: Announce not found (fire-and-forget)
    speak_tts_bg(announce)
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
    global _search_active, _search_cancel, _current_search_target
    _search_active = False
    _search_cancel = False
    _search_cancel_event.clear()
    _current_search_target = ""
    
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
    _search_cancel_event.clear()

    # Set cancel checker so motion can be interrupted (same as visual_search)
    motion._cancel_checker = lambda: _search_cancel

    found_objects = []
    not_found_objects = []

    try:  # ensure cleanup on any exit path

        logger.info(f"═" * 50)
        logger.info(f"🔍 MULTI-OBJECT SEARCH (One VLM, Many LLM): {targets}")
        logger.info(f"═" * 50)

        # Announce start
        targets_text = " กับ ".join(targets)
        speak_tts_bg(f"กำลังค้นหา {targets_text} ครับ")

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

                    # Remember observation in memory (observer pose, not object pose)
                    normalized = normalize_search_target(t)
                    _ox2 = _robot_pose.get("x", 0.0)
                    _oy2 = _robot_pose.get("y", 0.0)
                    _oth2 = _robot_pose.get("theta", 0.0)
                    _trust_ok2 = (
                        _odom_source not in ("", "none")
                        and time.time() >= _memory_block_until
                        and not (abs(_ox2) < 0.01 and abs(_oy2) < 0.01)
                    )
                    if _trust_ok2:
                        object_memory.remember(
                            object_name=normalized,
                            display_name=t,
                            location=location,
                            location_description="",
                            confidence=confidence,
                            observer_x=_ox2,
                            observer_y=_oy2,
                            observer_theta=_oth2,
                            localization_source=_odom_source,
                        )
                    else:
                        logger.warning(
                            f"📕 SKIP remember('{normalized}'): pose untrusted "
                            f"src={_odom_source!r} pose=({_ox2:.2f},{_oy2:.2f},{_oth2:.2f}) "
                            f"block_left={max(0.0, _memory_block_until - time.time()):.1f}s"
                        )

                    # NOTE: robot_x/robot_y kept for webapp compat — OBSERVER pose, not object pose
                    _obs_x2 = round(_robot_pose.get("x", 0.0), 3)
                    _obs_y2 = round(_robot_pose.get("y", 0.0), 3)
                    _obs_theta2 = round(_robot_pose.get("theta", 0.0), 3)
                    await _broadcast_and_cache({
                        "type": "search_status",
                        "status": "found",
                        "target": t,
                        "location": location,
                        "description": desc,
                        "robot_x": _obs_x2,            # DEPRECATED — use observer_x
                        "robot_y": _obs_y2,             # DEPRECATED — use observer_y
                        "observer_x": _obs_x2,
                        "observer_y": _obs_y2,
                        "observer_theta": _obs_theta2,
                        "section": get_section(_obs_x2, _obs_y2),
                        "bearing_deg": location_to_bearing(location),
                        "object_pose_estimated": None,
                        "pose_semantics": "observer_pose_not_object_pose",
                    })
                else:
                    not_found_objects.append(t)

        # Summary
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

        speak_tts_bg(summary)

        await _broadcast_and_cache({
            "type": "search_status",
            "status": "multi_complete",
            "targets": targets,
            "found": found_objects,
            "not_found": not_found_objects,
            "summary": summary,
        })

    except Exception as e:
        logger.error(f"❌ Multi-search error: {e}")
    finally:
        _search_active = False
        _search_cancel = False
        _search_cancel_event.clear()
        _current_search_target = ""
        motion._cancel_checker = None


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
            # math is imported at module top level (line 1)
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

        elif cmd == "relocalize":
            # ── Set AMCL initial pose (manual relocalization) ──
            rx = float(params.get("x", 0.0))
            ry = float(params.get("y", 0.0))
            rtheta = float(params.get("theta", 0.0))
            logger.info(
                f"📍 Relocalize REQ: x={rx:.3f} y={ry:.3f} "
                f"theta={rtheta:.3f}rad ({math.degrees(rtheta):.1f}°) "
                f"search_active={_search_active}"
            )

            # ── REFUSE relocalize during active search/navigation ──
            # Mid-task re-anchoring corrupts AMCL→costmap→memory. Caller must
            # cancel the active task explicitly first.
            if _search_active and not _search_cancel:
                logger.warning(
                    f"⚠️ Relocalize REJECTED: search/nav is active "
                    f"(target={_current_search_target!r}) — cancel the task first"
                )
                try:
                    await _broadcast_and_cache({
                        "type": "relocalize_status",
                        "status": "rejected",
                        "reason": "search_active",
                        "target": _current_search_target,
                    })
                except Exception:
                    pass
                return

            # Block memory writes for a short window after relocalize so
            # observer pose has a chance to settle (AMCL convergence).
            global _memory_block_until
            _memory_block_until = time.time() + 5.0

            if not MOCK_ROBOT:
                try:
                    ros = await ensure_ros(ROSBRIDGE)
                    import roslibpy
                    topic = roslibpy.Topic(
                        ros, "/initialpose",
                        "geometry_msgs/PoseWithCovarianceStamped",
                    )
                    qz = math.sin(rtheta / 2.0)
                    qw = math.cos(rtheta / 2.0)
                    msg = roslibpy.Message({
                        "header": {"frame_id": "map"},
                        "pose": {
                            "pose": {
                                "position": {"x": rx, "y": ry, "z": 0.0},
                                "orientation": {"x": 0.0, "y": 0.0, "z": qz, "w": qw},
                            },
                            "covariance": [
                                0.25, 0, 0, 0, 0, 0,
                                0, 0.25, 0, 0, 0, 0,
                                0, 0, 0, 0, 0, 0,
                                0, 0, 0, 0, 0, 0,
                                0, 0, 0, 0, 0, 0,
                                0, 0, 0, 0, 0, 0.068,
                            ],
                        },
                    })
                    # Publish multiple times to ensure AMCL receives and converges
                    for _pub_i in range(3):
                        topic.publish(msg)
                        await asyncio.sleep(0.2)
                    logger.info(f"✅ Published /initialpose x3: ({rx:.2f}, {ry:.2f}, {math.degrees(rtheta):.1f}°)")
                except Exception as e:
                    logger.error(f"❌ Relocalize failed: {e}")

            # Immediately update local pose for responsive feedback
            # Suppress AMCL overwrite for a brief window so the UI doesn't snap back
            global _amcl_suppress_until
            _amcl_suppress_until = time.time() + 3.0  # ignore AMCL for 3s after relocalize
            _robot_pose["x"] = rx
            _robot_pose["y"] = ry
            _robot_pose["theta"] = rtheta

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
    """Emergency-only forward stop for manual cmd_vel motion.

    Uses a narrow ±15° true frontal cone at 0.18m — triggers ONLY for
    genuine imminent collision. The wide ±16°–60° cone (get_forward_clearance)
    at 0.45m threshold is NOT used here: it picks up corridor side walls at
    0.30–0.32m slant range and causes false 'LIDAR INTERRUPT at step 0'.

    WARN range (0.18–0.35m): logged but does NOT interrupt motion.
    STOP (<0.18m): true frontal collision imminent — interrupt.
    """
    hard_front = _obstacle_avoidance._get_range_at(0, tol_deg=15)
    if hard_front is not None and hard_front < 0.18:
        logger.warning(f"[SAFETY] STOP: hard_front={hard_front:.2f}m < 0.18m — collision imminent")
        return True
    # Advisory warn — log only, do not stop
    wide_front = _obstacle_avoidance.get_forward_clearance()
    if wide_front < 0.35:
        logger.info(f"[OBS] WARN: wide_front={wide_front:.2f}m — close but continuing")
    return False


# ── AMCL localization readiness gate ──────────────────────────────────
# Tracks the last few AMCL samples so we can block navigation starts
# until the pose estimate stops jumping. Writes come from
# ``_amcl_pose_callback``; reads come from ``wait_for_localization_ready``.
_amcl_recent_poses: List[tuple] = []  # (x, y) from last few messages
_AMCL_HISTORY_MAX = 5
_AMCL_READY_MIN_SAMPLES = 3
_AMCL_READY_VAR_M2 = 0.09  # 30 cm stddev budget (loosened from 0.04 — low-motion AMCL still valid)


async def wait_for_localization_ready(timeout: float = 10.0) -> bool:
    """Block until AMCL has published at least 3 recent samples with variance
    below threshold.

    FAIL-OPEN: if AMCL has *any* data when timeout expires, we allow navigation
    rather than hard-blocking. Absence of AMCL entirely → returns False."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        hist = list(_amcl_recent_poses)
        if len(hist) >= _AMCL_READY_MIN_SAMPLES:
            mx = sum(p[0] for p in hist) / len(hist)
            my = sum(p[1] for p in hist) / len(hist)
            var = sum((p[0] - mx) ** 2 + (p[1] - my) ** 2 for p in hist) / len(hist)
            if var < _AMCL_READY_VAR_M2:
                logger.info(
                    f"[NAV] localization ready (samples={len(hist)}, var={var:.4f})"
                )
                return True
        await asyncio.sleep(0.2)
    # Timeout — fail-open if AMCL has any data (trust Nav2 to handle it)
    hist = list(_amcl_recent_poses)
    if hist:
        mx = sum(p[0] for p in hist) / len(hist)
        my = sum(p[1] for p in hist) / len(hist)
        var = sum((p[0] - mx) ** 2 + (p[1] - my) ** 2 for p in hist) / len(hist)
        logger.warning(
            f"[NAV] localization timeout but AMCL present — "
            f"samples={len(hist)} var={var:.4f} → ALLOW (trusting Nav2)"
        )
        return True
    logger.warning(
        f"[NAV] blocked: localization not ready — no AMCL data (timeout={timeout:.0f}s)"
    )
    return False


async def _rotate_deg(degrees: float = 90.0) -> None:
    """Module-level rotate-by-degrees used from nav preflight + watchdogs.

    Simple cmd_vel blocking rotate — 0.5 rad/s, duration = |deg|/90 * 3.0s.
    No rear_obstacle_checker is passed: rotation does not translate the robot,
    and the rear check was firing at step 0 on every in-place turn near a
    wall. Tight-space guard is owned by caller via ``_rotation_possible()``."""
    if MOCK_ROBOT:
        logger.info(f"🤖 [MOCK] Would rotate {degrees}°")
        await asyncio.sleep(0.3)
        return
    if abs(degrees) < 0.5:
        return
    duration = (abs(degrees) / 90.0) * 3.0
    direction = 0.5 if degrees > 0 else -0.5
    phys = "LEFT(CCW)" if degrees > 0 else "RIGHT(CW)"
    logger.info(f"🔄 [NAV] rotate {degrees:+.0f}° → {phys} (dur={duration:.2f}s)")
    try:
        await motion.exec_motion(
            {"type": "move", "linear_x": 0.0, "angular_z": direction,
             "duration": duration},
        )
    except Exception as e:
        logger.warning(f"[NAV] rotate_deg exec failed: {e}")
    await asyncio.sleep(0.3)


# Robot half-diagonal ≈ sqrt(0.13² + 0.105²) ≈ 0.167m. We need at least this
# much on both sides to rotate in place without clipping a wall.
_ROTATION_MIN_SIDE_CLEAR = 0.18


def _rotation_possible() -> bool:
    """Return False if both sides are too tight for a safe in-place rotation.
    Used before every recovery-rotate so we fail fast instead of grinding the
    body against a wall during a turn attempt."""
    try:
        left, right = _obstacle_avoidance.get_side_clearance()
    except Exception:
        return True  # no data — don't block
    if not math.isfinite(left):
        left = 10.0
    if not math.isfinite(right):
        right = 10.0
    if left < _ROTATION_MIN_SIDE_CLEAR and right < _ROTATION_MIN_SIDE_CLEAR:
        logger.warning(
            f"[NAV] turn impossible in tight space: "
            f"left={left:.2f} right={right:.2f} < {_ROTATION_MIN_SIDE_CLEAR}"
        )
        return False
    return True


async def _safe_reverse(speed: float = 0.10, duration: float = 1.0) -> bool:
    """BANNED unless the side-rear band is lit AND shows clearance.

    LiDAR has a ~130° rear blind spot so the rear is *never* fully known.
    We allow a short, slow reverse only when the side-rear band (±90–110°)
    has valid data and reports no obstacle within 0.25 m. Everything else
    → reverse is disabled; we stop and return False so the caller can
    fail cleanly instead of backing into a wall it cannot see."""
    if MOCK_ROBOT:
        logger.info(f"🤖 [MOCK] Would reverse speed={speed} dur={duration}")
        return True
    if not _obstacle_avoidance.has_rear_scan_data():
        logger.warning("[SAFETY] reverse disabled: rear unknown")
        return False
    checker = _obstacle_avoidance.get_rear_obstacle_checker(threshold_m=0.25)
    if checker():
        logger.warning("[SAFETY] reverse disabled: rear obstacle within 0.25m")
        return False
    logger.info(f"[SAFETY] reverse allowed: rear clear, speed={speed:.2f} dur={duration:.1f}s")
    try:
        await motion.exec_motion(
            {"type": "move", "linear_x": -abs(speed),
             "angular_z": 0.0, "duration": duration},
            rear_obstacle_checker=checker,
        )
    except Exception as e:
        logger.warning(f"[SAFETY] safe reverse exec failed: {e}")
        return False
    return True


async def _preflight_forward_clearance(min_clearance: float | None = None) -> bool:
    """READ-ONLY preflight: log LiDAR clearances, then yield to Nav2 immediately.
    No rotation, no movement. Nav2 owns all path planning from start to goal."""
    if min_clearance is None:
        min_clearance = _SAFE_FORWARD_START
    try:
        fwd0 = _obstacle_avoidance.get_forward_clearance()
    except Exception:
        fwd0 = 10.0

    _CAP = 5.0
    try:
        raw_left = _obstacle_avoidance._directional_clearance(45.0)[0]
        fwd_left = min(_CAP, raw_left) if math.isfinite(raw_left) else _CAP
    except Exception:
        fwd_left = 0.0
    try:
        raw_right = _obstacle_avoidance._directional_clearance(-45.0)[0]
        fwd_right = min(_CAP, raw_right) if math.isfinite(raw_right) else _CAP
    except Exception:
        fwd_right = 0.0

    logger.info(
        f"[SAFETY] preflight (read-only): fwd={fwd0:.2f} L+45={fwd_left:.2f} "
        f"R-45={fwd_right:.2f} threshold={min_clearance:.2f} — Nav2 handles path"
    )
    return True


async def _smooth_stop() -> None:
    """Ramp cmd_vel down before a hard stop so the robot doesn't slam.
    0.05 → 0 → publish stop. Non-fatal on any publish error."""
    try:
        await motion.exec_motion(
            {"type": "move", "linear_x": 0.05, "angular_z": 0.0, "duration": 0.2},
            obstacle_checker=None,
        )
    except Exception:
        pass
    try:
        await motion.exec_motion(
            {"type": "move", "linear_x": 0.0, "angular_z": 0.0, "duration": 0.1},
            obstacle_checker=None,
        )
    except Exception:
        pass
    try:
        motion.publish_stop() if hasattr(motion, "publish_stop") else None
    except Exception:
        pass


# ── Collision / Nav2 danger watchdog ─────────────────────────────────
# Runs alongside active Nav2 goals. Two triggers:
#   1. forward clearance drops below DANGER_FORWARD → cancel Nav2 now
#   2. robot barely moved (<0.02m) for STALL_WINDOW seconds while a goal
#      is active → log [COLLISION], cancel Nav2, recover with a 45° rotate
_SAFE_FORWARD_START = 0.45   # preflight: advisory only — never blocks
_SAFE_FORWARD_DANGER = 0.15  # runtime: emergency stop only (imminent collision)
_STALL_WINDOW = 12.0         # no-progress window — give Nav2 time to plan + rotate
_STALL_MIN_DELTA = 0.05      # minimum movement to reset stall clock (meters)
_STALL_GRACE = 40.0          # seconds before arming stall detection.
                             # Raised from 6→40: Jetson Nano DDS discovery + Nav2 costmap
                             # init takes 30-40s before robot-side accepts the goal.
                             # Firing the stall watchdog during that window was cancelling
                             # goals that would have succeeded.


async def _nav_safety_watchdog(cancel_event: asyncio.Event) -> Dict[str, Any]:
    """Runtime safety while Nav2 goal is active. Two triggers only:

    (A) HARD COLLISION: forward < SAFE_FORWARD_DANGER (0.15 m) → cancel now.
    (B) NO-PROGRESS STALL: BOTH amcl delta AND odom delta < STALL_MIN_DELTA for
        STALL_WINDOW seconds, armed only after grace period. Requiring both sources
        to agree prevents false stalls when /odom x,y is stuck at 0 (MyAGV bug)
        but AMCL is actively tracking real movement.

    Everything else: trust Nav2. Gateway emergency stop only."""
    watchdog_started = time.monotonic()
    stall_start = time.monotonic()
    # Snapshot both pose sources independently at watchdog start
    amcl_start = _amcl_watchdog_xy
    odom_start = _odom_watchdog_xy
    _acceptance_grace_logged = False  # one-time log when grace expires without acceptance
    while not cancel_event.is_set():
        # (A) Hard collision guard — imminent contact only
        try:
            fwd = _obstacle_avoidance.get_forward_clearance()
        except Exception:
            fwd = 999.0
        if fwd < _SAFE_FORWARD_DANGER:
            logger.warning(
                f"[SAFETY] emergency stop only fwd={fwd:.2f} < {_SAFE_FORWARD_DANGER} — cancel Nav2"
            )
            try:
                if _nav2_client is not None:
                    await _nav2_client.cancel_navigation()
            except Exception:
                pass
            await _smooth_stop()
            return {"reason": "danger_forward", "forward": fwd}

        # (B) No-progress stall — BOTH sources must show no movement
        amcl_cur = _amcl_watchdog_xy
        odom_cur = _odom_watchdog_xy
        amcl_delta = math.hypot(amcl_cur[0] - amcl_start[0], amcl_cur[1] - amcl_start[1])
        odom_delta = math.hypot(odom_cur[0] - odom_start[0], odom_cur[1] - odom_start[1])

        # ── Acceptance guard ─────────────────────────────────────────
        # Robot-side Nav2 acceptance can take 30-40s (Jetson Nano DDS + costmap init).
        # While the goal is still pending ("SENT" / no acknowledgement yet), the robot
        # hasn't started moving — treating zero motion as a stall is wrong.
        # Suppress stall until we see ACCEPTED/RUNNING OR the grace window expires.
        elapsed_total = time.monotonic() - watchdog_started
        try:
            _nav_status = (_nav2_client.nav_status_name or "").upper() if _nav2_client else ""
        except Exception:
            _nav_status = ""
        _nav_pending = _nav_status in ("", "SENT", "IDLE")

        if _nav_pending and elapsed_total < _STALL_GRACE:
            logger.info(
                "[NAV] waiting for robot-side Nav2 acceptance "
                "(status=%s elapsed=%.1fs grace=%.0fs) — stall watchdog suppressed",
                _nav_status or "none", elapsed_total, _STALL_GRACE,
            )
            # Keep resetting stall clock so the full STALL_WINDOW is available
            # after acceptance, not just whatever's left from a stale clock.
            stall_start = time.monotonic()
            amcl_start = amcl_cur
            odom_start = odom_cur
            await asyncio.sleep(0.2)
            continue

        if elapsed_total >= _STALL_GRACE and _nav_pending and not _acceptance_grace_logged:
            logger.warning(
                "[NAV] acceptance grace expired (%.0fs) — arming stall detection "
                "(status=%s; no ACCEPTED/RUNNING signal received from robot-side)",
                elapsed_total, _nav_status or "none",
            )
            _acceptance_grace_logged = True
        # ─────────────────────────────────────────────────────────────

        pose_reliable = _amcl_pose_active or _odom_xy_moving
        armed = (time.monotonic() - watchdog_started) >= _STALL_GRACE

        if armed and pose_reliable:
            # Movement in EITHER source resets the stall clock.
            # This prevents false stalls when odom is stuck at 0 but AMCL is moving.
            if amcl_delta >= _STALL_MIN_DELTA or odom_delta >= _STALL_MIN_DELTA:
                amcl_start = amcl_cur
                odom_start = odom_cur
                stall_start = time.monotonic()
            else:
                elapsed = time.monotonic() - stall_start
                logger.debug(
                    "[NAV] movement check: amcl=%.3fm, odom=%.3fm, stall_elapsed=%.1fs",
                    amcl_delta, odom_delta, elapsed,
                )
                if elapsed >= _STALL_WINDOW:
                    logger.error(
                        "[SAFETY] no-progress stall — amcl=%.3fm odom=%.3fm both < %.2fm for %.1fs",
                        amcl_delta, odom_delta, _STALL_MIN_DELTA, _STALL_WINDOW,
                    )
                    try:
                        if _nav2_client is not None:
                            await _nav2_client.cancel_navigation()
                    except Exception:
                        pass
                    await _smooth_stop()
                    return {"reason": "collision_stall"}
        else:
            # Pose not yet reliable → reset stall clock; don't fire false positive
            stall_start = time.monotonic()
            amcl_start = amcl_cur
            odom_start = odom_cur

        await asyncio.sleep(0.2)
    return {}


async def _navigate_with_watchdog(x: float, y: float, timeout: float = 45.0) -> bool:
    """Run navigate_to_zone alongside the safety watchdog.

    Nav success → True.
    Watchdog fires (danger or stall) → cancels nav task, returns False immediately
    so the caller's retry loop can take corrective action without hanging."""
    if _nav2_client is None:
        return False
    cancel_event = asyncio.Event()
    nav_task = asyncio.create_task(
        _nav2_client.navigate_to_zone(x=x, y=y, timeout=timeout)
    )
    watch_task = asyncio.create_task(_nav_safety_watchdog(cancel_event))
    done, _pending = await asyncio.wait(
        {nav_task, watch_task}, return_when=asyncio.FIRST_COMPLETED
    )
    if nav_task in done:
        # Nav finished first — stop watchdog cleanly
        cancel_event.set()
        watch_task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(watch_task), timeout=0.5)
        except Exception:
            pass
        try:
            return bool(nav_task.result())
        except Exception:
            return False
    # Watchdog fired first — cancel nav task, no hang
    cancel_event.set()
    nav_task.cancel()
    try:
        await asyncio.wait_for(asyncio.shield(nav_task), timeout=2.0)
    except Exception:
        pass
    return False


# ── Camera Frame Push (Gateway → Server) ─────────────────────────────

# ── Robot Pose Tracking (from /odom via ROSBridge) ────────────────────
_robot_pose = {"x": 0.0, "y": 0.0, "theta": 0.0}

# ── Current semantic zone (persists across searches) ──
# Updated only after a CONFIRMED navigate_to_zone arrival. The decision
# layer reads this to decide whether memory is trustworthy for the current
# request (memory from other zones is ignored when target_zone differs).
_current_zone: Optional[str] = None
_odom_subscribed = False
_odom_msg_count = 0
_odom_source = "none"  # "amcl" | "odom_hybrid" | "dead_reckoning" | "none"
_odom_xy_moving = False  # True if /odom x,y actually changes over time

# Track whether /odom provides real x,y or just yaw
_odom_first_pos = None  # (x, y) from first odom message
_odom_xy_drift = 0.0    # cumulative x,y drift from first position

# AMCL localized pose (map frame) — takes priority when available
_amcl_pose_active = False
_amcl_pose_count = 0
_amcl_suppress_until = 0.0  # timestamp — AMCL callback ignores updates until this time

# Separate watchdog tracking — AMCL and odom maintained independently so the
# stall detector can require BOTH to show no movement before firing.
# odom x,y is always tracked raw (even when AMCL is active) so the watchdog
# has two independent signals and won't false-fire when only one is stuck.
_amcl_watchdog_xy: tuple = (0.0, 0.0)   # last AMCL map-frame x,y
_odom_watchdog_xy: tuple = (0.0, 0.0)   # last raw /odom x,y

def _odom_callback(msg):
    """Extract theta from /odom. MyAGV odom provides reliable yaw but
    x,y is often stuck near 0 due to Mecanum wheel encoder limitations.
    We use odom ONLY for theta; dead reckoning handles x,y."""
    global _odom_msg_count, _odom_source, _odom_first_pos, _odom_xy_drift, _odom_xy_moving
    global _odom_watchdog_xy
    try:
        pos = msg["pose"]["pose"]["position"]
        ori = msg["pose"]["pose"]["orientation"]
        # Quaternion → yaw
        siny = 2.0 * (ori["w"] * ori["z"] + ori["x"] * ori["y"])
        cosy = 1.0 - 2.0 * (ori["y"] ** 2 + ori["z"] ** 2)
        theta = math.atan2(siny, cosy)

        # Always use odom theta — it's reliable from IMU/encoders
        # BUT if AMCL is active, AMCL provides map-frame pose (more accurate)
        if not _amcl_pose_active:
            _robot_pose["theta"] = theta

        # Track whether /odom x,y actually changes
        ox, oy = pos["x"], pos["y"]
        # Always update raw odom watchdog tracking (independent of AMCL state)
        _odom_watchdog_xy = (ox, oy)
        if _odom_first_pos is None:
            _odom_first_pos = (ox, oy)
        else:
            drift = math.sqrt((ox - _odom_first_pos[0])**2 + (oy - _odom_first_pos[1])**2)
            if drift > _odom_xy_drift:
                _odom_xy_drift = drift
            # If odom x,y has moved >5cm total, it's working — use it for position too
            if _odom_xy_drift > 0.05 and not _amcl_pose_active:
                _odom_xy_moving = True
                _robot_pose["x"] = ox
                _robot_pose["y"] = oy

        if not _amcl_pose_active:
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

def _amcl_pose_callback(msg):
    """Extract position from /amcl_pose (map frame). Takes priority over /odom
    because AMCL provides the corrected map-frame position for display on SLAM map."""
    global _amcl_pose_active, _amcl_pose_count, _odom_source, _amcl_watchdog_xy
    try:
        # Suppress AMCL updates briefly after manual relocalize to avoid snap-back
        if time.time() < _amcl_suppress_until:
            _amcl_pose_count += 1
            return

        pos = msg["pose"]["pose"]["position"]
        ori = msg["pose"]["pose"]["orientation"]
        siny = 2.0 * (ori["w"] * ori["z"] + ori["x"] * ori["y"])
        cosy = 1.0 - 2.0 * (ori["y"] ** 2 + ori["z"] ** 2)
        theta = math.atan2(siny, cosy)

        _robot_pose["x"] = pos["x"]
        _robot_pose["y"] = pos["y"]
        _robot_pose["theta"] = theta
        _odom_source = "amcl"
        _amcl_pose_active = True
        _amcl_watchdog_xy = (pos["x"], pos["y"])  # independent watchdog tracking
        _amcl_pose_count += 1

        # Track last N samples for wait_for_localization_ready() variance check.
        _amcl_recent_poses.append((pos["x"], pos["y"]))
        if len(_amcl_recent_poses) > _AMCL_HISTORY_MAX:
            _amcl_recent_poses.pop(0)

        if _amcl_pose_count <= 3 or _amcl_pose_count % 200 == 0:
            logger.info(
                f"📍 AMCL #{_amcl_pose_count}: x={pos['x']:.3f} y={pos['y']:.3f} θ={math.degrees(theta):.1f}° [map frame]"
            )
    except (KeyError, TypeError) as e:
        _amcl_pose_count += 1
        if _amcl_pose_count <= 5:
            logger.warning(f"⚠️ AMCL pose parse error #{_amcl_pose_count}: {e}")

def _update_dead_reckoning(linear_x: float, angular_z: float, duration: float):
    """Update robot pose x,y via dead reckoning from cmd_vel commands.
    Always updates x,y unless /odom x,y has been verified as working.
    Theta is always from /odom when available (more accurate than DR)."""
    global _odom_source
    # If /odom x,y is verified working, skip DR for position
    if _odom_xy_moving:
        return
    # AMCL provides map-frame pose but publishes infrequently.
    # We ALWAYS apply DR so that stuck detection works between AMCL updates.
    # When AMCL publishes again, it overwrites x,y with corrected values.
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

async def _subscribe_odom(ros=None):
    """Subscribe to /odom via ROSBridge for robot position tracking.
    If `ros` is provided (already connected), use it directly (no ensure_ros() call).
    Otherwise retries up to 5 times with 10s delay if ROSBridge isn't ready."""
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
            if ros is None:
                logger.info(f"📍 Odom subscription attempt {attempt}/{MAX_RETRIES} via {ROSBRIDGE}...")
                ros = await ensure_ros(ROSBRIDGE)
            logger.info(f"📍 ROSBridge connected (is_connected={ros.is_connected})")
            topic = roslibpy.Topic(ros, "/odom", "nav_msgs/Odometry")
            topic.subscribe(_odom_callback)
            _odom_subscribed = True
            logger.info("📍 Subscribed to /odom — robot pose tracking active")

            # Also subscribe to /amcl_pose for map-frame localized position
            amcl_topic = roslibpy.Topic(ros, "/amcl_pose", "geometry_msgs/PoseWithCovarianceStamped")
            amcl_topic.subscribe(_amcl_pose_callback)
            logger.info("📍 Subscribed to /amcl_pose — AMCL localization active")
            return
        except Exception as e:
            logger.warning(f"⚠️ Odom attempt {attempt}/{MAX_RETRIES} failed: {e}")
            ros = None  # clear so next attempt calls ensure_ros() fresh
            if attempt < MAX_RETRIES:
                await asyncio.sleep(10)

async def _push_pose_to_server():
    """Background task: push robot pose to Server every ~500ms."""
    push_url = f"{SERVER_BASE}/map/pose"
    await asyncio.sleep(5)  # Wait for odom to start
    push_count = 0
    fail_count = 0
    while True:
        try:
            pose_data = {**_robot_pose, "source": _odom_source}
            resp = await _http.post(push_url, json=pose_data, timeout=3.0)
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
    while True:
        try:
            # Build marker list from object memory
            markers = []
            for obj_name in object_memory.get_all_objects():
                loc = object_memory.recall(obj_name)
                if loc:
                    # NOTE: robot_x/robot_y kept for webapp compat — OBSERVER pose, not object pose
                    markers.append({
                        "name": loc.display_name or obj_name,
                        "location": loc.location,
                        "location_description": loc.location_description,
                        "confidence": loc.confidence,
                        "age_hours": round(loc.age_hours(), 1),
                        "robot_x": round(loc.observer_x, 3),   # DEPRECATED — use observer_x
                        "robot_y": round(loc.observer_y, 3),    # DEPRECATED — use observer_y
                        "observer_x": round(loc.observer_x, 3),
                        "observer_y": round(loc.observer_y, 3),
                        "observer_theta": round(loc.observer_theta, 3),
                        "section": loc.section,
                        "bearing_deg": loc.bearing_deg,
                        "localization_source": loc.localization_source,
                        "object_pose_estimated": None,
                        "pose_semantics": "observer_pose_not_object_pose",
                    })
            await _http.post(push_url, json=markers, timeout=3.0)
        except Exception:
            pass
        # Authoritative full-memory push (Server is source of truth for
        # planning). Keeping the marker push above for webapp UI only.
        try:
            full_payload = {
                name: [
                    {
                        "object_name": loc.object_name,
                        "display_name": loc.display_name,
                        "location": loc.location,
                        "location_description": loc.location_description,
                        "timestamp": loc.timestamp,
                        "confidence": loc.confidence,
                        "image_url": loc.image_url,
                        "observer_x": loc.observer_x,
                        "observer_y": loc.observer_y,
                        "observer_theta": loc.observer_theta,
                        "estimated_x": loc.estimated_x,
                        "estimated_y": loc.estimated_y,
                        "bearing_deg": loc.bearing_deg,
                        "section": loc.section,
                        "localization_source": loc.localization_source,
                        "last_verified_at": loc.last_verified_at,
                    }
                    for loc in object_memory.get_history(name, limit=10)
                ]
                for name in object_memory.get_all_objects()
            }
            await _http.post(
                f"{SERVER_BASE}/map/objects/full",
                json=full_payload, timeout=3.0,
            )
        except Exception:
            pass
        await asyncio.sleep(5.0)

async def _sync_semantic_map_from_server():
    """Background task: fetch semantic map from Server (source of truth) every ~30s.
    Gateway uses this data READ-ONLY for search planning.
    The Server owns all CRUD; Gateway just keeps a local cache."""
    fetch_url = f"{SERVER_BASE}/map/annotations"
    await asyncio.sleep(5)
    while True:
        try:
            resp = await _http.get(fetch_url, timeout=5.0)
            if resp.status_code == 200:
                data = resp.json()
                # Update local SemanticMap from server data
                new_zone_ids = set()
                for zd in data.get("zones", []):
                    z = Zone(
                        id=zd["id"],
                        label_th=zd.get("label_th", ""),
                        label_en=zd.get("label_en", ""),
                        center_x=float(zd.get("center_x", 0)),
                        center_y=float(zd.get("center_y", 0)),
                        radius=float(zd.get("radius", 1.0)),
                        expected_objects=zd.get("expected_objects", []),
                        notes=zd.get("notes", ""),
                        color=zd.get("color", "#3b82f6"),
                        source=zd.get("source", "seed"),
                    )
                    semantic_map._zones[z.id] = z
                    new_zone_ids.add(z.id)
                # Remove zones that no longer exist on Server
                for zid in list(semantic_map._zones.keys()):
                    if zid not in new_zone_ids:
                        del semantic_map._zones[zid]
                # Update landmarks
                new_lm_ids = set()
                for ld in data.get("landmarks", []):
                    lm = Landmark(
                        id=ld["id"],
                        label=ld.get("label", ""),
                        x=float(ld.get("x", 0)),
                        y=float(ld.get("y", 0)),
                        zone_id=ld.get("zone_id", ""),
                        category=ld.get("category", ""),
                        notes=ld.get("notes", ""),
                    )
                    semantic_map._landmarks[lm.id] = lm
                    new_lm_ids.add(lm.id)
                for lid in list(semantic_map._landmarks.keys()):
                    if lid not in new_lm_ids:
                        del semantic_map._landmarks[lid]
                logger.debug(f"🗺️ Synced semantic map from Server: {list(new_zone_ids)}")
        except Exception as e:
            logger.debug(f"🗺️ Semantic map sync failed: {e}")
        await asyncio.sleep(30.0)


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

    while True:
        try:
            cam = get_camera()
            frame = cam.get_frame()
            count = cam.frame_count

            if frame and count > last_count:
                resp = await _http.post(
                    push_url,
                    content=frame,
                    headers={"Content-Type": "image/jpeg"},
                    timeout=3.0,
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
    """Start Gateway → Server connection when app starts.

    Bug 1 fix: เชื่อม ROSBridge ครั้งเดียวตอน startup แล้วส่ง ros object ที่ connected
    ไปให้ทุก component (camera, odom, lidar) ใช้ร่วมกัน
    แทนที่แต่ละ component จะสร้าง roslibpy.Ros() ของตัวเอง → หลาย Twisted factory
    """
    asyncio.create_task(connect_to_server())
    logger.info("🚀 Gateway startup: Server connection task started")

    # Start pushing camera frames to Server
    asyncio.create_task(_push_frames_to_server())
    logger.info("📤 Camera frame push task started")

    # Start robot pose tracking + push
    motion.post_motion_hook = _update_dead_reckoning  # dead-reckoning fallback
    # Wire ObjectMemory → Server authoritative store. Must happen before
    # the first reload()/clear() so object_memory has a server URL.
    _set_object_memory_server_base(SERVER_BASE)
    asyncio.create_task(_push_pose_to_server())
    asyncio.create_task(_push_objects_to_server())
    asyncio.create_task(_sync_semantic_map_from_server())
    logger.info("📍 Robot pose + object memory push tasks started")
    logger.info("🗺️ Semantic map sync from Server task started (read-only)")

    if MOCK_ROBOT:
        logger.info("🧪 MOCK_ROBOT=1 — skipping ROSBridge connection")
        try:
            start_camera()
        except Exception as e:
            logger.warning(f"⚠️ Camera not started: {e}")
        return

    # ── Connect to ROSBridge ONCE, share across all ROS components ──────
    # เชื่อม ROSBridge แค่ครั้งเดียว แล้วส่งไปให้ camera, odom, lidar ใช้ร่วม
    # ป้องกัน race condition จากหลาย task เรียก ensure_ros() พร้อมกัน
    asyncio.create_task(_connect_ros_and_start_components())

async def _connect_ros_and_start_components():
    """เชื่อม ROSBridge ครั้งเดียว แล้วส่ง ros object ที่ connected ให้ทุก component.

    Bug 1 fix: แทนที่ _subscribe_odom, _start_lidar_with_retry, และ CameraStream
    จะต่างคนต่าง ensure_ros() พร้อมกัน (race → หลาย Twisted factory)
    ฟังก์ชันนี้รอ connection เดียว แล้วแจกให้ทุก component ใช้ร่วมกัน
    """
    MAX_ATTEMPTS = 10
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            logger.info(f"🔌 ROSBridge connection attempt {attempt}/{MAX_ATTEMPTS}: {ROSBRIDGE}")
            ros = await ensure_ros(ROSBRIDGE)
            logger.info(f"✅ ROSBridge connected — sharing with camera, odom, LiDAR")

            # Nav2: inject shared ros — prevents duplicate Twisted factory on search start
            # Wrapped in try/except: inject_ros() itself is safe now, but belt-and-suspenders
            # ensures any unexpected error here never cascades to break LiDAR setup.
            if _nav2_client is not None:
                try:
                    _nav2_client.inject_ros(ros)
                    logger.info("🧭 Nav2 client initialized with shared ros")
                except Exception as e:
                    logger.warning(f"⚠️ Nav2 inject failed (Nav2 not running yet): {e}")

            # Camera: reuse shared ros (no separate Twisted factory)
            try:
                start_camera(ros=ros)
                logger.info("📷 Camera stream started (shared ros)")
            except Exception as e:
                logger.warning(f"⚠️ Camera not started: {e}")

            # Odom + AMCL: subscribe on shared ros
            await _subscribe_odom(ros=ros)

            # LiDAR / obstacle avoidance
            await _obstacle_avoidance.start(ros)
            logger.info("✅ Obstacle avoidance active")
            return

        except Exception as e:
            logger.warning(f"⚠️ ROSBridge attempt {attempt}/{MAX_ATTEMPTS} failed: {e}")
            if attempt < MAX_ATTEMPTS:
                await asyncio.sleep(10)

    logger.error("❌ ROSBridge failed after all retries — camera/odom/LiDAR offline")
    # Fallback: let camera try its own connection
    try:
        start_camera()
    except Exception:
        pass


async def _start_lidar_with_retry():
    """Try to start LiDAR/obstacle avoidance, retry up to 5 times if ROSBridge isn't ready.
    NOTE: Only used directly in legacy paths. Normally called from _connect_ros_and_start_components."""
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
    """Bug 2 fix: /health ตอบทันทีโดยไม่ทำ outbound HTTP ไปหา Server.
    ก่อนหน้านี้ endpoint นี้ ping Server (Tailscale) ทุกครั้งที่ถูกเรียก
    → webapp วัด latency ได้ ~1048ms (= Gateway round-trip + Tailscale overhead)
    ทั้งที่ Gateway response ตัวเองเร็วมาก (<5ms)
    แก้: ใช้ cached server_ok จาก connect_to_server() background task แทน
    """
    return {
        "status": "ok",
        "server_base": SERVER_BASE,
        "server_ws": SERVER_WS,
        "server_connected": server_gateway_ws is not None,
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
        r = await _http.get(f"{SERVER_BASE}/health", timeout=10.0)
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
            "robot_x": round(h.observer_x, 3),   # DEPRECATED — use observer_x
            "robot_y": round(h.observer_y, 3),    # DEPRECATED — use observer_y
            "observer_x": round(h.observer_x, 3),
            "observer_y": round(h.observer_y, 3),
            "observer_theta": round(h.observer_theta, 3),
            "section": h.section,
            "bearing_deg": h.bearing_deg,
            "localization_source": h.localization_source,
            "object_pose_estimated": None,
            "pose_semantics": "observer_pose_not_object_pose",
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
            "robot_x": round(h.observer_x, 3),   # DEPRECATED — use observer_x
            "robot_y": round(h.observer_y, 3),    # DEPRECATED — use observer_y
            "observer_x": round(h.observer_x, 3),
            "observer_y": round(h.observer_y, 3),
            "observer_theta": round(h.observer_theta, 3),
            "section": h.section,
            "bearing_deg": h.bearing_deg,
            "localization_source": h.localization_source,
            "object_pose_estimated": None,
            "pose_semantics": "observer_pose_not_object_pose",
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
        await _http.post(f"{SERVER_BASE}/map/objects", json=[], timeout=3.0)
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

# ═══════════════════════════════════════════════════════════════════
#  Semantic Map — READ-ONLY (Server owns CRUD)
# ═══════════════════════════════════════════════════════════════════
# Gateway no longer owns semantic map CRUD. The Server at :8080 is
# the sole owner. Gateway syncs via _sync_semantic_map_from_server()
# and exposes read-only access for internal use (SearchPlanner, etc.)

@app.get("/annotations")
async def get_annotations():
    """Read-only: return Gateway's cached copy of semantic map."""
    return semantic_map.to_dict()


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
        # ยิงไปหา LLM Planner ที่ Server
        r = await _http.post(f"{SERVER_BASE}/plan/plan_from_text", json={"text": text, "lang": lang, "max_waypoints": 8}, timeout=60.0)
        
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
            _t0_cmd = time.monotonic()
            _stripped = text.strip()
            logger.info(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            logger.info(f"📝 RAW TRANSCRIPT [{len(_stripped)}ch]: '{_stripped}'")

            # --- Filler / noise filter ---
            # Reject transcripts too short to be real commands (STT artefacts, breathing)
            _FILLER_EXACT = {"อ่า", "อ้า", "เอ่อ", "เอ้อ", "อ", "ฮัลโหล", "hello", "hi",
                             "um", "uh", "ah", "er", "hmm", "mm", "น่า", "เนาะ", "อือ"}
            if len(_stripped) < 3 or _stripped.lower() in _FILLER_EXACT:
                logger.info(f"⏭️  Filler/noise — ignored (len={len(_stripped)})")
                return

            # ================================================================
            # PRIORITY 1: STOP / Emergency — bypass wakeword entirely (safety)
            # หยุด always works immediately regardless of whether VORA was said
            # ================================================================
            if re.search(r"(?:^|\s)(หยุด|พอ|สต็อป|stop)(?:\s|$)", _stripped, re.IGNORECASE):
                logger.info(f"🛑 STOP bypass (no wakeword needed): '{_stripped}'")
                _stop_cmd = {"type": "stop"}
                if _search_active:
                    await cancel_search()
                if not MOCK_ROBOT:
                    await motion.exec_motion(_stop_cmd, obstacle_checker=_lidar_obstacle_check)
                else:
                    logger.info(f"🤖 [MOCK] STOP executed")
                logger.info(f"✅ STOP done in {(time.monotonic()-_t0_cmd)*1000:.0f}ms")
                logger.info(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                return

            # ================================================================
            # PRIORITY 2: Wakeword check (standard path)
            # ================================================================
            cmd = extract_after_wake_word(text)

            if cmd is None:
                # ================================================================
                # PRIORITY 2b: Motion bypass — no wakeword, but short + unambiguous
                # Allows "เดินหน้า", "เลี้ยวซ้าย", "ถอยหลัง" etc. without saying VORA
                # Gate: transcript ≤ 40 chars, no question markers, parse_intent matches
                # General chat / find / LLM commands stay behind wakeword
                # ================================================================
                _no_question = not re.search(r"(ไหม|มั้ย|หรือเปล่า|รึเปล่า|จริงไหม|\?)", _stripped)
                if len(_stripped) <= 40 and _no_question:
                    _bypass_cmd = parse_intent(_stripped)
                    if _bypass_cmd:
                        logger.info(f"🎮 Motion bypass (no wakeword, len={len(_stripped)}): '{_stripped}' → {_bypass_cmd}")
                        if not MOCK_ROBOT:
                            if _bypass_cmd.get("linear_x", 0) > 0:
                                obs = await _obstacle_avoidance.check_and_avoid()
                                if obs and obs.get("obstacle_detected"):
                                    logger.warning(f"🚧 Obstacle detected (bypass path) — skipping")
                                    await manager.broadcast(json.dumps({"type": "obstacle", "distance": obs.get("distance"), "strategy": obs.get("strategy")}))
                                    return
                            await motion.exec_motion(_bypass_cmd, obstacle_checker=_lidar_obstacle_check)
                            logger.info(f"✅ Motion bypass done in {(time.monotonic()-_t0_cmd)*1000:.0f}ms")
                        else:
                            logger.info(f"🤖 [MOCK] Motion bypass: {_bypass_cmd}")
                        logger.info(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                        return

                logger.info(f"⏭️  No wake word — ignored (general chat stays behind wakeword)")
                logger.info(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                return

            # ตัดคำว่า VORA ออก เหลือแต่คำสั่งจริง เช่น "เดินไปหน้าห้อง"
            text = cmd
            logger.info(f"🎯 COMMAND EXTRACTED: '{text}' (wakeword in {(time.monotonic()-_t0_cmd)*1000:.0f}ms)")
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
                    logger.info(f"✅ Motion executed via /cmd_vel in {(time.monotonic()-_t0_cmd)*1000:.0f}ms")
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
                    if _current_search_target == target_with_desc:
                        logger.info(f"[DEDUP] same search '{target_with_desc}' already active — ignoring duplicate")
                        return
                    await cancel_search()
                    await asyncio.sleep(1)

                # Use full description in VLM query
                asyncio.create_task(visual_search(target_with_desc))
                return

            # Standard single object search
            find_target = parse_find_intent(text)
            if find_target:
                logger.info(f"🔍 Find Object Detected: '{find_target}'")

                # Dedup: if same target is already active, ignore the duplicate request
                if _search_active:
                    if _current_search_target == find_target:
                        logger.info(f"[DEDUP] same search '{find_target}' already active — ignoring duplicate")
                        return
                    await cancel_search()
                    await asyncio.sleep(1)  # Wait for previous search to clean up
                
                # Start visual search as background task immediately (don't block WebSocket)
                # LLM enrichment runs inside the task to avoid delaying the response
                _raw_after_wake = text  # text already has wake word removed
                _original_target = find_target  # Thai display name (before enrichment)

                async def _enrich_then_search_audio(_raw=_raw_after_wake, _base=find_target, _display=_original_target):
                    _query = _base
                    if len(_raw) > len(_base) + 8 and len(_raw) > 15:
                        logger.info(f"🧠 Rich query (raw={len(_raw)} vs target={len(_base)} chars) — enriching...")
                        enriched = await _llm_enrich_search_query(_raw)
                        if enriched != _raw:
                            _query = enriched
                    await visual_search(_query, display_name=_display)

                asyncio.create_task(_enrich_then_search_audio())
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
            if _current_search_target == find_target:
                logger.info(f"[DEDUP] same search '{find_target}' already active — ignoring duplicate")
                return {"ok": True, "mode": "visual_search_active", "target": find_target}
            await cancel_search()
            await asyncio.sleep(1)
        # Start search immediately — enrichment runs inside the background task
        # so the HTTP response returns at once (avoids "signal timed out" from webapp)
        _original_target = find_target  # Thai display name

        async def _enrich_then_search_text(_raw=text, _base=find_target, _display=_original_target):
            _query = _base
            if len(_raw) > len(_base) + 8 and len(_raw) > 15:
                logger.info(f"🧠 Rich query (raw={len(_raw)} vs target={len(_base)} chars) — enriching...")
                enriched = await _llm_enrich_search_query(_raw)
                if enriched != _raw:
                    _query = enriched
            await visual_search(_query, display_name=_display)

        asyncio.create_task(_enrich_then_search_text())
        return {"ok": True, "mode": "visual_search", "target": _original_target}
    
    # 2. ถ้า Regex ไม่จับ → ลอง LLM Planner
    if not MOCK_ROBOT:
        if await try_plan_and_execute(text, lang):
            return {"ok": True, "mode": "planner"}
    else:
        logger.info(f"🤖 [MOCK] Would call LLM Planner for: {text}")
    
    # 3. Fallback → LLM ตอบคำถาม (chitchat/question) ผ่าน Server
    try:
        r = await _http.post(
                f"{SERVER_BASE}/pipeline/command",
                json={"text": text, "session_id": "gateway", "lang": lang},
                timeout=30.0,
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