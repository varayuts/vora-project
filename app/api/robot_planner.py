from typing import List, Optional, Literal, Dict, Any
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
import logging

from ..providers.llm.ollama import OllamaProvider
from ..core.settings import settings

router = APIRouter(prefix="/robot", tags=["RobotPlanner"])
logger = logging.getLogger(__name__)

# ----- Optimized Schemas -----

class Action(BaseModel):
    # รวมทุกอย่างไว้ใน 'cmd' และ 'params' เพื่อให้ง่ายต่อการเพิ่มฟีเจอร์ในอนาคตโดยไม่ต้องแก้ Schema
    cmd: Literal["move", "rotate", "goto", "stop", "search"]
    params: Dict[str, Any] = Field(
        default_factory=dict, 
        description="พารามิเตอร์ เช่น distance, angle, target, speed"
    )
    note: Optional[str] = None

class RobotPlanResponse(BaseModel):
    intent: Literal["navigation", "control", "exploration", "unknown"]
    plan: List[Action]
    speech: str = Field(..., description="ประโยคที่หุ่นยนต์จะพูด")
    need_vision: bool = False
    debug_reasoning: str

# ----- LLM Provider -----
# แนะนำให้ใช้โมเดลตัวเดียวกับ Refine (4B) เพราะเน้นโครงสร้าง JSON ไม่ต้องใช้ Reasoning สูงมาก
LLM_PLANNER = OllamaProvider(model=settings.OLLAMA_REFINE_MODEL or "gemma3:4b-it")

ROBOT_PLANNER_PROMPT = """
คุณคือ "VORA Motion Controller" หน้าที่ของคุณคือแปลงภาษาไทยให้เป็นคำสั่ง JSON สำหรับหุ่นยนต์ myAGV

[กติกาการคำนวณ]
- "เดินหน้า/ถอยหลัง": หน่วยเมตร (m) ถ้าสั่ง 'นิดหน่อย' = 0.3, 'มาก' = 1.0
- "เลี้ยว/หมุน": หน่วยองศา (deg) ซ้ายเป็นบวก (+), ขวาเป็นลบ (-)
- "ไปที่...": ใช้ cmd: "goto" และระบุชื่อสถานที่ใน target

[รูปแบบการตอบกลับ]
ต้องเป็น JSON ที่มีโครงสร้าง:
{
  "intent": "navigation|control|exploration|unknown",
  "plan": [
    {"cmd": "move", "params": {"distance": 1.0, "direction": "forward"}},
    {"cmd": "rotate", "params": {"angle": 90}},
    {"cmd": "goto", "params": {"target": "workstation"}}
  ],
  "speech": "ข้อความตอบกลับผู้ใช้",
  "need_vision": false,
  "debug_reasoning": "เหตุผลประกอบ"
}

[ตัวอย่าง]
สั่ง: "เดินหน้า 2 เมตรแล้วไปที่ตู้เก็บของ"
ตอบ: {
  "intent": "navigation",
  "plan": [
    {"cmd": "move", "params": {"distance": 2.0}},
    {"cmd": "goto", "params": {"target": "storage"}}
  ],
  "speech": "กำลังเดินหน้า 2 เมตรและมุ่งหน้าไปที่ตู้เก็บของครับ",
  "debug_reasoning": "multi-step navigation: move + goto"
}
"""

@router.post("/plan", response_model=RobotPlanResponse)
async def generate_plan(text: str, session_id: Optional[str] = None):
    try:
        # เรียก LLM เพื่อทำ Semantic Mapping
        data = LLM_PLANNER.generate_json(
            system=ROBOT_PLANNER_PROMPT,
            prompt=f"คำสั่งจากผู้ใช้: {text}",
            temperature=0.1
        )

        # จัดการข้อมูลให้อยู่ในรูปแบบ Response
        return RobotPlanResponse(
            intent=data.get("intent", "unknown"),
            plan=[Action(**a) for a in data.get("plan", [])],
            speech=data.get("speech", "รับทราบครับ"),
            need_vision=bool(data.get("need_vision", False)),
            debug_reasoning=data.get("debug_reasoning", "Parsed from LLM")
        )
    except Exception as e:
        logger.error(f"Planner Error: {e}")
        raise HTTPException(status_code=500, detail="ไม่สามารถสร้างแผนงานได้")