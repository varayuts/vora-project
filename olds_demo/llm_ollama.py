# app/llm_ollama.py
import os, json, re, urllib.request

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")
MODEL_NAME  = os.getenv("OLLAMA_MODEL", "gpt-oss:20b")

def _post_json(url: str, payload: dict, timeout: int = 60) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
        return json.loads(body)

def generate(prompt: str,
             system: str | None = None,
             temperature: float = 0.3,
             top_p: float = 0.9,
             max_tokens: int | None = None,
             model: str | None = None) -> str:
    """
    เรียก Ollama /api/generate แบบ non-stream → คืนสตริงข้อความ
    """
    url = f"{OLLAMA_HOST}/api/generate"
    payload: dict = {
        "model": model or MODEL_NAME,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": temperature,
            "top_p": top_p,
        }
    }
    if system:
        payload["system"] = system
    if max_tokens is not None:
        payload["options"]["num_predict"] = max_tokens

    res = _post_json(url, payload, timeout=120)
    return (res.get("response") or "").strip()

def generate_json(prompt: str,
                  system: str,
                  temperature: float = 0.1,
                  top_p: float = 0.9,
                  max_tokens: int | None = 512) -> dict:
    """
    เรียกโมเดลแล้ว 'พยายาม' ให้คืน JSON เท่านั้น
    ถ้าได้ไม่ใช่ JSON จะพยายามตัดส่วนที่เป็น JSON ก้อนแรกออกมา
    """
    txt = generate(
        prompt=prompt,
        system=(
            system
            + "\n\n!!! IMPORTANT: Return ONLY valid JSON. No explanation. No markdown. No preface."
        ),
        temperature=temperature,
        top_p=top_p,
        max_tokens=max_tokens,
    )
    # ปกติหวังว่าเป็น JSON ตรง ๆ
    try:
        return json.loads(txt)
    except Exception:
        pass
    # หา { ... } ก้อนแรกสุด
    try:
        start = txt.find("{")
        end = txt.rfind("}")
        if start != -1 and end != -1 and end > start:
            blob = txt[start : end + 1]
            return json.loads(blob)
    except Exception:
        pass
    # fallback แบบปลอดภัย
    return {"clean_text": txt.strip()}

def refine_query(raw_text: str,
                 lang_hint: str | None = None,
                 system_extra: str | None = None) -> dict:
    """
    ใช้โมเดลเดียวกันกับ LLM หลักเพื่อทำ 'Agent Refinement'
    คืนโครงสร้าง JSON มาตรฐานสำหรับ pipeline ถัดไป
    """
    system = (
        "คุณคือตัวกรอง/ทำความเข้าใจคำพูดจาก STT เพื่อเตรียมส่งให้ผู้ช่วยตอบต่อ "
        "งานของคุณคือ: (1) ทำความสะอาดข้อความ (ลบคำฟุ่มเฟือย/อืม/เอ่อ ฯลฯ), "
        "(2) ระบุ intent, (3) สกัด entity สำคัญ, (4) สร้าง final_prompt ที่พร้อมใช้งานกับโมเดลสรุป/ตอบกลับ, "
        "(5) ตอบกลับเป็น JSON เท่านั้นตามสคีมาด้านล่าง\n\n"
        "Schema JSON:\n"
        "{\n"
        '  "language": "th|en|...",\n'
        '  "intent": "ask|command|search|chitchat|control|other",\n'
        '  "clean_text": "ข้อความที่สะอาดและกระชับ",\n'
        '  "entities": [{"type":"<ประเภทหรือany>","text":"<คำที่เจอ>"}],\n'
        '  "needs_more_info": true|false,\n'
        '  "final_prompt": "พรอมป์ที่เหมาะจะส่งต่อให้ผู้ช่วยตอบ",\n'
        '  "notes": "ข้อสังเกตสั้น ๆ (ถ้ามี)"\n'
        "}\n"
        "ข้อกำหนด: "
        "1) รักษาความหมายต้นฉบับให้มากที่สุด "
        "2) ถ้าไม่แน่ใจ ให้ตั้ง needs_more_info=true และใส่ notes สั้น ๆ "
        "3) ห้ามมีข้อความนอก JSON เด็ดขาด"
    )
    if system_extra:
        system += f"\n\nเพิ่มเติม: {system_extra}"

    prompt = f"ข้อความดิบจาก STT (lang_hint={lang_hint or 'auto'}):\n{raw_text.strip()}\n\nสร้าง JSON ตาม schema."

    data = generate_json(prompt=prompt, system=system)
    # ใส่ค่าเริ่มต้นกัน field หาย
    return {
        "language": data.get("language") or (lang_hint or "th"),
        "intent": data.get("intent") or "other",
        "clean_text": (data.get("clean_text") or raw_text or "").strip(),
        "entities": data.get("entities") or [],
        "needs_more_info": bool(data.get("needs_more_info", False)),
        "final_prompt": (data.get("final_prompt") or data.get("clean_text") or raw_text or "").strip(),
        "notes": data.get("notes") or "",
    }
