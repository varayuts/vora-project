import logging
import os
import asyncio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# นำเข้า settings เดิมของคุณ
from .core.settings import settings

# นำเข้า Routers และ WebSocket helper
from .api.stt_ws import router as stt_router, register_ws
from .api.llm_router import router as llm_router
from .api.agent_router import router as agent_router
from .api.search_router import router as search_router
from .api.tts_router import router as tts_router
from .api.plan_router import router as plan_router
from .api.robot_planner import router as robot_planner_router
from .api.pipeline_router import router as pipeline_router
from .api.server_router import router as server_router  # NEW: Server APIs (TTS, Queue, State)   

# ตั้งค่า Logger เพื่อดูสถานะการโหลดโมเดลใน A6000
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("VORA")

app = FastAPI(title="VORA – AI Voice Assistant Engine (A6000 Optimized)")

# CORS Setup
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ------------------------------------------------------------------------------------
# Startup Validation
# ------------------------------------------------------------------------------------

import shutil
import httpx

@app.on_event("startup")
async def startup_checks():
    """Validate critical dependencies - non-blocking with timeout"""
    logger.info("🔍 Running startup checks...")
    
    errors = []
    
    # Check FFmpeg (quick)
    if not shutil.which("ffmpeg"):
        errors.append("❌ ffmpeg not found in PATH")
    else:
        logger.info("✅ ffmpeg found")
    
    # Check Whisper model (non-blocking - lazy load on first use)
    app_root = os.path.dirname(os.path.dirname(__file__))
    model_path = os.environ.get("WHISPER_MODEL", 
                               os.path.join(app_root, "models", "asr", "distill-whisper-th-large-v3-ct2"))
    if not os.path.exists(model_path):
        logger.warning(f"⚠️ Local model not found at {model_path}, will download 'base' model on first STT use")
    else:
        logger.info(f"✅ Whisper model exists (will load on first use)")
    
    # Check Ollama connectivity (with timeout to avoid blocking)
    ollama_host = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")
    try:
        async with httpx.AsyncClient(timeout=0.5) as client:
            resp = await client.get(f"{ollama_host}/api/tags")
            if resp.status_code == 200:
                logger.info(f"✅ Ollama reachable at {ollama_host}")
            else:
                logger.warning(f"⚠️ Ollama health check failed: {resp.status_code}")
    except asyncio.TimeoutError:
        logger.warning(f"⚠️ Ollama timeout (non-blocking)")
    except Exception as e:
        logger.warning(f"⚠️ Cannot reach Ollama at {ollama_host}: {e}")
    
    # Check SearXNG (optional)
    searxng_url = os.getenv("SEARXNG_URL", "http://127.0.0.1:8080")
    try:
        async with httpx.AsyncClient(timeout=0.5) as client:
            resp = await client.get(f"{searxng_url}/status")
            if resp.status_code == 200:
                logger.info(f"✅ SearXNG reachable")
    except asyncio.TimeoutError:
        logger.debug(f"⚠️ SearXNG timeout (non-blocking)")
    except Exception as e:
        logger.debug(f"⚠️ SearXNG not available (optional): {e}")
    
    # Report results
    if errors:
        logger.error("\n".join(errors))
        raise RuntimeError("Startup validation failed. See errors above.")
    else:
        logger.info("✅ All startup checks passed! VORA is ready.")

# ------------------------------------------------------------------------------------

@app.get("/health")
def health():
    return {
        "status": "ok",
        "gpu_enabled": True,
        "model": getattr(settings, "OLLAMA_MODEL", None),
        "searxng": getattr(settings, "SEARXNG_URL", None),
    }

@app.get("/")
def root():
    return {"ok": True, "app": "VORA Backend is running on NVIDIA A6000"}

@app.get("/status")
def status():
    """Alias for /health"""
    return {
        "status": "ok",
        "server": "running",
        "timestamp": str(__import__("datetime").datetime.now())
    }

# ------------------------------------------------------------------------------------
# รวม API Routers

app.include_router(llm_router,     prefix="",         tags=["LLM"])
app.include_router(agent_router,   prefix="/agent",   tags=["Agent"])
app.include_router(search_router,  prefix="/search",  tags=["Search"])
app.include_router(tts_router,     prefix="/tts",     tags=["TTS"])
app.include_router(plan_router,    prefix="/plan",    tags=["Plan"])
app.include_router(robot_planner_router, prefix="/robot", tags=["Robot"])
app.include_router(pipeline_router)  # /pipeline/* endpoints
app.include_router(server_router)    # /api/server/* endpoints (TTS Thai, Queue, State)

# ลงทะเบียน WebSocket STT หลัง HTTP routes
register_ws(app)

# ------------------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    # รันเซิร์ฟเวอร์ที่ port 8000
    uvicorn.run(app, host="0.0.0.0", port=8000)