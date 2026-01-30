# app/api/server_router.py
"""
VORA Server API
===============
API หลักสำหรับจัดการ Server - Status, TTS, Sessions, Commands
"""

import asyncio
import base64
import io
import logging
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel, Field

from ..core.command_queue import command_queue, queue_robot_command, emergency_stop, Priority
from ..core.state_manager import state_manager, SystemMode, RobotStatus
from ..core.session_manager import session_manager
from ..services.thai_tts import thai_tts, speak_thai

router = APIRouter(prefix="/api/server", tags=["VORA Server"])
logger = logging.getLogger("vora.server")


# ============ Request/Response Models ============

class TTSRequest(BaseModel):
    text: str = Field(..., description="ข้อความที่จะพูด")
    voice: str = Field("default", description="เสียงที่ใช้")
    speed: float = Field(1.0, description="ความเร็ว (0.5-2.0)")
    format: str = Field("wav", description="รูปแบบไฟล์ (wav)")


class CommandQueueRequest(BaseModel):
    cmd: str = Field(..., description="คำสั่ง")
    params: Dict[str, Any] = Field(default_factory=dict, description="พารามิเตอร์")
    priority: int = Field(2, description="Priority (0=emergency, 1=high, 2=normal, 3=low)")
    target_robot: Optional[str] = Field(None, description="Robot ID (None=broadcast)")


class SessionRequest(BaseModel):
    session_id: str = Field(..., description="Session ID")
    device_type: str = Field("unknown", description="ประเภทอุปกรณ์")


class ModeChangeRequest(BaseModel):
    mode: str = Field(..., description="โหมด: standby, active, emergency, maintenance")


class RobotCommandRequest(BaseModel):
    robot_id: str = Field(..., description="Robot ID")
    command: Dict[str, Any] = Field(..., description="Robot command (cmd, params)")


# ============ TTS Endpoints ============

@router.post("/tts/speak")
async def tts_speak(req: TTSRequest):
    """
    🔊 Text-to-Speech ภาษาไทย
    
    Returns: WAV audio bytes
    """
    logger.info(f"🔊 TTS request: {req.text[:30]}...")
    
    try:
        wav_bytes = await thai_tts.synthesize(
            text=req.text,
            voice=req.voice,
            speed=req.speed
        )
        
        return StreamingResponse(
            io.BytesIO(wav_bytes),
            media_type="audio/wav",
            headers={
                "Content-Disposition": f'inline; filename="tts.wav"',
                "X-TTS-Text": base64.b64encode(req.text.encode()).decode()
            }
        )
    except Exception as e:
        logger.error(f"TTS error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tts/status")
async def tts_status():
    """📊 TTS Service Status"""
    return thai_tts.get_status()


# ============ Command Queue Endpoints ============

@router.post("/queue/add")
async def queue_add_command(req: CommandQueueRequest):
    """
    📥 เพิ่มคำสั่งเข้าคิว
    """
    cmd_id = await command_queue.enqueue(
        cmd=req.cmd,
        params=req.params,
        priority=Priority(req.priority),
        target_robot=req.target_robot
    )
    
    return {
        "success": True,
        "command_id": cmd_id,
        "message": f"Command queued: {req.cmd}"
    }


@router.post("/queue/batch")
async def queue_add_batch(commands: List[CommandQueueRequest]):
    """
    📥 เพิ่มหลายคำสั่งพร้อมกัน
    """
    ids = []
    for c in commands:
        cmd_id = await command_queue.enqueue(
            cmd=c.cmd,
            params=c.params,
            priority=Priority(c.priority),
            target_robot=c.target_robot
        )
        ids.append(cmd_id)
    
    return {
        "success": True,
        "command_ids": ids,
        "count": len(ids)
    }


@router.get("/queue/status")
async def queue_status():
    """📊 สถานะคิวคำสั่ง"""
    return command_queue.get_stats()


@router.get("/queue/pending")
async def queue_pending():
    """📋 คำสั่งที่รอดำเนินการ"""
    return command_queue.get_pending_commands()


@router.get("/queue/history")
async def queue_history(limit: int = 20):
    """📜 ประวัติคำสั่ง"""
    return command_queue.get_history(limit)


@router.post("/queue/cancel/{cmd_id}")
async def queue_cancel(cmd_id: str):
    """❌ ยกเลิกคำสั่ง"""
    success = await command_queue.cancel_command(cmd_id)
    return {"success": success, "command_id": cmd_id}


@router.post("/emergency-stop")
async def do_emergency_stop(target_robot: Optional[str] = None):
    """
    🚨 หยุดฉุกเฉิน - ยกเลิกทุกคำสั่ง
    """
    cmd_id = await emergency_stop(target_robot)
    await state_manager.emergency_stop()
    
    logger.warning(f"🚨 EMERGENCY STOP triggered! (cmd_id={cmd_id})")
    
    return {
        "success": True,
        "message": "Emergency stop activated",
        "command_id": cmd_id
    }


@router.post("/robot-command")
async def send_robot_command(req: RobotCommandRequest):
    """
    🤖 ส่งคำสั่งไปยัง Robot ผ่าน Gateway
    """
    from ..api.pipeline_router import send_to_robot
    from ..schemas.agent import RobotCommand
    
    logger.info(f"📤 Sending command to {req.robot_id}: {req.command}")
    
    # สร้าง RobotCommand object
    robot_cmd = RobotCommand(
        cmd=req.command.get("cmd"),
        params=req.command.get("params", {}),
        priority=req.command.get("priority", 2)
    )
    
    # ส่งไป Robot
    success = await send_to_robot(req.robot_id, robot_cmd)
    
    if success:
        return {
            "success": True,
            "message": f"Command sent to {req.robot_id}",
            "command": req.command
        }
    else:
        raise HTTPException(
            status_code=503,
            detail=f"Robot {req.robot_id} not connected"
        )


# ============ Session Endpoints ============

@router.post("/session/create")
async def session_create(req: SessionRequest):
    """📱 สร้าง Session ใหม่"""
    session = session_manager.create_session(
        session_id=req.session_id,
        device_type=req.device_type
    )
    return session.to_dict()


@router.get("/session/{session_id}")
async def session_get(session_id: str):
    """📱 ดู Session"""
    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session.to_dict()


@router.get("/session/{session_id}/history")
async def session_history(session_id: str, last_n: int = 20):
    """💬 ประวัติสนทนา"""
    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    return {
        "session_id": session_id,
        "messages": [m.to_dict() for m in session.messages[-last_n:]]
    }


@router.delete("/session/{session_id}")
async def session_end(session_id: str):
    """📴 จบ Session"""
    session_manager.end_session(session_id)
    return {"success": True, "message": f"Session {session_id} ended"}


@router.get("/sessions")
async def sessions_list():
    """📱 Sessions ทั้งหมด"""
    return {
        "sessions": [s.to_dict() for s in session_manager.get_all_sessions()],
        "stats": session_manager.get_stats()
    }


# ============ State Endpoints ============

@router.get("/state")
async def get_system_state():
    """🎛️ สถานะระบบทั้งหมด"""
    return state_manager.get_system_status()


@router.post("/state/mode")
async def set_system_mode(req: ModeChangeRequest):
    """🔄 เปลี่ยนโหมดระบบ"""
    try:
        mode = SystemMode(req.mode)
        await state_manager.set_mode(mode)
        return {"success": True, "mode": mode.value}
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid mode: {req.mode}")


@router.get("/state/robots")
async def get_robots_state():
    """🤖 สถานะ Robots"""
    robots = state_manager.get_all_robots()
    return {
        "robots": [r.to_dict() for r in robots],
        "count": len(robots),
        "online": sum(1 for r in robots if r.status != RobotStatus.OFFLINE)
    }


# ============ Health & Status ============

@router.get("/health")
async def health_check():
    """💚 Health Check"""
    return {
        "status": "healthy",
        "timestamp": time.time(),
        "services": {
            "tts": thai_tts.get_status()["primary"] != "none",
            "queue": True,
            "state": True,
            "sessions": True
        }
    }


@router.get("/status")
async def server_status():
    """
    📊 Server Status Dashboard
    
    รวมข้อมูลทั้งหมด:
    - System state
    - Command queue
    - Sessions
    - TTS
    """
    return {
        "server": "VORA Server",
        "version": "1.0.0",
        "system": state_manager.get_system_status(),
        "queue": command_queue.get_stats(),
        "sessions": session_manager.get_stats(),
        "tts": thai_tts.get_status()
    }


# ============ WebSocket for Real-time Updates ============

# Active websocket connections for status updates
status_connections: List[WebSocket] = []


@router.websocket("/ws/status")
async def status_websocket(websocket: WebSocket):
    """
    🔌 WebSocket สำหรับ real-time status updates
    
    ส่ง:
    - System state changes
    - Robot status changes
    - Command updates
    """
    await websocket.accept()
    status_connections.append(websocket)
    
    try:
        # Send initial status
        await websocket.send_json({
            "type": "connected",
            "data": {
                "message": "Connected to VORA status stream",
                "status": state_manager.get_system_status()
            }
        })
        
        # Keep connection alive
        while True:
            try:
                data = await asyncio.wait_for(
                    websocket.receive_json(),
                    timeout=30
                )
                
                # Handle ping
                if data.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
                    
            except asyncio.TimeoutError:
                # Send periodic status update
                await websocket.send_json({
                    "type": "status",
                    "data": {
                        "queue": command_queue.get_stats(),
                        "sessions": session_manager.get_stats()
                    }
                })
                
    except WebSocketDisconnect:
        logger.info("Status WebSocket disconnected")
    except Exception as e:
        logger.error(f"Status WebSocket error: {e}")
    finally:
        if websocket in status_connections:
            status_connections.remove(websocket)


async def broadcast_status_update(event_type: str, data: Any):
    """Broadcast status update to all connected clients"""
    message = {
        "type": event_type,
        "data": data,
        "timestamp": time.time()
    }
    
    for ws in list(status_connections):
        try:
            await ws.send_json(message)
        except Exception:
            status_connections.remove(ws)


# ============ Lab Context ============

@router.get("/lab/objects")
async def get_lab_objects():
    """🔧 สิ่งของในห้องแล็บ"""
    return {
        "objects": state_manager._known_objects,
        "locations": state_manager._known_locations
    }


@router.post("/lab/object/{obj_name}/location")
async def update_object_location(obj_name: str, location: str, confidence: float = 0.8):
    """📍 อัพเดทตำแหน่งสิ่งของ"""
    state_manager.update_object_location(obj_name, location, confidence)
    return {
        "success": True,
        "object": obj_name,
        "location": location,
        "confidence": confidence
    }
