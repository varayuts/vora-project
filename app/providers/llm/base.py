# app/providers/llm/base.py
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any

class LLMProvider(ABC):
    @abstractmethod
    def generate(self, prompt: str, system: Optional[str] = None,
                 temperature: float = 0.3, top_p: float = 0.9,
                 max_tokens: Optional[int] = None) -> str:
        ...

    @abstractmethod
    def generate_json(self, system: str, prompt: str,
                      temperature: float = 0.1, top_p: float = 0.9,
                      max_tokens: Optional[int] = 512) -> Dict[str, Any]:
        ...


