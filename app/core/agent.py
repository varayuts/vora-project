import asyncio
import re
import logging
from typing import Tuple, List, Optional

from ..providers.llm.ollama import OllamaProvider
from ..providers.search.searxng import SearxNGProvider
from ..schemas.agent import RefineResult
from ..core.settings import settings
from .memory import MEMORY

logger = logging.getLogger(__name__)

# --- Providers Setup ---
# gemma3:12b-it-qat = Text Cleaning/Filtering (fast)
LLM_REFINE = OllamaProvider(model=settings.OLLAMA_REFINE_MODEL or "gemma3:12b-it-qat")
# gemma3:27b-it-qat = Main Reasoning (complex tasks)
LLM_ANSWER = OllamaProvider(model=settings.OLLAMA_MODEL)
SEARCH = SearxNGProvider()

LAB_CONTEXT = """
ข้อมูลพื้นที่ห้องแล็บ VORA:
- จุดสำคัญ: โต๊ะทำงาน (Workstation), ตู้เก็บของ (Storage), ประตูหน้า (Entrance), แท่นชาร์จ (Charging Dock)
- สิ่งของที่หาได้: ไขควง, มัลติมิเตอร์, สายไฟ, บอร์ด Arduino
"""

# บังคับให้ Schema ให้ตรงกับที่ Frontend รอรับ
REFINE_SYSTEM = (
    "คุณคือระบบวิเคราะห์คำสั่งเสียงสำหรับหุ่นยนต์ VORA\n"
    "หน้าที่: แก้ไขคำผิดและวิเคราะห์ Intent คืนค่าเป็น JSON เท่านั้น\n"
    "SCHEMA:\n"
    "{\n"
    '  "intent": "navigate|find_object|chitchat|info",\n'
    '  "clean_text": "ข้อความที่แก้คำผิดแล้ว",\n'
    '  "target": "สถานที่หรือสิ่งของที่ระบุ (ถ้าไม่มีให้ใส่ว่าง)",\n'
    '  "must_search": false\n'
    "}\n"
    "- ห้ามตอบข้อความอื่นนอกจาก JSON"
)

_TH_FIX = {
    "หมาสมุด": "ห้องสมุด",
    "วอร่า": "VORA",
    "ไปที่โต๊ะ": "ไปที่โต๊ะทำงาน",
    "หาไขควง": "หาไขควงในตู้เก็บของ"
}

def _clean_gemma_output(text: str) -> str:
    """ลบขยะและแท็กที่มักหลุดมาจาก Gemma 3"""
    if not text: return ""
    # ลบแท็กทางเทคนิคและช่องว่างส่วนเกิน
    text = re.sub(r'<(?:/)?(?:end_of_turn|pad||file|s|u)>', '', text)
    text = re.sub(r'\{\{.*?\}\}', '', text)
    return text.strip()

def _normalize_typos(s: str) -> str:
    out = s
    for k, v in _TH_FIX.items():
        out = out.replace(k, v)
    out = re.sub(r"([ก-๙A-Za-z])\1+", r"\1", out)
    return out

def refine(text: str, lang_hint: str = "th") -> RefineResult:
    text = _normalize_typos(text)
    try:
        data = LLM_REFINE.generate_json(
            system=REFINE_SYSTEM,
            prompt=f"raw_text: {text}",
            temperature=0.1,
            max_tokens=settings.OLLAMA_JSON_MAX_TOKENS,
        )
    except Exception as e:
        logger.error(f"Refine error: {e}")
        data = {}

    # ดึงค่าพร้อมกำหนด Default ป้องกัน undefined
    clean_text = _clean_gemma_output(data.get("clean_text", text))
    intent = data.get("intent", "chitchat")
    target = data.get("target", "")

    return RefineResult(
        language=data.get("language", lang_hint),
        intent=intent,
        clean_text=clean_text,
        short_prompt=clean_text,
        search_query=target,
        entities=[],
        needs_more_info=False,
        missing_info=[],
        must_search=bool(data.get("must_search", False)),
        final_prompt=clean_text,
        notes=target # Frontend มักเรียกหาฟิลด์นี้มาแสดงผลเป้าหมาย
    )

def answer(
    refine_res: RefineResult,
    search_when: str = "never",
    topk: int = 3,
    session_id: str | None = None,
) -> Tuple[str, list]:
    
    memory_ctx = MEMORY.compose_context(session_id) if session_id else ""

    # --- Case 1: Robot Command (Intent: navigate / find_object) ---
    if refine_res.intent in ["navigate", "find_object"]:
        system = (
            "คุณคือผู้ควบคุมหุ่นยนต์ VORA\n"
            f"{LAB_CONTEXT}\n"
            "- ตอบรับคำสั่งสั้นๆ และชัดเจน ห้ามเกิน 1 ประโยค"
        )
        reply = LLM_ANSWER.generate(
            prompt=f"คำสั่ง: {refine_res.clean_text}",
            system=system,
            temperature=0.2,
            max_tokens=100
        )
        reply = _clean_gemma_output(reply)
        if not reply: reply = f"รับทราบครับ กำลังดำเนินการ{refine_res.clean_text}"
        
        if session_id: MEMORY.add(session_id, "assistant", reply)
        return f"🤖 [CMD] {reply}", []

    # --- Case 2: Information / General Chitchat ---
    system = "คุณคือ VORA ผู้ช่วยประจำแล็บ ตอบอย่างกระชับ ฉลาด และเป็นทางการ"
    
    # เช็คเงื่อนไขการค้นเว็บ
    if search_when != "never" and refine_res.must_search:
        reply, sources = web_synth_answer(refine_res.clean_text, memory_block=memory_ctx)
    else:
        prompt = f"บริบท:\n{memory_ctx}\nคำถาม: {refine_res.clean_text}"
        reply = LLM_ANSWER.generate(prompt=prompt, system=system, temperature=0.4, max_tokens=350)
        sources = []

    reply = _clean_gemma_output(reply)
    if not reply: reply = "ขออภัยครับ ผมยังไม่พบข้อมูลที่แน่ชัดในขณะนี้"

    if session_id: MEMORY.add(session_id, "assistant", reply)
    return reply, sources

def web_synth_answer(question: str, memory_block: str = "", topk: int = 3) -> tuple[str, list]:
    res = SEARCH.search(question, num=topk)
    sources = [{"title": r.title, "url": r.url} for r in res] if res else []
    
    if not res:
        return "ไม่พบข้อมูลจากเว็บครับ", []

    context = "\n".join([f"- {r.title}: {r.snippet}" for r in res])
    prompt = f"บริบทเว็บ:\n{context}\nสรุปคำถาม: {question}"
    
    reply = LLM_ANSWER.generate(prompt=prompt, system="สรุปสั้นๆ เป็นภาษาไทย", max_tokens=400)
    return _clean_gemma_output(reply), sources