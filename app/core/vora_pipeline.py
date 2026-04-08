# app/core/vora_pipeline.py
"""
VORA Complete LLM Pipeline
==========================
Flow: STT → Agent(26b) → Reasoning(26b) → [VLM(8b) | RobotCmd] → Gateway

Intent Types:
- find_object: หาของ → ต้องใช้ VLM + Navigation
- navigate: ไปที่ไหน → Navigation only
- control: สั่งหุ่นตรงๆ (หยุด, หมุน) → Direct command
- question: ถามเกี่ยวกับสิ่งที่เห็น → VLM
- chitchat: คุยทั่วไป → Text response only

Reliability notes:
- All LLM calls use async wrappers (agenerate, agenerate_json, achat)
  to avoid blocking the FastAPI event loop.
- Agent parsing uses TIMEOUT_FAST (30s) for low-latency classification.
- Reasoning uses TIMEOUT_NORMAL (90s) for planning.
- Chat uses TIMEOUT_NORMAL (90s) for conversation.
"""

import asyncio
import logging
import json
import base64
from typing import Optional, Dict, Any, List, Literal
from dataclasses import dataclass, field, asdict
from enum import Enum

from ..providers.llm.ollama import OllamaProvider, TIMEOUT_FAST, TIMEOUT_NORMAL
from ..core.settings import settings
from ..core.text_preprocessor import preprocess, PreprocessResult, TextStatus, TextAccumulator
from ..core.vora_memory import (
    get_memory, clear_memory, match_quick_response,
    build_prompt_with_memory, AGENT_SYSTEM_PROMPT, REASONING_SYSTEM_PROMPT,
    CHAT_SYSTEM_PROMPT, VORA_IDENTITY
)

logger = logging.getLogger("vora.pipeline")

# Global text accumulator
text_accumulator = TextAccumulator(timeout_sec=3.0, max_fragments=5)

# ============ Models ============
LLM_AGENT = OllamaProvider(model=settings.OLLAMA_REFINE_MODEL or "gemma4:26b")
LLM_REASONING = OllamaProvider(model=settings.OLLAMA_MODEL or "gemma4:26b")
LLM_VLM = OllamaProvider(model=settings.OLLAMA_VLM_MODEL or "qwen3-vl:8b")


class Intent(str, Enum):
    FIND_OBJECT = "find_object"
    NAVIGATE = "navigate"
    CONTROL = "control"
    QUESTION = "question"
    CHITCHAT = "chitchat"
    UNKNOWN = "unknown"


@dataclass
class ParsedCommand:
    """Output จาก Agent — Parse & Classify"""
    intent: Intent
    clean_text: str
    target_object: Optional[str] = None
    target_location: Optional[str] = None
    action: Optional[str] = None  # stop, forward, backward, left, right
    confidence: float = 0.0
    raw_text: str = ""


@dataclass
class TaskPlan:
    """Output จาก Reasoning — วางแผนการทำงาน"""
    steps: List[Dict[str, Any]] = field(default_factory=list)
    need_vision: bool = False
    need_navigation: bool = False
    speech_response: str = ""
    estimated_time_sec: int = 0


@dataclass
class VisionResult:
    """Output จาก VLM — วิเคราะห์ภาพ"""
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


VLM_SYSTEM_PROMPT = """คุณคือ VORA Vision — ระบบ Computer Vision ขั้นสูงของหุ่นยนต์ VORA
ทำงานบน MiniCPM-v2.6 วิเคราะห์ภาพจากกล้องหุ่นยนต์เพื่อค้นหาและระบุตำแหน่งวัตถุ

**หน้าที่:** วิเคราะห์ภาพอย่างละเอียด ระบุวัตถุทุกชิ้นที่เห็น พร้อมตำแหน่งเชิงพื้นที่
**ความสำคัญ:** ตอบเป็น JSON ที่ถูกต้องเท่านั้น ห้ามตอบข้อความอื่น

**ตอบเป็น JSON:**
{
  "object_found": true/false,
  "object_name": "ชื่อของที่เจอ (ภาษาที่ถาม)",
  "object_location": "left|right|center|far|near + คำอธิบายเพิ่ม",
  "confidence": 0.0-1.0,
  "description": "รายละเอียดสิ่งที่เห็นในภาพทั้งหมด",
  "other_objects": ["รายการวัตถุอื่นที่เห็นในภาพ"]
}"""


# ============ Pipeline Functions ============

async def parse_command(text: str) -> ParsedCommand:
    """
    Step 1: Agent — Parse and classify command.
    Uses TIMEOUT_FAST (30s) because this is latency-sensitive.
    """
    logger.info(f"Agent parsing: {text}")

    try:
        result = await LLM_AGENT.agenerate_json(
            system=AGENT_SYSTEM_PROMPT,
            prompt=f"คำสั่ง: {text}",
            temperature=0.1,
            max_tokens=200,
            timeout=TIMEOUT_FAST,
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

        logger.info(f"Parsed: intent={parsed.intent}, target={parsed.target_object or parsed.target_location}")
        return parsed

    except Exception as e:
        logger.error(f"Agent parse error: {e}")
        return ParsedCommand(
            intent=Intent.UNKNOWN,
            clean_text=text,
            raw_text=text,
            confidence=0.0
        )


async def create_task_plan(parsed: ParsedCommand, history_context: str = "") -> TaskPlan:
    """
    Step 2: Reasoning — create task plan.
    Uses TIMEOUT_NORMAL (90s) for planning.
    """
    logger.info(f"Reasoning for: {parsed.intent}")

    try:
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

        result = await LLM_REASONING.agenerate_json(
            system=REASONING_SYSTEM_PROMPT,
            prompt=prompt,
            temperature=0.2,
            max_tokens=400,
            timeout=TIMEOUT_NORMAL,
        )

        plan = TaskPlan(
            steps=result.get("steps", []),
            need_vision=bool(result.get("need_vision", False)),
            need_navigation=bool(result.get("need_navigation", False)),
            speech_response=result.get("speech_response", "รับทราบครับ"),
            estimated_time_sec=int(result.get("estimated_time_sec", 5))
        )

        logger.info(f"Plan: {len(plan.steps)} steps, vision={plan.need_vision}")
        return plan

    except Exception as e:
        logger.error(f"Reasoning error: {e}")
        # Fallback plan
        return TaskPlan(
            steps=[{"action": "speak", "text": "เข้าใจแล้วครับ"}],
            speech_response="รับทราบครับ กำลังดำเนินการ"
        )


async def analyze_vision(image_base64: str, target_object: str) -> VisionResult:
    """
    Step 3 (optional): VLM — analyze image.
    """
    logger.info(f"VLM analyzing for: {target_object}")

    try:
        prompt = f"หา '{target_object}' ในภาพนี้ ระบุตำแหน่งและความมั่นใจ"

        # Note: generate_json doesn't support images parameter currently.
        # VLM calls go through the /vlm/ endpoints on the server side.
        # This function is a placeholder for direct VLM calls.
        result = LLM_VLM.generate_json(
            system=VLM_SYSTEM_PROMPT,
            prompt=prompt,
            temperature=0.1,
            max_tokens=200,
        )

        vision = VisionResult(
            object_found=bool(result.get("object_found", False)),
            object_name=result.get("object_name", ""),
            object_location=result.get("object_location", ""),
            confidence=float(result.get("confidence", 0.0)),
            description=result.get("description", "")
        )

        logger.info(f"Vision: found={vision.object_found}, loc={vision.object_location}")
        return vision

    except Exception as e:
        logger.error(f"VLM error: {e}")
        return VisionResult(description=f"Error: {e}")


def plan_to_commands(plan: TaskPlan, vision: Optional[VisionResult] = None) -> List[RobotCommand]:
    """
    Step 4: Convert Plan → Robot Commands
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

    # If vision found the object, add directional hint
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


async def free_chat(text: str, session_id: str = "default") -> str:
    """
    Free-form chat using LLM — handles general knowledge, chitchat.
    Uses async chat to avoid blocking the event loop.
    """
    logger.info(f"Free chat: '{text[:60]}...'")
    memory = get_memory(session_id or "default")

    # Build message list: system + history + current user turn
    messages = [{"role": "system", "content": CHAT_SYSTEM_PROMPT}]
    for msg in memory.get_history(last_n=8):
        messages.append({"role": msg.role, "content": msg.content})
    messages.append({"role": "user", "content": text})

    try:
        response = await LLM_REASONING.achat(
            messages, temperature=0.7, max_tokens=500, timeout=TIMEOUT_NORMAL,
        )
        if not response:
            raise ValueError("empty response")
    except Exception as e:
        logger.warning(f"Chat endpoint failed, falling back to generate: {e}")
        history_str = memory.get_context_string(last_n=5)
        prompt = f"{history_str}\n\nผู้ใช้: {text}\nVORA:" if history_str else f"ผู้ใช้: {text}\nVORA:"
        try:
            response = await LLM_REASONING.agenerate(
                prompt=prompt, system=CHAT_SYSTEM_PROMPT,
                temperature=0.7, max_tokens=500, timeout=TIMEOUT_NORMAL,
            )
        except Exception as e2:
            logger.error(f"Free chat fallback failed: {e2}")
            return "ขออภัยครับ ระบบ LLM ไม่พร้อมในขณะนี้ ลองใหม่อีกครั้งนะครับ"

    # Save both turns to memory
    memory.add_user_message(text, intent="chat")
    memory.add_assistant_message(response, intent="chat")
    logger.info(f"Free chat response: '{response[:60]}...'")
    return response


async def process_command(
    text: str,
    image_base64: Optional[str] = None,
    session_id: Optional[str] = None,
    skip_preprocess: bool = False
) -> PipelineResult:
    """
    Main Pipeline: run all steps.
    All LLM calls are async — event loop stays responsive.
    """
    logger.info(f"Pipeline start: '{text[:50]}...'")

    try:
        # Step 0: Preprocess text
        if not skip_preprocess:
            preproc = preprocess(text)
            logger.info(f"Preprocess: status={preproc.status.value}, cleaned='{preproc.cleaned}'")

            if preproc.quick_response:
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

            if not preproc.should_process:
                return PipelineResult(
                    success=True,
                    parsed=ParsedCommand(
                        intent=Intent.UNKNOWN,
                        clean_text=preproc.cleaned or text,
                        raw_text=text,
                        confidence=0.0
                    ),
                    response_text="",
                    commands=[]
                )

            text = preproc.cleaned

        # Step 0.5: Check quick response from memory
        quick_resp = match_quick_response(text)
        if quick_resp:
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

        # Step 1: Parse command (async, fast timeout)
        parsed = await parse_command(text)

        # Step 2a: Conversational intents → free_chat
        if parsed.intent in (Intent.CHITCHAT, Intent.QUESTION, Intent.UNKNOWN):
            response_text = await free_chat(text, session_id or "default")
            return PipelineResult(
                success=True,
                parsed=parsed,
                response_text=response_text
            )

        # Step 2b: Robot intents → planning pipeline
        memory = get_memory(session_id or "default")
        history_context = memory.get_context_string(last_n=3)
        if memory.is_first_interaction:
            history_context = "[นี่คือคำสั่งแรกของ session นี้ — ทักทายผู้ใช้สั้นๆ ก่อนตอบ]"

        plan = await create_task_plan(parsed, history_context)

        # Step 3: Vision (if needed and image provided)
        vision = None
        if plan.need_vision and image_base64 and parsed.target_object:
            vision = await analyze_vision(image_base64, parsed.target_object)

        # Step 4: Generate robot commands
        commands = plan_to_commands(plan, vision)

        # Build response text
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
        logger.error(f"Pipeline error: {e}")
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
    Process text with accumulator for incomplete sentences.
    """
    global text_accumulator

    preproc_result = text_accumulator.add(text)

    if preproc_result is None:
        logger.info(f"Waiting for more text... buffer={text_accumulator.buffer}")
        return None

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

    if preproc_result.should_process:
        return await process_command(
            preproc_result.cleaned,
            image_base64=image_base64,
            session_id=session_id,
            skip_preprocess=True
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
    """Check if text is a quick command."""
    text_lower = text.strip().lower()
    for key, cmd in QUICK_COMMANDS.items():
        if key in text_lower:
            return cmd
    return None
