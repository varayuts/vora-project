import logging
import os
import asyncio
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response

# นำเข้า settings เดิมของคุณ
from .core.settings import settings

# นำเข้า Routers และ WebSocket helper
from .api.stt_ws import router as stt_router, register_ws
from .api.llm_router import router as llm_router
from .api.agent_router import router as agent_router
from .api.plan_router import router as plan_router
from .api.robot_planner import router as robot_planner_router
from .api.pipeline_router import router as pipeline_router
from .api.server_router import router as server_router  # Server APIs (TTS gTTS, Queue, State)   
from .api.vlm_router import router as vlm_router        # VLM Vision endpoints (Qwen3-VL)
from .api.camera_router import router as camera_router  # Camera proxy from Gateway

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
        "tts": getattr(settings, "TTS_BACKEND", "gtts"),
    }

@app.get("/config.js")
def config_js(request: Request):
    """
    Dynamic config injection - Frontend จะโหลด script นี้เพื่อรับ port/host
    ตัวอย่าง: http://localhost:8080/config.js
    """
    # อ่าน port จาก environment หรือ default
    api_port = int(os.getenv("VORA_API_PORT", "8080"))
    frontend_port = int(os.getenv("VORA_FRONTEND_PORT", "9000"))
    is_https = os.getenv("VORA_HTTPS", "false").lower() == "true"
    
    # สร้าง JavaScript config
    config_content = f"""
// VORA Runtime Configuration (Auto-generated)
window.VORA_CONFIG = {{
    API_PORT: {api_port},
    FRONTEND_PORT: {frontend_port},
    IS_HTTPS: {str(is_https).lower()},
    WS_ENDPOINT: '/ws/stt',
    GENERATED_AT: '{__import__("datetime").datetime.now().isoformat()}'
}};
console.log('✅ VORA Config loaded:', window.VORA_CONFIG);
"""
    
    return Response(
        content=config_content,
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"}
    )

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
app.include_router(plan_router,    prefix="",         tags=["Plan"])     # plan_router มี prefix="/plan" แล้ว
app.include_router(robot_planner_router, prefix="",   tags=["Robot"])   # robot_planner มี prefix="/robot" แล้ว
app.include_router(pipeline_router)  # /pipeline/* endpoints
app.include_router(server_router)    # /api/server/* endpoints (TTS gTTS, Queue, State)
app.include_router(vlm_router)       # /vlm/* endpoints (Qwen3-VL image understanding)
app.include_router(camera_router)    # /camera/* endpoints (Camera proxy from Gateway)

# ลงทะเบียน WebSocket STT หลัง HTTP routes
register_ws(app)

# ------------------------------------------------------------------------------------
# Static Files & Frontend Serving
# ------------------------------------------------------------------------------------

# Serve frontend static files (CSS, JS, images)
app_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
frontend_dir = os.path.join(app_root, "app", "frontend")

logger.info(f"📁 App root: {app_root}")
logger.info(f"📁 Frontend dir: {frontend_dir}")

if os.path.exists(frontend_dir):
    # Serve static assets
    app.mount("/static", StaticFiles(directory=frontend_dir), name="static")
    logger.info(f"✅ Serving static files from {frontend_dir}")
    
    @app.get("/app")
    @app.get("/app/")
    async def serve_app():
        """Serve the main web app (index.html) — no cache"""
        index_path = os.path.join(frontend_dir, "index.html")
        if os.path.exists(index_path):
            logger.info(f"📄 Serving index.html from {index_path}")
            return FileResponse(
                index_path,
                media_type="text/html",
                headers={
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "Expires": "0",
                }
            )
        else:
            logger.error(f"❌ index.html not found at {index_path}")
            return {"error": "Frontend index.html not found", "path": index_path}
    
    @app.get("/debug")
    async def serve_debug():
        """Debug page for testing connectivity"""
        debug_path = os.path.join(frontend_dir, "debug.html")
        if os.path.exists(debug_path):
            return FileResponse(debug_path)
        else:
            return {"error": "debug.html not found"}
else:
    logger.warning(f"⚠️ Frontend directory not found at {frontend_dir}")

# ------------------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    # รันเซิร์ฟเวอร์ที่ port ตรงกับ config
    api_port = int(os.getenv("VORA_API_PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=api_port)