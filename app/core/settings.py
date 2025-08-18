# app/core/settings.py
import os
from dataclasses import dataclass

@dataclass
class Settings:
    OLLAMA_HOST: str = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")
    OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "gemma3:12b-it-qat")
    SEARXNG_URL: str = os.getenv("SEARXNG_URL", "http://127.0.0.1:8080")

    # LLM performance
    OLLAMA_TIMEOUT: int = int(os.getenv("OLLAMA_TIMEOUT", "600"))
    OLLAMA_KEEP_ALIVE: str = os.getenv("OLLAMA_KEEP_ALIVE", "30m")
    OLLAMA_JSON_MAX_TOKENS: int = int(os.getenv("OLLAMA_JSON_MAX_TOKENS", "200"))
    OLLAMA_REFINE_MODEL: str | None = os.getenv("OLLAMA_REFINE_MODEL") or None

    # Chat memory (in-memory, per-process)
    MEMORY_TTL_MIN: int = int(os.getenv("MEMORY_TTL_MIN", "60"))           # หมดอายุหลังไม่ถูกแตะเป็น X นาที
    MEMORY_MAX_TURNS: int = int(os.getenv("MEMORY_MAX_TURNS", "12"))       # เก็บล่าสุดกี่ข้อความ (รวม user+assistant)
    MEMORY_MAX_CHARS: int = int(os.getenv("MEMORY_MAX_CHARS", "4000"))     # จำกัดความยาว context ที่ส่งเข้า LLM

settings = Settings()
