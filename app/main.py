from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .core.settings import settings
from .api.agent_router import router as agent_router
from .api.llm_router import router as llm_router
from .api.search_router import router as search_router
from .api.stt_ws import register_ws

app = FastAPI(title="VORA – STT + Agent + Search + LLM")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

@app.get("/health")
def health():
    return {"status": "ok", "model": settings.OLLAMA_MODEL, "searxng": settings.SEARXNG_URL}

app.include_router(llm_router, prefix="", tags=["LLM"])
app.include_router(agent_router, prefix="/agent", tags=["Agent"])
app.include_router(search_router, prefix="/search", tags=["Search"])  # << สำคัญ
register_ws(app)
