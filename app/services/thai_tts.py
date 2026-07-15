# app/services/thai_tts.py
"""
Thai TTS Service (gTTS Only)
============================
Text-to-Speech ภาษาไทย - ใช้ Google TTS (gTTS)
"""

import asyncio
import concurrent.futures
import logging
import os
import struct
import subprocess
import tempfile

logger = logging.getLogger("vora.tts")

# Default settings
DEFAULT_SAMPLE_RATE = 22050
DEFAULT_SPEED = 1.0


class ThaiTTSService:
    """
    Thai TTS Service - gTTS Only
    ============================
    ใช้ Google TTS สำหรับสังเคราะห์เสียงภาษาไทย
    """
    
    def __init__(self):
        self._gtts_available = False
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)
        self._check_gtts()
    
    def _check_gtts(self):
        """Check if gTTS is available"""
        try:
            import gtts
            self._gtts_available = True
            logger.info("✅ gTTS: Available (Google TTS)")
        except ImportError:
            logger.error("❌ gTTS: Not installed! Run: pip install gtts")
            self._gtts_available = False
    
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
        
        if not self._gtts_available:
            logger.error("gTTS not available!")
            return self._generate_silence(0.5, sample_rate)
        
        try:
            return await self._synth_gtts(text, speed, sample_rate)
        except Exception as e:
            logger.error(f"gTTS failed: {e}")
            return self._generate_silence(0.5, sample_rate)
    
    def _clean_thai_text(self, text: str) -> str:
        """Clean and prepare Thai text for TTS"""
        # Remove excessive whitespace
        text = " ".join(text.split())
        
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
    
    async def _synth_gtts(
        self,
        text: str,
        speed: float,
        sample_rate: int
    ) -> bytes:
        """Synthesize with Google TTS"""
        from gtts import gTTS
        
        logger.info(f"🔊 gTTS: '{text[:30]}...'")
        
        def _blocking_gtts():
            mp3_path = None
            wav_path = None
            try:
                tts = gTTS(text=text, lang="th", slow=(speed < 0.8))
                
                mp3_path = tempfile.mktemp(suffix=".mp3")
                wav_path = tempfile.mktemp(suffix=".wav")
                
                tts.save(mp3_path)
                
                result = subprocess.run([
                    "ffmpeg", "-y", "-i", mp3_path,
                    "-ar", str(sample_rate),
                    "-ac", "1",
                    "-acodec", "pcm_s16le",
                    wav_path
                ], capture_output=True, timeout=30)
                
                if result.returncode != 0:
                    raise RuntimeError(f"ffmpeg error: {result.stderr.decode()}")
                
                with open(wav_path, "rb") as f:
                    wav_bytes = f.read()
                
                return wav_bytes
            finally:
                # Cleanup
                if mp3_path and os.path.exists(mp3_path):
                    os.remove(mp3_path)
                if wav_path and os.path.exists(wav_path):
                    os.remove(wav_path)
        
        # Use asyncio.to_thread (simpler than run_in_executor)
        wav = await asyncio.to_thread(_blocking_gtts)
        logger.info(f"✅ gTTS: {len(wav)} bytes")
        return wav
    
    def _generate_silence(self, duration: float, sample_rate: int) -> bytes:
        """Generate silent WAV"""
        num_samples = int(sample_rate * duration)
        
        # WAV header
        header = b'RIFF'
        header += struct.pack('<I', 36 + num_samples * 2)
        header += b'WAVEfmt '
        header += struct.pack('<I', 16)  # Subchunk1Size
        header += struct.pack('<H', 1)   # AudioFormat (PCM)
        header += struct.pack('<H', 1)   # NumChannels
        header += struct.pack('<I', sample_rate)
        header += struct.pack('<I', sample_rate * 2)  # ByteRate
        header += struct.pack('<H', 2)   # BlockAlign
        header += struct.pack('<H', 16)  # BitsPerSample
        header += b'data'
        header += struct.pack('<I', num_samples * 2)
        
        # Silent samples
        samples = b'\x00' * (num_samples * 2)
        
        return header + samples
    
    def get_status(self) -> dict:
        """Get TTS service status"""
        return {
            "backends": {
                "gtts": self._gtts_available
            },
            "primary": "gtts" if self._gtts_available else None,
            "language": "th-TH"
        }


# Singleton instance
thai_tts = ThaiTTSService()


# Convenience function
async def speak_thai(text: str, speed: float = 1.0) -> bytes:
    """Quick function to synthesize Thai speech"""
    return await thai_tts.synthesize(text, speed=speed)


