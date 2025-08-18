# app/core/memory.py
from __future__ import annotations
from dataclasses import dataclass, field
from collections import deque
from time import time
from typing import Deque, Dict, List, Literal, Optional
from .settings import settings

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
    """in-memory session store (per uvicorn worker) — ถ้าจะรันหลาย worker/หลายเครื่อง แนะนำย้ายไป Redis"""
    def __init__(self):
        self._buf: Dict[str, SessionBuf] = {}

    def _prune(self):
        ttl = settings.MEMORY_TTL_MIN * 60
        now = time()
        dead = [sid for sid, s in self._buf.items() if now - s.touched > ttl]
        for sid in dead:
            self._buf.pop(sid, None)

    def add(self, sid: str, role: Role, text: str):
        if not sid:
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

    def compose_context(self, sid: str) -> str:
        """รวมบทสนทนาล่าสุดเป็นข้อความสั้น ๆ สำหรับยัดเข้า LLM"""
        turns = self.get_turns(sid)
        if not turns:
            return ""
        lines: List[str] = []
        for t in turns:
            prefix = "ผู้ใช้" if t.role == "user" else ("ผู้ช่วย" if t.role == "assistant" else "ระบบ")
            lines.append(f"{prefix}: {t.text.strip()}")
        ctx = "\n".join(lines)
        if len(ctx) > settings.MEMORY_MAX_CHARS:
            ctx = ctx[-settings.MEMORY_MAX_CHARS:]  # keep tail
        return ctx

MEMORY = MemoryStore()
