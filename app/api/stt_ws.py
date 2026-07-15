# app/api/stt_ws.py
import asyncio
import json
import os
import logging
import numpy as np
from typing import Optional
from fastapi import APIRouter, FastAPI, WebSocket, WebSocketDisconnect
from faster_whisper import WhisperModel

logger = logging.getLogger("vora.stt")
logger.setLevel(logging.DEBUG)  # Show all debug messages
router = APIRouter()

# -------------------- Configuration (Tunable) --------------------
TARGET_SR = 16000
SAMPLE_WIDTH = 2

# ===== TUNING PARAMETERS (OPTIMIZED FOR LOW LATENCY) =====
# จังหวะการประมวลผล
STEP_SEC = 0.5                 # ประมวลผลทุก 0.5 วินาที (เร็วขึ้น 2x)
MIN_PARTIAL_SEC = 1.0          # ลดจาก 2.5 → 1.0 (เริ่มแปลเร็วขึ้น)
EOU_SILENCE_MS = 1200          # ลดจาก 2500 → 1200ms (รู้ว่าจบเร็วขึ้น)
EOU_RMS_THRESH = 0.008         # เพิ่มจาก 0.005 → 0.008 (ตัดเงียบเร็วขึ้น)
DEBOUNCE_SEC = 0.5             # ลดจาก 3.0 → 0.5 วินาที (ส่ง final บ่อยขึ้น)

# Whisper Transcription Settings
WHISPER_BEAM_SIZE = 5          # beam_size 5 สำหรับความแม่นยำ
WHISPER_VAD_THRESHOLD = 0.5    # เพิ่มจาก 0.4 → 0.5 (ลดการ detect noise เป็น speech)
WHISPER_MIN_SILENCE_MS = 800   # เพิ่มจาก 500 → 800 (รอให้พูดจบประโยค)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
# Try local model first, fallback to "base"
MODEL_PATH = os.environ.get("WHISPER_MODEL", os.path.join(BASE_DIR, "models", "asr", "distill-whisper-th-large-v3-ct2"))

# Lazy load model (load on first use, not on startup)
model = None

def get_whisper_model():
    """Load Whisper model on first use"""
    global model
    if model is not None:
        return model
    
    logger.info(f"🚀 Loading Whisper model on first use...")
    logger.info(f"📂 Model path: {MODEL_PATH}")
    logger.info(f"   Exists: {os.path.exists(MODEL_PATH)}")
    
    try:
        if os.path.exists(MODEL_PATH):
            logger.info(f"✅ Loading local model (CUDA)...")
            try:
                model = WhisperModel(MODEL_PATH, device="cuda", compute_type="float16")
                logger.info(f"✅ Whisper model loaded on CUDA successfully")
            except Exception as cuda_err:
                logger.warning(f"⚠️ CUDA load failed, trying CPU: {cuda_err}")
                model = WhisperModel(MODEL_PATH, device="cpu", compute_type="float32")
                logger.info(f"✅ Whisper model loaded on CPU (slower)")
        else:
            logger.warning(f"⚠️ Local model not found at {MODEL_PATH}")
            logger.warning(f"⚠️ Downloading: tiny")
            model = WhisperModel("tiny", device="cpu", compute_type="float32")
            logger.warning(f"⚠️ Using tiny model on CPU (not optimized for Thai)")
    except Exception as e:
        logger.error(f"❌ Failed to load Whisper model: {e}")
        raise RuntimeError(f"Cannot load Whisper model: {e}")
    
    return model

# -------------------- Core Helpers --------------------

def bytes_s16_to_float32(pcm_s16: bytes) -> np.ndarray:
    if not pcm_s16: return np.zeros((0,), dtype=np.float32)
    return (np.frombuffer(pcm_s16, dtype=np.int16).astype(np.float32) / 32768.0)

class PCMBuffer:
    def __init__(self, sr: int):
        self.sr = sr
        self._buf = bytearray()
        self._since_final_bytes = 0

    def append(self, chunk: bytes):
        self._buf.extend(chunk)
        self._since_final_bytes += len(chunk)

    def seconds_since_final(self):
        return self._since_final_bytes / (self.sr * 2)

    def take_all(self):
        return bytes(self._buf)

    def mark_final_and_compact(self):
        """หลังแปล clear buffer ทั้งหมด - ไม่เก็บ cache"""
        self._since_final_bytes = 0
        # ลบเสียงเก่า - ไม่เก็บ overlap เพื่อหลีกเลี่ยง cache
        self._buf = bytearray()

async def transcribe_with_vad(pcm_bytes: bytes, lang: str) -> str:
    """
    Transcribe audio with VAD filter to prevent hallucination.
    - ลบ Hallucination: "ส่วน ส่วน ส่วน" หรือ "ขอบคุณ ขอบคุณ"
    - VAD ตัดคำที่ไม่เสียง
    - Temperature 0 = ตรง ไม่สร้างสมมติ
    """
    try:
        audio = bytes_s16_to_float32(pcm_bytes)
        
        # ต้องมีเสียงอย่างน้อย 0.5 วินาที
        min_samples = int(0.5 * TARGET_SR)
        if len(audio) < min_samples:
            return ""
        
        loop = asyncio.get_running_loop()
        def _run():
            whisper = get_whisper_model()
            segments, info = whisper.transcribe(
                audio=audio,
                language=lang,
                beam_size=WHISPER_BEAM_SIZE,  # ใช้ค่าที่ tune ได้
                vad_filter=True,
                vad_parameters=dict(
                    min_silence_duration_ms=WHISPER_MIN_SILENCE_MS,
                    threshold=WHISPER_VAD_THRESHOLD,
                    speech_pad_ms=100,  # padding รอบ speech segments
                ),
                without_timestamps=True,
                temperature=0,  # 0 = deterministic (ไม่สุ่มคำ)
                best_of=1,
                condition_on_previous_text=False,  # ไม่ใช้ context ก่อนหน้า (ลด hallucination)
            )
            
            # Log language detection confidence
            if info and hasattr(info, 'language_probability'):
                logger.debug(f"🌐 Language: {info.language} ({info.language_probability:.1%})")
            
            # รวมข้อความทั้งหมดและลบช่องว่าง
            text = "".join([s.text.strip() for s in segments if s.text.strip()])
            
            # ลบ Hallucination: คำซ้ำ >= 3 ครั้ง
            tokens = text.split()
            if tokens and len(tokens) < 100:  # ข้อความจริงไม่ยาวเกิน 100 คำ
                # ตรวจหากำลังพูดคำเดียวซ้ำ
                from collections import Counter
                counts = Counter(tokens)
                if counts.most_common(1)[0][1] >= 3:
                    logger.warning(f"Hallucination detected: {text}")
                    return ""
            
            return text
        
        return await loop.run_in_executor(None, _run)
    
    except Exception as e:
        logger.error(f"Transcription error: {e}")
        return ""

# -------------------- FFmpeg Handling --------------------

class FFmpegDecoder:
    def __init__(self):
        self.proc = None
        self.queue = asyncio.Queue()

    async def start(self, rate=16000):
        """Start FFmpeg decoder for audio resampling and format conversion"""
        # Ensure input is s16le at the input rate, output 16kHz s16le mono
        cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-fflags', 'nobuffer',
               '-f', 's16le', '-ar', str(rate), '-ac', '1', '-i', 'pipe:0',
               '-f', 's16le', '-ar', '16000', '-ac', '1', 'pipe:1']
        try:
            self.proc = await asyncio.create_subprocess_exec(
                *cmd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            asyncio.create_task(self._read_stdout())
            logger.info(f"FFmpeg decoder started for {rate}Hz input → 16000Hz output")
        except Exception as e:
            logger.error(f"FFmpeg start error: {e}")

    async def _read_stdout(self):
        """Read decoded audio from FFmpeg stdout"""
        if not self.proc:
            return
        try:
            while self.proc and self.proc.stdout:
                # ลดเหลือ 100ms สำหรับ real-time processing
                chunk = await self.proc.stdout.read(3200)  # 100ms at 16kHz (ลดจาก 200ms)
                if not chunk:
                    break
                await self.queue.put(chunk)
        except Exception as e:
            logger.error(f"Error reading FFmpeg output: {e}")

    async def write(self, data: bytes):
        """Write audio data to FFmpeg stdin"""
        try:
            if self.proc and self.proc.stdin:
                self.proc.stdin.write(data)
                await self.proc.stdin.drain()
        except Exception as e:
            logger.error(f"Error writing to FFmpeg: {e}")

    async def close(self):
        if self.proc:
            self.proc.kill()
            self.proc = None


class DirectPCMQueue:
    """
    Direct PCM passthrough — skips FFmpeg when input is already 16kHz.
    ลดความหน่วง ~200-500ms จาก subprocess pipe buffer ของ FFmpeg
    ใช้เมื่อ client (เช่น MyAGV) ส่ง 16kHz PCM16 mono มาตรง
    """
    def __init__(self):
        self.queue = asyncio.Queue()

    async def start(self, rate=16000):
        logger.info(f"⚡ Direct PCM mode (no FFmpeg): input {rate}Hz = target {TARGET_SR}Hz")

    async def write(self, data: bytes):
        if data:
            await self.queue.put(data)

    async def close(self):
        pass


# -------------------- Main Socket --------------------

@router.websocket("/ws/stt")
async def ws_stt(ws: WebSocket):
    await ws.accept()
    logger.info("🔌 New STT WebSocket connection")
    pcm_buf = PCMBuffer(TARGET_SR)
    decoder = None  # Created lazily — DirectPCMQueue for 16kHz, FFmpegDecoder otherwise
    last_emit_time = 0.0
    last_final_time = 0.0  # เวลาที่ส่ง final ล่าสุด (สำหรับ debounce)
    silence_counter = 0.0
    session_lang = "th"
    is_started = False
    audio_chunk_count = 0
    total_bytes_received = 0

    async def process_audio_queue():
        nonlocal last_emit_time, last_final_time, silence_counter, audio_chunk_count, total_bytes_received
        import time
        
        while True:
            try:
                chunk = await decoder.queue.get()
                if not chunk:
                    continue
                
                audio_chunk_count += 1
                total_bytes_received += len(chunk)
                pcm_buf.append(chunk)
                
                now = pcm_buf.seconds_since_final()
                current_time = time.time()
                
                # ตรวจความเงียบ (ปรับให้ตรงกับ chunk size ใหม่)
                chunk_audio = bytes_s16_to_float32(chunk[-320:] if len(chunk) >= 320 else chunk)
                if len(chunk_audio) > 0:
                    tail_rms = np.sqrt(np.mean(chunk_audio**2))
                    
                    # Adaptive silence tracking (works with any chunk size)
                    chunk_sec = len(chunk) / (TARGET_SR * SAMPLE_WIDTH)
                    if tail_rms < EOU_RMS_THRESH:
                        silence_counter += chunk_sec
                    else:
                        silence_counter = 0

                # เฉพาะเมื่อเงียบลง = end-of-utterance
                if silence_counter >= EOU_SILENCE_MS / 1000:  # Convert to seconds
                    # Debounce: ไม่ส่ง final ถี่กว่า DEBOUNCE_SEC
                    time_since_last_final = current_time - last_final_time
                    
                    if now > MIN_PARTIAL_SEC and time_since_last_final >= DEBOUNCE_SEC:
                        # ดึง audio ออกมาก่อน transcribe (เรียกแค่ครั้งเดียว)
                        audio_data = pcm_buf.take_all()
                        logger.info(f"📊 Audio buffer: {now:.2f}s, {len(audio_data)} bytes, {audio_chunk_count} chunks")
                        
                        # Measure transcription time
                        transcribe_start = time.time()
                        logger.info(f"🎙️ Starting Whisper transcription...")
                        text = await transcribe_with_vad(audio_data, session_lang)
                        transcribe_time = time.time() - transcribe_start
                        logger.info(f"⏱️ Whisper took {transcribe_time:.2f}s to transcribe {now:.2f}s audio")
                        if text:
                            logger.info(f"✅ FINAL: {text}")
                            # ส่งแค่ครั้งเดียว (frontend รองรับทั้ง final และ transcript)
                            await ws.send_json({"type": "final", "text": text})
                            last_final_time = current_time  # Update debounce timer
                        else:
                            logger.warning("⚠️ No text detected (might be hallucination or silence)")
                        
                        # Reset buffer หลัง transcribe สำเร็จ
                        pcm_buf.mark_final_and_compact()
                        silence_counter = 0
                        last_emit_time = 0
                        audio_chunk_count = 0
                        total_bytes_received = 0
                    elif now > MIN_PARTIAL_SEC and time_since_last_final < DEBOUNCE_SEC:
                        # Debounce active - รอก่อน แต่ยังสะสม buffer ไว้
                        logger.debug(f"⏳ Debounce ({time_since_last_final:.1f}s/{DEBOUNCE_SEC}s), keeping buffer...")
                    else:
                        # Audio สั้นเกินไป → reset แค่ silence_counter ให้สะสมต่อ
                        logger.debug(f"⏳ Audio short ({now:.2f}s), waiting for more...")
                        silence_counter = 0  # reset silence แต่ไม่ล้าง buffer
            
            except Exception as e:
                logger.error(f"❌ Audio queue processing error: {e}", exc_info=True)

    try:
        while True:
            msg = await ws.receive()
            if "text" in msg:
                try:
                    data = json.loads(msg["text"])
                    
                    # รองรับทั้ง 2 protocol:
                    # 1. Backend style: {"rate": 16000}
                    # 2. Frontend style: {"type": "start_session", "session_id": "...", "language": "th"}
                    rate = data.get("rate", data.get("sampleRate", 44100))  # Default to 44100Hz (AudioContext default)
                    session_lang = data.get("language", "th")
                    
                    if not is_started:
                        # ⚡ Skip FFmpeg when input is already 16kHz (MyAGV sends 16kHz PCM)
                        if rate == TARGET_SR:
                            decoder = DirectPCMQueue()
                            logger.info(f"⚡ STT session: {rate}Hz DIRECT PCM (no FFmpeg), lang={session_lang}")
                        else:
                            decoder = FFmpegDecoder()
                            logger.info(f"🎤 STT session: {rate}Hz → {TARGET_SR}Hz via FFmpeg, lang={session_lang}")
                        if data.get("type") == "start_session":
                            logger.info(f"📋 Session ID: {data.get('session_id', 'unknown')}")
                        await decoder.start(rate)
                        asyncio.create_task(process_audio_queue())
                        is_started = True
                        # ส่ง ack กลับ frontend
                        await ws.send_json({"type": "session_started", "rate": rate})
                except json.JSONDecodeError as e:
                    logger.error(f"❌ Invalid init JSON: {e}")
                    await ws.send_json({"type": "error", "text": "Invalid init message"})
                    
            elif "bytes" in msg:
                audio_bytes = msg["bytes"]
                if len(audio_bytes) > 0:
                    # Auto-start ถ้ายังไม่ได้ init (fallback สำหรับ frontend เก่า)
                    if not is_started:
                        logger.warning(f"⚠️ Audio received before init, auto-starting with 44100Hz")
                        decoder = FFmpegDecoder()
                        await decoder.start(44100)  # AudioContext default
                        asyncio.create_task(process_audio_queue())
                        is_started = True
                    
                    logger.debug(f"📨 Received {len(audio_bytes)} bytes")
                    try:
                        await decoder.write(audio_bytes)
                    except Exception as e:
                        logger.error(f"❌ Error writing audio: {e}")
    
    except WebSocketDisconnect:
        logger.info("🔌 STT WebSocket disconnected")
    except RuntimeError as e:
        if "disconnect" in str(e).lower():
            logger.info("🔌 STT WebSocket client disconnected")
        else:
            logger.error(f"❌ STT WebSocket runtime error: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"❌ STT WebSocket error: {e}", exc_info=True)
    finally:
        if decoder:
            await decoder.close()
        logger.info("🛑 STT session ended")

def register_ws(app: FastAPI):
    app.include_router(router)

