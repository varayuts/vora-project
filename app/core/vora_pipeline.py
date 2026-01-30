# app/core/vora_pipeline.py
"""
VORA Complete LLM Pipeline
==========================
Flow: STT → Agent(12b) → Reasoning(27b) → [VLM(e4b) | RobotCmd] → Gateway

Intent Types:
- find_object: หาของ → ต้องใช้ VLM + Navigation
- navigate: ไปที่ไหน → Navigation only
- control: สั่งหุ่นตรงๆ (หยุด, หมุน) → Direct command
- question: ถามเกี่ยวกับสิ่งที่เห็น → VLM
- chitchat: คุยทั่วไป → Text response only
"""

import asyncio
import logging
import json
import base64
from typing import Optional, Dict, Any, List, Literal
from dataclasses import dataclass, field, asdict
from enum import Enum

from ..providers.llm.ollama import OllamaProvider
from ..core.settings import settings
from ..core.text_preprocessor import preprocess, PreprocessResult, TextStatus, TextAccumulator
from ..core.vora_memory import (
    get_memory, clear_memory, match_quick_response,
    build_prompt_with_memory, AGENT_SYSTEM_PROMPT, REASONING_SYSTEM_PROMPT,
    VORA_IDENTITY
)

logger = logging.getLogger("vora.pipeline")

# Global text accumulator
text_accumulator = TextAccumulator(timeout_sec=3.0, max_fragments=5)

# ============ Models ============
LLM_AGENT = OllamaProvider(model=settings.OLLAMA_REFINE_MODEL or "gemma3:12b-it-qat")
LLM_REASONING = OllamaProvider(model=settings.OLLAMA_MODEL or "gemma3:27b-it-qat")
LLM_VLM = OllamaProvider(model=settings.OLLAMA_VLM_MODEL or "gemma3n:e4b")


class Intent(str, Enum):
    FIND_OBJECT = "find_object"
    NAVIGATE = "navigate"
    CONTROL = "control"
    QUESTION = "question"
    CHITCHAT = "chitchat"
    UNKNOWN = "unknown"


@dataclass
class ParsedCommand:
    """Output จาก Agent (12b) - Parse & Classify"""
    intent: Intent
    clean_text: str
    target_object: Optional[str] = None
    target_location: Optional[str] = None
    action: Optional[str] = None  # stop, forward, backward, left, right
    confidence: float = 0.0
    raw_text: str = ""


@dataclass
class TaskPlan:
    """Output จาก Reasoning (27b) - วางแผนการทำงาน"""
    steps: List[Dict[str, Any]] = field(default_factory=list)
    need_vision: bool = False
    need_navigation: bool = False
    speech_response: str = ""
    estimated_time_sec: int = 0


@dataclass
class VisionResult:
    """Output จาก VLM (e4b) - วิเคราะห์ภาพ"""
    object_found: bool = False
    object_name: str = ""
    object_location: str = ""  # left, right, center, far, near
    confidence: float = 0.0
    description: str = ""
    bounding_box: Optional[Dict[str, int]] = None  # x, y, w, h


@dataclass
class RobotCommand:
    """คำสั่งส่งไป Gateway → Robot"""
    cmd: Literal["move", "rotate", "goto", "stop", "search", "speak", "camera"]
    params: Dict[str, Any] = field(default_factory=dict)
    priority: int = 0  # 0=normal, 1=high, 2=emergency


@dataclass
class PipelineResult:
    """ผลลัพธ์รวมจาก Pipeline"""
    success: bool
    parsed: Optional[ParsedCommand] = None
    plan: Optional[TaskPlan] = None
    vision: Optional[VisionResult] = None
    commands: List[RobotCommand] = field(default_factory=list)
    response_text: str = ""
    error: Optional[str] = None


# ============ Lab Context ============
LAB_OBJECTS = [
    "ไขควง", "กรรไกร", "กุญแจ", "คีม", "ยางลบ", 
    "สายไฟ", "ปลั๊กไฟ", "เทปกาว", "มัลติมิเตอร์",
    "บอร์ด Arduino", "Raspberry Pi", "สาย USB", "หัวแร้ง", "ปากกา"
]

LAB_LOCATIONS = [
    "โต๊ะทำงาน", "ตู้เก็บของ", "ประตูหน้า", "แท่นชาร์จ",
    "มุมห้อง", "หน้าต่าง", "ชั้นวางของ", "หน้าห้อง", "หลังห้อง"
]


# ============ Agent Prompts (imported from vora_memory) ============
# AGENT_SYSTEM_PROMPT และ REASONING_SYSTEM_PROMPT 
# import จาก vora_memory.py แล้ว

VLM_SYSTEM_PROMPT = """คุณคือ VORA Vision - ระบบมองเห็นของหุ่นยนต์

**หน้าที่:** วิเคราะห์ภาพและหาสิ่งของที่ต้องการ

**ตอบเป็น JSON:**
{
  "object_found": true/false,
  "object_name": "ชื่อของที่เจอ",
  "object_location": "left|right|center|far|near",
  "confidence": 0.0-1.0,
  "description": "รายละเอียดสิ่งที่เห็น"
}"""


# ============ Pipeline Functions ============

async def parse_command(text: str) -> ParsedCommand:
    """
    Step 1: Agent (12b) - Parse และ Classify คำสั่ง
    """
    logger.info(f"🔍 Agent parsing: {text}")
    
    try:
        result = LLM_AGENT.generate_json(
            system=AGENT_SYSTEM_PROMPT,
            prompt=f"คำสั่ง: {text}",
            temperature=0.1,
            max_tokens=200
        )
        
        intent_str = result.get("intent", "unknown")
        try:
            intent = Intent(intent_str)
        except ValueError:
            intent = Intent.UNKNOWN
        
        parsed = ParsedCommand(
            intent=intent,
            clean_text=result.get("clean_text", text),
            target_object=result.get("target_object"),
            target_location=result.get("target_location"),
            action=result.get("action"),
            confidence=float(result.get("confidence", 0.5)),
            raw_text=text
        )
        
        logger.info(f"✅ Parsed: intent={parsed.intent}, target={parsed.target_object or parsed.target_location}")
        return parsed
        
    except Exception as e:
        logger.error(f"❌ Agent parse error: {e}")
        return ParsedCommand(
            intent=Intent.UNKNOWN,
            clean_text=text,
            raw_text=text,
            confidence=0.0
        )


async def create_task_plan(parsed: ParsedCommand, history_context: str = "") -> TaskPlan:
    """
    Step 2: Reasoning (27b) - วางแผนการทำงาน
    
    Args:
        parsed: ParsedCommand จาก Agent
        history_context: ประวัติการสนทนา (optional)
    """
    logger.info(f"🧠 Reasoning for: {parsed.intent}")
    
    try:
        # Build prompt with history context
        prompt_parts = []
        
        if history_context:
            prompt_parts.append(history_context)
            prompt_parts.append("")
        
        prompt_parts.append(f"""คำสั่งที่ parse แล้ว:
- Intent: {parsed.intent.value}
- Clean text: {parsed.clean_text}
- Target object: {parsed.target_object or 'ไม่ระบุ'}
- Target location: {parsed.target_location or 'ไม่ระบุ'}
- Action: {parsed.action or 'ไม่ระบุ'}

วางแผนการทำงาน:""")
        
        prompt = "\n".join(prompt_parts)

        result = LLM_REASONING.generate_json(
            system=REASONING_SYSTEM_PROMPT,
            prompt=prompt,
            temperature=0.2,
            max_tokens=400
        )
        
        plan = TaskPlan(
            steps=result.get("steps", []),
            need_vision=bool(result.get("need_vision", False)),
            need_navigation=bool(result.get("need_navigation", False)),
            speech_response=result.get("speech_response", "รับทราบครับ"),
            estimated_time_sec=int(result.get("estimated_time_sec", 5))
        )
        
        logger.info(f"✅ Plan: {len(plan.steps)} steps, vision={plan.need_vision}")
        return plan
        
    except Exception as e:
        logger.error(f"❌ Reasoning error: {e}")
        # Fallback plan
        return TaskPlan(
            steps=[{"action": "speak", "text": "เข้าใจแล้วครับ"}],
            speech_response="รับทราบครับ กำลังดำเนินการ"
        )


async def analyze_vision(image_base64: str, target_object: str) -> VisionResult:
    """
    Step 3 (optional): VLM (e4b) - วิเคราะห์ภาพ
    """
    logger.info(f"👁️ VLM analyzing for: {target_object}")
    
    try:
        prompt = f"หา '{target_object}' ในภาพนี้ ระบุตำแหน่งและความมั่นใจ"
        
        # Ollama VLM format
        result = LLM_VLM.generate_json(
            system=VLM_SYSTEM_PROMPT,
            prompt=prompt,
            images=[image_base64],  # Base64 encoded image
            temperature=0.1,
            max_tokens=200
        )
        
        vision = VisionResult(
            object_found=bool(result.get("object_found", False)),
            object_name=result.get("object_name", ""),
            object_location=result.get("object_location", ""),
            confidence=float(result.get("confidence", 0.0)),
            description=result.get("description", "")
        )
        
        logger.info(f"✅ Vision: found={vision.object_found}, loc={vision.object_location}")
        return vision
        
    except Exception as e:
        logger.error(f"❌ VLM error: {e}")
        return VisionResult(description=f"Error: {e}")


def plan_to_commands(plan: TaskPlan, vision: Optional[VisionResult] = None) -> List[RobotCommand]:
    """
    Step 4: แปลง Plan → Robot Commands
    """
    commands = []
    
    for step in plan.steps:
        action = step.get("action", "")
        
        if action == "speak":
            commands.append(RobotCommand(
                cmd="speak",
                params={"text": step.get("text", "")}
            ))
            
        elif action == "search":
            commands.append(RobotCommand(
                cmd="search",
                params={"target": step.get("target", "")}
            ))
            
        elif action == "navigate":
            commands.append(RobotCommand(
                cmd="goto",
                params={"target": step.get("target", "")}
            ))
            
        elif action == "move":
            commands.append(RobotCommand(
                cmd="move",
                params={
                    "distance": step.get("distance", 0.5),
                    "direction": step.get("direction", "forward")
                }
            ))
            
        elif action == "rotate":
            commands.append(RobotCommand(
                cmd="rotate",
                params={"angle": step.get("angle", 0)}
            ))
            
        elif action == "stop":
            commands.append(RobotCommand(
                cmd="stop",
                params={},
                priority=2  # Emergency
            ))
    
    # ถ้ามี vision result และเจอของ ให้เพิ่ม command ชี้ทิศ
    if vision and vision.object_found:
        loc = vision.object_location
        if loc == "left":
            commands.append(RobotCommand(cmd="rotate", params={"angle": 30}))
        elif loc == "right":
            commands.append(RobotCommand(cmd="rotate", params={"angle": -30}))
        
        commands.append(RobotCommand(
            cmd="speak",
            params={"text": f"เจอ{vision.object_name}แล้วครับ อยู่ทาง{loc}"}
        ))
    
    return commands


async def process_command(
    text: str,
    image_base64: Optional[str] = None,
    session_id: Optional[str] = None,
    skip_preprocess: bool = False
) -> PipelineResult:
    """
    Main Pipeline: รันทุกขั้นตอน
    
    Args:
        text: คำสั่งจาก STT
        image_base64: ภาพ (optional)
        session_id: session ID
        skip_preprocess: ข้าม preprocessing (สำหรับ text ที่ clean แล้ว)
    """
    logger.info(f"🚀 Pipeline start: '{text[:50]}...'")
    
    try:
        # Step 0: Preprocess text (ถ้าไม่ skip)
        if not skip_preprocess:
            preproc = preprocess(text)
            logger.info(f"📋 Preprocess: status={preproc.status.value}, cleaned='{preproc.cleaned}'")
            
            # Handle quick response (greetings, thanks, etc.)
            if preproc.quick_response:
                logger.info(f"⚡ Quick response: '{preproc.quick_response}'")
                return PipelineResult(
                    success=True,
                    parsed=ParsedCommand(
                        intent=Intent.CHITCHAT,
                        clean_text=preproc.cleaned,
                        raw_text=text,
                        confidence=1.0
                    ),
                    commands=[RobotCommand(cmd="speak", params={"text": preproc.quick_response})],
                    response_text=preproc.quick_response
                )
            
            # Skip if not processable
            if not preproc.should_process:
                logger.info(f"⏭️ Skipping: {preproc.status.value}")
                return PipelineResult(
                    success=True,
                    parsed=ParsedCommand(
                        intent=Intent.UNKNOWN,
                        clean_text=preproc.cleaned or text,
                        raw_text=text,
                        confidence=0.0
                    ),
                    response_text="",  # ไม่ตอบอะไร
                    commands=[]
                )
            
            # Use cleaned text
            text = preproc.cleaned
            logger.info(f"✅ Using cleaned text: '{text}'")
        
        # Step 0.5: Check quick response from memory (ชื่ออะไร, ทำอะไรได้, etc.)
        quick_resp = match_quick_response(text)
        if quick_resp:
            logger.info(f"⚡ Quick response matched: '{quick_resp[:30]}...'")
            
            # Save to memory
            memory = get_memory(session_id or "default")
            memory.add_user_message(text, intent="question")
            memory.add_assistant_message(quick_resp, intent="question")
            
            return PipelineResult(
                success=True,
                parsed=ParsedCommand(
                    intent=Intent.CHITCHAT,
                    clean_text=text,
                    raw_text=text,
                    confidence=1.0
                ),
                commands=[RobotCommand(cmd="speak", params={"text": quick_resp})],
                response_text=quick_resp
            )
        
        # Step 1: Parse command
        parsed = await parse_command(text)
        
        if parsed.intent == Intent.UNKNOWN:
            return PipelineResult(
                success=False,
                parsed=parsed,
                response_text="ขออภัยครับ ไม่เข้าใจคำสั่ง",
                error="Unknown intent"
            )
        
        # Step 2: Create plan (with memory context)
        memory = get_memory(session_id or "default")
        history_context = memory.get_context_string(last_n=3)
        plan = await create_task_plan(parsed, history_context)
        
        # Step 3: Vision (if needed and image provided)
        vision = None
        if plan.need_vision and image_base64 and parsed.target_object:
            vision = await analyze_vision(image_base64, parsed.target_object)
        
        # Step 4: Generate robot commands
        commands = plan_to_commands(plan, vision)
        
        # สร้าง response text
        response_text = plan.speech_response
        if vision and vision.object_found:
            response_text = f"เจอ{vision.object_name}แล้วครับ! {vision.description}"
        elif vision and not vision.object_found:
            response_text = f"ยังไม่เจอ{parsed.target_object}ครับ กำลังค้นหาต่อ..."
        
        # Step 5: Save to memory
        memory.add_user_message(text, intent=parsed.intent.value)
        memory.add_assistant_message(response_text, intent=parsed.intent.value)
        
        return PipelineResult(
            success=True,
            parsed=parsed,
            plan=plan,
            vision=vision,
            commands=commands,
            response_text=response_text
        )
        
    except Exception as e:
        logger.error(f"❌ Pipeline error: {e}")
        return PipelineResult(
            success=False,
            error=str(e),
            response_text="เกิดข้อผิดพลาดในระบบครับ"
        )


async def process_with_accumulator(
    text: str,
    image_base64: Optional[str] = None,
    session_id: Optional[str] = None
) -> Optional[PipelineResult]:
    """
    Process text พร้อม accumulator สำหรับ incomplete sentences
    
    Returns:
        PipelineResult ถ้าพร้อม process, None ถ้ายังรอ text เพิ่ม
    """
    global text_accumulator
    
    preproc_result = text_accumulator.add(text)
    
    if preproc_result is None:
        logger.info(f"⏳ Waiting for more text... buffer={text_accumulator.buffer}")
        return None
    
    # Quick response
    if preproc_result.quick_response:
        return PipelineResult(
            success=True,
            parsed=ParsedCommand(
                intent=Intent.CHITCHAT,
                clean_text=preproc_result.cleaned,
                raw_text=preproc_result.original,
                confidence=1.0
            ),
            commands=[RobotCommand(cmd="speak", params={"text": preproc_result.quick_response})],
            response_text=preproc_result.quick_response
        )
    
    # Process cleaned text
    if preproc_result.should_process:
        return await process_command(
            preproc_result.cleaned,
            image_base64=image_base64,
            session_id=session_id,
            skip_preprocess=True  # Already preprocessed
        )
    
    return None


# ============ Quick Commands ============
QUICK_COMMANDS = {
    "หยุด": RobotCommand(cmd="stop", params={}, priority=2),
    "เดินหน้า": RobotCommand(cmd="move", params={"distance": 0.5, "direction": "forward"}),
    "ถอยหลัง": RobotCommand(cmd="move", params={"distance": 0.5, "direction": "backward"}),
    "หมุนซ้าย": RobotCommand(cmd="rotate", params={"angle": 90}),
    "หมุนขวา": RobotCommand(cmd="rotate", params={"angle": -90}),
}

def get_quick_command(text: str) -> Optional[RobotCommand]:
    """ตรวจสอบว่าเป็น quick command ไหม"""
    text_lower = text.strip().lower()
    for key, cmd in QUICK_COMMANDS.items():
        if key in text_lower:
            return cmd
    return None
