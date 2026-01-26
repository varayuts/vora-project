# app/core/session_manager.py
"""
VORA Session Manager
====================
จัดการ Session ของผู้ใช้ - conversation history, context, preferences
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
import json

logger = logging.getLogger("vora.session")


@dataclass
class Message:
    """ข้อความในการสนทนา"""
    role: str  # user, assistant, system
    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp.isoformat(),
            "metadata": self.metadata
        }
    
    def to_llm_format(self) -> Dict[str, str]:
        """Format for LLM API"""
        return {
            "role": self.role,
            "content": self.content
        }


@dataclass
class UserPreferences:
    """การตั้งค่าของผู้ใช้"""
    tts_enabled: bool = True
    tts_voice: str = "default"
    tts_speed: float = 1.0
    
    stt_language: str = "th"
    stt_auto_send: bool = True
    
    ui_theme: str = "auto"
    ui_font_size: str = "medium"
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "tts_enabled": self.tts_enabled,
            "tts_voice": self.tts_voice,
            "tts_speed": self.tts_speed,
            "stt_language": self.stt_language,
            "stt_auto_send": self.stt_auto_send,
            "ui_theme": self.ui_theme,
            "ui_font_size": self.ui_font_size
        }


@dataclass
class Session:
    """Session ของผู้ใช้"""
    session_id: str
    created_at: datetime = field(default_factory=datetime.now)
    last_activity: datetime = field(default_factory=datetime.now)
    
    # User info
    user_name: Optional[str] = None
    device_type: str = "unknown"  # mobile, desktop, tablet
    user_agent: Optional[str] = None
    ip_address: Optional[str] = None
    
    # Conversation
    messages: List[Message] = field(default_factory=list)
    max_messages: int = 50
    
    # Context
    current_intent: Optional[str] = None
    current_target: Optional[str] = None
    pending_confirmation: Optional[Dict[str, Any]] = None
    
    # State
    is_listening: bool = False
    is_speaking: bool = False
    
    # Preferences
    preferences: UserPreferences = field(default_factory=UserPreferences)
    
    # Stats
    total_commands: int = 0
    successful_commands: int = 0
    
    def add_user_message(self, content: str, **metadata):
        """เพิ่มข้อความจากผู้ใช้"""
        self.messages.append(Message(
            role="user",
            content=content,
            metadata=metadata
        ))
        self._trim_messages()
        self.last_activity = datetime.now()
        self.total_commands += 1
    
    def add_assistant_message(self, content: str, **metadata):
        """เพิ่มข้อความจาก Assistant"""
        self.messages.append(Message(
            role="assistant",
            content=content,
            metadata=metadata
        ))
        self._trim_messages()
        self.last_activity = datetime.now()
    
    def add_system_message(self, content: str):
        """เพิ่ม System message"""
        self.messages.append(Message(
            role="system",
            content=content
        ))
        self._trim_messages()
    
    def _trim_messages(self):
        """ลบข้อความเก่าเกิน limit"""
        if len(self.messages) > self.max_messages:
            # Keep first system message if exists
            system_msgs = [m for m in self.messages if m.role == "system"]
            other_msgs = [m for m in self.messages if m.role != "system"]
            
            # Trim other messages
            other_msgs = other_msgs[-(self.max_messages - len(system_msgs)):]
            
            self.messages = system_msgs + other_msgs
    
    def get_conversation_context(self, last_n: int = 10) -> List[Dict[str, str]]:
        """ดึงบริบทสนทนาสำหรับ LLM"""
        recent = self.messages[-last_n:]
        return [m.to_llm_format() for m in recent]
    
    def get_last_user_message(self) -> Optional[str]:
        """ข้อความล่าสุดของผู้ใช้"""
        for msg in reversed(self.messages):
            if msg.role == "user":
                return msg.content
        return None
    
    def clear_conversation(self):
        """ล้างประวัติสนทนา"""
        self.messages = []
        self.current_intent = None
        self.current_target = None
        self.pending_confirmation = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "created_at": self.created_at.isoformat(),
            "last_activity": self.last_activity.isoformat(),
            "user_name": self.user_name,
            "device_type": self.device_type,
            "message_count": len(self.messages),
            "current_intent": self.current_intent,
            "is_listening": self.is_listening,
            "is_speaking": self.is_speaking,
            "total_commands": self.total_commands,
            "successful_commands": self.successful_commands,
            "preferences": self.preferences.to_dict()
        }


class SessionManager:
    """
    Session Manager
    ===============
    - สร้าง/จัดการ Sessions
    - Track conversation history
    - Handle preferences
    - Auto-cleanup stale sessions
    """
    
    def __init__(self, session_timeout_minutes: int = 60):
        self._sessions: Dict[str, Session] = {}
        self._timeout_minutes = session_timeout_minutes
        self._cleanup_task: Optional[asyncio.Task] = None
        
        logger.info("📱 Session Manager initialized")
    
    def create_session(
        self,
        session_id: str = None,
        device_type: str = "unknown",
        user_agent: str = None,
        ip_address: str = None
    ) -> Session:
        """สร้าง Session ใหม่"""
        if not session_id:
            session_id = str(uuid.uuid4())[:12]
        
        session = Session(
            session_id=session_id,
            device_type=device_type,
            user_agent=user_agent,
            ip_address=ip_address
        )
        
        # Add system prompt
        session.add_system_message(
            "คุณคือ VORA (Voice Oriented Robotics Assistant) ผู้ช่วยควบคุมหุ่นยนต์ด้วยเสียงภาษาไทย "
            "หน้าที่หลักคือรับคำสั่งจากผู้ใช้แล้วควบคุมหุ่นยนต์ MyAGV ในห้องแล็บ IT "
            "ตอบสั้นกระชับ ใช้ภาษาไทยสุภาพ"
        )
        
        self._sessions[session_id] = session
        logger.info(f"📱 Session created: {session_id} ({device_type})")
        
        return session
    
    def get_session(self, session_id: str) -> Optional[Session]:
        """ดู Session"""
        session = self._sessions.get(session_id)
        if session:
            session.last_activity = datetime.now()
        return session
    
    def get_or_create_session(
        self,
        session_id: str,
        device_type: str = "unknown",
        **kwargs
    ) -> Session:
        """ดูหรือสร้าง Session"""
        if session_id in self._sessions:
            session = self._sessions[session_id]
            session.last_activity = datetime.now()
            return session
        
        return self.create_session(session_id, device_type, **kwargs)
    
    def end_session(self, session_id: str):
        """จบ Session"""
        if session_id in self._sessions:
            logger.info(f"📴 Session ended: {session_id}")
            del self._sessions[session_id]
    
    def get_all_sessions(self) -> List[Session]:
        """ดู Sessions ทั้งหมด"""
        return list(self._sessions.values())
    
    def get_active_sessions(self) -> List[Session]:
        """Sessions ที่ active (listening/speaking)"""
        return [s for s in self._sessions.values() if s.is_listening or s.is_speaking]
    
    def cleanup_stale_sessions(self):
        """ลบ Sessions ที่หมดอายุ"""
        now = datetime.now()
        stale = []
        
        for sid, session in self._sessions.items():
            age = now - session.last_activity
            if age > timedelta(minutes=self._timeout_minutes):
                stale.append(sid)
        
        for sid in stale:
            del self._sessions[sid]
            logger.info(f"🧹 Session expired: {sid}")
        
        return len(stale)
    
    async def start_cleanup_loop(self, interval_seconds: int = 300):
        """Start auto-cleanup loop"""
        async def _loop():
            while True:
                await asyncio.sleep(interval_seconds)
                cleaned = self.cleanup_stale_sessions()
                if cleaned > 0:
                    logger.info(f"🧹 Cleaned up {cleaned} stale sessions")
        
        self._cleanup_task = asyncio.create_task(_loop())
    
    def stop_cleanup_loop(self):
        """Stop auto-cleanup"""
        if self._cleanup_task:
            self._cleanup_task.cancel()
    
    def get_stats(self) -> Dict[str, Any]:
        """สถิติ Sessions"""
        total = len(self._sessions)
        active = len(self.get_active_sessions())
        
        devices = {}
        for s in self._sessions.values():
            devices[s.device_type] = devices.get(s.device_type, 0) + 1
        
        return {
            "total_sessions": total,
            "active_sessions": active,
            "by_device": devices,
            "timeout_minutes": self._timeout_minutes
        }


# ============ Global Instance ============

session_manager = SessionManager()


# ============ Shortcut Functions ============

def get_session(session_id: str) -> Optional[Session]:
    """Quick function to get session"""
    return session_manager.get_session(session_id)


def get_or_create(session_id: str, device_type: str = "unknown") -> Session:
    """Quick function to get or create session"""
    return session_manager.get_or_create_session(session_id, device_type)
