# app/providers/tts/typhoon2.py
from __future__ import annotations
import os, httpx
from typing import Optional

class Typhoon2Client:
    def __init__(self,
                 base_url: Optional[str] = None,
                 provider_mode: str = "standard"):
        # ตัวอย่าง: http://localhost:8001
        self.base = (base_url or os.getenv("TTS_BASE_URL", "http://localhost:8000")).rstrip("/")
        # "standard" = REST ปกติ | "gradio" = เซิร์ฟเวอร์แบบ Gradio
        self.mode = (provider_mode or os.getenv("TTS_PROVIDER_MODE", "standard")).lower()

    async def synth(self, text: str, speaker: Optional[str] = None,
                    speed: float = 1.0, audio_format: str = "wav") -> bytes:
        if self.mode == "gradio":
            return await self._synth_gradio(text, speaker, speed, audio_format)
        return await self._synth_standard(text, speaker, speed, audio_format)

    async def _synth_standard(self, text: str, speaker: Optional[str],
                              speed: float, audio_format: str) -> bytes:
        """
        สมมุติ API มาตรฐานแบบ:
        POST {base}/api/tts  JSON: {text, speaker, speed, format}  -> audio bytes
        """
        payload = {
            "text": text,
            "speaker": speaker or os.getenv("TTS_DEFAULT_SPEAKER", "th_female_01"),
            "speed": float(speed),
            "format": audio_format,
        }
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(f"{self.base}/api/tts", json=payload)
            r.raise_for_status()
            return r.content

    async def _synth_gradio(self, text: str, speaker: Optional[str],
                            speed: float, audio_format: str) -> bytes:
        """
        สำหรับเคสที่ Typhoon2 เปิดแบบ Gradio (เช่น http://localhost:7860)
        ส่วนใหญ่มักเป็น /run/predict หรือ /api/predict
        โค้ดนี้เป็น template—ถ้า endpoint จริงต่างไปนิด ให้แก้ path/keys ตามเซิร์ฟเวอร์คุณ
        """
        payload = {
            "data": [
                text,
                speaker or os.getenv("TTS_DEFAULT_SPEAKER", "th_female_01"),
                float(speed),
                audio_format
            ]
        }
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(f"{self.base}/run/predict", json=payload)
            r.raise_for_status()
            # สมมุติได้ base64 หรือ bytes กลับมา—ที่นี่สมมุติส่ง bytes ตรง ๆ
            return r.content
