# app/core/command_queue.py
"""
VORA Command Queue System
=========================
จัดการคิวคำสั่งสำหรับ Robot - รองรับ priority, retry, และ status tracking
"""

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional
from collections import defaultdict

logger = logging.getLogger("vora.command_queue")


class CommandStatus(Enum):
    """สถานะของคำสั่ง"""
    PENDING = "pending"          # รอดำเนินการ
    SENT = "sent"                # ส่งไปยัง Robot แล้ว
    EXECUTING = "executing"      # Robot กำลังทำ
    COMPLETED = "completed"      # เสร็จแล้ว
    FAILED = "failed"            # ล้มเหลว
    CANCELLED = "cancelled"      # ยกเลิก
    TIMEOUT = "timeout"          # หมดเวลา


class Priority(Enum):
    """ระดับความสำคัญ"""
    EMERGENCY = 0    # ฉุกเฉิน (หยุด!)
    HIGH = 1         # สำคัญมาก
    NORMAL = 2       # ปกติ
    LOW = 3          # ไม่เร่ง


@dataclass
class QueuedCommand:
    """คำสั่งที่อยู่ในคิว"""
    id: str
    cmd: str
    params: Dict[str, Any]
    priority: Priority = Priority.NORMAL
    target_robot: Optional[str] = None  # None = broadcast
    
    # Metadata
    created_at: datetime = field(default_factory=datetime.now)
    sent_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    
    # Status
    status: CommandStatus = CommandStatus.PENDING
    retry_count: int = 0
    max_retries: int = 3
    timeout_sec: float = 30.0
    
    # Response
    result: Optional[Any] = None
    error: Optional[str] = None
    
    # Callback
    session_id: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "cmd": self.cmd,
            "params": self.params,
            "priority": self.priority.value,
            "target_robot": self.target_robot,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "sent_at": self.sent_at.isoformat() if self.sent_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "retry_count": self.retry_count,
            "result": self.result,
            "error": self.error
        }


class CommandQueue:
    """
    คิวคำสั่งหลัก
    - Priority Queue (EMERGENCY > HIGH > NORMAL > LOW)
    - Per-robot queues
    - Automatic retry
    - Timeout handling
    """
    
    def __init__(self):
        # คิวหลัก แยกตาม priority
        self._queues: Dict[Priority, asyncio.Queue] = {
            Priority.EMERGENCY: asyncio.Queue(),
            Priority.HIGH: asyncio.Queue(),
            Priority.NORMAL: asyncio.Queue(),
            Priority.LOW: asyncio.Queue(),
        }
        
        # Command tracking
        self._commands: Dict[str, QueuedCommand] = {}
        
        # Per-robot current command
        self._robot_current: Dict[str, str] = {}  # robot_id -> command_id
        
        # History (last 100)
        self._history: List[QueuedCommand] = []
        self._max_history = 100
        
        # Callbacks
        self._on_send: Optional[Callable] = None
        self._on_complete: Optional[Callable] = None
        
        # Running flag
        self._running = False
        self._processor_task: Optional[asyncio.Task] = None
        
        logger.info("📋 Command Queue initialized")
    
    async def enqueue(
        self,
        cmd: str,
        params: Dict[str, Any],
        priority: Priority = Priority.NORMAL,
        target_robot: Optional[str] = None,
        session_id: Optional[str] = None,
        timeout_sec: float = 30.0
    ) -> str:
        """เพิ่มคำสั่งเข้าคิว"""
        
        command = QueuedCommand(
            id=str(uuid.uuid4())[:8],
            cmd=cmd,
            params=params,
            priority=priority,
            target_robot=target_robot,
            session_id=session_id,
            timeout_sec=timeout_sec
        )
        
        # Emergency command ยกเลิกคำสั่งอื่นทั้งหมด
        if priority == Priority.EMERGENCY:
            await self._cancel_all_pending()
        
        # Add to queue
        self._commands[command.id] = command
        await self._queues[priority].put(command.id)
        
        logger.info(f"📥 Queued [{priority.name}]: {cmd} -> {target_robot or 'all'} (id={command.id})")
        return command.id
    
    async def enqueue_batch(
        self,
        commands: List[Dict[str, Any]],
        session_id: Optional[str] = None
    ) -> List[str]:
        """เพิ่มหลายคำสั่งพร้อมกัน"""
        ids = []
        for c in commands:
            cmd_id = await self.enqueue(
                cmd=c.get("cmd"),
                params=c.get("params", {}),
                priority=Priority(c.get("priority", 2)),
                target_robot=c.get("target_robot"),
                session_id=session_id
            )
            ids.append(cmd_id)
        return ids
    
    async def get_next(self) -> Optional[QueuedCommand]:
        """ดึงคำสั่งถัดไป (priority order)"""
        for priority in Priority:
            queue = self._queues[priority]
            if not queue.empty():
                cmd_id = await queue.get()
                if cmd_id in self._commands:
                    return self._commands[cmd_id]
        return None
    
    def get_command(self, cmd_id: str) -> Optional[QueuedCommand]:
        """ดูคำสั่งตาม ID"""
        return self._commands.get(cmd_id)
    
    async def update_status(
        self,
        cmd_id: str,
        status: CommandStatus,
        result: Any = None,
        error: str = None
    ):
        """อัพเดทสถานะคำสั่ง"""
        if cmd_id not in self._commands:
            return
        
        cmd = self._commands[cmd_id]
        cmd.status = status
        
        if status == CommandStatus.SENT:
            cmd.sent_at = datetime.now()
        elif status in (CommandStatus.COMPLETED, CommandStatus.FAILED, CommandStatus.TIMEOUT):
            cmd.completed_at = datetime.now()
            cmd.result = result
            cmd.error = error
            
            # Move to history
            self._add_to_history(cmd)
            
            # Clear robot current
            if cmd.target_robot and cmd.target_robot in self._robot_current:
                if self._robot_current[cmd.target_robot] == cmd_id:
                    del self._robot_current[cmd.target_robot]
            
            # Callback
            if self._on_complete:
                await self._on_complete(cmd)
        
        logger.info(f"📊 Command {cmd_id}: {status.value}")
    
    async def retry_command(self, cmd_id: str) -> bool:
        """Retry คำสั่งที่ล้มเหลว"""
        cmd = self._commands.get(cmd_id)
        if not cmd:
            return False
        
        if cmd.retry_count >= cmd.max_retries:
            await self.update_status(cmd_id, CommandStatus.FAILED, error="Max retries exceeded")
            return False
        
        cmd.retry_count += 1
        cmd.status = CommandStatus.PENDING
        await self._queues[cmd.priority].put(cmd_id)
        
        logger.info(f"🔄 Retry {cmd_id} (attempt {cmd.retry_count}/{cmd.max_retries})")
        return True
    
    async def cancel_command(self, cmd_id: str) -> bool:
        """ยกเลิกคำสั่ง"""
        cmd = self._commands.get(cmd_id)
        if not cmd:
            return False
        
        if cmd.status not in (CommandStatus.PENDING, CommandStatus.SENT):
            return False
        
        await self.update_status(cmd_id, CommandStatus.CANCELLED)
        return True
    
    async def _cancel_all_pending(self):
        """ยกเลิกทุกคำสั่งที่รอ (สำหรับ EMERGENCY)"""
        for cmd_id, cmd in list(self._commands.items()):
            if cmd.status == CommandStatus.PENDING:
                await self.update_status(cmd_id, CommandStatus.CANCELLED, error="Emergency stop")
        
        # Clear queues
        for queue in self._queues.values():
            while not queue.empty():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
        
        logger.warning("🚨 All pending commands cancelled (EMERGENCY)")
    
    def _add_to_history(self, cmd: QueuedCommand):
        """เพิ่มเข้า history"""
        self._history.append(cmd)
        if len(self._history) > self._max_history:
            self._history.pop(0)
    
    # ============ Stats ============
    
    def get_stats(self) -> Dict[str, Any]:
        """สถิติคิว"""
        pending = sum(1 for c in self._commands.values() if c.status == CommandStatus.PENDING)
        executing = sum(1 for c in self._commands.values() if c.status == CommandStatus.EXECUTING)
        
        return {
            "total_commands": len(self._commands),
            "pending": pending,
            "executing": executing,
            "queue_sizes": {
                p.name: self._queues[p].qsize() for p in Priority
            },
            "history_count": len(self._history),
            "robot_current": dict(self._robot_current)
        }
    
    def get_pending_commands(self) -> List[Dict[str, Any]]:
        """คำสั่งที่รอดำเนินการ"""
        return [
            c.to_dict() for c in self._commands.values()
            if c.status in (CommandStatus.PENDING, CommandStatus.SENT, CommandStatus.EXECUTING)
        ]
    
    def get_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        """ประวัติคำสั่ง"""
        return [c.to_dict() for c in self._history[-limit:]]
    
    # ============ Callbacks ============
    
    def on_send(self, callback: Callable):
        """Set callback เมื่อส่งคำสั่ง"""
        self._on_send = callback
    
    def on_complete(self, callback: Callable):
        """Set callback เมื่อคำสั่งเสร็จ"""
        self._on_complete = callback


# Global instance
command_queue = CommandQueue()


# ============ Utility Functions ============

async def queue_robot_command(
    cmd: str,
    params: Dict[str, Any],
    priority: int = 2,
    target_robot: str = None,
    session_id: str = None
) -> str:
    """Shortcut function สำหรับเพิ่มคำสั่ง"""
    return await command_queue.enqueue(
        cmd=cmd,
        params=params,
        priority=Priority(priority),
        target_robot=target_robot,
        session_id=session_id
    )


async def emergency_stop(target_robot: str = None) -> str:
    """คำสั่งหยุดฉุกเฉิน"""
    return await command_queue.enqueue(
        cmd="stop",
        params={"emergency": True},
        priority=Priority.EMERGENCY,
        target_robot=target_robot
    )
