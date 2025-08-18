# app/api/stt_ws.py
from fastapi import WebSocket, WebSocketDisconnect
import asyncio, contextlib, os, tempfile
import numpy as np, soundfile as sf
from faster_whisper import WhisperModel

# ใช้ล้าง session memory ตอน RESET
from ..core.memory import MEMORY

SAMPLE_RATE = 16000
WINDOW_SEC  = 12           # หน้าต่างเลื่อน (วินาที)
STEP_SEC    = 1.0          # ส่ง partial ทุก ๆ กี่วิ
READ_CHUNK  = 32000        # ~1 วินาทีของ PCM s16le 16k mono

# ===== โหลดโมเดลครั้งเดียว =====
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "large-v3")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cuda")
WHISPER_COMPUTE = os.getenv("WHISPER_COMPUTE", "float16")
_model = WhisperModel(WHISPER_MODEL, device=WHISPER_DEVICE, compute_type=WHISPER_COMPUTE)


def register_ws(app):
    @app.websocket("/ws/stt")
    async def ws_stt(ws: WebSocket):
        """รับ Opus (webm/ogg) ทาง WS -> แปลงเป็น PCM ด้วย ffmpeg -> ทำ STT แบบหน้าต่างเลื่อน
           คำสั่ง text: RESET / STOP / START|RESUME / CLOSE
           Query: lang=th, prompt=..., sid=<session id>
        """
        await ws.accept()
        q = dict(ws.query_params)
        lang = q.get("lang", "th")
        prompt = q.get("prompt")
        sid = q.get("sid")  # session id (optional)

        # ffmpeg: opus/webm -> PCM s16le 16k mono
        try:
            ff = await asyncio.create_subprocess_exec(
                "ffmpeg", "-nostdin", "-loglevel", "quiet",
                "-i", "pipe:0",
                "-f", "s16le", "-ac", "1", "-ar", str(SAMPLE_RATE),
                "pipe:1",
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE
            )
        except FileNotFoundError:
            await ws.send_json({"error": "ffmpeg not found in PATH"})
            await ws.close()
            return

        ring = bytearray()
        max_bytes = SAMPLE_RATE * 2 * WINDOW_SEC  # 2 bytes/int16 * window seconds
        reading_pcm, paused, last_text = True, False, ""

        async def pump_pcm():
            nonlocal ring, reading_pcm
            try:
                while True:
                    chunk = await ff.stdout.read(READ_CHUNK)
                    if not chunk:
                        reading_pcm = False
                        break
                    if paused:
                        ring.clear()
                        continue
                    ring.extend(chunk)
                    if len(ring) > max_bytes:
                        ring[:] = ring[-max_bytes:]
            except Exception:
                reading_pcm = False

        async def transcribe_loop():
            nonlocal last_text
            while reading_pcm:
                await asyncio.sleep(STEP_SEC)
                if paused or len(ring) < SAMPLE_RATE * 2 * 2:  # >= ~2s
                    continue

                # ring -> wav ชั่วคราว
                pcm16 = np.frombuffer(ring, dtype=np.int16).astype(np.float32) / 32768.0
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                    sf.write(tmp.name, pcm16, SAMPLE_RATE, subtype="PCM_16")
                    wav = tmp.name
                try:
                    segs, _info = _model.transcribe(
                        wav, language=lang or "th", vad_filter=True, beam_size=1,
                        initial_prompt=prompt
                    )
                    txt = "".join(s.text for s in segs).strip()
                    if txt:
                        last_text = txt
                        await ws.send_json({"type": "partial", "text": txt})
                finally:
                    with contextlib.suppress(Exception):
                        os.remove(wav)

        pcm_task = asyncio.create_task(pump_pcm())
        tr_task  = asyncio.create_task(transcribe_loop())

        try:
            while True:
                msg = await ws.receive()
                data = msg.get("bytes")
                if data:
                    ff.stdin.write(data)
                    await ff.stdin.drain()
                    continue

                cmd = (msg.get("text") or "").strip().upper()
                if cmd == "RESET":
                    if last_text:
                        await ws.send_json({"type": "final", "text": last_text})
                    ring.clear(); last_text = ""; paused = False
                    if sid:
                        with contextlib.suppress(Exception):
                            MEMORY.clear(sid)   # ล้าง memory ของ session นี้
                    await ws.send_json({"type": "reset_ok"})

                elif cmd == "STOP":
                    if last_text:
                        await ws.send_json({"type": "final", "text": last_text})
                    ring.clear(); last_text = ""; paused = True
                    await ws.send_json({"type": "stopped"})

                elif cmd in ("START", "RESUME"):
                    paused = False
                    await ws.send_json({"type": "started"})

                elif cmd == "CLOSE":
                    break
                # else: มองข้าม

        except WebSocketDisconnect:
            pass
        finally:
            with contextlib.suppress(Exception):
                ff.stdin.close()
            with contextlib.suppress(Exception):
                await ff.wait()
            for t in (pcm_task, tr_task):
                if not t.done():
                    t.cancel()
                    with contextlib.suppress(Exception):
                        await t
