# app/core/text_preprocessor.py
"""
VORA Text Preprocessor
=======================
Agent สำหรับ clean และ validate text ก่อนส่งไป LLM Reasoning

Functions:
1. Filter - กรองคำพูดที่ไม่มีความหมาย (เอ่อ, ครับ, คือ)
2. Correct - แก้ไขคำที่ STT แปลผิด
3. Complete - ตรวจสอบว่าประโยคสมบูรณ์พอหรือยัง
4. Normalize - ทำให้ข้อความเป็นมาตรฐาน
"""

import re
import logging
from typing import Optional, Tuple, List, Dict
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger("vora.preprocessor")


class TextStatus(str, Enum):
    """สถานะของ text หลังประมวลผล"""
    VALID = "valid"           # ส่ง LLM ได้
    INCOMPLETE = "incomplete" # ยังไม่สมบูรณ์ รอเพิ่ม
    FILLER_ONLY = "filler"    # มีแต่ filler words ไม่ต้องส่ง
    TOO_SHORT = "too_short"   # สั้นเกินไป
    GREETING = "greeting"     # เป็นคำทักทาย ตอบ chitchat ได้เลย


@dataclass
class PreprocessResult:
    """ผลลัพธ์จากการ preprocess"""
    status: TextStatus
    original: str
    cleaned: str
    corrections: List[str]  # รายการการแก้ไข
    should_process: bool    # ควรส่งต่อ LLM หรือไม่
    quick_response: Optional[str] = None  # ตอบกลับทันทีไม่ต้องผ่าน LLM


# ============ Thai Filler Words ============
# คำที่มักพูดโดยไม่มีความหมาย
FILLER_WORDS = {
    # Filler sounds
    "เอ่อ", "อืม", "อ่า", "อ้า", "เออ", "อื้อ", "ฮึ", "ฮืม", "เอิ่ม",
    # Confirmations/endings (standalone)
    "ครับ", "ค่ะ", "คะ", "จ้า", "จ๊ะ", "นะ", "น่ะ", "นะครับ", "นะคะ",
    "เนอะ", "เน้อ", "ล่ะ", "สิ", "ซิ", "เถอะ",
    # Hesitations  
    "คือ", "ก็", "แบบ", "งั้น", "อ่ะ", "ฮะ", "หา", "เหรอ",
    # Single letters/sounds (STT errors)
    "เอ", "เอส", "อี", "เอ็ม", "เค", "โอ", "เจ",
}

# คำที่ถ้ามีอย่างเดียวไม่ต้อง process
STANDALONE_SKIP = {
    "ครับ", "ค่ะ", "คะ", "จ้า", "จ๊ะ", "โอเค", "ได้", "ดี", 
    "เอ่อ", "อืม", "คือ", "นะครับ", "นะคะ", "เอส", "เอ็ม",
    "หา", "อะไร", "เหรอ", "จริงเหรอ", "อ้อ",
}

# คำทักทาย - ตอบ chitchat ได้เลย
GREETINGS = {
    "สวัสดี", "สวัสดีครับ", "สวัสดีค่ะ", 
    "หวัดดี", "ฮัลโหล", "ฮาโหล", "ไฮ", "เฮ้",
    "ดีครับ", "ดีค่ะ", "ดีจ้า",
}

# Quick responses สำหรับคำเฉพาะ
QUICK_RESPONSES = {
    "ขอบคุณ": "ยินดีครับ มีอะไรให้ช่วยอีกไหม",
    "ขอบคุณครับ": "ยินดีครับ มีอะไรให้ช่วยอีกไหม",
    "ขอบคุณค่ะ": "ยินดีค่ะ มีอะไรให้ช่วยอีกไหมคะ",
    "โอเค": "ครับ มีอะไรให้ช่วยไหม",
    "ได้เลย": "ครับ พร้อมรับคำสั่งครับ",
    "ได้แล้ว": "ครับ มีอะไรให้ช่วยต่อไหม",
    "หยุด": None,  # None = ส่งต่อ LLM (เป็น control command)
    "stop": None,
}


# ============ STT Correction Dictionary ============
# คำที่ STT มักแปลผิด → แก้เป็นคำที่ถูก
STT_CORRECTIONS = {
    # Lab equipment
    "ปลากา": "ปากกา",
    "ปลอกกา": "ปากกา", 
    "เทปก่า": "เทปกาว",
    "เท็ปกาว": "เทปกาว",
    "เทส": "เทป",  # STT error: เทป → เทส
    "เท็ป": "เทป",
    "ไหควง": "ไขควง",
    "ขันควง": "ไขควง",
    "ไขขวง": "ไขควง",
    "กันไกร": "กรรไกร",
    "กรรไก": "กรรไกร",
    "กุญแจ": "กุญแจ",  # เหมือนกัน แต่ใส่ไว้เผื่อ
    "กุ้ญแจ": "กุญแจ",
    "ปลักไฟ": "ปลั๊กไฟ",
    "ปลั๊ก": "ปลั๊กไฟ",
    "สายฟ้า": "สายไฟ",
    "มัลติ": "มัลติมิเตอร์",
    "อาดูโน่": "Arduino",
    "อาดูอิโน่": "Arduino",
    "ราสเบอร์รี่": "Raspberry Pi",
    "หัวแล้ง": "หัวแร้ง",
    
    # Common STT errors - pronouns
    "กู": "ฉัน",
    "มึง": "คุณ",
    "เมิง": "คุณ",
    
    # Politeness
    "วะ": "ครับ",
    # NOTE: ลบ 'อะ' ออกเพราะจะทำลายคำไทยอื่น เช่น 'อะไร' → 'ไร'
    
    # Robot commands
    "เดิ้นหน้า": "เดินหน้า",
    "ถอยหลั่ง": "ถอยหลัง",
    "หมุ่น": "หมุน",
    "ซ้าว": "ซ้าย",
    "ข้วา": "ขวา",
    
    # Locations  
    "แทนชาร์จ": "แท่นชาร์จ",
    "โตะ": "โต๊ะ",
    "โต้ะ": "โต๊ะ",
    "ตู้เก็บข้อง": "ตู้เก็บของ",
    "ชันวาง": "ชั้นวาง",
    
    # Actions
    "หาให้หน่อย": "ช่วยหาให้หน่อย",
    "เอามาให้": "ช่วยเอามาให้",
    "เอามา": "ช่วยเอามา",
}

# Pattern-based corrections (regex)
PATTERN_CORRECTIONS = [
    # "X อยู่ด้วย" → "X ด้วย" (ลบคำฟุ่มเฟือย)
    (r"(.+?)อยู่ด้วย", r"\1ด้วย"),
    # "ช่วย X ให้หน่อย" → "ช่วยหา X ให้หน่อย"
    (r"ช่วย\s*([ก-๙]+)\s*ให้หน่อย", r"ช่วยหา \1 ให้หน่อย"),
    # "ต้อง X ด้วย" → "ต้องการหา X ด้วย" (need X)
    (r"ต้อง\s*([ก-๙a-zA-Z]+)\s*ด้วย", r"ต้องการหา \1 ด้วย"),
    # "ต้อง X" (standalone) → "ต้องการ X"
    (r"ต้อง\s+([ก-๙a-zA-Z]+)$", r"ต้องการ \1"),
    # Remove repeated words
    (r"(\S+)\s+\1", r"\1"),
]


# ============ Core Functions ============

def clean_fillers(text: str) -> str:
    """ลบ filler words ออกจาก text"""
    words = text.split()
    cleaned = []
    
    for word in words:
        # ลบ filler ที่อยู่ต้นหรือท้ายประโยค
        word_lower = word.lower()
        if word_lower not in FILLER_WORDS:
            cleaned.append(word)
        elif cleaned:  # ถ้ามีคำอื่นแล้ว และ filler อยู่ท้าย อาจเก็บไว้
            # เก็บ ครับ/ค่ะ ที่ท้ายประโยค (ถ้ามีคำอื่นนำหน้า)
            if word_lower in {"ครับ", "ค่ะ", "คะ", "จ้า"}:
                cleaned.append(word)
    
    return " ".join(cleaned)


def correct_stt_errors(text: str) -> Tuple[str, List[str]]:
    """แก้ไขคำที่ STT แปลผิด"""
    corrections = []
    result = text
    
    # Word-based corrections
    for wrong, correct in STT_CORRECTIONS.items():
        if wrong in result:
            result = result.replace(wrong, correct)
            if wrong != correct:  # ไม่ log ถ้าเหมือนกัน
                corrections.append(f"'{wrong}' → '{correct}'")
    
    # Pattern-based corrections
    for pattern, replacement in PATTERN_CORRECTIONS:
        new_result = re.sub(pattern, replacement, result)
        if new_result != result:
            corrections.append(f"pattern: {pattern}")
            result = new_result
    
    return result.strip(), corrections


def normalize_text(text: str) -> str:
    """ทำให้ text เป็นมาตรฐาน"""
    # 1. ลบช่องว่างเกิน
    text = re.sub(r'\s+', ' ', text).strip()
    
    # 2. ลบอักขระพิเศษที่ไม่จำเป็น
    text = re.sub(r'[^\u0E00-\u0E7Fa-zA-Z0-9\s\.,!?]', '', text)
    
    # 3. ลบจุด/comma ซ้ำ
    text = re.sub(r'[.,]{2,}', '.', text)
    
    return text.strip()


def is_complete_sentence(text: str) -> bool:
    """ตรวจสอบว่าประโยคสมบูรณ์พอส่ง LLM หรือไม่"""
    # ภาษาไทยไม่มีช่องว่าง ดังนั้นใช้จำนวนตัวอักษรแทนจำนวนคำ
    
    # 1. ต้องมีอย่างน้อย 4 ตัวอักษร (ยกเว้นคำสั่งสั้นๆ)
    if len(text) < 4:
        single_commands = {"หยุด", "stop"}
        if text in single_commands:
            return True
        return False
    
    # 2. ต้องมี verb หรือ action word
    action_indicators = [
        "หา", "ช่วย", "ไป", "มา", "เอา", "ดู", "บอก", "ทำ",
        "หยุด", "เดิน", "หมุน", "ถอย", "เลี้ยว",
        "อะไร", "ที่ไหน", "ยังไง", "เท่าไหร่",
        "หน่อย", "ให้", "ที่", "ตรง", "ข้าง",
        "ต้อง", "ต้องการ", "อยาก", "ขอ",  # need/want
    ]
    
    has_action = any(action in text for action in action_indicators)
    
    # 3. ถ้าไม่มี action แต่มีชื่อสิ่งของ หรือ location → ถือว่าสมบูรณ์
    object_indicators = [
        "ไขควง", "กรรไกร", "กุญแจ", "คีม", "ยางลบ",
        "สายไฟ", "ปลั๊กไฟ", "เทปกาว", "เทป", "มัลติมิเตอร์",
        "Arduino", "Raspberry", "USB", "หัวแร้ง", "ปากกา",
    ]
    
    location_indicators = [
        "โต๊ะ", "ตู้", "ประตู", "แท่น", "มุม", "หน้าต่าง", "ชั้น",
        "ห้อง", "แล็บ", "ข้าง", "หน้า", "หลัง",
    ]
    
    has_object = any(obj in text for obj in object_indicators)
    has_location = any(loc in text for loc in location_indicators)
    
    # ถ้ามี action + (object หรือ location) = สมบูรณ์
    if has_action and (has_object or has_location):
        return True
    # ถ้ามี object + location = สมบูรณ์
    if has_object and has_location:
        return True
    # ถ้ามี action เพียงพอ (มี action แบบ complete เช่น "หยุด")
    if text in {"หยุด", "stop", "เดินหน้า", "ถอยหลัง", "หมุนซ้าย", "หมุนขวา"}:
        return True
    # ถ้ามี "หา/ต้อง/อยาก" + object ถือว่าสมบูรณ์
    if ("หา" in text or "ต้อง" in text or "อยาก" in text or "ขอ" in text) and has_object:
        return True
    # ถ้ามี "ไป" + location ถือว่าสมบูรณ์
    if "ไป" in text and has_location:
        return True
    # ถ้ามี "ต้อง/ต้องการ/อยาก" + action ถือว่าสมบูรณ์
    if ("ต้อง" in text or "อยาก" in text or "ขอ" in text) and ("หา" in text or "ไป" in text or "ดู" in text):
        return True
    
    # ถ้ามี action + มีความยาวพอ (> 8 ตัวอักษร) ถือว่าสมบูรณ์
    if has_action and len(text) > 8:
        return True
    
    return False


def detect_quick_response(text: str) -> Optional[str]:
    """ตรวจสอบว่าตอบได้ทันทีไหม"""
    text_lower = text.strip()
    
    # Check exact match
    if text_lower in QUICK_RESPONSES:
        return QUICK_RESPONSES[text_lower]
    
    # Check greetings
    for greeting in GREETINGS:
        if greeting in text_lower:
            return "สวัสดีครับ ผม VORA พร้อมให้บริการ มีอะไรให้ช่วยไหมครับ"
    
    return None


# ============ Main Preprocessor ============

def preprocess(text: str) -> PreprocessResult:
    """
    Main function: ประมวลผล text ก่อนส่ง LLM
    
    Returns:
        PreprocessResult with status and cleaned text
    """
    original = text.strip()
    corrections = []
    
    logger.info(f"📝 Preprocessing: '{original}'")
    
    # 1. Normalize
    normalized = normalize_text(original)
    
    # 2. Check if too short
    if len(normalized) < 2:
        logger.info("⏭️ Too short, skipping")
        return PreprocessResult(
            status=TextStatus.TOO_SHORT,
            original=original,
            cleaned="",
            corrections=[],
            should_process=False
        )
    
    # 3. Check standalone skip words (filler only)
    if normalized in STANDALONE_SKIP:
        logger.info(f"⏭️ Filler only: '{normalized}'")
        return PreprocessResult(
            status=TextStatus.FILLER_ONLY,
            original=original,
            cleaned=normalized,
            corrections=[],
            should_process=False,
            quick_response=None
        )
    
    # 4. Check quick response (greetings, thanks, etc.)
    quick_resp = detect_quick_response(normalized)
    if quick_resp:
        logger.info(f"⚡ Quick response for: '{normalized}'")
        return PreprocessResult(
            status=TextStatus.GREETING,
            original=original,
            cleaned=normalized,
            corrections=[],
            should_process=False,  # ไม่ต้องส่ง LLM
            quick_response=quick_resp
        )
    
    # 5. Correct STT errors
    corrected, stt_corrections = correct_stt_errors(normalized)
    corrections.extend(stt_corrections)
    
    # 6. Clean filler words
    cleaned = clean_fillers(corrected)
    
    # 7. Final check - is it complete?
    if not cleaned or len(cleaned) < 2:
        logger.info(f"⏭️ Empty after cleaning")
        return PreprocessResult(
            status=TextStatus.FILLER_ONLY,
            original=original,
            cleaned="",
            corrections=corrections,
            should_process=False
        )
    
    if not is_complete_sentence(cleaned):
        # ถ้ามีตัวอักษรไทย >= 3 ตัว ถือว่าเป็น chitchat — ส่งต่อ LLM ได้
        thai_chars = sum(1 for c in cleaned if '\u0E00' <= c <= '\u0E7F')
        if thai_chars >= 3 or len(cleaned) >= 6:
            logger.info(f"💬 Treating as chitchat: '{cleaned}' (thai_chars={thai_chars})")
            return PreprocessResult(
                status=TextStatus.VALID,
                original=original,
                cleaned=cleaned,
                corrections=corrections,
                should_process=True
            )
        logger.info(f"⏳ Incomplete sentence: '{cleaned}'")
        return PreprocessResult(
            status=TextStatus.INCOMPLETE,
            original=original,
            cleaned=cleaned,
            corrections=corrections,
            should_process=False  # รอเพิ่มข้อความ
        )
    
    # 8. Valid - ready to process
    logger.info(f"✅ Valid: '{original}' → '{cleaned}'")
    if corrections:
        logger.info(f"   Corrections: {corrections}")
    
    return PreprocessResult(
        status=TextStatus.VALID,
        original=original,
        cleaned=cleaned,
        corrections=corrections,
        should_process=True
    )


# ============ Accumulator for Incomplete Text ============

class TextAccumulator:
    """
    สะสม text ที่ยังไม่สมบูรณ์ รอจนครบแล้วค่อย process
    """
    def __init__(self, timeout_sec: float = 3.0, max_fragments: int = 5):
        self.buffer: List[str] = []
        self.timeout_sec = timeout_sec
        self.max_fragments = max_fragments
        self.last_update = 0.0
    
    def add(self, text: str) -> Optional[PreprocessResult]:
        """
        เพิ่ม text เข้า buffer และ try process
        
        Returns:
            PreprocessResult ถ้าพร้อม process, None ถ้ายังรอ
        """
        import time
        current_time = time.time()
        
        # Reset buffer ถ้า timeout
        if self.buffer and (current_time - self.last_update) > self.timeout_sec:
            logger.info(f"⏰ Buffer timeout, clearing: {self.buffer}")
            self.buffer.clear()
        
        self.last_update = current_time
        
        # Preprocess new text
        result = preprocess(text)
        
        # If valid, return immediately
        if result.status == TextStatus.VALID:
            self.buffer.clear()  # Clear any pending
            return result
        
        # If quick response available
        if result.quick_response:
            self.buffer.clear()
            return result
        
        # If filler only or too short, skip
        if result.status in (TextStatus.FILLER_ONLY, TextStatus.TOO_SHORT):
            return None
        
        # If incomplete, add to buffer
        if result.status == TextStatus.INCOMPLETE and result.cleaned:
            self.buffer.append(result.cleaned)
            
            # Try combining buffer
            combined = " ".join(self.buffer)
            combined_result = preprocess(combined)
            
            if combined_result.status == TextStatus.VALID:
                logger.info(f"✅ Buffer combined: {self.buffer} → '{combined}'")
                self.buffer.clear()
                return combined_result
            
            # If buffer too long, force process
            if len(self.buffer) >= self.max_fragments:
                logger.info(f"⚠️ Buffer full, forcing: '{combined}'")
                self.buffer.clear()
                return PreprocessResult(
                    status=TextStatus.VALID,
                    original=combined,
                    cleaned=combined,
                    corrections=[],
                    should_process=True
                )
        
        return None
    
    def flush(self) -> Optional[PreprocessResult]:
        """Force process whatever is in buffer"""
        if not self.buffer:
            return None
        
        combined = " ".join(self.buffer)
        self.buffer.clear()
        
        if combined.strip():
            return PreprocessResult(
                status=TextStatus.VALID,
                original=combined,
                cleaned=combined,
                corrections=[],
                should_process=True
            )
        return None
    
    def clear(self):
        """Clear buffer"""
        self.buffer.clear()


# ============ Utility Functions ============

def should_skip(text: str) -> bool:
    """Quick check: ควรข้าม text นี้หรือไม่"""
    result = preprocess(text)
    return not result.should_process


def get_cleaned(text: str) -> str:
    """Quick clean: รับ cleaned text"""
    result = preprocess(text)
    return result.cleaned if result.cleaned else text


# ============ Test ============
if __name__ == "__main__":
    # Test cases
    test_cases = [
        "เอ่อ",
        "ครับ",
        "คือ",
        "นะครับ",
        "ฮัลโหล",
        "ขอบคุณครับ",
        "เอ่อ กูต้องเทปอยู่ด้วย",
        "ช่วยหาปลากาให้หน่อย",
        "แล้วก็เอ็มช่วยหาปลากาให้หน่อยข้างหน้าห้องแล็บครับโต๊ะที่หนึ่ง",
        "หยุด",
        "ไปที่โต๊ะทำงาน",
        "หาไขควงให้หน่อย",
    ]
    
    print("=" * 60)
    print("VORA Text Preprocessor Test")
    print("=" * 60)
    
    for text in test_cases:
        result = preprocess(text)
        print(f"\n📝 Input: '{text}'")
        print(f"   Status: {result.status.value}")
        print(f"   Cleaned: '{result.cleaned}'")
        print(f"   Process: {result.should_process}")
        if result.corrections:
            print(f"   Fixes: {result.corrections}")
        if result.quick_response:
            print(f"   Quick: '{result.quick_response}'")


