# app/providers/search/base.py
from abc import ABC, abstractmethod
from typing import List, Optional
from ...schemas.search import SearchResult

class SearchProvider(ABC):
    @abstractmethod
    def search(self, query: str, *, engine: Optional[str] = None,
               language: Optional[str] = None, num: int = 5) -> List[SearchResult]:
        ...
