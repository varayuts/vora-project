# app/api/agent_router.py
from fastapi import APIRouter, HTTPException, Query
from ..schemas.agent import RefineRequest, RefineResult, AnswerRequest, AnswerResponse, MemoryClearRequest, MemoryState
from ..core import agent as agent_core
from ..core.memory import MEMORY

router = APIRouter()

@router.post("/refine", response_model=RefineResult)
def refine(req: RefineRequest):
    try:
        return agent_core.refine(req.text, req.lang_hint or "th")
    except Exception as e:
        raise HTTPException(500, f"Refine error: {e}")

@router.post("/answer", response_model=AnswerResponse)
def agent_answer(req: AnswerRequest):
    try:
        sid = req.session_id or ""

        # 1) refine ครั้งเดียว
        ref = agent_core.refine(req.text, req.lang_hint or "th")

        # 2) ใช้ clean_text เก็บลง memory แทนข้อความดิบ
        clean = getattr(ref, "clean_text", None) or req.text
        if sid:
            MEMORY.add(sid, "user", clean)

        # 3) ส่ง refine_res เข้า core.answer โดยตรง
        ans, sources = agent_core.answer(
            ref,
            session_id=sid,
        )
        return AnswerResponse(answer=ans, refine=ref, sources=sources)

    except Exception as e:
        raise HTTPException(500, f"Agent error: {e}")

# ----- memory ops -----
@router.post("/memory/clear")
def memory_clear(req: MemoryClearRequest):
    MEMORY.clear(req.session_id)
    return {"ok": True}

@router.get("/memory", response_model=MemoryState)
def memory_get(session_id: str = Query(..., alias="sid")):
    turns = [{"role": t.role, "text": t.text, "ts": t.ts} for t in MEMORY.get_turns(session_id)]
    return MemoryState(session_id=session_id, turns=turns)
