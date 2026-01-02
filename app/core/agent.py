# app/core/agent.py
from typing import Tuple, List
import asyncio
import re

from ..providers.llm.ollama import OllamaProvider
from ..providers.search.searxng import SearxNGProvider
from ..providers.search.fetch_and_extract import fetch_many
from ..agent.web_synth import SYNTH_PROMPT_TMPL, prepare_snippets, build_sources_block, MIN_SOURCES
from ..schemas.agent import RefineResult
from ..core.settings import settings
from .memory import MEMORY

LLM_ANSWER = OllamaProvider()
LLM_REFINE = OllamaProvider(model=(settings.OLLAMA_REFINE_MODEL or settings.OLLAMA_MODEL))
SEARCH = SearxNGProvider()

REFINE_SYSTEM = (
    "คุณคือตัวกรอง STT ภาษาไทย ให้ส่ง JSON ตรงตาม schema นี้เท่านั้น:\n"
    "{"
    '"language":"...",'
    '"intent":"ask|search|navigate|chitchat|other",'
    '"clean_text":"...",'
    '"short_prompt":"...",'
    '"search_query":"...",'
    '"entities":[{"type":"...","text":"..."}],'
    '"needs_more_info":true|false,'
    '"missing_info":["..."],'
    '"must_search":true|false,'
    '"final_prompt":"...",'
    '"notes":""'
    "}\n"
    "- ห้ามใส่ Markdown หรือข้อความอื่นนอก JSON\n"
    "- แก้คำซ้ำ/สะกดผิดเล็กน้อยใน clean_text\n"
    "- short_prompt <= 20 คำ, search_query เน้นคีย์เวิร์ดค้นเว็บ\n"
)

_TH_FIX = {
    "หมาสมุด": "ห้องสมุด",
    "มหาวิทยาลัยพระจอม 9 พระนครเหนือ": "มหาวิทยาลัยพระจอมเกล้าพระนครเหนือ",
    "มหาวิทยาลัยเทคโนโลยีพจ.": "มหาวิทยาลัยเทคโนโลยีพระจอมเกล้า",
}
_TH_STOP = set("ช่วย หน่อย ล่าสุด ตอนนี้ สรุป สถานการณ์ เกี่ยวกับ ให้หน่อย ยังไง ทางไหน ไปทางไหน บอกทาง".split())


def _normalize_typos(s: str) -> str:
    out = s
    for k, v in _TH_FIX.items():
        out = out.replace(k, v)
    out = re.sub(r"([ก-๙A-Za-z])\1+", r"\1", out)  # ผผม -> ผม
    return out


def _shorten_query(q: str) -> str:
    q2 = re.sub(r"[^0-9A-Za-zก-๙\s]", " ", q)
    toks = [t for t in q2.split() if t and t not in _TH_STOP]
    return " ".join(toks) if toks else q.strip()


def refine(text: str, lang_hint: str = "th") -> RefineResult:
    text = _normalize_typos(text)
    data = LLM_REFINE.generate_json(
        system=REFINE_SYSTEM,
        prompt=f"lang_hint={lang_hint or 'auto'}\nraw_text: {text}\n",
        temperature=0.1,
        top_p=0.9,
        max_tokens=settings.OLLAMA_JSON_MAX_TOKENS,
    )
    language = data.get("language") or (lang_hint or "th")
    clean_text = data.get("clean_text") or text
    short_prompt = data.get("short_prompt") or clean_text
    search_query = data.get("search_query") or _shorten_query(clean_text)
    return RefineResult(
        language=language,
        intent=(data.get("intent") or "other"),
        clean_text=clean_text.strip(),
        short_prompt=short_prompt.strip(),
        search_query=search_query.strip(),
        entities=[
            e for e in (data.get("entities") or [])
            if isinstance(e, dict) and e.get("text")
        ],
        needs_more_info=bool(data.get("needs_more_info", False)),
        missing_info=[
            m for m in (data.get("missing_info") or [])
            if isinstance(m, str)
        ],
        must_search=bool(data.get("must_search", False)),
        final_prompt=(data.get("final_prompt") or short_prompt or clean_text).strip(),
        notes=data.get("notes") or "",
    )


def _search_context(primary_query: str, topk: int = 3) -> Tuple[str, list]:
    res = SEARCH.search(primary_query, num=topk)
    if not res:
        res = SEARCH.search(_shorten_query(primary_query), num=topk)
    if not res:
        res = SEARCH.search(primary_query, engine="wikipedia", num=topk)

    sources = [{"title": r.title, "url": r.url, "engine": r.engine} for r in res]
    bullets = [
        f"- {r.title} — {r.snippet} (source: {r.url})"
        for r in res
        if r.url
    ]
    context = "ข้อมูลจากเว็บ:\n" + "\n".join(bullets) if bullets else ""
    return context, sources


def answer(
    refine_res: RefineResult,
    search_when: str = "always",
    topk: int = 3,
    session_id: str | None = None,
) -> Tuple[str, list]:
    # 1) memory → context (ย่อ ไม่ให้ยาวเกินไป)
    memory_ctx = MEMORY.compose_context(session_id) if session_id else ""
    if memory_ctx:
        # เอาแค่ไม่กี่บรรทัดท้าย ๆ พอเป็นบริบท
        short_mem = "\n".join(memory_ctx.splitlines()[-8:])
        memory_block = f"บริบทก่อนหน้า (ย่อ):\n{short_mem}\n\n"
    else:
        memory_block = ""

    # 2) ตัดสินใจว่าจะค้นเว็บไหม
    context, sources = ("", [])
    should_search = (
        refine_res.must_search
        or (
            search_when in ("always", "auto")
            and refine_res.intent in {"ask", "search", "navigate"}
        )
    )

    if should_search:
        # ใช้โหมดสรุปสังเคราะห์จากหลายแหล่ง
        reply, sources = web_synth_answer(
            refine_res.search_query or refine_res.clean_text,
            memory_block=memory_block,
            topk=topk,
        )
        if session_id:
            MEMORY.add(session_id, "assistant", reply)
        return reply, sources

    # 3) ตอบแบบไม่ค้นเว็บ ใช้ memory แต่อย่าท่องข้อความเก่า
    system = (
        "You are a helpful Thai assistant.\n"
        "- Use the provided context/memory only to understand the situation.\n"
        "- Do NOT copy or repeat long previous messages from memory.\n"
        "- Always answer freshly based on the user's latest request.\n"
        "- If context is insufficient or ambiguous, ask a brief follow-up in Thai.\n"
        "- Be concise."
    )

    base_prompt = (
        refine_res.short_prompt
        or refine_res.final_prompt
        or refine_res.clean_text
    )
    prompt = f"{memory_block}{base_prompt}"

    reply = LLM_ANSWER.generate(
        prompt=prompt,
        system=system,
        temperature=0.3,
        top_p=0.9,
        max_tokens=320,
    ).strip()

    if not reply:
        reply = LLM_ANSWER.generate(
            prompt=base_prompt,
            system=system,
            temperature=0.5,
            top_p=0.95,
            max_tokens=320,
        ).strip()

    if not reply:
        reply = (
            "ขอรายละเอียดเพิ่มนิดนึงครับ เช่น จุดเริ่มต้นที่ชัดเจนหรืออาคารใกล้เคียง"
        )

    if session_id:
        MEMORY.add(session_id, "assistant", reply)

    return reply, []


def web_synth_answer(
    question: str,
    memory_block: str = "",
    topk: int = 3,
) -> tuple[str, list]:
    """ค้นเว็บหลายแหล่ง -> ดึงเนื้อหา -> ส่งให้ LLM สรุปแบบสังเคราะห์
    คืนค่า: (คำตอบ, sources)
    """
    res = SEARCH.search(question, num=topk)
    if not res:
        res = SEARCH.search(_shorten_query(question), num=topk)
    if not res:
        res = SEARCH.search(question, engine="wikipedia", num=topk)

    sources = [{"title": r.title, "url": r.url, "engine": r.engine} for r in res]
    urls = [r["url"] for r in sources if r.get("url")][:6]

    if not urls:
        return "ขออภัย ไม่พบข้อมูลจากเว็บเพียงพอ", sources

    async def _run_fetch():
        from ..providers.search.fetch_and_extract import fetch_many  # local import to avoid cycles
        return await fetch_many(urls)

    try:
        docs = asyncio.run(_run_fetch())
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            docs = loop.run_until_complete(_run_fetch())
        finally:
            loop.close()

    from ..agent.web_synth import (
        prepare_snippets,
        build_sources_block,
        SYNTH_PROMPT_TMPL,
        MIN_SOURCES,
    )
    snippets = prepare_snippets(docs)
    sources_block = (
        build_sources_block(snippets) if snippets else "(ไม่มีเนื้อหาที่ดึงมาได้)"
    )

    question_full = (memory_block + question).strip()
    prompt = SYNTH_PROMPT_TMPL.format(
        question=question_full,
        sources_block=sources_block,
        min_sources=MIN_SOURCES,
    )

    reply = LLM_ANSWER.generate(
        prompt=prompt,
        system=None,
        temperature=0.3,
        top_p=0.9,
        max_tokens=800,
    ).strip()
    if not reply:
        reply = LLM_ANSWER.generate(
            prompt=prompt,
            system=None,
            temperature=0.5,
            top_p=0.95,
            max_tokens=800,
        ).strip()
    if not reply:
        reply = "ขออภัย สรุปจากเว็บไม่ได้ในตอนนี้"

    return reply, sources
