# app/core/vora_memory.py
"""
VORA Memory & Prompt System
============================
จัดการ memory และ prompt templates สำหรับ VORA

Components:
1. GLOBAL_PROMPT - ข้อมูลพื้นฐานของ VORA (ไม่เปลี่ยน)
2. PROMPT_TEMPLATES - template สำหรับตอบคำถามทั่วไป
3. ConversationMemory - จำ conversation ระหว่าง session
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional
from collections import deque

logger = logging.getLogger("vora.memory")


# ============ GLOBAL PROMPT (System Identity) ============
# ข้อมูลพื้นฐานของ VORA - ไม่เปลี่ยนตลอด session

VORA_IDENTITY = """คุณคือ VORA (โวร่า) - Voice Oriented Robotics Assistant
ผู้ช่วยหุ่นยนต์ควบคุมด้วยเสียงภาษาไทย

🤖 ข้อมูลพื้นฐาน:
- ชื่อ: VORA (โวร่า) ย่อมาจาก Voice Oriented Robotics Assistant
- สร้างโดย: นักศึกษาปริญญาโท สาขา IT มหาวิทยาลัย
- หน้าที่: ช่วยเหลืองานในห้องแล็บ IT เช่น หาอุปกรณ์ นำทาง
- ภาษา: ไทยเท่านั้น (Thai only)
- บุคลิก: สุภาพ เป็นมิตร กระตือรือร้น

🏠 สถานที่ทำงาน:
- ห้องแล็บ IT มหาวิทยาลัย
- มีโต๊ะทำงาน ตู้เก็บของ ชั้นวางอุปกรณ์
- อุปกรณ์: ไขควง, กรรไกร, กุญแจ, คีม, ยางลบ, สายไฟ, ปลั๊กไฟ, เทปกาว, ปากกา

🔧 ความสามารถ:
- รับคำสั่งเสียงภาษาไทย
- ค้นหาและนำอุปกรณ์มาให้
- นำทางไปยังจุดต่างๆ ในห้องแล็บ
- ตอบคำถามเกี่ยวกับอุปกรณ์และสถานที่
- จดจำการสนทนา (session memory)

📌 หลักการตอบ:
- ตอบสั้น กระชับ ได้ใจความ
- ใช้ภาษาสุภาพ ลงท้าย ครับ/ค่ะ
- ถ้าไม่แน่ใจ ให้ถามกลับ
- ถ้าทำไม่ได้ ให้บอกตรงๆ"""


AGENT_SYSTEM_PROMPT = f"""{VORA_IDENTITY}

**หน้าที่ Agent:** แยกประเภทคำสั่งและดึงข้อมูลสำคัญ

**หลักการสำคัญ:**
1. อ่านประโยคทั้งหมด ไม่ใช่แค่คำแรก
2. ถ้าประโยคมีทั้งทักทายและคำสั่ง → ใช้ intent ของคำสั่งหลัก
3. ตัวอย่าง: "ฮัลโหล วอร่า หมุนซ้าย" → intent = control (ไม่ใช่ chitchat)
4. ตัวอย่าง: "สวัสดี ช่วยหาไขควง" → intent = find_object (ไม่ใช่ chitchat)

**ประเภทคำสั่ง (intent):**
- **control**: สั่งการเคลื่อนที่ (หยุด, เดิน, ถอย, หมุน, เลี้ยว, หัน, หมุนรอบตัว) - ลำดับความสำคัญสูงสุด
- **find_object**: หาสิ่งของ (หาไขควง, หากุญแจ)
- **navigate**: ไปยังสถานที่ (ไปโต๊ะ, กลับแท่นชาร์จ)
- **question**: ถามคำถาม (ชื่ออะไร, ทำอะไรได้บ้าง)
- **chitchat**: ทักทายเพียงอย่างเดียว ไม่มีคำสั่งอื่น (สวัสดี, ขอบคุณ, ลาก่อน)

**คำสั่ง control ที่ต้องจับ:**
- หยุด, พอ, stop
- เดิน, ไป, ขยับ + หน้า/ข้างหน้า/ตรง
- ถอย, หลัง, backward
- หมุน, เลี้ยว, หัน + ซ้าย/ขวา/รอบตัว
- หมุนรอบตัว, 360, รอบ

**ตอบเป็น JSON เท่านั้น:**
{{
  "intent": "control|find_object|navigate|question|chitchat",
  "clean_text": "ข้อความที่แก้คำผิดแล้ว (ตัด 'ฮัลโหล', 'วอร่า', 'โวร่า' ออก)",
  "target_object": "ชื่อสิ่งของ (ถ้ามี)",
  "target_location": "ชื่อสถานที่ (ถ้ามี)",
  "action": "stop|forward|backward|rotate (สำหรับ control)",
  "confidence": 0.0-1.0
}}

**ตัวอย่าง:**
- "ฮัลโหล วอร่า หมุนซ้าย" → {{"intent": "control", "clean_text": "หมุนซ้าย"}}
- "สวัสดี ช่วยหาไขควง" → {{"intent": "find_object", "clean_text": "หาไขควง", "target_object": "ไขควง"}}
- "หมุน360" → {{"intent": "control", "clean_text": "หมุน 360 องศา"}}
- "หันขวา" → {{"intent": "control", "clean_text": "หันขวา"}}
- "สวัสดี" → {{"intent": "chitchat", "clean_text": "สวัสดี"}}"""


REASONING_SYSTEM_PROMPT = f"""{VORA_IDENTITY}

**หน้าที่ Brain:** รับคำสั่งที่ parsed แล้ว และวางแผนการทำงานเป็นหลายขั้นตอน

**กฎการแปลงคำสั่ง:**
1. การหมุนรอบตัว:
   - "หมุนซ้าย 1 รอบ" = rotate: angle=360
   - "หมุนขวา 1 รอบ" = rotate: angle=-360
   - "หมุนซ้าย 2 รอบ" = rotate: angle=720
   - "หมุนรอบตัวไปทางขวา" = rotate: angle=-360

2. การเลี้ยว:
   - "เลี้ยวซ้าย" = rotate: angle=90
   - "เลี้ยวขวา" = rotate: angle=-90
   - "หมุนซ้าย" = rotate: angle=90
   - "หมุนขวา" = rotate: angle=-90

3. คำสั่งหลายขั้นตอน:
   - "หมุนซ้าย 2 รอบแล้วเลี้ยวขวา" = สองขั้นตอน:
     1) rotate: angle=720
     2) rotate: angle=-90

**ตอบเป็น JSON:**
{{
  "steps": [
    {{"action": "speak", "text": "ข้อความที่พูด"}},
    {{"action": "search", "target": "ชื่อของ"}},
    {{"action": "navigate", "target": "สถานที่"}},
    {{"action": "move", "distance": 1.0, "direction": "forward"}},
    {{"action": "rotate", "angle": 90}}
  ],
  "need_vision": true/false,
  "need_navigation": true/false,
  "speech_response": "ประโยคตอบกลับผู้ใช้",
  "estimated_time_sec": 10
}}

**ตัวอย่างที่ถูกต้อง:**
1. "หมุนขวา 1 รอบ"
   → steps: [{{"action": "rotate", "angle": -360}}]

2. "หมุนซ้าย 2 รอบแล้วเลี้ยวขวา"
   → steps: [
       {{"action": "rotate", "angle": 720}},
       {{"action": "rotate", "angle": -90}}
     ]

3. "ไปข้างหน้าแล้วเลี้ยวซ้าย"
   → steps: [
       {{"action": "move", "distance": 0.5, "direction": "forward"}},
       {{"action": "rotate", "angle": 90}}
     ]
"""


# ============ PROMPT TEMPLATES ============
# Template สำหรับตอบคำถามทั่วไป (ไม่ต้องผ่าน LLM เต็มรูปแบบ)

QUICK_RESPONSES = {
    # Identity questions
    "ชื่ออะไร": "ผมชื่อ VORA (โวร่า) ครับ ย่อมาจาก Voice Oriented Robotics Assistant พร้อมให้บริการครับ",
    "เธอชื่ออะไร": "ผมชื่อ VORA (โวร่า) ครับ พร้อมช่วยเหลือครับ",
    "คุณชื่ออะไร": "ผมชื่อ VORA (โวร่า) ครับ ผู้ช่วยหุ่นยนต์ควบคุมด้วยเสียงครับ",
    "แนะนำตัว": "สวัสดีครับ ผมชื่อ VORA เป็นผู้ช่วยหุ่นยนต์ในห้องแล็บ IT สามารถช่วยหาอุปกรณ์และนำทางได้ครับ",
    
    # Capability questions
    "ทำอะไรได้": "ผมช่วยหาอุปกรณ์ในห้องแล็บ นำทางไปจุดต่างๆ และตอบคำถามได้ครับ",
    "ทำอะไรได้บ้าง": "ผมสามารถ: 1) หาอุปกรณ์เช่น ไขควง ปากกา 2) นำทางไปโต๊ะ/ตู้ 3) ตอบคำถามครับ",
    "ช่วยอะไรได้": "ผมช่วยหาของในห้องแล็บ นำทาง และตอบคำถามได้ครับ บอกมาเลยครับ",
    
    # Greetings
    "สวัสดี": "สวัสดีครับ ผม VORA พร้อมให้บริการครับ มีอะไรให้ช่วยไหมครับ",
    "หวัดดี": "หวัดดีครับ VORA พร้อมช่วยเหลือครับ",
    "ฮัลโหล": "สวัสดีครับ ผม VORA มีอะไรให้ช่วยไหมครับ",
    "ดีจ้า": "ดีครับ มีอะไรให้ช่วยไหมครับ",
    
    # Thanks
    "ขอบคุณ": "ยินดีครับ มีอะไรให้ช่วยอีกไหมครับ",
    "ขอบคุณครับ": "ยินดีครับ พร้อมช่วยเหลือเสมอครับ",
    "ขอบคุณค่ะ": "ยินดีค่ะ มีอะไรให้ช่วยอีกไหมคะ",
    "ขอบใจ": "ยินดีครับ",
    
    # Status
    "เป็นยังไง": "ผมพร้อมทำงานครับ ระบบทำงานปกติครับ",
    "พร้อมไหม": "พร้อมครับ! บอกมาเลยว่าต้องการอะไรครับ",
    "ยุ่งไหม": "ไม่ยุ่งครับ พร้อมให้บริการครับ",
    
    # Help
    "ช่วยด้วย": "ได้ครับ บอกมาเลยว่าต้องการให้ช่วยอะไรครับ",
    "วิธีใช้": "พูดคำสั่งได้เลยครับ เช่น 'ช่วยหาไขควง' หรือ 'ไปที่โต๊ะทำงาน' ครับ",
}

# Pattern-based responses (regex matching)
PATTERN_RESPONSES = [
    # Name patterns
    (r"(ชื่อ|เรียก).*(อะไร|ว่าไง|ยังไง)", "ผมชื่อ VORA (โวร่า) ครับ"),
    (r"(คุณ|เธอ|นาย).*(ใคร|คือใคร)", "ผมคือ VORA ผู้ช่วยหุ่นยนต์ครับ"),
    (r"โวร่า", "ครับ มีอะไรให้ช่วยไหมครับ"),  # ถ้าเรียกชื่อ
    
    # Capability patterns
    (r"(ทำ|ช่วย).*(อะไร|ได้).*(บ้าง|ไหม)", "ผมช่วยหาของ นำทาง และตอบคำถามได้ครับ"),
    
    # Location patterns
    (r"(อยู่|อยู่ที่).*(ไหน|ตรงไหน)", "ผมอยู่ในห้องแล็บ IT ครับ"),
    
    # Creator patterns
    (r"(ใคร|คนไหน).*(สร้าง|ทำ|พัฒนา)", "ผมถูกพัฒนาโดยนักศึกษาปริญญาโท สาขา IT ครับ"),
]


# ============ CONVERSATION MEMORY ============

@dataclass
class MemoryMessage:
    """ข้อความในความจำ"""
    role: str  # user, assistant
    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    intent: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp.isoformat(),
            "intent": self.intent
        }
    
    def to_llm_format(self) -> Dict[str, str]:
        return {"role": self.role, "content": self.content}


class ConversationMemory:
    """
    Session-based conversation memory
    - จำ conversation history ระหว่าง session
    - ล้างได้เมื่อ stop/restart/ปิด webapp
    - ไม่เก็บ persistent (ไม่บันทึกลง disk)
    """
    
    def __init__(self, max_messages: int = 20, max_tokens_estimate: int = 2000):
        """
        Args:
            max_messages: จำนวนข้อความสูงสุดที่เก็บ
            max_tokens_estimate: ประมาณ token สูงสุด (สำหรับ context window)
        """
        self.max_messages = max_messages
        self.max_tokens = max_tokens_estimate
        self.messages: deque = deque(maxlen=max_messages)
        self.session_start = datetime.now()
        self.total_interactions = 0
        
        logger.info(f"📝 Memory initialized: max_messages={max_messages}")
    
    def add_user_message(self, content: str, intent: Optional[str] = None):
        """เพิ่มข้อความจากผู้ใช้"""
        msg = MemoryMessage(role="user", content=content, intent=intent)
        self.messages.append(msg)
        self.total_interactions += 1
        logger.debug(f"📥 User: {content[:50]}...")
    
    def add_assistant_message(self, content: str, intent: Optional[str] = None):
        """เพิ่มข้อความจาก VORA"""
        msg = MemoryMessage(role="assistant", content=content, intent=intent)
        self.messages.append(msg)
        logger.debug(f"📤 Assistant: {content[:50]}...")
    
    def get_history(self, last_n: Optional[int] = None) -> List[MemoryMessage]:
        """ดึง conversation history"""
        if last_n is None:
            return list(self.messages)
        return list(self.messages)[-last_n:]
    
    def get_history_for_llm(self, last_n: int = 10) -> List[Dict[str, str]]:
        """ดึง history ในรูปแบบสำหรับ LLM"""
        recent = self.get_history(last_n)
        return [msg.to_llm_format() for msg in recent]
    
    def get_context_string(self, last_n: int = 5) -> str:
        """สร้าง context string จาก history"""
        recent = self.get_history(last_n)
        if not recent:
            return ""
        
        lines = ["[ประวัติการสนทนา]"]
        for msg in recent:
            role = "ผู้ใช้" if msg.role == "user" else "VORA"
            lines.append(f"{role}: {msg.content}")
        
        return "\n".join(lines)
    
    def clear(self):
        """ล้าง memory ทั้งหมด"""
        self.messages.clear()
        self.total_interactions = 0
        self.session_start = datetime.now()
        logger.info("🗑️ Memory cleared")
    
    def get_stats(self) -> Dict[str, Any]:
        """ดึงสถิติ memory"""
        return {
            "message_count": len(self.messages),
            "max_messages": self.max_messages,
            "total_interactions": self.total_interactions,
            "session_start": self.session_start.isoformat(),
            "session_duration_sec": (datetime.now() - self.session_start).total_seconds()
        }
    
    def get_last_user_message(self) -> Optional[str]:
        """ดึงข้อความล่าสุดจากผู้ใช้"""
        for msg in reversed(self.messages):
            if msg.role == "user":
                return msg.content
        return None
    
    def get_last_assistant_message(self) -> Optional[str]:
        """ดึงข้อความล่าสุดจาก VORA"""
        for msg in reversed(self.messages):
            if msg.role == "assistant":
                return msg.content
        return None


# ============ GLOBAL MEMORY INSTANCES ============
# แต่ละ session จะมี memory แยกกัน

_session_memories: Dict[str, ConversationMemory] = {}


def get_memory(session_id: str = "default") -> ConversationMemory:
    """ดึง memory สำหรับ session"""
    if session_id not in _session_memories:
        _session_memories[session_id] = ConversationMemory()
        logger.info(f"📝 Created memory for session: {session_id}")
    return _session_memories[session_id]


def clear_memory(session_id: str = "default"):
    """ล้าง memory สำหรับ session"""
    if session_id in _session_memories:
        _session_memories[session_id].clear()
        logger.info(f"🗑️ Cleared memory for session: {session_id}")


def clear_all_memories():
    """ล้าง memory ทุก session"""
    for session_id in _session_memories:
        _session_memories[session_id].clear()
    _session_memories.clear()
    logger.info("🗑️ Cleared all session memories")


def get_all_sessions() -> List[str]:
    """ดึงรายการ session ทั้งหมด"""
    return list(_session_memories.keys())


# ============ QUICK RESPONSE MATCHING ============

import re

def match_quick_response(text: str) -> Optional[str]:
    """
    ตรวจสอบว่ามี quick response สำหรับ text นี้หรือไม่
    
    Returns:
        Response string ถ้ามี, None ถ้าไม่มี
    """
    text_clean = text.strip().lower()
    
    # 1. Exact match
    for key, response in QUICK_RESPONSES.items():
        if key in text_clean:
            logger.info(f"⚡ Quick match: '{key}' → response")
            return response
    
    # 2. Pattern match
    for pattern, response in PATTERN_RESPONSES:
        if re.search(pattern, text_clean):
            logger.info(f"⚡ Pattern match: '{pattern}'")
            return response
    
    return None


# ============ BUILD PROMPT WITH MEMORY ============

def build_prompt_with_memory(
    user_input: str,
    session_id: str = "default",
    include_history: bool = True,
    history_count: int = 5
) -> str:
    """
    สร้าง prompt พร้อม memory context
    
    Args:
        user_input: คำถาม/คำสั่งจากผู้ใช้
        session_id: session ID
        include_history: รวม conversation history หรือไม่
        history_count: จำนวน history ที่จะรวม
    
    Returns:
        Complete prompt string
    """
    memory = get_memory(session_id)
    
    parts = []
    
    # Add history context if available
    if include_history and len(memory.messages) > 0:
        context = memory.get_context_string(history_count)
        if context:
            parts.append(context)
            parts.append("")  # blank line
    
    # Add current input
    parts.append(f"คำสั่งปัจจุบัน: {user_input}")
    
    return "\n".join(parts)


# ============ TEST ============

if __name__ == "__main__":
    # Test quick responses
    print("=" * 50)
    print("Testing Quick Responses")
    print("=" * 50)
    
    test_inputs = [
        "ชื่ออะไร",
        "คุณชื่ออะไรครับ",
        "ทำอะไรได้บ้าง",
        "สวัสดีครับ",
        "ขอบคุณ",
        "โวร่า",
        "ใครสร้างคุณ",
    ]
    
    for inp in test_inputs:
        response = match_quick_response(inp)
        if response:
            print(f"✅ '{inp}' → '{response[:50]}...'")
        else:
            print(f"❌ '{inp}' → No match")
    
    # Test memory
    print("\n" + "=" * 50)
    print("Testing Memory")
    print("=" * 50)
    
    memory = get_memory("test-session")
    memory.add_user_message("ช่วยหาไขควง", intent="find_object")
    memory.add_assistant_message("กำลังหาไขควงครับ")
    memory.add_user_message("ขอบคุณ", intent="chitchat")
    memory.add_assistant_message("ยินดีครับ")
    
    print(f"Stats: {memory.get_stats()}")
    print(f"History: {memory.get_history_for_llm()}")
    print(f"Context:\n{memory.get_context_string()}")
