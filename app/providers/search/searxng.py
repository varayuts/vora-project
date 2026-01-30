# app/providers/search/searxng.py
import json, urllib.parse, urllib.request
from typing import List, Optional
from ...core.settings import settings
from ...schemas.search import SearchResult
from .base import SearchProvider

class SearxNGProvider(SearchProvider):
    def __init__(self, base_url: Optional[str] = None):
        # SearXNG is DISABLED - use RAG instead
        # This provider is kept for backwards compatibility
        self.base = (base_url or "http://127.0.0.1:8080").rstrip("/")

    def search(self, query: str, *, engine: Optional[str] = None,
               language: Optional[str] = "th-TH", num: int = 5) -> List[SearchResult]:
        params = {
            "q": query,
            "format": "json",
            "language": language or "th-TH",
            "safesearch": 1,
            "categories": "general",
        }
        if engine:
            params["engines"] = engine

        url = f"{self.base}/search?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"Accept":"application/json"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        out: List[SearchResult] = []
        for item in (data.get("results") or [])[:num]:
            out.append(SearchResult(
                title=item.get("title") or "",
                url=item.get("url") or "",
                snippet=item.get("content") or item.get("snippet") or "",
                engine=item.get("engine") or "",
            ))
        return out
