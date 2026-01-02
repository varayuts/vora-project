# app/api/robot_planner.py
from typing import List, Optional, Literal
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..providers.llm.ollama import OllamaProvider
from ..core.settings import settings

# ----- Router -----
router = APIRouter(prefix="/robot", tags=["RobotPlanner"])

# ----- Type aliases -----
Level = Literal["easy", "normal", "hard"]
Intent = Literal["move", "navigate", "search", "unknown"]
ActionType = Literal["move", "rotate", "goto"]
Direction = Literal["forward", "backward", "left", "right", "none"]

# ----- Schemas -----
class RobotAction(BaseModel):
    type: ActionType = Field(..., description="ประเภทของ action: move / rotate / goto")
    direction: Direction = Field(..., description="ทิศทาง: forward/backward/left/right/none")
    duration_sec: float = Field(0.0, description="เวลา (วินาที) ถ้าใช้คำสั่งแบบตามเวลา")
    distance_m: float = Field(0.0, description="ระยะทาง (เมตร) ถ้าใช้คำสั่งแบบตามระยะ")
    angle_deg: float = Field(0.0, description="มุมหมุน (องศา) ถ้าเป็น rotate")
    target_location: str = Field("", description="ชื่อจุดหมายในแผนที่ เช่น front_of_room")
    note: str = Field("", description="คำอธิบายเพิ่มเติมของ action นั้น ๆ")

class RobotPlanRequest(BaseModel):
    text: str = Field(..., description="คำสั่งที่ถอดมาจาก Whisper (ภาษาไทย)")
    lang: str = Field("th", description="lang hint เช่น th/en/auto")
    level_hint: Optional[Level] = Field(
        None,
        description="optional: แนะนำว่าอยากให้ตีเป็น easy / normal / hard"
    )

class RobotPlanResponse(BaseModel):
    level: Level
    intent: Intent
    actions: List[RobotAction]
    need_vision: bool = False
    natural_response: str
    debug: str

# ----- LLM Provider -----
LLM_PLANNER = OllamaProvider()

# ----- SYSTEM PROMPT สำหรับ Robot Planner -----
ROBOT_PLANNER_SYSTEM_PROMPT = """
คุณคือ "VORA Robot Planner" หน้าที่ของคุณคือแปลงคำสั่งเสียงภาษาไทย (ที่ถูกถอดโดย Whisper แล้ว)
ให้กลายเป็นชุดคำสั่งควบคุมหุ่นยนต์เคลื่อนที่ (mobile robot)

กติกา:
1) ตอบกลับเป็น JSON เท่านั้น ห้ามมีข้อความอื่นนอกเหนือจาก JSON
2) โครงสร้าง JSON ต้องมีคีย์ดังนี้เท่านั้น:
   - level: "easy" | "normal" | "hard"
   - intent: "move" | "navigate" | "search" | "unknown"
   - actions: รายการของ action แต่ละตัว มีฟิลด์:
       {
         "type": "move" | "rotate" | "goto",
         "direction": "forward" | "backward" | "left" | "right" | "none",
         "duration_sec": number,
         "distance_m": number,
         "angle_deg": number,
         "target_location": string,
         "note": string
       }
   - need_vision: true/false
   - natural_response: string (ประโยคที่หุ่นจะพูดกับผู้ใช้)
   - debug: string (อธิบายสั้น ๆ ว่าเข้าใจคำสั่งว่าอะไร)

3) การกำหนด level:
   - "easy" = คำสั่งเคลื่อนที่ง่าย ๆ สั้น ๆ เช่น ไปข้างหน้า หันซ้าย หันขวา ถอยหลัง พร้อมกำหนดเวลา/ระยะง่าย ๆ
   - "normal" = มีหลายขั้นตอน หรือมีเป้าหมายชื่อเฉพาะในแผนที่ เช่น ไปหน้าห้อง แล้วหันซ้ายเช็คทาง
   - "hard" = ต้องใช้กล้อง/ค้นหาวัตถุ เช่น หาของ สำรวจพื้นที่ (ตอนนี้ยังไม่ต้องเน้นมาก ให้ใช้เมื่อคำสั่งชัดเจนว่าต้องใช้กล้อง)

4) การกำหนด intent:
   - แค่เคลื่อนที่/หมุน = "move"
   - ไปยังตำแหน่งชื่อเฉพาะ เช่น หน้าห้อง หน้าโต๊ะ = "navigate"
   - หาของ/สำรวจพื้นที่ = "search"
   - ไม่เข้าใจ = "unknown"

5) การตีความตัวเลขจากคำพูด (โดยประมาณ):
   - "ไปข้างหน้าหน่อย" = distance_m ≈ 0.5
   - "ไปข้างหน้าหนึ่งเมตร" = distance_m = 1.0
   - "ไปข้างหน้าสองเมตร" = distance_m = 2.0
   - "ไปข้างหน้า 2 วิ" = duration_sec = 2.0
   - "หมุนซ้าย" = angle_deg ≈ 90
   - "หมุนขวา" = angle_deg ≈ -90 (หรือ 90 แล้ว direction = right ตามที่ระบุ)

6) ตัวอย่าง mapping:
   - "ไปข้างหน้า 2 วิ แล้วหันซ้าย"
     -> level = "easy", intent = "move"
     -> actions = [
          {type="move", direction="forward", duration_sec=2.0, distance_m=0, angle_deg=0, target_location="", note="ไปข้างหน้า 2 วินาที"},
          {type="rotate", direction="left", duration_sec=0, distance_m=0, angle_deg=90, target_location="", note="หมุนซ้ายประมาณ 90 องศา"}
        ]

   - "ไปข้างหน้าสามเมตรแล้วหยุดที่หน้าห้อง"
     -> level = "normal", intent = "navigate"
     -> actions = [
          {type="move",  direction="forward", distance_m=3.0, duration_sec=0, angle_deg=0, target_location="", note="เดินหน้าประมาณ 3 เมตร"},
          {type="goto",  direction="none",    distance_m=0.0, duration_sec=0, angle_deg=0, target_location="front_of_room", note="ไปยังตำแหน่ง front_of_room ในแผนที่"}
        ]

7) ช่อง natural_response:
   - ให้ตอบแบบสุภาพสั้น ๆ ว่าหุ่นจะทำอะไร เช่น "โอเค ผมจะเดินหน้า 2 วินาทีแล้วหมุนซ้ายให้นะ"

8) ช่อง debug:
   - อธิบาย logic แบบสั้น ๆ ภาษาไทยเช่น "easy: เดินหน้า 2 วิ แล้วหมุนซ้าย 90 องศา"

9) ถ้าไม่เข้าใจ ให้ตั้ง:
   - level = "easy"
   - intent = "unknown"
   - actions = []
   - need_vision = false
   - natural_response = "ผมยังไม่เข้าใจคำสั่ง ช่วยพูดใหม่อีกครั้งได้ไหมครับ"
   - debug = "unknown command"

โฟกัสตอนนี้ที่ easy และ normal ถ้าไม่จำเป็น ไม่ต้องใช้ hard
"""

# ----- Endpoint -----
@router.post("/plan", response_model=RobotPlanResponse)
def plan_from_text(req: RobotPlanRequest) -> RobotPlanResponse:
    """
    แปลงข้อความ (ที่ได้จาก Whisper) ให้กลายเป็นแผนคำสั่งหุ่นยนต์ด้วย LLM
    """
    try:
        # เตรียม prompt ฝั่ง user
        user_prompt_lines = [
            f"lang_hint={req.lang or 'th'}",
            f"text={req.text.strip()}",
        ]
        if req.level_hint:
            user_prompt_lines.append(f"level_hint={req.level_hint}")
        user_prompt = "\n".join(user_prompt_lines) + "\n"

        # เรียก LLM แบบ JSON
        data = LLM_PLANNER.generate_json(
            system=ROBOT_PLANNER_SYSTEM_PROMPT,
            prompt=user_prompt,
            temperature=0.1,
            top_p=0.9,
            max_tokens=settings.OLLAMA_JSON_MAX_TOKENS,
        )

        # ดึงค่าออกมาแบบมี default เผื่อ LLM ลืม field
        level = data.get("level", "easy")
        intent = data.get("intent", "move")
        need_vision = bool(data.get("need_vision", False))
        natural_response = data.get("natural_response") or ""
        debug = data.get("debug") or ""

        raw_actions = data.get("actions") or []
        actions: List[RobotAction] = []

        for a in raw_actions:
            # กัน LLM ส่ง field แปลก ๆ มา → map ใส่ RobotAction
            action = RobotAction(
                type=a.get("type", "move"),
                direction=a.get("direction", "none"),
                duration_sec=float(a.get("duration_sec", 0.0) or 0.0),
                distance_m=float(a.get("distance_m", 0.0) or 0.0),
                angle_deg=float(a.get("angle_deg", 0.0) or 0.0),
                target_location=a.get("target_location", "") or "",
                note=a.get("note", "") or "",
            )
            actions.append(action)

        # ถ้า LLM ไม่ส่ง action มาเลย แต่ intent ไม่ใช่ unknown
        if not actions and intent != "unknown":
            debug = (debug + " | no_actions_generated").strip()

        return RobotPlanResponse(
            level=level, intent=intent,
            actions=actions,
            need_vision=need_vision,
            natural_response=natural_response,
            debug=debug,
        )

    except Exception as e:
        raise HTTPException(500, f"Robot planner error: {e}")
