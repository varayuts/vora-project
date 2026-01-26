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

logger = logging.getLogger("vora.pipeline")

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
    "บอร์ด Arduino", "Raspberry Pi", "สาย USB", "หัวแร้ง"
]

LAB_LOCATIONS = [
    "โต๊ะทำงาน", "ตู้เก็บของ", "ประตูหน้า", "แท่นชาร์จ",
    "มุมห้อง", "หน้าต่าง", "ชั้นวางของ"
]


# ============ Agent Prompts ============
AGENT_SYSTEM_PROMPT = f"""คุณคือ VORA Agent - ระบบวิเคราะห์คำสั่งเสียงสำหรับหุ่นยนต์

**หน้าที่:** แยกประเภทคำสั่งและดึงข้อมูลสำคัญ

**ประเภทคำสั่ง (intent):**
- find_object: หาสิ่งของ (เช่น "หาไขควง", "ช่วยหากุญแจ")
- navigate: ไปยังสถานที่ (เช่น "ไปที่โต๊ะ", "กลับแท่นชาร์จ")
- control: สั่งการหุ่นยนต์โดยตรง (เช่น "หยุด", "หมุนซ้าย", "เดินหน้า")
- question: ถามเกี่ยวกับสิ่งที่เห็น (เช่น "นี่คืออะไร", "เห็นอะไรบ้าง")
- chitchat: สนทนาทั่วไป

**สิ่งของในห้องแล็บ:** {", ".join(LAB_OBJECTS)}
**สถานที่:** {", ".join(LAB_LOCATIONS)}

**ตอบเป็น JSON เท่านั้น:**
{{
  "intent": "find_object|navigate|control|question|chitchat",
  "clean_text": "ข้อความที่แก้คำผิดแล้ว",
  "target_object": "ชื่อสิ่งของ (ถ้ามี)",
  "target_location": "ชื่อสถานที่ (ถ้ามี)",
  "action": "stop|forward|backward|left|right (สำหรับ control)",
  "confidence": 0.0-1.0
}}"""

REASONING_SYSTEM_PROMPT = """คุณคือ VORA Brain - ระบบวางแผนการทำงานของหุ่นยนต์

**หน้าที่:** รับคำสั่งที่ parsed แล้ว และวางแผนขั้นตอนการทำงาน

**ตอบเป็น JSON:**
{
  "steps": [
    {"action": "speak", "text": "ข้อความที่พูด"},
    {"action": "search", "target": "ชื่อของ"},
    {"action": "navigate", "target": "สถานที่"},
    {"action": "move", "distance": 1.0, "direction": "forward"},
    {"action": "rotate", "angle": 90}
  ],
  "need_vision": true/false,
  "need_navigation": true/false,
  "speech_response": "ประโยคตอบกลับผู้ใช้",
  "estimated_time_sec": 10
}"""

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


async def create_task_plan(parsed: ParsedCommand) -> TaskPlan:
    """
    Step 2: Reasoning (27b) - วางแผนการทำงาน
    """
    logger.info(f"🧠 Reasoning for: {parsed.intent}")
    
    try:
        prompt = f"""คำสั่งที่ parse แล้ว:
- Intent: {parsed.intent.value}
- Clean text: {parsed.clean_text}
- Target object: {parsed.target_object or 'ไม่ระบุ'}
- Target location: {parsed.target_location or 'ไม่ระบุ'}
- Action: {parsed.action or 'ไม่ระบุ'}

วางแผนการทำงาน:"""

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
    session_id: Optional[str] = None
) -> PipelineResult:
    """
    Main Pipeline: รันทุกขั้นตอน
    """
    logger.info(f"🚀 Pipeline start: {text[:50]}...")
    
    try:
        # Step 1: Parse command
        parsed = await parse_command(text)
        
        if parsed.intent == Intent.UNKNOWN:
            return PipelineResult(
                success=False,
                parsed=parsed,
                response_text="ขออภัยครับ ไม่เข้าใจคำสั่ง",
                error="Unknown intent"
            )
        
        # Step 2: Create plan
        plan = await create_task_plan(parsed)
        
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
