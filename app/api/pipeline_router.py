# app/api/pipeline_router.py
"""
VORA Pipeline API
=================
Main endpoint for complete voice → robot command pipeline
"""

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
import logging
import json
import asyncio

from ..core.vora_pipeline import (
    process_command, 
    parse_command, 
    create_task_plan,
    analyze_vision,
    get_quick_command,
    PipelineResult,
    RobotCommand,
    Intent
)

router = APIRouter(prefix="/pipeline", tags=["VORA Pipeline"])
logger = logging.getLogger("vora.pipeline.api")


# ============ Request/Response Models ============

class CommandRequest(BaseModel):
    text: str = Field(..., description="คำสั่งเสียงที่แปลงเป็นข้อความแล้ว")
    image_base64: Optional[str] = Field(None, description="ภาพจากกล้อง (Base64)")
    session_id: Optional[str] = Field(None, description="Session ID สำหรับ tracking")


class CommandResponse(BaseModel):
    success: bool
    intent: Optional[str] = None
    clean_text: Optional[str] = None
    target: Optional[str] = None
    response_text: str
    commands: List[Dict[str, Any]] = []
    need_vision: bool = False
    vision_result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class QuickCommandRequest(BaseModel):
    command: str = Field(..., description="Quick command: หยุด, เดินหน้า, ถอยหลัง, หมุนซ้าย, หมุนขวา")


class VisionRequest(BaseModel):
    image_base64: str = Field(..., description="ภาพจากกล้อง (Base64)")
    target_object: str = Field(..., description="สิ่งของที่ต้องการหา")


# ============ Endpoints ============

@router.post("/command", response_model=CommandResponse)
async def process_voice_command(req: CommandRequest):
    """
    🎯 Main Endpoint: รับคำสั่งเสียง → ประมวลผล → ส่งคำสั่งหุ่นยนต์
    
    Flow:
    1. Agent (12b) parse คำสั่ง
    2. Reasoning (27b) วางแผน
    3. VLM (e4b) วิเคราะห์ภาพ (ถ้าจำเป็น)
    4. สร้าง Robot Commands
    """
    logger.info(f"📨 Command received: {req.text[:50]}...")
    
    try:
        # ตรวจสอบ quick command ก่อน (เร็วกว่า)
        quick_cmd = get_quick_command(req.text)
        if quick_cmd:
            logger.info(f"⚡ Quick command detected: {quick_cmd.cmd}")
            return CommandResponse(
                success=True,
                intent="control",
                clean_text=req.text,
                response_text=f"รับคำสั่ง{req.text}ครับ",
                commands=[{
                    "cmd": quick_cmd.cmd,
                    "params": quick_cmd.params,
                    "priority": quick_cmd.priority
                }]
            )
        
        # Full pipeline
        result = await process_command(
            text=req.text,
            image_base64=req.image_base64,
            session_id=req.session_id
        )
        
        # แปลงผลลัพธ์
        commands = []
        for cmd in result.commands:
            commands.append({
                "cmd": cmd.cmd,
                "params": cmd.params,
                "priority": cmd.priority
            })
        
        vision_result = None
        if result.vision:
            vision_result = {
                "object_found": result.vision.object_found,
                "object_name": result.vision.object_name,
                "object_location": result.vision.object_location,
                "confidence": result.vision.confidence,
                "description": result.vision.description
            }
        
        return CommandResponse(
            success=result.success,
            intent=result.parsed.intent.value if result.parsed else None,
            clean_text=result.parsed.clean_text if result.parsed else None,
            target=result.parsed.target_object or result.parsed.target_location if result.parsed else None,
            response_text=result.response_text,
            commands=commands,
            need_vision=result.plan.need_vision if result.plan else False,
            vision_result=vision_result,
            error=result.error
        )
        
    except Exception as e:
        logger.error(f"❌ Pipeline error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/quick", response_model=CommandResponse)
async def quick_command(req: QuickCommandRequest):
    """
    ⚡ Quick Command: คำสั่งด่วนที่ไม่ต้องผ่าน LLM
    - หยุด
    - เดินหน้า
    - ถอยหลัง
    - หมุนซ้าย
    - หมุนขวา
    """
    cmd = get_quick_command(req.command)
    if not cmd:
        return CommandResponse(
            success=False,
            response_text="ไม่รู้จักคำสั่งนี้ครับ",
            error="Unknown quick command"
        )
    
    return CommandResponse(
        success=True,
        intent="control",
        clean_text=req.command,
        response_text=f"รับคำสั่ง{req.command}ครับ",
        commands=[{
            "cmd": cmd.cmd,
            "params": cmd.params,
            "priority": cmd.priority
        }]
    )


@router.post("/vision")
async def analyze_image(req: VisionRequest):
    """
    👁️ Vision Analysis: วิเคราะห์ภาพหาสิ่งของ
    """
    logger.info(f"👁️ Vision request for: {req.target_object}")
    
    try:
        result = await analyze_vision(req.image_base64, req.target_object)
        
        return {
            "success": True,
            "object_found": result.object_found,
            "object_name": result.object_name,
            "object_location": result.object_location,
            "confidence": result.confidence,
            "description": result.description
        }
        
    except Exception as e:
        logger.error(f"❌ Vision error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/parse")
async def parse_only(req: CommandRequest):
    """
    🔍 Parse Only: แค่ parse คำสั่งไม่ทำอย่างอื่น (สำหรับ debug)
    """
    parsed = await parse_command(req.text)
    
    return {
        "intent": parsed.intent.value,
        "clean_text": parsed.clean_text,
        "target_object": parsed.target_object,
        "target_location": parsed.target_location,
        "action": parsed.action,
        "confidence": parsed.confidence
    }


@router.post("/plan")
async def plan_only(req: CommandRequest):
    """
    🧠 Plan Only: Parse + Plan (สำหรับ debug)
    """
    parsed = await parse_command(req.text)
    plan = await create_task_plan(parsed)
    
    return {
        "parsed": {
            "intent": parsed.intent.value,
            "clean_text": parsed.clean_text,
            "target": parsed.target_object or parsed.target_location
        },
        "plan": {
            "steps": plan.steps,
            "need_vision": plan.need_vision,
            "need_navigation": plan.need_navigation,
            "speech_response": plan.speech_response,
            "estimated_time_sec": plan.estimated_time_sec
        }
    }


# ============ WebSocket for Real-time ============

# Store active gateway connections
gateway_connections: Dict[str, WebSocket] = {}


@router.websocket("/gateway")
async def gateway_websocket(websocket: WebSocket):
    """
    🔌 Gateway WebSocket: สำหรับ Robot เชื่อมต่อรับคำสั่ง
    
    Protocol:
    - Robot → Server: {"type": "register", "robot_id": "myagv-01"}
    - Robot → Server: {"type": "status", "battery": 80, "position": {...}}
    - Server → Robot: {"type": "command", "cmd": "move", "params": {...}}
    """
    await websocket.accept()
    robot_id = None
    
    try:
        # Wait for registration
        data = await websocket.receive_json()
        if data.get("type") == "register":
            robot_id = data.get("robot_id", "unknown")
            gateway_connections[robot_id] = websocket
            logger.info(f"🤖 Robot registered: {robot_id}")
            
            await websocket.send_json({
                "type": "registered",
                "robot_id": robot_id,
                "message": "Connected to VORA Server"
            })
        
        # Main loop
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")
            
            if msg_type == "status":
                # Robot status update
                logger.debug(f"📊 Status from {robot_id}: {data}")
                
            elif msg_type == "vision_result":
                # Robot sent vision analysis result
                logger.info(f"👁️ Vision from {robot_id}: {data}")
                
            elif msg_type == "task_complete":
                # Robot completed a task
                logger.info(f"✅ Task complete from {robot_id}: {data}")
                
    except WebSocketDisconnect:
        logger.info(f"🔌 Robot disconnected: {robot_id}")
    except Exception as e:
        logger.error(f"❌ Gateway error: {e}")
    finally:
        if robot_id and robot_id in gateway_connections:
            del gateway_connections[robot_id]


async def send_to_robot(robot_id: str, command: RobotCommand) -> bool:
    """ส่งคำสั่งไปยัง Robot ผ่าน Gateway"""
    if robot_id not in gateway_connections:
        logger.warning(f"⚠️ Robot not connected: {robot_id}")
        return False
    
    try:
        ws = gateway_connections[robot_id]
        await ws.send_json({
            "type": "command",
            "cmd": command.cmd,
            "params": command.params,
            "priority": command.priority
        })
        logger.info(f"📤 Command sent to {robot_id}: {command.cmd}")
        return True
    except Exception as e:
        logger.error(f"❌ Failed to send command: {e}")
        return False


async def broadcast_to_all_robots(command: RobotCommand):
    """ส่งคำสั่งไปทุก Robot"""
    for robot_id in list(gateway_connections.keys()):
        await send_to_robot(robot_id, command)


# ============ Status Endpoint ============

@router.get("/status")
async def get_status():
    """📊 ดูสถานะ Pipeline และ Robot connections"""
    return {
        "pipeline": "ready",
        "models": {
            "agent": "gemma3:12b-it-qat",
            "reasoning": "gemma3:27b-it-qat",
            "vlm": "gemma3n:e4b"
        },
        "connected_robots": list(gateway_connections.keys()),
        "robot_count": len(gateway_connections)
    }
