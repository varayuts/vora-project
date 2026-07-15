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
1. การเดิน/ถอย:
   - หน่วย: เมตร (m)
   - "นิดหน่อย" = 0.3m, "ปกติ" = 0.5m, "มาก" = 1.0m
   
2. การหมุน/เลี้ยว:
   - หน่วย: องศา (degrees)
   - ซ้าย = บวก (+), ขวา = ลบ (-)
   - "เลี้ยวซ้าย" = +90°, "เลี้ยวขวา" = -90°
   - "หมุนซ้าย" = +90°, "หมุนขวา" = -90°
   
3. การหมุนรอบตัว (สำคัญ!):
   - "หมุน 1 รอบ" / "หมุนรอบตัว" = 360°
   - "หมุนครึ่งรอบ" = 180°
   - "หมุนซ้าย 1 รอบ" = +360°
   - "หมุนขวา 1 รอบ" = -360°
   - "หมุนรอบตัวไปทางขวา" = -360°
   - "หมุนรอบตัวไปทางซ้าย" = +360°
   - "หมุน 2 รอบ" = ±720° (ดูทิศทาง)

4. ไปยังจุดหมาย:
   - ใช้ cmd: "goto" พร้อม target

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

[ตัวอย่างที่ถูกต้อง]
1. สั่ง: "หมุนขวา 1 รอบ"
   ตอบ: {"plan": [{"cmd": "rotate", "params": {"angle": -360}}]}
   
2. สั่ง: "หมุนรอบตัวเองไปทางซ้าย"
   ตอบ: {"plan": [{"cmd": "rotate", "params": {"angle": 360}}]}

3. สั่ง: "เลี้ยวซ้าย 2 รอบแล้วเลี้ยวขวา"
   ตอบ: {"plan": [
     {"cmd": "rotate", "params": {"angle": 720}},
     {"cmd": "rotate", "params": {"angle": -90}}
   ]}

4. สั่ง: "เดินหน้า 2 เมตรแล้วไปที่ตู้เก็บของ"
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

class RobotPlanRequest(BaseModel):
    text: str = Field(..., description="คำสั่งจากผู้ใช้")
    session_id: Optional[str] = None

@router.post("/plan", response_model=RobotPlanResponse)
async def generate_plan(req: RobotPlanRequest):
    try:
        # เรียก LLM เพื่อทำ Semantic Mapping
        data = LLM_PLANNER.generate_json(
            system=ROBOT_PLANNER_PROMPT,
            prompt=f"คำสั่งจากผู้ใช้: {req.text}",
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

