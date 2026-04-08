import re, math
from typing import Optional, Dict, Any, List

# ===== Elephant MyAGV 2023 (Jetson Nano) Default Parameters =====
# Ref: Elephant Robotics myAGV 2023 Specs
#   - Mecanum 4-wheel drive, wheel base ~0.105m
#   - Max linear: 0.9 m/s, recommended: 0.15 m/s
#   - Max angular: ~1.5 rad/s, recommended: 0.50 rad/s
#   - Calibration: 0.85 (ชดเชย inertia ของ Mecanum wheel)
LINEAR_SPEED = 0.15  # m/s (increased from 0.10 — safe with LiDAR check every 0.1s)
ANGULAR_SPEED = 0.50  # rad/s (ค่า default ตาม spec ของ Elephant Robotics)
ROTATION_CALIBRATION = 0.95   # Increased from 0.87 — was undershooting 10-20°

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


# ============ Find Object Intent ============
# Detects "หา..." or "ค้นหา..." commands and extracts the target object name.
# Examples:
#   "หากุญแจ"     → "กุญแจ"
#   "หา ปากกา"    → "ปากกา"
#   "ช่วยหาไขควง" → "ไขควง"
#   "ค้นหาสาย USB" → "สาย USB"
#   "find key"    → "key"

_FIND_PATTERNS = [
    # Thai: หา/ค้นหา + object (with or without space)
    # Negative lookahead: reject questions like "หากุญแจเจอไหม" "หากุญแจยังไง"
    r"(?:ช่วย)?(?:ค้น)?หา\s*(.+?)(?:\s*(?:เจอ|พบ|อยู่|ยัง|ไหม|มั้ย|รึเปล่า|หรือเปล่า|ไหน|ตรงไหน).*)?$",
    # Thai: มองหา + object
    r"มองหา\s*(.+?)(?:\s*(?:เจอ|พบ|อยู่|ไหม|มั้ย|ไหน).*)?$",
    # English: find/search/look for + object
    r"(?:find|search|look\s*for)\s+(.+)",
]

def parse_find_intent(text: str) -> Optional[str]:
    """
    Detect "find object" intent and return the target object name.
    
    Aggressively strips STT noise: filler words, side-talk, particles.
    Rejects questions about finding ("เจอกุญแจไหม", "หากุญแจเจอรึยัง").
    
    Returns:
        The object name string (e.g. "กุญแจ") or None if not a find command.
    """
    t = text.strip()
    if not t:
        return None
    
    # Reject pure questions about objects (not actual search commands)
    if re.search(r"(เจอ|พบ).*(ไหม|มั้ย|รึเปล่า|ยัง|ไหน)", t, re.IGNORECASE):
        return None
    if re.search(r"(อยู่|อยู่ตรง)\s*(ไหน|ที่ไหน)", t, re.IGNORECASE):
        return None
    
    for pattern in _FIND_PATTERNS:
        m = re.search(pattern, t, re.IGNORECASE)
        if m:
            target = m.group(1).strip()
            target = _clean_search_target(target)
            if target and len(target) >= 2:  # minimum 2 chars for valid target
                return target
    
    return None


# STT noise patterns that commonly appear after the real object name
# These indicate the user is talking to someone else, commenting, etc.
_NOISE_CUTOFF_PATTERNS = [
    r"\s+อันนี้",       # "กุญแจ อันนี้มันไม่..."
    r"\s+ตัวนี้",       # "ปากกา ตัวนี้มันเขียน..."
    r"\s+มันไม่",       # side-talk: "มันไม่อัปเดต"
    r"\s+เออ\b",        # filler: เออ
    r"\s+แบบ\b",        # filler: แบบ
    r"\s+อ่ะ\b",        # filler: อ่ะ
    r"\s+อะ\b",         # filler: อะ
    r"\s+อ่า\b",        # filler: อ่า
    r"\s+อา\b",         # filler: อา
    r"\s+เนาะ\b",       # filler: เนาะ
    r"\s+ก็\b",         # filler: ก็
    r"\s+งั้น\b",       # filler: งั้น
    r"\s+แหละ\b",       # particle: แหละ
    r"\s+ละ\b",         # particle: ละ
    r"\s+นะครับ",       # trailing polite
    r"\s+นะคะ",         # trailing polite
    r"\s+ครับผม",       # trailing polite
    r"\s+จ้า\b",        # trailing
    r"\s+จ๊ะ\b",        # trailing
    r"\s+อัน\s",        # "กุญแจ อัน..."
    r"\s+ที่\s",        # if followed by a long clause
    r"\s+ซึ่ง\s",       # relative clause
    r"\s+แต่\s",        # but...
    r"\s+แล้วก็",       # and then...
    r"\s+คือ\s",        # is...
    r"\s+ว่า\s",        # that...
]

# Trailing particles to strip (applied after cutoff)
_TRAILING_PARTICLES = (
    r"\s*(ให้หน่อย|หน่อยสิ|หน่อยได้ไหม|ได้ไหม|ได้มั้ย|ได้เปล่า|"
    r"หน่อย|ให้ที|ให้ด้วย|ให้ผม|ให้ฉัน|ให้เรา|"
    r"ครับ|ค่ะ|คะ|นะ|นะครับ|นะคะ|ที|ด้วย|สิ|สิครับ|สิคะ|"
    r"please|pls|จ้า|จ๊ะ|อ่ะ|อะ|แหละ|ละ|เลย|ไป)$"
)

def _clean_search_target(target: str) -> str:
    """
    Aggressively clean STT noise from search target.
    
    Steps:
    0. Cut at Thai no-space boundaries (ให้หน่อย, ผมวาง, etc.)
    1. Cut at noise boundary (side-talk, filler)
    2. Strip trailing particles (ครับ, หน่อย, etc.)
    3. Validate result is a reasonable object name
    """
    if not target:
        return ""
    
    orig = target
    
    # Step 0: Cut at Thai no-space boundaries 
    # Thai text has no spaces, so we need word-level cutoffs.
    # Pattern: keep everything BEFORE these phrases (they indicate context/request, not the object name)
    _THAI_NOSPACE_CUTOFFS = [
        r"ให้หน่อย",   # "กุญแจให้หน่อย" → "กุญแจ"
        r"ให้ผม",      # "กุญแจให้ผมหน่อย" → "กุญแจ"
        r"ให้ฉัน",     # "ปากกาให้ฉัน" → "ปากกา"
        r"ให้เรา",     # "ดินสอให้เรา" → "ดินสอ" 
        r"ให้ที",      # "กุญแจให้ที" → "กุญแจ"
        r"ให้ด้วย",    # "ปากกาให้ด้วย" → "ปากกา"
        r"ผมวาง",      # "กุญแจผมวางไว้..." → "กุญแจ"
        r"ผมเอา",      # "ปากกาผมเอาไว้..." → "ปากกา"
        r"ที่ผม",      # "กุญแจที่ผมวาง" → "กุญแจ"
        r"ที่อยู่",     # "ปากกาที่อยู่บนโต๊ะ" → "ปากกา"
        r"ที่วาง",     # "กุญแจที่วางไว้" → "กุญแจ" 
        r"วางไว้",     # "กุญแจวางไว้บน" → "กุญแจ"
        r"อยู่บน",     # "ปากกาอยู่บนโต๊ะ" → "ปากกา"
        r"อยู่ที่",     # "กุญแจอยู่ที่" → "กุญแจ"
        r"อยู่ตรง",    # "ปากกาอยู่ตรง" → "ปากกา"
        r"อยู่ใน",     # "กุญแจอยู่ใน" → "กุญแจ"
        r"อยู่ข้าง",   # "ปากกาอยู่ข้าง" → "ปากกา"
        r"บนโต๊ะ",     # "กุญแจบนโต๊ะ" → "กุญแจ" (location hint, not object name)
        r"บนพื้น",     # "กุญแจบนพื้น" → "กุญแจ"
        r"บนกระดาษ",   # "กุญแจบนกระดาษ" → "กุญแจ"
        r"เริ่มจาก",   # "กุญแจ เริ่มจาก..." → "กุญแจ"
        r"ตรงที่",     # "กุญแจตรงที่..." → "กุญแจ"
        r"ตรงนี้",
        r"ตรงนั้น",
        r"ข้างๆ",
        r"ใกล้ๆ",
    ]
    # Polite words that commonly appear between the object name and description
    _LEADING_POLITE = re.compile(
        r'^(?:หน่อย|ครับผม|ครับ|ค่ะ|คะ|นะครับ|นะคะ|นะ|จ้า|จ๊ะ|เลย|สิ|\s)+',
        re.IGNORECASE,
    )
    for pat in _THAI_NOSPACE_CUTOFFS:
        m = re.search(pat, target)
        if m and m.start() > 0:  # must keep at least 1 char before
            before = target[:m.start()].strip()
            after = target[m.end():]
            # Strip polite words from the beginning of remaining text
            after_clean = _LEADING_POLITE.sub('', after).strip()
            if after_clean and len(after_clean) > 2:
                # Meaningful description follows (brand name, color, etc.) — keep it
                target = f"{before} {after_clean}"
            else:
                target = before
            break
    
    # Step 1: Cut at noise boundary patterns (space-prefixed)
    for noise_pat in _NOISE_CUTOFF_PATTERNS:
        m = re.search(noise_pat, target, re.IGNORECASE)
        if m:
            target = target[:m.start()].strip()
            break
    
    # Step 2: Strip trailing particles (repeat to handle stacked particles)
    for _ in range(5):
        new = re.sub(_TRAILING_PARTICLES, "", target, flags=re.IGNORECASE).strip()
        if new == target:
            break
        target = new
    
    # Step 3: If result is too long (>80 chars), probably contains noise
    # Try to extract just the first meaningful words
    if len(target) > 80:
        # Take first few words (object + description/brand)
        short = re.match(r"^[\u0E00-\u0E7F\w]+(?:[\s][\u0E00-\u0E7F\w]+){0,5}", target)
        if short:
            target = short.group(0)
    
    # Step 4: Final cleanup - remove leading/trailing whitespace and symbols
    target = target.strip(" .,!?")
    
    return target


def parse_find_multi_objects(text: str) -> Optional[List[str]]:
    """
    Parse multi-object find command.
    
    Examples:
        "หาปากกากับดินสอ"     → ["ปากกา", "ดินสอ"]
        "หา card และ wallet" → ["card", "wallet"]
        "ช่วยหาปากกา ดินสอ และกุญแจ" → ["ปากกา", "ดินสอ", "กุญแจ"]
    
    Returns:
        List of object names to search, or None if not a multi-object query.
    """
    # First try to get the full target string
    single = parse_find_intent(text)
    if not single:
        return None
    
    # Split by connectors: กับ, และ, with, and, หรือ comma/space
    objects = re.split(r"\s*(?:กับ|และ|with|and|,)\s*", single, flags=re.IGNORECASE)
    objects = [o.strip() for o in objects if o.strip()]
    
    if len(objects) <= 1:
        return None  # Single object, not multi
    
    # Clean each object name
    cleaned = []
    for obj in objects:
        for _ in range(3):
            obj = re.sub(
                r"\s*(ให้หน่อย|ได้ไหม|หน่อย|ครับ|ค่ะ|นะ|ที|ด้วย|please)$",
                "", obj, flags=re.IGNORECASE
            ).strip()
        if obj:
            cleaned.append(obj)
    
    return cleaned if len(cleaned) > 1 else None


def parse_find_with_description(text: str) -> Optional[Dict[str, str]]:
    """
    Parse find command with object description (color, size, etc.).
    
    Examples:
        "หาปากกาสีน้ำเงิน"      → {"object": "ปากกา", "description": "สีน้ำเงิน"}
        "หา wallet สีดำ"        → {"object": "wallet", "description": "สีดำ"}
        "หาดินสอตัวใหญ่"        → {"object": "ดินสอ", "description": "ตัวใหญ่"}
        "หากระเป๋าสตางค์สีน้ำตาล" → {"object": "กระเป๋าสตางค์", "description": "สีน้ำตาล"}
    
    Returns:
        {"object": ..., "description": ...} or None if no description found.
    """
    single = parse_find_intent(text)
    if not single:
        return None
    
    # Pattern: object + description (color, size, etc.)
    # Common Thai descriptions: สี*, ตัว*, ขนาด*, อัน*
    desc_pattern = r"^(.+?)((?:สี|ตัว|ขนาด|อัน|ของ)[^\s]+|(?:black|white|blue|red|green|yellow|small|big|large|my)\s*\w*)$"
    m = re.search(desc_pattern, single, re.IGNORECASE)
    
    if m:
        obj = m.group(1).strip()
        desc = m.group(2).strip()
        if obj and desc:
            return {"object": obj, "description": desc}
    
    return None


# ============ Multi-Step Commands ============
# Parses commands with "แล้ว" (then) connector and special patterns.
# Examples:
#   "เดินหน้าแล้วเลี้ยวซ้าย"     → [forward, turn_left]
#   "หมุนซ้ายแล้วเดินหน้า"       → [turn_left, forward]
#   "เดินหน้า 2 เมตรแล้วหมุนขวา" → [forward_2m, turn_right]
#   "เดินรูปตัว U"               → [forward, turn_left, forward]
#   "หมุนกลับ"                   → [turn_180]

def parse_multi_intent(text: str) -> Optional[List[Dict[str, Any]]]:
    """
    Parse multi-step motion commands connected by "แล้ว" (then).
    
    Returns:
        List of motion commands (each is a dict like parse_intent returns),
        or None if not a multi-step command.
    """
    t = text.strip()
    if not t:
        return None
    
    t = _normalize_stt(t)
    
    # === Special Patterns (return immediately) ===
    
    # หมุนกลับ / U-turn (180°)
    if re.search(r"(หมุน|หัน)\s*(กลับ|กลับหลัง|180|ยูเทิร์น|u-?turn)", t, re.IGNORECASE):
        rad = math.radians(180)
        dur = rad / ANGULAR_SPEED * ROTATION_CALIBRATION
        return [{"type": "move", "linear_x": 0.0, "angular_z": ANGULAR_SPEED, "duration": dur}]
    
    # เดินรูปตัว U / U-shape path (forward → left 90° → forward)
    if re.search(r"(เดิน|ไป).*(รูป|ตัว).*(U|ยู|u)", t, re.IGNORECASE):
        d = _parse_distance(t) or 1.0
        dur_fwd = d / LINEAR_SPEED
        rad = math.radians(90)
        dur_turn = rad / ANGULAR_SPEED * ROTATION_CALIBRATION
        return [
            {"type": "move", "linear_x": LINEAR_SPEED, "angular_z": 0.0, "duration": dur_fwd},
            {"type": "move", "linear_x": 0.0, "angular_z": ANGULAR_SPEED, "duration": dur_turn},  # left 90°
            {"type": "move", "linear_x": LINEAR_SPEED, "angular_z": 0.0, "duration": dur_fwd},
        ]
    
    # เดินรูปตัว L (forward → left/right 90°)
    if re.search(r"(เดิน|ไป).*(รูป|ตัว).*(L|แอล)", t, re.IGNORECASE):
        d = _parse_distance(t) or 1.0
        dur_fwd = d / LINEAR_SPEED
        rad = math.radians(90)
        dur_turn = rad / ANGULAR_SPEED * ROTATION_CALIBRATION
        # Check direction (default left)
        az = ANGULAR_SPEED if not re.search(r"(ขวา|right)", t, re.IGNORECASE) else -ANGULAR_SPEED
        return [
            {"type": "move", "linear_x": LINEAR_SPEED, "angular_z": 0.0, "duration": dur_fwd},
            {"type": "move", "linear_x": 0.0, "angular_z": az, "duration": dur_turn},
        ]
    
    # === "แล้ว" Connector (Split and parse each part) ===
    # Split by แล้ว/และ/then
    parts = re.split(r"\s*(แล้ว|และ|then|and)\s*", t, flags=re.IGNORECASE)
    parts = [p.strip() for p in parts if p.strip() and p.lower() not in ["แล้ว", "และ", "then", "and"]]
    
    if len(parts) < 2:
        return None  # Not a multi-step command
    
    # Parse each part
    commands = []
    for part in parts:
        cmd = parse_intent(part)
        if cmd:
            commands.append(cmd)
    
    if len(commands) < 2:
        return None  # At least one part didn't parse
    
    return commands


# ============ 5 Official Search Items ============
SEARCH_ITEMS = ["card", "coil", "pen", "pencil", "wallet"]

def normalize_search_target(target: str) -> Optional[str]:
    """
    Normalize search target to one of the 5 official items.
    
    Maps Thai to English and handles variations:
      บัตร/การ์ด → card
      ขดลวด/คอยล์ → coil
      ปากกา → pen
      ดินสอ → pencil
      กระเป๋าสตางค์/กระเป๋าตัง → wallet
    """
    t = target.strip().lower()
    
    mappings = {
        # key (กุญแจ)
        "key": "key", "กุญแจ": "key", "ลูกกุญแจ": "key",
        # card
        "card": "card", "การ์ด": "card", "บัตร": "card", "นามบัตร": "card",
        # coil
        "coil": "coil", "คอยล์": "coil", "ขดลวด": "coil", "ขดลวดทองแดง": "coil",
        # pen
        "pen": "pen", "ปากกา": "pen",
        # pencil
        "pencil": "pencil", "ดินสอ": "pencil",
        # wallet
        "wallet": "wallet", "กระเป๋าสตางค์": "wallet", "กระเป๋าตังค์": "wallet", 
        "กระเป๋าตัง": "wallet", "กระเป๋า": "wallet",
    }
    
    # Direct match
    if t in mappings:
        return mappings[t]
    
    # Fuzzy match (contains)
    for thai, eng in mappings.items():
        if thai in t or t in thai:
            return eng
    
    # Not in official list — return original (for description matching later)
    return target
