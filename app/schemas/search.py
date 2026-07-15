# app/schemas/search.py
from pydantic import BaseModel
from typing import List, Optional

class SearchQuery(BaseModel):
    q: str
    engine: Optional[str] = None
    language: Optional[str] = "th-TH"
    num: int = 5

class SearchResult(BaseModel):
    title: str
    url: str
    snippet: str
    engine: Optional[str] = None

class SearchResponse(BaseModel):
    results: List[SearchResult]


