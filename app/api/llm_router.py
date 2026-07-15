# app/api/llm_router.py
from fastapi import APIRouter, HTTPException
from ..schemas.llm import GenerateRequest, GenerateResponse
from ..providers.llm.ollama import OllamaProvider

router = APIRouter()
LLM = OllamaProvider()

@router.post("/generate", response_model=GenerateResponse)
def generate(req: GenerateRequest):
    try:
        resp = LLM.generate(
            prompt=req.prompt,
            system=req.system,
            temperature=req.temperature,
            top_p=req.top_p,
            max_tokens=req.max_tokens
        )
        return GenerateResponse(response=resp)
    except Exception as e:
        raise HTTPException(500, f"LLM error: {e}")


