# app/services/thai_tts.py
"""
Thai TTS Service
================
Text-to-Speech ภาษาไทย - ใช้ Piper TTS หรือ Typhoon2

Piper TTS: https://github.com/rhasspy/piper
- รองรับภาษาไทย (th_TH)
- เร็ว, รันได้ local
- ใช้ ONNX model

Typhoon2: fallback สำหรับคุณภาพสูง
"""

import asyncio
import io
import logging
import os
import struct
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import Optional, List, Tuple
import httpx

logger = logging.getLogger("vora.tts")

# ============ Config ============

# Piper TTS paths
PIPER_EXECUTABLE = os.getenv("PIPER_PATH", "piper")
PIPER_MODEL_DIR = os.getenv("PIPER_MODEL_DIR", "/home/user/vora_project/VORA/VORA/models/tts")

# Thai voice model
PIPER_THAI_MODEL = os.getenv("PIPER_THAI_MODEL", "th_TH-thaivoice-medium")

# Typhoon2 fallback
TYPHOON2_URL = os.getenv("TY2A_BASE_URL", "http://127.0.0.1:8100")
TYPHOON2_TIMEOUT = float(os.getenv("TY2A_TIMEOUT_S", "30"))

# Default settings
DEFAULT_SAMPLE_RATE = 22050
DEFAULT_SPEED = 1.0


class ThaiTTSService:
    """
    Thai TTS Service
    ================
    Priority:
    1. Piper TTS (local, fast)
    2. Typhoon2 (API, high quality)
    3. gTTS fallback (online, Google)
    """
    
    def __init__(self):
        self._piper_available = False
        self._typhoon2_available = False
        self._gtts_available = False
        
        # Check available backends
        self._check_backends()
    
    def _check_backends(self):
        """Check which TTS backends are available"""
        # Check Piper + Thai model
        try:
            model_path = Path(PIPER_MODEL_DIR) / f"{PIPER_THAI_MODEL}.onnx"
            if model_path.exists():
                result = subprocess.run(
                    [PIPER_EXECUTABLE, "--help"],
                    capture_output=True,
                    timeout=5
                )
                self._piper_available = result.returncode == 0
                if self._piper_available:
                    logger.info("✅ Piper TTS: Available with Thai model")
            else:
                logger.warning(f"⚠️ Piper TTS: Thai model not found at {model_path}")
                self._piper_available = False
        except Exception as e:
            logger.warning(f"⚠️ Piper TTS: Not available ({e})")
        
        # Typhoon2 - check if actually running
        try:
            import httpx
            response = httpx.get(f"{TYPHOON2_URL}/health", timeout=2)
            self._typhoon2_available = response.status_code == 200
            if self._typhoon2_available:
                logger.info("✅ Typhoon2: Available")
            else:
                logger.warning("⚠️ Typhoon2: Not responding")
                self._typhoon2_available = False
        except Exception as e:
            logger.warning(f"⚠️ Typhoon2: Not running ({e})")
            self._typhoon2_available = False
        
        # gTTS always available if installed
        try:
            import gtts
            self._gtts_available = True
            logger.info("✅ gTTS: Available (Google TTS)")
        except ImportError:
            logger.warning("⚠️ gTTS: Not installed")
    
    async def synthesize(
        self,
        text: str,
        voice: str = "default",
        speed: float = DEFAULT_SPEED,
        sample_rate: int = DEFAULT_SAMPLE_RATE
    ) -> bytes:
        """
        สังเคราะห์เสียงภาษาไทย
        
        Returns: WAV bytes
        """
        if not text or not text.strip():
            return self._generate_silence(0.5, sample_rate)
        
        # Clean text
        text = self._clean_thai_text(text)
        
        # Try backends in order
        errors = []
        
        # 1. Try Piper
        if self._piper_available:
            try:
                return await self._synth_piper(text, voice, speed, sample_rate)
            except Exception as e:
                errors.append(f"Piper: {e}")
                logger.warning(f"Piper TTS failed: {e}")
        
        # 2. Try Typhoon2
        if self._typhoon2_available:
            try:
                return await self._synth_typhoon2(text, voice, speed, sample_rate)
            except Exception as e:
                errors.append(f"Typhoon2: {e}")
                logger.warning(f"Typhoon2 TTS failed: {e}")
                self._typhoon2_available = False  # Disable for future
        
        # 3. Try gTTS
        if self._gtts_available:
            try:
                return await self._synth_gtts(text, speed, sample_rate)
            except Exception as e:
                errors.append(f"gTTS: {e}")
                logger.warning(f"gTTS failed: {e}")
        
        # 4. Generate error beep
        logger.error(f"All TTS backends failed: {errors}")
        return self._generate_error_beep(sample_rate)
    
    def _clean_thai_text(self, text: str) -> str:
        """Clean and prepare Thai text for TTS"""
        # Remove excessive whitespace
        text = " ".join(text.split())
        
        # Add pauses for punctuation
        text = text.replace("...", " . . . ")
        text = text.replace("。", ".")
        
        # Remove unsupported characters
        # Keep Thai, English, numbers, basic punctuation
        allowed = set("กขฃคฅฆงจฉชซฌญฎฏฐฑฒณดตถทธนบปผฝพฟภมยรลวศษสหฬอฮ"
                     "ะาิีึืุูเแโใไๅๆ็่้๊๋์ํ๎"
                     "ฯๆ"
                     "0123456789๐๑๒๓๔๕๖๗๘๙"
                     "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
                     " .,!?-:;\"'()[]")
        text = "".join(c for c in text if c in allowed)
        
        return text.strip()
    
    async def _synth_piper(
        self,
        text: str,
        voice: str,
        speed: float,
        sample_rate: int
    ) -> bytes:
        """Synthesize with Piper TTS"""
        model_path = Path(PIPER_MODEL_DIR) / f"{PIPER_THAI_MODEL}.onnx"
        config_path = Path(PIPER_MODEL_DIR) / f"{PIPER_THAI_MODEL}.onnx.json"
        
        # Create temp file for output
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            output_path = f.name
        
        try:
            # Build command
            cmd = [
                PIPER_EXECUTABLE,
                "--model", str(model_path),
                "--config", str(config_path),
                "--output_file", output_path,
                "--length_scale", str(1.0 / speed),  # Inverse for speed
            ]
            
            # Run Piper
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, stderr = await asyncio.wait_for(
                process.communicate(input=text.encode("utf-8")),
                timeout=30
            )
            
            if process.returncode != 0:
                raise RuntimeError(f"Piper error: {stderr.decode()}")
            
            # Read output
            with open(output_path, "rb") as f:
                wav_bytes = f.read()
            
            # Resample if needed
            if sample_rate != 22050:
                wav_bytes = self._resample_wav(wav_bytes, sample_rate)
            
            return wav_bytes
            
        finally:
            # Cleanup
            if os.path.exists(output_path):
                os.remove(output_path)
    
    async def _synth_typhoon2(
        self,
        text: str,
        voice: str,
        speed: float,
        sample_rate: int
    ) -> bytes:
        """Synthesize with Typhoon2 API"""
        url = f"{TYPHOON2_URL}/api/tts"
        
        payload = {
            "text": text,
            "voice": voice if voice != "default" else None,
            "speed": speed,
            "sample_rate_hz": sample_rate,
        }
        
        async with httpx.AsyncClient(timeout=TYPHOON2_TIMEOUT) as client:
            response = await client.post(url, json=payload)
            
            if response.status_code != 200:
                raise RuntimeError(f"Typhoon2 HTTP {response.status_code}")
            
            return response.content
    
    async def _synth_gtts(
        self,
        text: str,
        speed: float,
        sample_rate: int
    ) -> bytes:
        """Synthesize with Google TTS (fallback)"""
        from gtts import gTTS
        
        # gTTS is synchronous, run in thread
        def _do_gtts():
            tts = gTTS(text=text, lang="th", slow=(speed < 0.8))
            
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                mp3_path = f.name
            
            tts.save(mp3_path)
            
            # Convert MP3 to WAV using ffmpeg
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                wav_path = f.name
            
            subprocess.run([
                "ffmpeg", "-y", "-i", mp3_path,
                "-ar", str(sample_rate),
                "-ac", "1",
                "-acodec", "pcm_s16le",
                wav_path
            ], capture_output=True)
            
            with open(wav_path, "rb") as f:
                wav_bytes = f.read()
            
            # Cleanup
            os.remove(mp3_path)
            os.remove(wav_path)
            
            return wav_bytes
        
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _do_gtts)
    
    def _resample_wav(self, wav_bytes: bytes, target_rate: int) -> bytes:
        """Resample WAV to target sample rate"""
        # Use ffmpeg for resampling
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f_in:
            f_in.write(wav_bytes)
            input_path = f_in.name
        
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f_out:
            output_path = f_out.name
        
        try:
            subprocess.run([
                "ffmpeg", "-y", "-i", input_path,
                "-ar", str(target_rate),
                "-ac", "1",
                output_path
            ], capture_output=True, check=True)
            
            with open(output_path, "rb") as f:
                return f.read()
        finally:
            os.remove(input_path)
            os.remove(output_path)
    
    def _generate_silence(self, duration_sec: float, sample_rate: int) -> bytes:
        """Generate silence WAV"""
        num_samples = int(duration_sec * sample_rate)
        
        bio = io.BytesIO()
        with wave.open(bio, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(b"\x00\x00" * num_samples)
        
        return bio.getvalue()
    
    def _generate_error_beep(self, sample_rate: int) -> bytes:
        """Generate error beep (two short beeps)"""
        import math
        
        freq = 440  # A4
        duration = 0.15
        gap = 0.1
        
        def generate_tone(freq, dur):
            samples = []
            for i in range(int(dur * sample_rate)):
                t = i / sample_rate
                val = int(10000 * math.sin(2 * math.pi * freq * t))
                samples.append(struct.pack("<h", val))
            return b"".join(samples)
        
        silence = b"\x00\x00" * int(gap * sample_rate)
        tone = generate_tone(freq, duration)
        
        audio = tone + silence + tone
        
        bio = io.BytesIO()
        with wave.open(bio, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(audio)
        
        return bio.getvalue()
    
    def get_status(self) -> dict:
        """Get TTS service status"""
        return {
            "backends": {
                "piper": self._piper_available,
                "typhoon2": self._typhoon2_available,
                "gtts": self._gtts_available
            },
            "primary": (
                "piper" if self._piper_available else
                "typhoon2" if self._typhoon2_available else
                "gtts" if self._gtts_available else
                "none"
            ),
            "language": "th-TH"
        }


# ============ Global Instance ============

thai_tts = ThaiTTSService()


# ============ Shortcut Functions ============

async def speak_thai(text: str, speed: float = 1.0) -> bytes:
    """Quick function to synthesize Thai speech"""
    return await thai_tts.synthesize(text, speed=speed)


async def speak_response(text: str) -> bytes:
    """Synthesize robot response"""
    # Add slight pause before speaking
    prefix = ""
    if not text.startswith(("รับ", "ครับ", "ค่ะ", "โอ", "ได้")):
        prefix = "ครับ "
    
    return await thai_tts.synthesize(prefix + text)
