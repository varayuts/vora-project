from typing import List, Optional
import math, re
from fastapi import APIRouter
from pydantic import BaseModel, Field

# NOTE: ถ้าใน main.py ใส่ prefix="/plan" ไว้แล้ว ให้เปลี่ยนเป็น router = APIRouter(tags=["Planner"])
router = APIRouter(prefix="/plan", tags=["Planner"])

class PlanIn(BaseModel):
    text: str = Field("", description="Navigation command")
    lang: Optional[str] = "th"
    max_waypoints: int = Field(8, ge=1, le=64)

class Waypoint(BaseModel):
    x: float
    y: float
    theta: float = 0.0

class PlanOut(BaseModel):
    waypoints: List[Waypoint] = []
    note: Optional[str] = None

UNIT_PAT = re.compile(r"(-?\d+(?:\.\d+)?)\s*(เมตร|m|cm|เซน(?:ติเมตร)?)", re.I)
DEG_PAT  = re.compile(r"(-?\d+(?:\.\d+)?)\s*(องศา|degree|deg)", re.I)

def _dist_m(s: str) -> Optional[float]:
    m = UNIT_PAT.search(s)
    if not m:
        n = re.search(r"(-?\d+(?:\.\d+)?)", s)
        return float(n.group(1)) if n else None
    v = float(m.group(1)); unit = m.group(2).lower()
    return v/100.0 if unit in ("cm","เซน","เซนติเมตร") else v

def _angle_rad(s: str) -> Optional[float]:
    m = DEG_PAT.search(s)
    if m:
        return math.radians(float(m.group(1)))
    if re.search(r"(หัน|หมุน|turn)", s, re.I) and re.search(r"(ซ้าย|left|ขวา|right)", s, re.I):
        return math.radians(90 if re.search(r"(ซ้าย|left)", s, re.I) else -90)
    return None

def _has_turn_left(s: str) -> bool:
    return bool(re.search(r"(หัน|หมุน|turn).*(ซ้าย|left)", s, re.I))

def _has_turn_right(s: str) -> bool:
    return bool(re.search(r"(หัน|หมุน|turn).*(ขวา|right)", s, re.I))

def _move_dir(s: str) -> Optional[str]:
    t = s.lower()
    if re.search(r"(ไปหน้า|เดินหน้า|ตรงไป|forward|\bgo\b)", t): return "forward"
    if re.search(r"(ถอย|ถอยหลัง|backward|\bback\b)", t): return "back"
    if re.search(r"(เลี้ยวซ้าย|ไปซ้าย|ซ้าย|left)", t): return "left"
    if re.search(r"(เลี้ยวขวา|ไปขวา|ขวา|right)", t): return "right"
    return None

@router.post("/plan_from_text", response_model=PlanOut)
async def plan_from_text(inp: PlanIn) -> PlanOut:
    txt = (inp.text or "").strip()
    if not txt:
        return PlanOut(waypoints=[], note="empty")

    x = 0.0
    y = 0.0
    yaw = 0.0
    wps: List[Waypoint] = []

    def push(dx: float = 0.0, dy: float = 0.0, dth: float = 0.0):
        nonlocal x, y, yaw, wps
        rx = math.cos(yaw) * dx - math.sin(yaw) * dy
        ry = math.sin(yaw) * dx + math.cos(yaw) * dy
        x += rx
        y += ry
        yaw += dth
        wps.append(Waypoint(x=x, y=y, theta=yaw))

    # แยกวลีด้วย "แล้ว", คอมมา, ;, .
    clauses = [c.strip() for c in re.split(r"(?:แล้ว|and|,|;|\.)", txt) if c.strip()]
    if not clauses:
        clauses = [txt]

    for c in clauses:
        if re.search(r"(หยุด|stop)", c, re.I):
            return PlanOut(waypoints=[], note="stop")

        # หมุนก่อน (ถ้ามี)
        if _has_turn_left(c):
            ang = _angle_rad(c) or math.radians(90)
            push(dth=ang)
        elif _has_turn_right(c):
            ang = _angle_rad(c) or math.radians(-90)
            push(dth=ang)

        # แล้วค่อยขยับระยะ (ถ้ามี)
        md = _move_dir(c)
        if md:
            d = _dist_m(c) or 0.3  # default 30cm
            if md == "forward":
                push(dx=d)
            elif md == "back":
                push(dx=-d)
            elif md == "left":
                push(dy=d)
            elif md == "right":
                push(dy=-d)

    if not wps:
        return PlanOut(waypoints=[], note="no_waypoints")

    limit = max(1, inp.max_waypoints)
    return PlanOut(waypoints=wps[:limit], note="rule_planner")

# alias กันพลาด prefix
@router.post("/plan_from_text_compat", response_model=PlanOut)
async def plan_from_text_compat(inp: PlanIn) -> PlanOut:
    return await plan_from_text(inp)