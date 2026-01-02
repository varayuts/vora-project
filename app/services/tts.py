# app/services/tts.py
import io
import os
import wave
import json
import tempfile
from typing import List, Optional, Tuple

import requests

# ---------- small utils ----------
def _is_wav(b: bytes) -> bool:
    return isinstance(b, (bytes, bytearray)) and len(b) >= 12 and b[:4] == b"RIFF" and b[8:12] == b"WAVE"

def _ensure_valid_wav(wav_bytes: bytes) -> bytes:
    if not _is_wav(wav_bytes):
        raise RuntimeError("TTS produced non-WAV data (missing RIFF/WAVE header)")
    # sanity check
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        _ = (wf.getnchannels(), wf.getframerate(), wf.getsampwidth())
    return wav_bytes

# ---------- base ----------
class TTSBackend:
    name = "BASE"
    def synth(self, text: str, voice: Optional[str] = None, sample_rate_hz: int = 16000) -> bytes:
        raise NotImplementedError
    def list_voices(self) -> List[Tuple[str, str]]:
        return []

# ---------- TY2A: typhoon2-audio over HTTP ----------
class Typhoon2AudioBackend(TTSBackend):
    """
    Proxy ไปที่ typhoon2-audio ที่รันใน Docker
    ตั้งค่าโดย:
      export TY2A_BASE_URL="http://127.0.0.1:8100"   # หรือ URL/Port ที่คุณ map จาก container
      export TY2A_TTS_PATH="/api/tts"               # endpoint สังเคราะห์ (ถ้าแตกต่างแก้ได้)
      export TY2A_VOICES_PATH="/api/tts/voices"     # endpoint ดูรายชื่อเสียง (ถ้ามี)
      export TY2A_TIMEOUT_S="10"
    """
    name = "TY2A"

    def __init__(self):
        self.base = os.getenv("TY2A_BASE_URL", "http://127.0.0.1:8100").rstrip("/")
        self.path_tts = os.getenv("TY2A_TTS_PATH", "/api/tts")
        self.path_voices = os.getenv("TY2A_VOICES_PATH", "/api/tts/voices")
        self.timeout = float(os.getenv("TY2A_TIMEOUT_S", "10"))

    def synth(self, text: str, voice: Optional[str] = None, sample_rate_hz: int = 16000) -> bytes:
        url = f"{self.base}{self.path_tts}"
        payload = {
            "text": text,
            "voice": voice,
            "sample_rate_hz": sample_rate_hz,
            # ใส่ช่อง option อื่น ๆ ตาม API ของ typhoon2-audio ถ้ามี เช่น speed/pitch ฯลฯ
        }
        # คาดหวังให้ปลายทางตอบเป็น audio/wav (bytes)
        # ถ้า typhoon2-audio ตอบเป็น JSON + base64 ให้แปลงที่นี่
        r = requests.post(url, json=payload, timeout=self.timeout)
        if r.status_code != 200:
            # ถ้าเป็น JSON error
            try:
                detail = r.json()
            except Exception:
                detail = r.text[:200]
            raise RuntimeError(f"ty2a tts http {r.status_code}: {detail}")
        data = r.content
        return _ensure_valid_wav(data)

    def list_voices(self) -> List[Tuple[str, str]]:
        url = f"{self.base}{self.path_voices}"
        try:
            r = requests.get(url, timeout=self.timeout)
            if r.status_code != 200:
                return []
            j = r.json()
            # รองรับรูปแบบทั่วไป: {"voices":[{"name":"...", "lang":"th-TH"}, ...]}
            out = []
            if isinstance(j, dict) and "voices" in j:
                for v in j["voices"]:
                    name = v.get("name") or v.get("id") or "unknown"
                    lang = v.get("lang") or v.get("language") or "th-TH"
                    out.append((name, lang))
            return out
        except Exception:
            return []

# ---------- Dummy: beep สำหรับทดสอบพาธ ----------
class DummyTTS(TTSBackend):
    name = "DUMMY"
    def synth(self, text: str, voice: Optional[str] = None, sample_rate_hz: int = 16000) -> bytes:
        import math
        dur_s = 0.6; sr = sample_rate_hz; n = int(dur_s*sr); amp = 20000; freq = 880.0
        pcm = bytearray()
        for i in range(n):
            val = int(amp * math.sin(2*math.pi*freq*(i/sr)))
            pcm += int(val).to_bytes(2, "little", signed=True)
        bio = io.BytesIO()
        with wave.open(bio, "wb") as wf:
            wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(sr); wf.writeframes(bytes(pcm))
        return bio.getvalue()

# ---------- factory ----------
def get_backend() -> TTSBackend:
    choice = (os.getenv("TTS_BACKEND") or "TY2A").upper()
    if choice == "TY2A":
        return Typhoon2AudioBackend()
    if choice == "DUMMY":
        return DummyTTS()
    return Typhoon2AudioBackend()

# ---------- public API ----------
def synthesize(text: str, voice: Optional[str] = None, sample_rate_hz: int = 16000) -> bytes:
    text = (text or "").strip()
    if not text:
        raise ValueError("text is empty")
    backend = get_backend()
    return backend.synth(text=text, voice=voice, sample_rate_hz=sample_rate_hz)

def voices() -> List[Tuple[str, str]]:
    backend = get_backend()
    return backend.list_voices()
