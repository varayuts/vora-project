# app/providers/llm/ollama.py
import json, re, urllib.request, urllib.error, socket
from typing import Optional, Dict, Any
from ...core.settings import settings
from .base import LLMProvider

def _post_json(url: str, payload: dict, timeout: int) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
        return json.loads(body)

def _strip_code_fences(s: str) -> str:
    if not s:
        return s
    s = s.strip()
    # ```json ... ``` or ``` ... ```
    if s.startswith("```"):
        s = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", s, flags=re.S)
    return s.strip()

class OllamaProvider(LLMProvider):
    def __init__(self, host: Optional[str] = None, model: Optional[str] = None, timeout: Optional[int] = None):
        self.host = (host or settings.OLLAMA_HOST).rstrip("/")
        self.model = model or settings.OLLAMA_MODEL
        self.timeout = int(timeout or settings.OLLAMA_TIMEOUT)

    def generate(self, prompt: str, system: Optional[str] = None,
                 temperature: float = 0.3, top_p: float = 0.9,
                 max_tokens: Optional[int] = None) -> str:
        url = f"{self.host}/api/generate"
        payload: dict = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "keep_alive": settings.OLLAMA_KEEP_ALIVE,
            "options": {"temperature": temperature, "top_p": top_p}
        }
        if system:
            payload["system"] = system
        if max_tokens is not None:
            payload["options"]["num_predict"] = int(max_tokens)
        try:
            res = _post_json(url, payload, timeout=self.timeout)
            return (res.get("response") or "").strip()
        except (urllib.error.URLError, socket.timeout) as e:
            raise TimeoutError("ollama generate timed out") from e

    def generate_json(self, system: str, prompt: str,
                      temperature: float = 0.1, top_p: float = 0.9,
                      max_tokens: Optional[int] = 200) -> Dict[str, Any]:
        url = f"{self.host}/api/generate"
        payload: dict = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "keep_alive": settings.OLLAMA_KEEP_ALIVE,
            # format=json บังคับให้โมเดลส่ง JSON ล้วน (ถ้าโมเดลรองรับ)
            "format": "json",
            "options": {
                "temperature": temperature,
                "top_p": top_p,
                "num_predict": int(max_tokens) if max_tokens is not None else None
            }
        }
        if system:
            payload["system"] = system
        try:
            res = _post_json(url, payload, timeout=self.timeout)
            txt = (res.get("response") or "").strip()
        except (urllib.error.URLError, socket.timeout):
            # ตกกระบวน—fallback ไปวิธีเดิม
            txt = self.generate(
                prompt=prompt,
                system=system + "\n\nReturn ONLY valid JSON. No markdown. No preface.",
                temperature=temperature, top_p=top_p, max_tokens=max_tokens
            )
        txt = _strip_code_fences(txt)
        try:
            return json.loads(txt)
        except Exception:
            start, end = txt.find("{"), txt.rfind("}")
            if start != -1 and end != -1 and end > start:
                try:
                    return json.loads(txt[start:end+1])
                except Exception:
                    pass
        return {"clean_text": txt}
