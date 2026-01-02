# app/agent/web_synth.py
from __future__ import annotations
from typing import List, Tuple

MAX_CHARS_PER_SOURCE = 2000
MIN_SOURCES = 3

SYNTH_PROMPT_TMPL = """คุณคือนักสรุปข้อมูลภาษาไทยที่รัดกุมและเชื่อถือได้
งานของคุณ: สรุปคำตอบจากหลายแหล่งข้อมูลด้านล่าง โดย:
- เขียนเป็นไทย กระชับ มีตัวเลข/สถานที่/วันที่เท่าที่จำเป็น
- รวมให้เป็นเนื้อเดียวกัน ไม่แปะลิงก์กลางย่อหน้า
- ถ้าข้อมูลขัดแย้ง ให้ระบุความไม่แน่นอนสั้น ๆ
- ปิดท้ายด้วยหัวข้อ "แหล่งอ้างอิง" เป็นชื่อเว็บสั้น ๆ 3–5 รายการ (ไม่ต้องใส่ URL)
- ถ้าแหล่งข้อมูล < {min_sources} ให้ระบุว่า “ข้อมูลจากเว็บมีจำกัด จึงสรุปเท่าที่มี”

คำถาม:
{question}

แหล่งข้อมูล (ย่อ):
{sources_block}

เขียนคำตอบ 2–4 ย่อหน้า แล้วตามด้วย:
แหล่งอ้างอิง:
- [1] ชื่อเว็บ
- [2] ชื่อเว็บ
- [3] ชื่อเว็บ
"""


def prepare_snippets(docs: List[dict]) -> List[Tuple[int, str, str]]:
    used = set()
    out: List[Tuple[int,str,str]] = []
    idx = 1
    for d in docs:
        if not d.get("ok"):
            continue
        dom = d.get("domain") or "source"
        if dom in used:
            continue
        used.add(dom)
        text = (d.get("text") or "")[:MAX_CHARS_PER_SOURCE]
        out.append((idx, dom, text))
        idx += 1
    return out

def build_sources_block(snippets: List[Tuple[int,str,str]]) -> str:
    lines = []
    for idx, label, text in snippets:
        lines.append(f"[{idx}] {label}\n{text}\n")
    return "\n".join(lines)
