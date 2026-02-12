import re, math
from typing import Optional, Dict, Any

# ===== Elephant MyAGV 2023 (Jetson Nano) Default Parameters =====
# Ref: Elephant Robotics myAGV 2023 Specs
#   - Mecanum 4-wheel drive, wheel base ~0.105m
#   - Max linear: 0.9 m/s, recommended: 0.15 m/s
#   - Max angular: ~1.5 rad/s, recommended: 0.50 rad/s
#   - Calibration: 0.85 (ชดเชย inertia ของ Mecanum wheel)
LINEAR_SPEED = 0.15  # m/s (ค่า default ที่ปลอดภัยสำหรับ MyAGV 2023)
ANGULAR_SPEED = 0.50  # rad/s (ค่า default ตาม spec ของ Elephant Robotics)
ROTATION_CALIBRATION = 1.0   # จูนใหม่ที่ 0.50 rad/s (cal เดิม 0.85 วัดที่ 0.30 rad/s → undershoot)

DIST_PAT = r"(\d+(?:\.\d+)?)\s*(?:กิโล(?:เมตร)?|km|เมตร|ม\.|ม|เซนติ(?:เมตร)?|cm|ซม|มิลลิ(?:เมตร)?|mm|มม)"
TIME_PAT = r"(\d+(?:\.\d+)?)\s*(?:วินาที|วิ|นาที|sec|s)"
DEG_PAT  = r"(\d+(?:\.\d+)?)\s*(?:องศา|degree|deg|ดีกรี)"
ROUND_PAT = r"(\d+(?:\.\d+)?)\s*(?:รอบ|round|circle|เที่ยว)"  # เพิ่ม pattern รอบ

# --- STT Text Normalization ---
# Whisper Thai มักฟังคำว่า "เลี้ยว" เพี้ยนเป็นคำอื่นๆ
_STT_FIXES = [
    (r"เรียว", "เลี้ยว"),               # เรียวซ้าย → เลี้ยวซ้าย (common!)
    (r"แล้ว\s*(ซ้าย|ขวา)", r"เลี้ยว\1"),  # แล้วซ้าย → เลี้ยวซ้าย
    (r"เทิง|เทิน", "เดิน"),              # เทิงหน้า → เดินหน้า
    (r"ถ่อย", "ถอย"),                    # ถ่อยหลัง → ถอยหลัง
]

def _normalize_stt(text: str) -> str:
    """แก้คำที่ STT มักฟังผิดก่อนเข้า regex matcher"""
    for pattern, replacement in _STT_FIXES:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text

def _parse_distance(text: str) -> Optional[float]:
    """แปลงระยะทางเป็นเมตร รองรับ: km, m, cm, mm"""
    m = re.search(DIST_PAT, text, re.IGNORECASE)
    if not m:
        return None
    val = float(m.group(1))
    unit = re.search(r"(กิโล(?:เมตร)?|km|เมตร|ม\.|ม|เซนติ(?:เมตร)?|cm|ซม|มิลลิ(?:เมตร)?|mm|มม)", text, re.IGNORECASE)
    unit = unit.group(1) if unit else "ม"
    
    # กิโลเมตร
    if re.search(r"กิโล|km", unit, re.IGNORECASE):
        return val * 1000.0
    # เซนติเมตร
    if re.search(r"เซนติ|cm|ซม", unit, re.IGNORECASE):
        return val / 100.0
    # มิลลิเมตร
    if re.search(r"มิลลิ|mm|มม", unit, re.IGNORECASE):
        return val / 1000.0
    # เมตร (default)
    return val

def _parse_time(text: str) -> Optional[float]:
    """แปลงเวลาเป็นวินาที สำหรับคำสั่งแบบ 'เดิน 5 วินาที'"""
    m = re.search(TIME_PAT, text, re.IGNORECASE)
    if not m:
        return None
    return float(m.group(1))

def _parse_degree(text: str) -> Optional[float]:
    """แปลงองศาเป็น radian"""
    m = re.search(DEG_PAT, text, re.IGNORECASE)
    if not m:
        return None
    deg = float(m.group(1))
    return math.radians(deg)

def _parse_rounds(text: str) -> Optional[float]:
    """แปลงรอบเป็น radian (1 รอบ = 2π rad = 360°)"""
    m = re.search(ROUND_PAT, text, re.IGNORECASE)
    if not m:
        return None
    rounds = float(m.group(1))
    return rounds * 2 * math.pi  # 1 รอบ = 2π radians

def parse_intent(text: str) -> Optional[Dict[str, Any]]:
    """
    Parse basic motion commands ด้วย regex (ไม่ต้องใช้ LLM)
    
    รองรับ:
    - หยุด
    - เดินหน้า [X เมตร/cm]
    - ถอยหลัง [X เมตร/cm]
    - เลี้ยวซ้าย/ขวา [X องศา]
    - หมุนซ้าย/ขวา [X รอบ]
    - หมุนรอบตัว [ทางซ้าย/ขวา]
    """
    t = text.strip()
    if not t:
        return None
    
    # แก้คำเพี้ยนจาก STT ก่อนเข้า regex
    t = _normalize_stt(t)
    
    # 1. หยุด
    if re.search(r"(หยุด|พอ|สต็อป|stop)", t, re.IGNORECASE):
        return {"type": "stop"}
    
    # 2. หมุนรอบตัว (ต้องเช็คก่อน "หมุนซ้าย/ขวา" ทั่วไป)
    if re.search(r"หมุน.*(รอบ.*ตัว|ตัว.*รอบ)", t, re.IGNORECASE):
        # ตรวจสอบทิศทาง
        if re.search(r"(ขวา|right)", t, re.IGNORECASE):
            angle_rad = -(2 * math.pi)  # -360° ขวา
        else:
            angle_rad = 2 * math.pi  # +360° ซ้าย (default)
        
        # ดูว่ามีระบุจำนวนรอบไหม
        rounds = _parse_rounds(t)
        if rounds:
            angle_rad = rounds if re.search(r"ซ้าย|left", t, re.IGNORECASE) else -rounds
        
        dur = abs(angle_rad) / ANGULAR_SPEED * ROTATION_CALIBRATION
        return {"type": "move", "linear_x": 0.0, "angular_z": angle_rad / dur, "duration": dur}
    
    # 3. หมุน X รอบ (เช่น "หมุนซ้าย 2 รอบ")
    rounds = _parse_rounds(t)
    if rounds and re.search(r"(หมุน|หัน|turn|rotate)", t, re.IGNORECASE):
        # ตรวจสอบทิศทาง
        if re.search(r"(ขวา|right)", t, re.IGNORECASE):
            angle_rad = -rounds  # ขวา = ลบ
        else:
            angle_rad = rounds  # ซ้าย = บวก (default)
        
        dur = abs(angle_rad) / ANGULAR_SPEED * ROTATION_CALIBRATION
        return {"type": "move", "linear_x": 0.0, "angular_z": angle_rad / dur, "duration": dur}
    
    # 4. เดินหน้า (รองรับทั้งระยะทางและเวลา)
    if re.search(r"(เดิน|เดิง|เทิน|ไป|ขยับ|move|go|forward|ฟอร์เวิร์ด).*(หน้า|front|ตรง)", t, re.IGNORECASE) or \
       re.search(r"(เดินหน้า|ไปข้างหน้า|ลุย)", t, re.IGNORECASE):
        # ลองหาเวลาก่อน (เช่น "เดิน 5 วินาที")
        dur_time = _parse_time(t)
        if dur_time:
            return {"type": "move", "linear_x": +LINEAR_SPEED, "angular_z": 0.0, "duration": dur_time}
        
        # ไม่มีเวลา ใช้ระยะทาง
        d = _parse_distance(t) or 1.0  # default 1.0m (เปลี่ยนจาก 0.5)
        dur = d / LINEAR_SPEED
        return {"type": "move", "linear_x": +LINEAR_SPEED, "angular_z": 0.0, "duration": dur}
    
    # 5. ถอยหลัง (รองรับทั้งระยะทางและเวลา)
    if re.search(r"(ถอยหลัง|ขยับหลัง|หลัง|backward)", t, re.IGNORECASE):
        # ลองหาเวลาก่อน (เช่น "ถอย 3 วินาที")
        dur_time = _parse_time(t)
        if dur_time:
            return {"type": "move", "linear_x": -LINEAR_SPEED, "angular_z": 0.0, "duration": dur_time}
        
        # ไม่มีเวลา ใช้ระยะทาง
        d = _parse_distance(t) or 1.0  # default 1.0m (เปลี่ยนจาก 0.5)
        dur = d / LINEAR_SPEED
        return {"type": "move", "linear_x": -LINEAR_SPEED, "angular_z": 0.0, "duration": dur}
    
    # 6. เลี้ยว/หมุน ซ้าย/ขวา (ระบุองศา หรือ default 90°)
    if re.search(r"(หัน|เลี้ยว|เรียว|หมุน|turn).*(ซ้าย|left)", t, re.IGNORECASE):
        rad = _parse_degree(t) or math.radians(90.0)  # default 90°
        dur = abs(rad) / ANGULAR_SPEED * ROTATION_CALIBRATION
        return {"type": "move", "linear_x": 0.0, "angular_z": +ANGULAR_SPEED, "duration": dur}
    
    if re.search(r"(หัน|เลี้ยว|เรียว|หมุน|turn).*(ขวา|right)", t, re.IGNORECASE):
        rad = _parse_degree(t) or math.radians(90.0)
        dur = abs(rad) / ANGULAR_SPEED * ROTATION_CALIBRATION
        return {"type": "move", "linear_x": 0.0, "angular_z": -ANGULAR_SPEED, "duration": dur}
    
    return None
