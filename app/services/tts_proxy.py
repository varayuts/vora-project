from typing import Optional, Dict, Any
import httpx
from ..core.config import settings

class TTSProxyError(Exception):
    pass

async def synthesize_via_ty2a(
    text: str,
    sample_rate_hz: int = 22050,
    voice: Optional[str] = None,
    speed: Optional[float] = None,
    pitch: Optional[float] = None,
    audio_format: str = "wav",
    extra_payload: Optional[Dict[str, Any]] = None,
) -> bytes:
    """
    ยิงคำขอไป Typhoon2-audio แล้วคืนไฟล์เสียง (bytes)
    หมายเหตุ: ถ้า serve_tts.py ของพี่ยังรองรับแค่ text + sample_rate_hz
    ฟิลด์อื่น ๆ จะถูกแนบไปแต่ฝั่งนั้นจะไม่อ่าน ก็ไม่เป็นไร
    """
    base = settings.TY2A_BASE_URL.rstrip("/")
    path = settings.TY2A_TTS_PATH
    url = f"{base}{path}"

    payload: Dict[str, Any] = {
        "text": text,
        "sample_rate_hz": sample_rate_hz,
        "format": audio_format,
    }
    if voice is not None:
        payload["voice"] = voice
    if speed is not None:
        payload["speed"] = speed
    if pitch is not None:
        payload["pitch"] = pitch
    if extra_payload:
        payload.update(extra_payload)

    timeout = httpx.Timeout(connect=30.0, read=300.0, write=60.0, pool=60.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        # ขอแบบ stream เผื่อไฟล์ยาว
        resp = await client.post(url, json=payload)
        if resp.status_code != 200:
            try:
                detail = resp.json()
            except Exception:
                detail = resp.text
            raise TTSProxyError(f"Upstream error {resp.status_code}: {detail}")

        # บางเวอร์ชั่นอาจตั้ง content-type เป็น audio/wav อยู่แล้ว
        # เราดึง bytes ออกมาให้ฝั่ง router stream คืนไปยัง client
        return resp.content

async def list_voices_via_ty2a() -> Any:
    """
    ดึงรายการเสียงจาก Typhoon2-audio (ถ้า serve_tts.py ยังไม่มี /voices
    จะ fallback เป็น list เริ่มต้น)
    """
    base = settings.TY2A_BASE_URL.rstrip("/")
    path = settings.TY2A_VOICES_PATH
    url = f"{base}{path}"

    timeout = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resp = await client.get(url)
            if resp.status_code == 200:
                return resp.json()
            # fallback
            return {"voices": ["default"]}
        except Exception:
            return {"voices": ["default"]}
