# app/schemas/agent.py
from pydantic import BaseModel, Field
from typing import List, Optional, Literal, Dict, Any

class Entity(BaseModel):
    type: str
    text: str

class RefineRequest(BaseModel):
    text: str
    lang_hint: Optional[str] = None

class RefineResult(BaseModel):
    language: str = "th"
    intent: Literal["ask","search","navigate","chitchat","other","control","find_object","info"] = "other"
    clean_text: str
    short_prompt: str
    search_query: str
    entities: List[Entity] = []
    needs_more_info: bool = False
    missing_info: List[str] = []
    must_search: bool = False
    final_prompt: str
    notes: str = ""

class AnswerRequest(BaseModel):
    text: str
    session_id: Optional[str] = None
    lang_hint: Optional[str] = None
    topk: int = 3
    search_when: str = Field(default="always", description="auto|always|never")

class AnswerResponse(BaseModel):
    answer: str
    refine: RefineResult
    sources: list = []

# memory endpoints
class MemoryClearRequest(BaseModel):
    session_id: str

class MemoryState(BaseModel):
    session_id: str
    turns: List[Dict[str, Any]]


# Robot Commands
class RobotCommand(BaseModel):
    cmd: str = Field(..., description="Command name")
    params: Dict[str, Any] = Field(default_factory=dict, description="Command parameters")
    priority: int = Field(2, description="Priority (0=emergency, 1=high, 2=normal, 3=low)")
