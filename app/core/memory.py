from __future__ import annotations
from dataclasses import dataclass, field
from collections import deque
from time import time
from typing import Deque, Dict, List, Literal, Optional
import logging
from .settings import settings

logger = logging.getLogger(__name__)
Role = Literal["user", "assistant", "system"]

@dataclass
class Turn:
    role: Role
    text: str
    ts: float = field(default_factory=time)

@dataclass
class SessionBuf:
    turns: Deque[Turn] = field(default_factory=lambda: deque(maxlen=settings.MEMORY_MAX_TURNS))
    touched: float = field(default_factory=time)

class MemoryStore:
    def __init__(self):
        self._buf: Dict[str, SessionBuf] = {}

    def _prune(self):
        ttl = settings.MEMORY_TTL_MIN * 60
        now = time()
        dead = [sid for sid, s in self._buf.items() if now - s.touched > ttl]
        for sid in dead:
            self._buf.pop(sid, None)
            logger.info(f"Memory session {sid} expired and pruned.")

    def add(self, sid: str, role: Role, text: str):
        if not sid or not text:
            return
        self._prune()
        buf = self._buf.setdefault(sid, SessionBuf())
        buf.turns.append(Turn(role=role, text=text))
        buf.touched = time()

    def clear(self, sid: str):
        self._buf.pop(sid, None)

    def get_turns(self, sid: str) -> List[Turn]:
        self._prune()
        buf = self._buf.get(sid)
        return list(buf.turns) if buf else []

    def compose_context(self, sid: str, last_n: Optional[int] = None) -> str:
        """
        รวมบทสนทนาเป็น String
        - last_n: จำนวน turn ล่าสุดที่ต้องการ (เช่น 4B อาจใช้แค่ 2-3 turns เพื่อความเร็ว)
        """
        turns = self.get_turns(sid)
        if not turns:
            return ""
        
        if last_n:
            turns = turns[-last_n:]

        lines: List[str] = []
        for t in turns:
            # ใช้ Label ที่ Gemma 3 ไทยเข้าใจง่าย
            prefix = "ผู้ใช้" if t.role == "user" else "VORA"
            lines.append(f"{prefix}: {t.text.strip()}")
            
        ctx = "\n".join(lines)
        if len(ctx) > settings.MEMORY_MAX_CHARS:
            ctx = "..." + ctx[-settings.MEMORY_MAX_CHARS:]
        return ctx

MEMORY = MemoryStore()

