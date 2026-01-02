# app/api/stt_ws.py
import asyncio
import json
import os
import subprocess
from collections import deque
from typing import Deque, Optional

import numpy as np
from fastapi import APIRouter, FastAPI, WebSocket, WebSocketDisconnect
from faster_whisper import WhisperModel
import hashlib

router = APIRouter()

# -------------------- Config --------------------
TARGET_SR = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2  # bytes/sample

# partial / window
WINDOW_SEC = 50.0              # หน้าต่างยาวขึ้น → ต่อประโยคให้ครบขึ้น
STEP_SEC = 3.0                 # partial ทุก ~1 วิ
MIN_PARTIAL_SEC = 3          # ต้องมีเสียงอย่างน้อย 1.2 วิ ก่อนเริ่มถอด

# language detect
MIN_LANG_DETECT_SEC = 1.5      # มีเสียงพอ ก่อน dual-pass

# End-of-utterance (EOU)
EOU_SILENCE_MS = 10000          # เงียบต่อเนื่อง ≥ 5s → final
EOU_TAIL_SEC = 2.0             # ใช้ท้าย 1 วิ วัดความเงียบ
EOU_RMS_THRESH = 0.02          # เกณฑ์ RMS ที่ถือว่าเงียบ

# นโยบายยิง LLM
LLM_TRIGGER_MODE = "final_only"  # หรือ "final_only" final_and_stable
STABLE_TRIGGER_MS = 1500               # partial ไม่เปลี่ยน ≥ 1.5s → พิจารณา stable
MIN_STABLE_CHARS = 24                  # ยาวพอ (ตัวอักษร)
MIN_STABLE_WORDS = 8                   # หรือ ≥ 8 คำ

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
DEFAULT_MODEL_DIR = os.path.join(BASE_DIR, "models", "asr", "distill-whisper-th-large-v3-ct2")

MODEL_NAME = os.environ.get("WHISPER_MODEL", DEFAULT_MODEL_DIR)
# -------------------- โหลดโมเดล --------------------
#MODEL_NAME ตอนนี้คือ path โฟลเดอร์ CT2 ไม่ใช่ชื่อ HF model แล้ว
# ถ้าภายหลังอยากสลับกลับไปใช้ large-v3 ก็แค่ตอนรันเซิร์ฟเวอร์ set env:
# WHISPER_MODEL=/path/to/whisper-large-v3-ct2

model = WhisperModel(
    MODEL_NAME,
    device="cuda",        
    compute_type="float16"
)
ALLOWED_LANGS = {"th", "en"}

# -------------------- โมเดล --------------------
model = WhisperModel(MODEL_NAME, device="auto", compute_type="auto")


def register_ws(app: FastAPI):
    """Backward-compat shim ให้ main.py เดิมเรียก register_ws(app) ได้"""
    app.include_router(router)

# -------------------- Helpers พื้นฐาน --------------------
def bytes_s16_to_float32(pcm_s16: bytes) -> np.ndarray:
    """แปลง s16le bytes -> float32 [-1,1]"""
    if not pcm_s16:
        return np.zeros((0,), dtype=np.float32)
    int16 = np.frombuffer(pcm_s16, dtype=np.int16)
    return (int16.astype(np.float32) / 32768.0)


def float32_rms(x: np.ndarray) -> float:
    if x.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(x), dtype=np.float32)))


class PCMBuffer:
    """
    เก็บ PCM s16le เป็นบล็อก ๆ + นับ sample + จุดอ้างอิง 'ตั้งแต่ final ล่าสุด'
    """
    def __init__(self, sr: int):
        self.sr = sr
        self._buf: Deque[bytes] = deque()
        self._samples = 0               # นับตั้งแต่เริ่ม WS
        self._since_final_samples = 0   # นับตั้งแต่ปล่อย final ล่าสุด

    def append(self, pcm_chunk: bytes):
        if not pcm_chunk:
            return
        self._buf.append(pcm_chunk)
        ns = len(pcm_chunk) // SAMPLE_WIDTH
        self._samples += ns
        self._since_final_samples += ns

    def seconds(self) -> float:
        return self._samples / float(self.sr)

    def seconds_since_final(self) -> float:
        return self._since_final_samples / float(self.sr)

    def _join_all(self) -> bytes:
        return b"".join(self._buf)

    def take_last_window(self, window_sec: float) -> bytes:
        """ดึง PCM s16le 'ช่วงท้าย' ยาว window_sec วินาที (จากทั้งหมด)"""
        need = int(window_sec * self.sr) * SAMPLE_WIDTH
        out: Deque[bytes] = deque()
        got = 0
        for blk in reversed(self._buf):
            out.appendleft(blk)
            got += len(blk)
            if got >= need:
                break
        if got <= need:
            return b"".join(out)
        extra = got - need
        first = out[0]
        out[0] = first[extra:]
        return b"".join(out)

    def take_since_last_final(self, limit_sec: float) -> bytes:
        """
        ดึง PCM ตั้งแต่ final ล่าสุด (ถ้ามีน้อยกว่าให้เท่าที่มี)
        และไม่เกิน limit_sec วินาที เพื่อลดภาระโมเดล
        """
        want = min(self._since_final_samples, int(limit_sec * self.sr))
        if want <= 0:
            return b""
        need_bytes = want * SAMPLE_WIDTH
        out: Deque[bytes] = deque()
        got = 0
        for blk in reversed(self._buf):
            out.appendleft(blk)
            got += len(blk)
            if got >= need_bytes:
                break
        if got < need_bytes:
            data = b"".join(out)
        else:
            extra = got - need_bytes
            first = out[0]
            out[0] = first[extra:]
            data = b"".join(out)
        return data

    def mark_final_and_compact(self, keep_tail_sec: float = 10.0):
        """
        เรียกเมื่อตัดประโยคเป็น final:
          - รีเซ็ตเคาน์เตอร์ since_final
          - compact ทิ้งข้อมูลเก่า เหลือท้ายสุด ~ keep_tail_sec
        """
        self._since_final_samples = 0
        keep_bytes = int(keep_tail_sec * self.sr) * SAMPLE_WIDTH
        all_bytes = self._join_all()
        if len(all_bytes) <= keep_bytes:
            return
        tail = all_bytes[-keep_bytes:]
        self._buf.clear()
        self._buf.append(tail)

# -------------------- Whisper helpers --------------------
async def transcribe_fixed_lang(pcm_s16: bytes, lang: str) -> str:
    """ถอดด้วยภาษา fix (th/en)"""
    audio = bytes_s16_to_float32(pcm_s16)
    if audio.size < int(MIN_PARTIAL_SEC * TARGET_SR):
        return ""
    segments, _ = model.transcribe(
        audio=audio,
        language=lang,
        vad_filter=False,
        beam_size=1,
        best_of=1,
        without_timestamps=True,
        condition_on_previous_text=False,
        word_timestamps=False,
        temperature=0.0,
    )
    texts = [seg.text for seg in segments] if segments else []
    return " ".join(t.strip() for t in texts).strip()


async def choose_lang_by_dual_pass(pcm_s16: bytes) -> str:
    """
    เลือกภาษาโดยถอดทั้ง th และ en บนคลิปเดียวกัน
    แล้วเทียบ 'จำนวนตัวอักษร non-space' ว่าใครมีเนื้อหามากกว่า
    """
    if not pcm_s16:
        return "en"
    audio = bytes_s16_to_float32(pcm_s16)
    if audio.size < int(MIN_LANG_DETECT_SEC * TARGET_SR):
        return "en"  # เดาค่าเริ่ม

    th_text, en_text = await asyncio.gather(
        transcribe_fixed_lang(pcm_s16, "th"),
        transcribe_fixed_lang(pcm_s16, "en"),
    )

    def score(t: str) -> int:
        return sum(1 for c in t if not c.isspace())

    return "th" if score(th_text) >= score(en_text) else "en"


def tail_is_silence(pcm_s16: bytes) -> bool:
    """ตรวจช่วงท้ายเงียบหรือไม่ด้วย RMS"""
    if not pcm_s16:
        return False
    audio = bytes_s16_to_float32(pcm_s16)
    tail_samples = int(EOU_TAIL_SEC * TARGET_SR)
    tail = audio[-tail_samples:] if audio.size >= tail_samples else audio
    return float32_rms(tail) < EOU_RMS_THRESH


def text_hash(s: str) -> str:
    return hashlib.sha1((s or "").encode("utf-8")).hexdigest()


def count_words(s: str) -> int:
    # ตัดซ้ำซ้อนช่องว่าง/บรรทัด แล้วนับคำแบบง่าย
    return len([w for w in (s or "").strip().split() if w])


async def send_event(ws: WebSocket, ev_type: str, text: str, lang: str, llm: bool):
    await ws.send_text(json.dumps({
        "type": ev_type,     # "partial" | "stable" | "final" | "info" | "error"
        "lang": lang,        # "th" | "en" | "auto"
        "text": text,
        "llm": llm           # client ควรยิง LLM เมื่อ llm == True เท่านั้น
    }))

# -------------------- FFmpeg streaming decoder --------------------
class FFmpegStreamDecoder:
    """
    ffmpeg หนึ่งตัวต่อหนึ่ง WS:
      stdin  <- เขียนบายนารี webm/ogg/opus ต่อเนื่อง
      stdout -> PCM s16le 16k mono ต่อเนื่อง
    """
    def __init__(self, target_sr: int = TARGET_SR, channels: int = CHANNELS):
        self.target_sr = target_sr
        self.channels = channels
        self.proc: Optional[asyncio.subprocess.Process] = None
        self.reader_task: Optional[asyncio.Task] = None
        self.pcm_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._closed = False

    async def start(self):
        cmd = [
            "ffmpeg",
            "-hide_banner", "-loglevel", "error",
            "-fflags", "nobuffer",
            "-i", "pipe:0",
            "-ac", str(self.channels),
            "-ar", str(self.target_sr),
            "-f", "s16le",
            "pipe:1",
        ]
        self.proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self.reader_task = asyncio.create_task(self._reader_loop())

    async def _reader_loop(self):
        assert self.proc and self.proc.stdout
        try:
            while True:
                chunk = await self.proc.stdout.read(4096)
                if chunk:
                    await self.pcm_queue.put(chunk)
                else:
                    await asyncio.sleep(0.005)
                if self._closed:
                    break
        except Exception:
            pass

    async def write(self, data: bytes):
        if not data or not self.proc or not self.proc.stdin:
            return
        try:
            self.proc.stdin.write(data)
            await self.proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass

    async def read_pcm_nonblock(self) -> bytes:
        chunks = []
        while not self.pcm_queue.empty():
            try:
                chunks.append(self.pcm_queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return b"".join(chunks)

    async def close(self):
        if self._closed:
            return
        self._closed = True
        if self.proc:
            try:
                if self.proc.stdin:
                    self.proc.stdin.close()
                try:
                    await asyncio.wait_for(self.proc.wait(), timeout=0.3)
                except asyncio.TimeoutError:
                    self.proc.kill()
            except Exception:
                pass
        if self.reader_task:
            try:
                await asyncio.wait_for(self.reader_task, timeout=0.2)
            except Exception:
                pass

# -------------------- WS Endpoint --------------------
@router.websocket("/ws/stt")
async def ws_stt(ws: WebSocket):
    await ws.accept()

    # อ่าน lang จาก query (?lang=th|en|auto)
    q_lang = (ws.query_params.get("lang") or "auto").strip().lower()
    forced_lang: Optional[str] = q_lang if q_lang in ALLOWED_LANGS else None

    pcm_buf = PCMBuffer(TARGET_SR)
    last_emit = 0.0
    session_lang: Optional[str] = forced_lang
    last_lang_detect_at: float = 0.0
    silence_hold_ms = 0.0

    # stable detection state
    loop = asyncio.get_event_loop()
    last_partial_text = ""
    last_partial_hash = ""
    last_change_ts = loop.time()
    last_stable_sent_ts = 0.0

    decoder = FFmpegStreamDecoder(TARGET_SR, CHANNELS)
    await decoder.start()

    async def pump_decoded_pcm():
        while True:
            if decoder._closed:
                break
            pcm_chunk = await decoder.read_pcm_nonblock()
            if pcm_chunk:
                pcm_buf.append(pcm_chunk)
            await asyncio.sleep(0.01)

    pump_task = asyncio.create_task(pump_decoded_pcm())

    try:
        while True:
            msg = await ws.receive()

            # ---------- TEXT frames ----------
            if "text" in msg:
                try:
                    data = json.loads(msg["text"])
                except Exception:
                    data = {}
                if isinstance(data, dict):
                    # เปลี่ยนภาษา (th/en/auto)
                    if "lang" in data:
                        val = str(data["lang"]).strip().lower()
                        if val in ALLOWED_LANGS:
                            forced_lang = val
                            session_lang = val
                            await send_event(ws, "info", f"force_lang={val}", val, False)
                        else:
                            forced_lang = None
                            session_lang = None
                            await send_event(ws, "info", "force_lang=auto", "en", False)

                    # บังคับ EOU (flush = final ทันที)
                    if data.get("flush") == 1:
                        pcm_window = pcm_buf.take_since_last_final(WINDOW_SEC)
                        if pcm_window:
                            lang_for_use = session_lang
                            if not lang_for_use:
                                lang_for_use = await choose_lang_by_dual_pass(pcm_window)
                                session_lang = lang_for_use
                            text = await transcribe_fixed_lang(pcm_window, lang_for_use)
                            await send_event(ws, "final", text, lang_for_use, True)
                        else:
                            await send_event(ws, "final", "", (session_lang or "en"), True)

                        pcm_buf.mark_final_and_compact()
                        silence_hold_ms = 0.0
                        last_emit = pcm_buf.seconds()

                        # reset stable state
                        last_partial_text = ""
                        last_partial_hash = ""
                        last_change_ts = loop.time()
                        last_stable_sent_ts = 0.0
                continue

            # ---------- BINARY frames ----------
            if "bytes" in msg:
                bin_data: bytes = msg["bytes"]
                if not bin_data:
                    continue

                # ป้อน chunk ให้ ffmpeg ถอดเป็น PCM ต่อเนื่อง
                try:
                    await decoder.write(bin_data)
                except Exception as e:
                    await send_event(ws, "error", f"decoder_write_failed: {str(e)}", (session_lang or "en"), False)
                    continue

                now_sec = pcm_buf.seconds()

                # partial: ทุก STEP_SEC และต้องมีเสียงสะสมอย่างน้อย MIN_PARTIAL_SEC
                if (pcm_buf.seconds_since_final() >= MIN_PARTIAL_SEC) and (now_sec - last_emit >= STEP_SEC):
                    pcm_window = pcm_buf.take_since_last_final(WINDOW_SEC)

                    # เลือกภาษา: forced > dual-pass > default en
                    lang_for_use = session_lang
                    if not lang_for_use:
                        if (pcm_buf.seconds_since_final() >= MIN_LANG_DETECT_SEC) and (now_sec - last_lang_detect_at >= 2.5):
                            lang_for_use = await choose_lang_by_dual_pass(pcm_window)
                            session_lang = lang_for_use
                            last_lang_detect_at = now_sec
                        else:
                            lang_for_use = "en"

                    text = await transcribe_fixed_lang(pcm_window, lang_for_use)

                    # ส่ง partial เฉพาะเมื่อข้อความเปลี่ยนจริง
                    cur_hash = text_hash(text)
                    if cur_hash != last_partial_hash:
                        await send_event(ws, "partial", text, lang_for_use, False)
                        # อัปเดตสถานะ "ข้อความล่าสุด"
                        last_partial_text = text
                        last_partial_hash = cur_hash
                        last_change_ts = loop.time()
                    else:
                        # ข้อความไม่เปลี่ยนเลย → พิจารณา stable
                        now_ts = loop.time()
                        ms_since_change = (now_ts - last_change_ts) * 1000.0
                        ms_since_last_stable = (now_ts - last_stable_sent_ts) * 1000.0
                        enough_chars = len(text.strip()) >= MIN_STABLE_CHARS
                        enough_words = count_words(text) >= MIN_STABLE_WORDS

                        if (LLM_TRIGGER_MODE == "final_and_stable"
                            and (enough_chars or enough_words)
                            and ms_since_change >= STABLE_TRIGGER_MS
                            and ms_since_last_stable >= STABLE_TRIGGER_MS):
                            await send_event(ws, "stable", text, lang_for_use, True)
                            last_stable_sent_ts = now_ts

                    last_emit = now_sec

                # ตรวจ EOU (เงียบต่อเนื่อง) เพื่อส่ง final อัตโนมัติ
                if pcm_buf.seconds_since_final() >= MIN_PARTIAL_SEC:
                    tail = pcm_buf.take_since_last_final(
                        min(EOU_TAIL_SEC, pcm_buf.seconds_since_final())
                    )
                    if tail_is_silence(tail):
                        silence_hold_ms += STEP_SEC * 1000.0
                    else:
                        silence_hold_ms = 0.0

                    if silence_hold_ms >= EOU_SILENCE_MS:
                        # ส่ง final
                        pcm_window = pcm_buf.take_since_last_final(WINDOW_SEC)
                        if pcm_window:
                            lang_for_use = session_lang or "en"
                            text = await transcribe_fixed_lang(pcm_window, lang_for_use)
                            await send_event(ws, "final", text, lang_for_use, True)
                        pcm_buf.mark_final_and_compact()
                        silence_hold_ms = 0.0
                        last_emit = pcm_buf.seconds()

                        # รีเซ็ต stable state
                        last_partial_text = ""
                        last_partial_hash = ""
                        last_change_ts = loop.time()
                        last_stable_sent_ts = 0.0

                continue

            # ---------- อื่น ๆ ----------
            await send_event(ws, "error", "unsupported frame", (session_lang or "en"), False)

    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await send_event(ws, "error", f"server_exception: {str(e)}", (session_lang or "en"), False)
        finally:
            pass
    finally:
        try:
            pump_task.cancel()
        except Exception:
            pass
        await decoder.close()
        try:
            await ws.close()
        except Exception:
            pass
