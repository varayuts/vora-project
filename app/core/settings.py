# app/core/settings.py
import os
from dataclasses import dataclass

@dataclass
class Settings:
    OLLAMA_HOST: str = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")
    
    # ===== VORA Model Configuration =====
    # Main Reasoning LLM (complex tasks, long responses)
    # gemma3:27b-it-qat — 18GB on disk, ~20GB VRAM loaded.
    # gemma4:26b was tested but caused VRAM thrashing with VLM (both ~49GB total).
    # gemma3:27b-it-qat + qwen3-vl:8b fit comfortably in 49GB A6000 together.
    OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "gemma3:27b-it-qat")

    # Text Cleaning/Filtering LLM — same model as main to avoid cold-start swap delay
    OLLAMA_REFINE_MODEL: str = os.getenv("OLLAMA_REFINE_MODEL", "gemma3:27b-it-qat")

    # VLM for Vision tasks (navigation, object finding)
    # qwen3-vl:8b — 6.1GB on disk, ~8GB VRAM. Fits alongside 27b LLM.
    OLLAMA_VLM_MODEL: str = os.getenv("OLLAMA_VLM_MODEL", "qwen3-vl:8b")
    
    # Search: DISABLED - จะใช้ RAG แทนในอนาคต

    # LLM performance
    # Default timeout reduced from 600s to 120s. The old 600s caused 10-minute
    # blocks when ollama fell back to CPU. With GPU, gemma4:26b responds in <15s.
    # Per-call timeouts (TIMEOUT_FAST=30s, TIMEOUT_NORMAL=90s) in OllamaProvider
    # override this for latency-sensitive operations.
    OLLAMA_TIMEOUT: int = int(os.getenv("OLLAMA_TIMEOUT", "120"))
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
