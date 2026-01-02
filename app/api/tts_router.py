import io
from typing import Optional
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel, Field

from ..services.tts_proxy import synthesize_via_ty2a, list_voices_via_ty2a, TTSProxyError

router = APIRouter(prefix="/api/tts", tags=["tts"])

class TTSIn(BaseModel):
    text: str = Field(..., description="ข้อความที่จะสังเคราะห์เสียง")
    sample_rate_hz: int = Field(22050, description="อัตรา sample rate ของเสียง (Hz)")
    voice: Optional[str] = Field(None, description="รหัสเสียง/ผู้พูด (ถ้ามี)")
    speed: Optional[float] = Field(None, description="ปรับความเร็ว (เช่น 1.0 เท่ากับปกติ)")
    pitch: Optional[float] = Field(None, description="ปรับโทนเสียง (เช่น 0 คือปกติ)")
    format: str = Field("wav", description="รูปแบบไฟล์เสียง: wav/mp3 (ขึ้นกับฝั่ง Typhoon2)")

@router.get("/voices")
async def tts_list_voices():
    data = await list_voices_via_ty2a()
    return JSONResponse(content=data)

@router.post("/speak", response_class=StreamingResponse)
async def tts_endpoint(body: TTSIn):
    """
    Proxy ไป Typhoon2-audio แล้ว stream กลับมาเป็น audio/wav
    """
    try:
        wav_bytes = await synthesize_via_ty2a(
            text=body.text,
            sample_rate_hz=body.sample_rate_hz,
            voice=body.voice,
            speed=body.speed,
            pitch=body.pitch,
            audio_format=body.format,
        )
    except TTSProxyError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"TTS proxy error: {e}")

    filename = "tts.wav" if body.format == "wav" else f"tts.{body.format}"
    headers = {
        "Content-Disposition": f'inline; filename="{filename}"'
    }
    # หมายเหตุ: ถ้าฝั่ง Typhoon2 ส่ง mp3 กลับมา แต่เรา set เป็น audio/wav
    # ให้ปรับ media_type ตาม body.format
    media_type = "audio/wav" if body.format == "wav" else "audio/mpeg"
    return StreamingResponse(io.BytesIO(wav_bytes), headers=headers, media_type=media_type)
