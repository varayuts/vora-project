# app/main.py
from fastapi import FastAPI, UploadFile, File, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import asyncio, contextlib, os, tempfile, json

import numpy as np
import soundfile as sf
import torch
from faster_whisper import WhisperModel

from .llm_ollama import generate as llm_generate
from .llm_ollama import refine_query  # ← ใช้โมเดลเดียวกันมาทำ Agent

app = FastAPI(title="VORA Realtime STT + Agent + LLM")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

MODEL_NAME = "large-v3"
model = WhisperModel(MODEL_NAME, device="cuda", compute_type="float16")

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/gpu")
def gpu_info():
    return {
        "cuda": torch.cuda.is_available(),
        "gpu_count": torch.cuda.device_count(),
        "names": [torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())]
        if torch.cuda.is_available() else []
    }

# ---------- Batch STT ----------
@app.post("/stt")
async def stt(file: UploadFile = File(...), lang: str | None = None,
              task: str = "transcribe", prompt: str | None = None):
    suffix = os.path.splitext(file.filename or "")[-1] or ".wav"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        path = tmp.name
    try:
        segs, info = model.transcribe(
            path, language=lang or "th", task=task, vad_filter=True, beam_size=1,
            initial_prompt=prompt
        )
        text = "".join(s.text for s in segs).strip()
        return {"language": info.language, "duration": info.duration, "task": task, "text": text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"STT Error: {e}") from e
    finally:
        if os.path.exists(path):
            os.remove(path)

# ---------- Agent (Refine) ----------
class RefineReq(BaseModel):
    text: str
    lang_hint: str | None = None

@app.post("/agent/refine")
def agent_refine(req: RefineReq):
    try:
        data = refine_query(raw_text=req.text, lang_hint=req.lang_hint)
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Refine Error: {e}") from e

# ---------- LLM ----------
class GenerateRequest(BaseModel):
    prompt: str
    system: str | None = None
    temperature: float | None = 0.3
    top_p: float | None = 0.9
    max_tokens: int | None = None

@app.post("/generate")
def generate_endpoint(req: GenerateRequest):
    try:
        resp = llm_generate(
            prompt=req.prompt,
            system=req.system,
            temperature=req.temperature or 0.3,
            top_p=req.top_p or 0.9,
            max_tokens=req.max_tokens
        )
        return {"response": resp}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LLM Error: {e}") from e

# ---------- Realtime STT (WebSocket) ----------
SAMPLE_RATE = 16000
WINDOW_SEC  = 12
STEP_SEC    = 1.0
READ_CHUNK  = 32000

@app.websocket("/ws/stt")
async def ws_stt(ws: WebSocket):
    await ws.accept()
    q = dict(ws.query_params)
    lang = q.get("lang", "th")
    prompt = q.get("prompt")

    ff = await asyncio.create_subprocess_exec(
        "ffmpeg", "-nostdin", "-loglevel", "quiet",
        "-i", "pipe:0",
        "-f", "s16le", "-ac", "1", "-ar", str(SAMPLE_RATE),
        "pipe:1",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
    )

    ring = bytearray()
    max_bytes = SAMPLE_RATE * 2 * WINDOW_SEC
    reading_pcm = True
    paused = False
    last_text = ""

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
            if paused: continue
            if len(ring) < SAMPLE_RATE * 2 * 2:  # >= ~2s
                continue
            pcm16 = np.frombuffer(ring, dtype=np.int16).astype(np.float32) / 32768.0
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                sf.write(tmp.name, pcm16, SAMPLE_RATE, subtype="PCM_16")
                wav = tmp.name
            try:
                segs, info = model.transcribe(
                    wav, language=lang or "th", vad_filter=True, beam_size=1,
                    initial_prompt=prompt
                )
                txt = "".join(s.text for s in segs).strip()
                last_text = txt
                await ws.send_json({"type": "partial", "text": txt})
            finally:
                os.remove(wav)

    pcm_task = asyncio.create_task(pump_pcm())
    tr_task  = asyncio.create_task(transcribe_loop())

    try:
        while True:
            msg = await ws.receive()
            if (data := msg.get("bytes")):
                ff.stdin.write(data); await ff.stdin.drain(); continue

            text = (msg.get("text") or "").strip()
            cmd = text
            try:
                obj = json.loads(text)
                if isinstance(obj, dict) and "type" in obj:
                    cmd = str(obj["type"])
            except Exception:
                pass

            cmd_up = cmd.upper()
            if cmd_up == "RESET":
                if last_text:
                    await ws.send_json({"type": "final", "text": last_text})
                ring.clear(); last_text = ""; paused = False
                await ws.send_json({"type": "reset_ok"})
            elif cmd_up == "STOP":
                if last_text:
                    await ws.send_json({"type": "final", "text": last_text})
                ring.clear(); last_text = ""; paused = True
                await ws.send_json({"type": "stopped"})
            elif cmd_up in ("START", "RESUME"):
                paused = False; await ws.send_json({"type": "started"})
            elif cmd_up == "CLOSE":
                break
    except WebSocketDisconnect:
        pass
    finally:
        with contextlib.suppress(Exception): ff.stdin.close()
        with contextlib.suppress(Exception): await ff.wait()
        for t in (pcm_task, tr_task):
            if not t.done():
                t.cancel()
                with contextlib.suppress(Exception): await t
