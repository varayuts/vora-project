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
    process_with_accumulator,
    parse_command, 
    create_task_plan,
    analyze_vision,
    get_quick_command,
    PipelineResult,
    RobotCommand,
    Intent,
    text_accumulator
)
from ..core.text_preprocessor import preprocess, TextStatus
from ..core.vora_memory import (
    get_memory, clear_memory, clear_all_memories, get_all_sessions,
    match_quick_response, VORA_IDENTITY
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


class PreprocessRequest(BaseModel):
    text: str = Field(..., description="ข้อความที่ต้องการ preprocess")


class PreprocessResponse(BaseModel):
    status: str
    original: str
    cleaned: str
    corrections: List[str]
    should_process: bool
    quick_response: Optional[str] = None


# ============ Endpoints ============

@router.post("/preprocess", response_model=PreprocessResponse)
async def preprocess_text(req: PreprocessRequest):
    """
    📝 Preprocess: Clean และ validate text ก่อนส่ง LLM
    
    ใช้สำหรับ:
    - กรอง filler words (เอ่อ, ครับ, คือ)
    - แก้ไข STT errors (ปลากา → เทปกาว)
    - ตรวจสอบว่าควรส่ง LLM หรือไม่
    """
    logger.info(f"📝 Preprocess: '{req.text}'")
    
    result = preprocess(req.text)
    
    return PreprocessResponse(
        status=result.status.value,
        original=result.original,
        cleaned=result.cleaned,
        corrections=result.corrections,
        should_process=result.should_process,
        quick_response=result.quick_response
    )


@router.post("/command", response_model=CommandResponse)
async def process_voice_command(req: CommandRequest):
    """
    🎯 Main Endpoint: รับคำสั่งเสียง → ประมวลผล → ส่งคำสั่งหุ่นยนต์
    
    Flow:
    0. Preprocess (clean, correct, validate)
    1. Agent (12b) parse คำสั่ง
    2. Reasoning (27b) วางแผน
    3. VLM (e4b) วิเคราะห์ภาพ (ถ้าจำเป็น)
    4. สร้าง Robot Commands
    """
    logger.info(f"📨 Command received: '{req.text}'")
    
    try:
        # Step 0: Preprocess first
        preproc = preprocess(req.text)
        logger.info(f"📋 Preprocess: status={preproc.status.value}, should_process={preproc.should_process}")
        
        # If quick response available (greeting, thanks)
        if preproc.quick_response:
            logger.info(f"⚡ Quick response: '{preproc.quick_response}'")
            return CommandResponse(
                success=True,
                intent="chitchat",
                clean_text=preproc.cleaned,
                response_text=preproc.quick_response,
                commands=[{
                    "cmd": "speak",
                    "params": {"text": preproc.quick_response},
                    "priority": 0
                }]
            )
        
        # If should not process (filler, too short, incomplete)
        if not preproc.should_process:
            logger.info(f"⏭️ Skipping: {preproc.status.value}")
            return CommandResponse(
                success=True,
                intent="skip",
                clean_text=preproc.cleaned,
                response_text="",  # ไม่ตอบอะไร
                commands=[]
            )
        
        # Use cleaned text
        cleaned_text = preproc.cleaned
        logger.info(f"✅ Using cleaned: '{cleaned_text}'")
        
        # ตรวจสอบ quick command (หยุด, เดินหน้า, etc.)
        quick_cmd = get_quick_command(cleaned_text)
        if quick_cmd:
            logger.info(f"⚡ Quick command detected: {quick_cmd.cmd}")
            return CommandResponse(
                success=True,
                intent="control",
                clean_text=cleaned_text,
                response_text=f"รับคำสั่ง{cleaned_text}ครับ",
                commands=[{
                    "cmd": quick_cmd.cmd,
                    "params": quick_cmd.params,
                    "priority": quick_cmd.priority
                }]
            )
        
        # Full pipeline (with skip_preprocess since already done)
        result = await process_command(
            text=cleaned_text,
            image_base64=req.image_base64,
            session_id=req.session_id,
            skip_preprocess=True
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
        "robot_count": len(gateway_connections),
        "active_sessions": get_all_sessions()
    }


# ============ Memory Endpoints ============

@router.get("/memory/{session_id}")
async def get_session_memory(session_id: str):
    """📝 ดึง conversation history ของ session"""
    memory = get_memory(session_id)
    return {
        "session_id": session_id,
        "stats": memory.get_stats(),
        "history": [msg.to_dict() for msg in memory.get_history()],
        "context": memory.get_context_string()
    }


@router.delete("/memory/{session_id}")
async def clear_session_memory(session_id: str):
    """🗑️ ล้าง memory ของ session"""
    clear_memory(session_id)
    return {
        "success": True,
        "message": f"Cleared memory for session: {session_id}"
    }


@router.delete("/memory")
async def clear_all_session_memories():
    """🗑️ ล้าง memory ทุก session"""
    clear_all_memories()
    return {
        "success": True,
        "message": "Cleared all session memories"
    }


@router.get("/identity")
async def get_vora_identity():
    """🤖 ดึงข้อมูล identity ของ VORA"""
    return {
        "name": "VORA",
        "full_name": "Voice Oriented Robotics Assistant",
        "thai_name": "โวร่า",
        "identity": VORA_IDENTITY
    }

