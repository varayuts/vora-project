# app/schemas/llm.py
from pydantic import BaseModel
from typing import Optional

class GenerateRequest(BaseModel):
    prompt: str
    system: Optional[str] = None
    temperature: float = 0.3
    top_p: float = 0.9
    max_tokens: Optional[int] = None

class GenerateResponse(BaseModel):
    response: str
