# app/core/settings.py
import os
from dataclasses import dataclass

@dataclass
class Settings:
    OLLAMA_HOST: str = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")
    
    # ===== VORA Model Configuration =====
    # Main Reasoning LLM (complex tasks, long responses)
    OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "gemma3:27b-it-qat")
    
    # Text Cleaning/Filtering LLM (fast, short responses)
    OLLAMA_REFINE_MODEL: str = os.getenv("OLLAMA_REFINE_MODEL", "gemma3:12b-it-qat")
    
    # VLM for Vision tasks (navigation, object finding)
    # Qwen3-VL:32B — ~20GB, 256K context, 32 languages, spatial understanding
    # Upgraded from 8B → 32B for better scene description accuracy + less prompt echo
    OLLAMA_VLM_MODEL: str = os.getenv("OLLAMA_VLM_MODEL", "qwen3-vl:32b")
    
    # Search: DISABLED - จะใช้ RAG แทนในอนาคต

    # LLM performance
    OLLAMA_TIMEOUT: int = int(os.getenv("OLLAMA_TIMEOUT", "600"))
    OLLAMA_KEEP_ALIVE: str = os.getenv("OLLAMA_KEEP_ALIVE", "30m")
    OLLAMA_JSON_MAX_TOKENS: int = int(os.getenv("OLLAMA_JSON_MAX_TOKENS", "200"))

    # Chat memory (in-memory, per-process)
    MEMORY_TTL_MIN: int = int(os.getenv("MEMORY_TTL_MIN", "60"))           # หมดอายุหลังไม่ถูกแตะเป็น X นาที
    MEMORY_MAX_TURNS: int = int(os.getenv("MEMORY_MAX_TURNS", "12"))       # เก็บล่าสุดกี่ข้อความ (รวม user+assistant)
    MEMORY_MAX_CHARS: int = int(os.getenv("MEMORY_MAX_CHARS", "4000"))     # จำกัดความยาว context ที่ส่งเข้า LLM
    
    # TTS Configuration
    TTS_BACKEND: str = os.getenv("TTS_BACKEND", "gtts")  # gtts (default), piper, typhoon2 disabled
    
    # Gateway Configuration (Windows PC running Gateway)
    GATEWAY_URL: str = os.getenv("GATEWAY_URL", "http://192.168.0.60:9001")

settings = Settings()
