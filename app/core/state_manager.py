# app/core/state_manager.py
"""
VORA State Manager
==================
จัดการสถานะของระบบทั้งหมด - Robot, Session, Pipeline
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Callable
import json

logger = logging.getLogger("vora.state")


class RobotStatus(Enum):
    """สถานะ Robot"""
    OFFLINE = "offline"
    CONNECTING = "connecting"
    IDLE = "idle"
    BUSY = "busy"
    MOVING = "moving"
    SEARCHING = "searching"
    ERROR = "error"
    CHARGING = "charging"


class SystemMode(Enum):
    """โหมดการทำงานระบบ"""
    STANDBY = "standby"      # รอคำสั่ง
    ACTIVE = "active"        # กำลังทำงาน
    EMERGENCY = "emergency"  # ฉุกเฉิน (หยุดทุกอย่าง)
    MAINTENANCE = "maintenance"  # ซ่อมบำรุง


@dataclass
class RobotState:
    """สถานะของ Robot แต่ละตัว"""
    robot_id: str
    name: str = "MyAGV"
    
    # Status
    status: RobotStatus = RobotStatus.OFFLINE
    last_seen: Optional[datetime] = None
    
    # Position (จาก LiDAR/Odometry)
    position_x: float = 0.0
    position_y: float = 0.0
    orientation: float = 0.0  # degrees
    map_id: Optional[str] = None
    
    # Battery
    battery_percent: int = 100
    is_charging: bool = False
    
    # Current task
    current_task: Optional[str] = None
    current_command_id: Optional[str] = None
    
    # Sensors
    obstacle_detected: bool = False
    lidar_active: bool = False
    camera_active: bool = False
    
    # Connection
    ip_address: Optional[str] = None
    connected_at: Optional[datetime] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "robot_id": self.robot_id,
            "name": self.name,
            "status": self.status.value,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
            "position": {
                "x": self.position_x,
                "y": self.position_y,
                "orientation": self.orientation,
                "map_id": self.map_id
            },
            "battery": {
                "percent": self.battery_percent,
                "is_charging": self.is_charging
            },
            "current_task": self.current_task,
            "sensors": {
                "obstacle_detected": self.obstacle_detected,
                "lidar_active": self.lidar_active,
                "camera_active": self.camera_active
            },
            "connection": {
                "ip_address": self.ip_address,
                "connected_at": self.connected_at.isoformat() if self.connected_at else None
            }
        }


@dataclass
class SessionState:
    """สถานะ Session ของผู้ใช้"""
    session_id: str
    created_at: datetime = field(default_factory=datetime.now)
    last_activity: datetime = field(default_factory=datetime.now)
    
    # User context
    user_name: Optional[str] = None
    device_type: str = "unknown"  # mobile, desktop
    
    # Conversation history (last N)
    conversation: List[Dict[str, str]] = field(default_factory=list)
    max_conversation: int = 20
    
    # Current state
    is_listening: bool = False
    current_intent: Optional[str] = None
    pending_confirmation: Optional[str] = None
    
    # Preferences
    tts_enabled: bool = True
    tts_voice: str = "default"
    speech_rate: float = 1.0
    
    def add_message(self, role: str, content: str):
        """เพิ่มข้อความในประวัติ"""
        self.conversation.append({
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat()
        })
        if len(self.conversation) > self.max_conversation:
            self.conversation.pop(0)
        self.last_activity = datetime.now()
    
    def get_context(self, last_n: int = 5) -> List[Dict[str, str]]:
        """ดึงบริบทล่าสุด"""
        return self.conversation[-last_n:]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "created_at": self.created_at.isoformat(),
            "last_activity": self.last_activity.isoformat(),
            "user_name": self.user_name,
            "device_type": self.device_type,
            "is_listening": self.is_listening,
            "current_intent": self.current_intent,
            "tts_enabled": self.tts_enabled,
            "conversation_count": len(self.conversation)
        }


class StateManager:
    """
    State Manager หลัก
    - จัดการ Robot states
    - จัดการ User sessions
    - System-wide state
    - Event notifications
    """
    
    def __init__(self):
        # System state
        self._mode: SystemMode = SystemMode.STANDBY
        self._started_at: datetime = datetime.now()
        
        # Robot states
        self._robots: Dict[str, RobotState] = {}
        
        # Session states
        self._sessions: Dict[str, SessionState] = {}
        
        # Event subscribers
        self._subscribers: Dict[str, List[Callable]] = {
            "robot_connect": [],
            "robot_disconnect": [],
            "robot_status_change": [],
            "session_start": [],
            "session_end": [],
            "mode_change": [],
            "emergency": []
        }
        
        # Lab context (สิ่งของที่รู้จักในห้อง)
        self._known_objects: Dict[str, Dict[str, Any]] = {}
        self._known_locations: Dict[str, Dict[str, Any]] = {}
        
        # Initialize default locations
        self._init_lab_context()
        
        logger.info("🎛️ State Manager initialized")
    
    def _init_lab_context(self):
        """Initialize lab context"""
        # สถานที่ในห้องแล็บ
        self._known_locations = {
            "โต๊ะทำงาน": {"x": 2.0, "y": 3.0, "description": "โต๊ะทำงานหลัก"},
            "ตู้เก็บของ": {"x": 0.5, "y": 1.0, "description": "ตู้เก็บอุปกรณ์"},
            "ประตู": {"x": 5.0, "y": 0.0, "description": "ประตูทางเข้า"},
            "มุมชาร์จ": {"x": 0.0, "y": 0.0, "description": "จุดชาร์จแบตเตอรี่"},
            "ชั้นวางของ": {"x": 1.0, "y": 4.0, "description": "ชั้นวางอุปกรณ์"},
        }
        
        # สิ่งของที่รู้จัก (จะอัพเดทจาก VLM)
        self._known_objects = {
            "ไขควง": {"last_seen_location": "โต๊ะทำงาน", "confidence": 0.0},
            "กรรไกร": {"last_seen_location": None, "confidence": 0.0},
            "กุญแจ": {"last_seen_location": None, "confidence": 0.0},
            "คีม": {"last_seen_location": None, "confidence": 0.0},
            "สายไฟ": {"last_seen_location": None, "confidence": 0.0},
        }
    
    # ============ System Mode ============
    
    @property
    def mode(self) -> SystemMode:
        return self._mode
    
    async def set_mode(self, mode: SystemMode):
        """เปลี่ยนโหมดระบบ"""
        old_mode = self._mode
        self._mode = mode
        
        logger.info(f"🔄 System mode: {old_mode.value} → {mode.value}")
        
        if mode == SystemMode.EMERGENCY:
            await self._emit("emergency", {"reason": "emergency_mode_activated"})
        
        await self._emit("mode_change", {"old": old_mode.value, "new": mode.value})
    
    async def emergency_stop(self):
        """หยุดฉุกเฉินทั้งระบบ"""
        await self.set_mode(SystemMode.EMERGENCY)
        
        # Set all robots to error state
        for robot in self._robots.values():
            robot.status = RobotStatus.ERROR
            robot.current_task = None
    
    # ============ Robot Management ============
    
    def register_robot(self, robot_id: str, name: str = None, ip: str = None) -> RobotState:
        """ลงทะเบียน Robot ใหม่"""
        if robot_id in self._robots:
            robot = self._robots[robot_id]
        else:
            robot = RobotState(robot_id=robot_id, name=name or f"Robot-{robot_id}")
            self._robots[robot_id] = robot
        
        robot.status = RobotStatus.IDLE
        robot.last_seen = datetime.now()
        robot.connected_at = datetime.now()
        robot.ip_address = ip
        
        logger.info(f"🤖 Robot registered: {robot_id}")
        asyncio.create_task(self._emit("robot_connect", robot.to_dict()))
        
        return robot
    
    def unregister_robot(self, robot_id: str):
        """ยกเลิก Robot"""
        if robot_id in self._robots:
            robot = self._robots[robot_id]
            robot.status = RobotStatus.OFFLINE
            robot.last_seen = datetime.now()
            
            logger.info(f"🔌 Robot disconnected: {robot_id}")
            asyncio.create_task(self._emit("robot_disconnect", {"robot_id": robot_id}))
    
    def get_robot(self, robot_id: str) -> Optional[RobotState]:
        """ดู Robot state"""
        return self._robots.get(robot_id)
    
    def get_all_robots(self) -> List[RobotState]:
        """ดู Robot ทั้งหมด"""
        return list(self._robots.values())
    
    def get_available_robot(self) -> Optional[RobotState]:
        """หา Robot ที่ว่าง"""
        for robot in self._robots.values():
            if robot.status == RobotStatus.IDLE:
                return robot
        return None
    
    async def update_robot_status(
        self,
        robot_id: str,
        status: RobotStatus = None,
        position: Dict[str, float] = None,
        battery: int = None,
        task: str = None
    ):
        """อัพเดทสถานะ Robot"""
        robot = self._robots.get(robot_id)
        if not robot:
            return
        
        old_status = robot.status
        
        if status:
            robot.status = status
        if position:
            robot.position_x = position.get("x", robot.position_x)
            robot.position_y = position.get("y", robot.position_y)
            robot.orientation = position.get("orientation", robot.orientation)
        if battery is not None:
            robot.battery_percent = battery
            robot.is_charging = battery < robot.battery_percent  # Simple detection
        if task is not None:
            robot.current_task = task
        
        robot.last_seen = datetime.now()
        
        if status and status != old_status:
            await self._emit("robot_status_change", {
                "robot_id": robot_id,
                "old_status": old_status.value,
                "new_status": status.value
            })
    
    # ============ Session Management ============
    
    def create_session(self, session_id: str, device_type: str = "unknown") -> SessionState:
        """สร้าง Session ใหม่"""
        session = SessionState(session_id=session_id, device_type=device_type)
        self._sessions[session_id] = session
        
        logger.info(f"📱 Session created: {session_id} ({device_type})")
        asyncio.create_task(self._emit("session_start", session.to_dict()))
        
        return session
    
    def get_session(self, session_id: str) -> Optional[SessionState]:
        """ดู Session"""
        return self._sessions.get(session_id)
    
    def get_or_create_session(self, session_id: str, device_type: str = "unknown") -> SessionState:
        """ดูหรือสร้าง Session"""
        if session_id in self._sessions:
            session = self._sessions[session_id]
            session.last_activity = datetime.now()
            return session
        return self.create_session(session_id, device_type)
    
    def end_session(self, session_id: str):
        """จบ Session"""
        if session_id in self._sessions:
            session = self._sessions[session_id]
            logger.info(f"📴 Session ended: {session_id}")
            asyncio.create_task(self._emit("session_end", session.to_dict()))
            del self._sessions[session_id]
    
    def cleanup_stale_sessions(self, max_age_minutes: int = 60):
        """ลบ Session ที่ไม่ใช้งาน"""
        now = datetime.now()
        stale = []
        
        for sid, session in self._sessions.items():
            age = now - session.last_activity
            if age > timedelta(minutes=max_age_minutes):
                stale.append(sid)
        
        for sid in stale:
            self.end_session(sid)
        
        if stale:
            logger.info(f"🧹 Cleaned up {len(stale)} stale sessions")
    
    # ============ Lab Context ============
    
    def update_object_location(self, obj_name: str, location: str, confidence: float = 0.8):
        """อัพเดทตำแหน่งสิ่งของ (จาก VLM)"""
        if obj_name not in self._known_objects:
            self._known_objects[obj_name] = {}
        
        self._known_objects[obj_name]["last_seen_location"] = location
        self._known_objects[obj_name]["confidence"] = confidence
        self._known_objects[obj_name]["last_updated"] = datetime.now().isoformat()
        
        logger.info(f"📍 Object updated: {obj_name} at {location} ({confidence:.0%})")
    
    def get_object_location(self, obj_name: str) -> Optional[str]:
        """หาตำแหน่งสิ่งของ"""
        obj = self._known_objects.get(obj_name)
        if obj:
            return obj.get("last_seen_location")
        return None
    
    def get_location_coords(self, location_name: str) -> Optional[Dict[str, float]]:
        """หาพิกัดสถานที่"""
        return self._known_locations.get(location_name)
    
    # ============ Event System ============
    
    def subscribe(self, event: str, callback: Callable):
        """Subscribe รับ event"""
        if event in self._subscribers:
            self._subscribers[event].append(callback)
    
    def unsubscribe(self, event: str, callback: Callable):
        """Unsubscribe"""
        if event in self._subscribers and callback in self._subscribers[event]:
            self._subscribers[event].remove(callback)
    
    async def _emit(self, event: str, data: Any):
        """ส่ง event"""
        if event in self._subscribers:
            for callback in self._subscribers[event]:
                try:
                    if asyncio.iscoroutinefunction(callback):
                        await callback(data)
                    else:
                        callback(data)
                except Exception as e:
                    logger.error(f"Event callback error: {e}")
    
    # ============ Status ============
    
    def get_system_status(self) -> Dict[str, Any]:
        """สถานะระบบทั้งหมด"""
        return {
            "mode": self._mode.value,
            "uptime_seconds": (datetime.now() - self._started_at).total_seconds(),
            "robots": {
                "total": len(self._robots),
                "online": sum(1 for r in self._robots.values() if r.status != RobotStatus.OFFLINE),
                "busy": sum(1 for r in self._robots.values() if r.status == RobotStatus.BUSY),
                "list": [r.to_dict() for r in self._robots.values()]
            },
            "sessions": {
                "total": len(self._sessions),
                "active": sum(1 for s in self._sessions.values() if s.is_listening),
                "list": [s.to_dict() for s in self._sessions.values()]
            },
            "lab_context": {
                "known_objects": len(self._known_objects),
                "known_locations": len(self._known_locations)
            }
        }


# Global instance
state_manager = StateManager()
