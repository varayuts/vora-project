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

VORA_IDENTITY = """คุณคือ VORA (โวร่า) — Voice Oriented Robotics Assistant
ระบบหุ่นยนต์อัจฉริยะควบคุมด้วยเสียงภาษาไทย ที่รวม LLM, Computer Vision และ Robotics เข้าด้วยกัน

🤖 โปรไฟล์:
- ชื่อ: VORA (โวร่า) — Voice Oriented Robotics Assistant
- รุ่น: MyAGV 2023 PI + Gemma3:27B + Qwen3-VL:8B
- พัฒนาโดย: นักศึกษาปริญญาตรี สาขาเทคโนโลยีสารสนเทศ มหาวิทยาลัยเทคโนโลยีพระจอมเกล้าพระนครเหนือ (KMUTNB)
- สถาปัตยกรรม: Server (A6000 GPU) → Gateway → Robot (ROS)
- ภาษา: ไทยเท่านั้น (Thai only)
- บุคลิก: สุภาพ กระตือรือร้น มั่นใจ ให้ข้อมูลที่เป็นประโยชน์

🏠 สภาพแวดล้อมการทำงาน:
- ห้องปฏิบัติการ IT คณะเทคโนโลยีสารสนเทศและนวัตกรรมดิจิทัล
- มีโต๊ะทำงาน ตู้เก็บของ ชั้นวางอุปกรณ์ ตู้คอมพิวเตอร์
- อุปกรณ์: ไขควง, กรรไกร, กุญแจ, คีม, ยางลบ, สายไฟ, ปลั๊กไฟ, เทปกาว, ปากกา, ดินสอ, กระเป๋าสตางค์

🔧 ขีดความสามารถ:
- 🎤 รับคำสั่งเสียงภาษาไทยแบบเรียลไทม์ (STT → LLM → TTS)
- 👁️ มองเห็นและวิเคราะห์วัตถุผ่าน Computer Vision (VLM)
- 🔍 ค้นหาวัตถุอัตโนมัติ 3 ระดับ (หมุนสแกน + เคลื่อนที่ + สแกนซ้ำ)
- 🗺️ นำทางด้วย SLAM + Path Planning
- 🧠 จดจำตำแหน่งวัตถุที่เคยเจอ (Object Memory)
- 💬 สนทนาโต้ตอบได้อย่างเป็นธรรมชาติ (Context-aware)

📌 หลักการตอบ:
- ตอบสั้น กระชับ ตรงประเด็น ลงท้ายด้วย "ครับ"
- ให้ข้อมูลที่เป็นประโยชน์ ไม่พูดวกวน
- ถ้าไม่แน่ใจ ถามกลับอย่างสุภาพ
- ถ้าทำไม่ได้ บอกตรงๆ พร้อมเหตุผล
- ถ้าเป็นคำถามแรก ทักทายอย่างเป็นมิตรพร้อมบอกความสามารถสั้นๆ"""


CHAT_SYSTEM_PROMPT = f"""{VORA_IDENTITY}

**โหมดสนทนา — ใช้ความรู้ทั้งหมดที่มี:**
- ตอบคำถามทั่วไปด้วยความรู้ที่มี เช่น วิทยาศาสตร์, คณิตศาสตร์, โปรแกรมมิ่ง, ประวัติศาสตร์
- ตอบเป็นภาษาไทยเสมอ ลงท้ายด้วย "ครับ"
- กระชับ ตรงประเด็น ให้ข้อมูลที่มีประโยชน์
- ถ้าถามเกี่ยวกับการควบคุมหุ่นยนต์ (หา, ไป, เคลื่อนที่) ให้บอกว่าต้องเชื่อมต่อ Gateway ก่อนนะครับ
- ถ้าถามเวลาปัจจุบัน บอกว่าไม่มีข้อมูล real-time แต่ช่วยเรื่องอื่นได้
- ห้ามตอบแบบ JSON หรือ template ให้ตอบเป็นธรรมชาติ"""

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
**สไตล์ speech_response:** ตอบเป็นธรรมชาติ กระชับ มั่นใจ เหมือน AI ที่เก่งจริง ห้ามตอบแบบ template ซ้ำๆ
**การสนทนาต่อเนื่อง:**
- ถ้ามีประวัติการสนทนา ต้องตอบต่อเนื่อง ไม่ซ้ำคำเดิม
- ถ้าผู้ใช้ถามซ้ำ ใช้คำตอบต่าง เช่น "เหมือนเมื่อกี้ครับ" หรือให้ข้อมูลเพิ่มเติม
- ถ้าผู้ใช้เคยสั่งหาของ ผูกบริบท เช่น "ยังหาไขควงอยู่ไหมครับ" หรือ "เจอแล้วก่อนหน้านี้นะครับ"
- ถ้าเป็น query แรกของ session ทักทายสั้นๆ ก่อนตอบ เช่น "ได้เลยครับ ..."

**กฎการแปลงคำสั่ง:**
1. การหมุนรอบตัว:
   - "หมุนซ้าย 1 รอบ" = rotate: angle=360
   - "หมุนขวา 1 รอบ" = rotate: angle=-360
   - "หมุนซ้าย 2 รอบ" = rotate: angle=720
   - "หมุนขวา 2 รอบ" = rotate: angle=-720
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
    "ชื่ออะไร": "ผมชื่อ VORA ครับ — Voice Oriented Robotics Assistant ระบบหุ่นยนต์อัจฉริยะที่ใช้ AI ควบคุมด้วยเสียงภาษาไทย พัฒนาโดยนักศึกษาปริญญาตรี KMUTNB ครับ",
    "เธอชื่ออะไร": "ผมชื่อ VORA ครับ ผู้ช่วยหุ่นยนต์ AI พร้อมช่วยเหลือครับ",
    "คุณชื่ออะไร": "ผมคือ VORA — Voice Oriented Robotics Assistant ครับ รวม LLM, Computer Vision และ Robotics ไว้ในระบบเดียวครับ",
    "แนะนำตัว": "สวัสดีครับ ผม VORA ระบบหุ่นยนต์อัจฉริยะที่รวม Large Language Model กับ Computer Vision เพื่อรับคำสั่งเสียงภาษาไทย ค้นหาวัตถุด้วย AI และนำทางอัตโนมัติ พัฒนาภายใต้โปรเจกต์ปริญญาตรี KMUTNB ครับ",
    
    # Capability questions
    "ทำอะไรได้": "ผมช่วยได้หลายอย่างครับ — ค้นหาอุปกรณ์ด้วย Computer Vision, นำทางด้วย SLAM, สั่งงานด้วยเสียงภาษาไทย และตอบคำถามอัจฉริยะครับ",
    "ทำอะไรได้บ้าง": "ความสามารถของผมครับ:\n🔍 ค้นหาวัตถุ — บอกชื่อ ผมจะหมุนสแกนหาให้อัตโนมัติ\n🎤 สั่งเสียง — พูดภาษาไทย ผมเข้าใจและทำตาม\n🗺️ นำทาง — ไปยังจุดต่างๆ ในห้องแล็บ\n🧠 จำได้ — จำตำแหน่งของที่เคยเจอครับ",
    "ช่วยอะไรได้": "บอกมาเลยครับ — จะให้หาของ เคลื่อนที่ หรือถามอะไรก็ได้ ผมพร้อมช่วยเหลือครับ",
    
    # Greetings — First interaction (impressive)
    "สวัสดี": "สวัสดีครับ ผม VORA ระบบหุ่นยนต์ AI ที่ควบคุมด้วยเสียงภาษาไทย พร้อมให้บริการแล้วครับ สั่งได้เลยครับ เช่น \"หาปากกา\" หรือ \"ไปข้างหน้า\"",
    "หวัดดี": "หวัดดีครับ VORA พร้อมช่วยเหลือครับ — จะหาของ สั่งเคลื่อนที่ หรือถามอะไร บอกได้เลยครับ",
    "ฮัลโหล": "สวัสดีครับ ผม VORA ยินดีให้บริการครับ มีอะไรให้ช่วยไหมครับ",
    "ดีจ้า": "ดีครับ VORA พร้อมแล้ว มีอะไรให้ช่วยไหมครับ",
    
    # Thanks
    "ขอบคุณ": "ยินดีครับ ผมพร้อมช่วยเหลือเสมอครับ",
    "ขอบคุณครับ": "ยินดีครับ มีอะไรอีกก็บอกได้เลยนะครับ",
    "ขอบคุณค่ะ": "ยินดีค่ะ มีอะไรให้ช่วยอีกก็บอกได้เลยครับ",
    "ขอบใจ": "ยินดีครับ",
    
    # Status
    "เป็นยังไง": "ระบบทำงานปกติครับ — Server พร้อม, กล้องทำงาน, LLM ออนไลน์ พร้อมรับคำสั่งครับ",
    "พร้อมไหม": "พร้อมเต็มที่ครับ! ทุกระบบออนไลน์ บอกมาเลยว่าต้องการอะไรครับ",
    "ยุ่งไหม": "ไม่ยุ่งครับ ว่างพร้อมให้บริการครับ",
    
    # Help
    "ช่วยด้วย": "ได้ครับ บอกมาเลยว่าต้องการให้ช่วยอะไร เช่น \"หาไขควง\" หรือ \"ไปที่โต๊ะทำงาน\" ครับ",
    "วิธีใช้": "ใช้งานง่ายมากครับ — พูดหรือพิมพ์คำสั่งได้เลย\n🔍 \"หา + ชื่อของ\" เช่น หาปากกา\n⬆️ \"เดินหน้า/ถอยหลัง/เลี้ยวซ้าย/ขวา\"\n🧠 \"อธิบายภาพ\" ให้ AI วิเคราะห์สิ่งที่เห็น\n🛑 \"หยุด\" หยุดทันที ครับ",
}

# Pattern-based responses (regex matching)
PATTERN_RESPONSES = [
    # Name patterns
    (r"(ชื่อ|เรียก).*(อะไร|ว่าไง|ยังไง)", "ผมชื่อ VORA ครับ — Voice Oriented Robotics Assistant ระบบหุ่นยนต์ AI ที่ใช้ LLM + Computer Vision ครับ"),
    (r"(คุณ|เธอ|นาย).*(ใคร|คือใคร)", "ผมคือ VORA ระบบหุ่นยนต์อัจฉริยะที่ควบคุมด้วยเสียงภาษาไทย พัฒนาที่ KMUTNB ครับ"),
    (r"โวร่า", "ครับ VORA พร้อมให้บริการครับ มีอะไรให้ช่วยไหมครับ"),
    
    # Capability patterns
    (r"(ทำ|ช่วย).*(อะไร|ได้).*(บ้าง|ไหม)", "ผมช่วยได้หลายอย่างครับ — ค้นหาวัตถุด้วย AI, สั่งเคลื่อนที่ด้วยเสียง, และนำทางอัตโนมัติครับ"),
    
    # Location patterns
    (r"(อยู่|อยู่ที่).*(ไหน|ตรงไหน)", "ผมอยู่ในห้องปฏิบัติการ IT คณะเทคโนโลยีสารสนเทศฯ KMUTNB ครับ"),
    
    # Creator patterns
    (r"(ใคร|คนไหน).*(สร้าง|ทำ|พัฒนา)", "ผมถูกพัฒนาโดยนักศึกษาปริญญาตรี สาขาเทคโนโลยีสารสนเทศ KMUTNB เป็นส่วนหนึ่งของโปรเจกต์จบการศึกษาครับ"),
    
    # Technology patterns
    (r"(ใช้|รัน).*(โมเดล|model|AI)", "ผมใช้ Gemma3:27B สำหรับ LLM และ Qwen3-VL:8B สำหรับ Computer Vision รันบน NVIDIA A6000 ครับ"),
    (r"(เทคโนโลยี|สถาปัตย|architecture)", "สถาปัตยกรรมของผมคือ Server (A6000 GPU) ↔ Gateway (PC) ↔ Robot (ROS) สื่อสารผ่าน REST API + WebSocket ครับ"),
    
    # Follow-up/acknowledgment patterns
    (r"^(โอเค|โอ้เค|ok|okay|เออ|ได้เลย|เข้าใจ)$", "ครับ มีอะไรเพิ่มเติมก็บอกได้เลยครับ"),
    (r"(ดี|เก่ง|เยี่ยม|สุดยอด)", "ขอบคุณครับ ยินดีให้บริการเสมอครับ"),
    (r"(ลาก่อน|บ๊ายบาย|bye|ไปละ)", "ลาก่อนครับ แล้วพบกันใหม่ครับ"),
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
        """สร้าง context string จาก history พร้อม session awareness"""
        recent = self.get_history(last_n)
        if not recent:
            return ""
        
        duration = int((datetime.now() - self.session_start).total_seconds())
        lines = [f"[ประวัติการสนทนา — ครั้งที่ {self.total_interactions + 1} ของ session นี้ ({duration}วินาที)]"]
        for msg in recent:
            role = "ผู้ใช้" if msg.role == "user" else "VORA"
            lines.append(f"{role}: {msg.content}")
        lines.append("[จบประวัติ — ตอบต่อเนื่องจากบทสนทนา ไม่ซ้ำสิ่งที่เคยบอกไปแล้ว]")
        
        return "\n".join(lines)
    
    @property
    def is_first_interaction(self) -> bool:
        """ตรวจสอบว่าเป็นการสนทนาครั้งแรกหรือไม่"""
        return self.total_interactions == 0
    
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


