from fastapi import APIRouter, HTTPException
from ..schemas.search import SearchQuery, SearchResponse
from ..providers.search.searxng import SearxNGProvider

router = APIRouter()
SEARCH = SearxNGProvider()

@router.get("/", response_model=SearchResponse)
def search(q: str, engine: str | None = None, language: str | None = "th-TH", num: int = 5):
    try:
        return SearchResponse(results=SEARCH.search(q, engine=engine, language=language, num=num))
    except Exception as e:
        raise HTTPException(500, f"Search error: {e}")

@router.post("/", response_model=SearchResponse)
def search_post(query: SearchQuery):
    try:
        return SearchResponse(results=SEARCH.search(query.q, engine=query.engine, language=query.language, num=query.num))
    except Exception as e:
        raise HTTPException(500, f"Search error: {e}")

# เพิ่ม endpoint debug ที่ passthrough ดึง JSON ดิบจาก SearxNG
@router.get("/raw")
def search_raw(q: str, engine: str | None = None, language: str | None = "th-TH", num: int = 5):
    try:
        # ใช้ client เดิม แต่คืน list ของ dict (ไม่ห่อด้วย schema) เพื่อดูง่าย
        items = [r.model_dump() for r in SEARCH.search(q, engine=engine, language=language, num=num)]
        return {"results": items}
    except Exception as e:
        raise HTTPException(500, f"Search error: {e}")
