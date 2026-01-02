# app/main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .core.settings import settings

# Routers
from .api.stt_ws import register_ws
from .api.stt_ws import router as stt_router
from .api.llm_router import router as llm_router
from .api.agent_router import router as agent_router
from .api.search_router import router as search_router
from .api.tts_router import router as tts_router
from .api.plan_router import router as plan_router
from .api.robot_planner import router as robot_planner_router   

# ------------------------------------------------------------------------------------

app = FastAPI(title="VORA – STT + Agent + Search + LLM + TTS + RobotPlanner")

# CORS (เปิดกว้างไว้ก่อน)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ------------------------------------------------------------------------------------

@app.get("/health")
def health():
    return {
        "status": "ok",
        "model": getattr(settings, "OLLAMA_MODEL", None),
        "searxng": getattr(settings, "SEARXNG_URL", None),
    }

# ------------------------------------------------------------------------------------
# รวม API Routers

app.include_router(llm_router,    prefix="",        tags=["LLM"])
app.include_router(agent_router,  prefix="/agent",  tags=["Agent"])
app.include_router(search_router, prefix="/search", tags=["Search"])
app.include_router(tts_router)
app.include_router(plan_router)                # rule-based /plan/plan_from_text
app.include_router(robot_planner_router)       # LLM-based /robot/plan
app.include_router(stt_router)                 # websocket REST wrapper

# ลงทะเบียน WebSocket STT
register_ws(app)

# ------------------------------------------------------------------------------------

@app.get("/")
def root():
    return {"ok": True, "app": "VORA STT server up"}
