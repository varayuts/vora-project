# app/providers/llm/ollama.py
"""
Ollama LLM Provider for VORA
=============================
Handles all LLM/VLM inference calls to the local Ollama server.

Reliability improvements:
- <think> block stripping (gemma4/qwen3 chain-of-thought)
- Robust JSON extraction with multiple fallback strategies
- Separate timeouts for fast vs slow operations
- Proper error classification (timeout vs connection vs model error)
- async wrappers to avoid blocking the event loop
"""
import json, re, logging, asyncio
import urllib.request, urllib.error, socket
from typing import Optional, Dict, Any
from ...core.settings import settings
from .base import LLMProvider

logger = logging.getLogger("vora.ollama")

# ── Timeout presets ──────────────────────────────────────────────────────
# Fast: agent parsing, JSON extraction — latency-sensitive, small output
TIMEOUT_FAST = 30   # seconds
# Normal: reasoning, chat — moderate latency, medium output
TIMEOUT_NORMAL = 90  # seconds
# Slow: VLM, complex reasoning — quality-sensitive, large output
TIMEOUT_SLOW = int(settings.OLLAMA_TIMEOUT) if int(settings.OLLAMA_TIMEOUT) <= 300 else 180


def _post_json(url: str, payload: dict, timeout: int) -> dict:
    """POST JSON to Ollama synchronously. Returns parsed response dict."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
        return json.loads(body)


def _strip_think_blocks(text: str) -> str:
    """Remove <think>...</think> chain-of-thought blocks from model output.

    Models like gemma4 and qwen3 produce these even when not requested.
    They corrupt JSON parsing and waste tokens in downstream processing.
    Handles both closed and unclosed blocks.
    """
    if not text or "<think>" not in text:
        return text
    # Remove closed <think>...</think> blocks
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    # Handle unclosed <think> (model hit token limit mid-thought)
    if "<think>" in cleaned:
        cleaned = cleaned.split("<think>")[0]
    return cleaned.strip()


def _strip_code_fences(s: str) -> str:
    """Remove markdown code fences: ```json ... ``` or ``` ... ```"""
    if not s:
        return s
    s = s.strip()
    if s.startswith("```"):
        s = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", s, flags=re.S)
    return s.strip()


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    """Extract a JSON object from text with multiple fallback strategies.

    Strategy order:
    1. Direct parse (text is pure JSON)
    2. Find first { ... last } substring
    3. Line-by-line scan for JSON-like content

    Returns None if no valid JSON found.
    """
    if not text:
        return None

    text = text.strip()

    # Strategy 1: Direct parse
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass

    # Strategy 2: Find outermost { ... }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except (json.JSONDecodeError, ValueError):
            pass

    # Strategy 3: Try each line that starts with {
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                return json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue

    return None


class OllamaProvider(LLMProvider):
    def __init__(self, host: Optional[str] = None, model: Optional[str] = None, timeout: Optional[int] = None):
        self.host = (host or settings.OLLAMA_HOST).rstrip("/")
        self.model = model or settings.OLLAMA_MODEL
        self.timeout = int(timeout or settings.OLLAMA_TIMEOUT)

    def generate(self, prompt: str, system: Optional[str] = None,
                 temperature: float = 0.3, top_p: float = 0.9,
                 max_tokens: Optional[int] = None,
                 timeout: Optional[int] = None) -> str:
        """Generate text from a prompt. Strips <think> blocks automatically."""
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
        effective_timeout = timeout or self.timeout
        try:
            res = _post_json(url, payload, timeout=effective_timeout)
            text = (res.get("response") or "").strip()
            return _strip_think_blocks(text)
        except socket.timeout as e:
            raise TimeoutError(f"ollama generate timed out after {effective_timeout}s") from e
        except urllib.error.URLError as e:
            if "timed out" in str(e):
                raise TimeoutError(f"ollama generate timed out after {effective_timeout}s") from e
            raise ConnectionError(f"ollama unreachable: {e}") from e

    async def agenerate(self, prompt: str, system: Optional[str] = None,
                        temperature: float = 0.3, top_p: float = 0.9,
                        max_tokens: Optional[int] = None,
                        timeout: Optional[int] = None) -> str:
        """Async wrapper for generate() — does not block the event loop."""
        return await asyncio.to_thread(
            self.generate, prompt, system, temperature, top_p, max_tokens, timeout
        )

    def chat(self, messages: list, temperature: float = 0.7,
             max_tokens: Optional[int] = None,
             timeout: Optional[int] = None) -> str:
        """Multi-turn chat using /api/chat endpoint. Strips <think> blocks."""
        url = f"{self.host}/api/chat"
        payload: dict = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "keep_alive": settings.OLLAMA_KEEP_ALIVE,
            "options": {"temperature": temperature}
        }
        if max_tokens is not None:
            payload["options"]["num_predict"] = int(max_tokens)
        effective_timeout = timeout or self.timeout
        try:
            res = _post_json(url, payload, timeout=effective_timeout)
            text = (res.get("message", {}).get("content") or "").strip()
            return _strip_think_blocks(text)
        except socket.timeout as e:
            raise TimeoutError(f"ollama chat timed out after {effective_timeout}s") from e
        except urllib.error.URLError as e:
            if "timed out" in str(e):
                raise TimeoutError(f"ollama chat timed out after {effective_timeout}s") from e
            raise ConnectionError(f"ollama unreachable: {e}") from e

    async def achat(self, messages: list, temperature: float = 0.7,
                    max_tokens: Optional[int] = None,
                    timeout: Optional[int] = None) -> str:
        """Async wrapper for chat() — does not block the event loop."""
        return await asyncio.to_thread(
            self.chat, messages, temperature, max_tokens, timeout
        )

    def generate_json(self, system: str, prompt: str,
                      temperature: float = 0.1, top_p: float = 0.9,
                      max_tokens: Optional[int] = 200,
                      timeout: Optional[int] = None) -> Dict[str, Any]:
        """Generate a JSON response with robust extraction and fallbacks.

        Uses Ollama's format=json mode first, with fallback to manual extraction.
        Strips <think> blocks and code fences before parsing.
        """
        url = f"{self.host}/api/generate"
        effective_timeout = timeout or TIMEOUT_FAST

        # Build options dict without None values
        options = {"temperature": temperature, "top_p": top_p}
        if max_tokens is not None:
            options["num_predict"] = int(max_tokens)

        payload: dict = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "keep_alive": settings.OLLAMA_KEEP_ALIVE,
            "format": "json",
            "options": options,
        }
        if system:
            payload["system"] = system

        txt = ""
        try:
            res = _post_json(url, payload, timeout=effective_timeout)
            txt = (res.get("response") or "").strip()
        except (urllib.error.URLError, socket.timeout) as e:
            logger.warning(f"generate_json format=json failed ({type(e).__name__}), trying without format")
            # Fallback: try without format=json constraint
            try:
                txt = self.generate(
                    prompt=prompt,
                    system=(system or "") + "\n\nReturn ONLY valid JSON. No markdown. No explanation.",
                    temperature=temperature, top_p=top_p,
                    max_tokens=max_tokens,
                    timeout=effective_timeout,
                )
            except (TimeoutError, ConnectionError) as e2:
                logger.error(f"generate_json fallback also failed: {e2}")
                return {"_error": str(e2), "clean_text": prompt[:100]}

        # Clean the output
        txt = _strip_think_blocks(txt)
        txt = _strip_code_fences(txt)

        # Try to extract JSON
        result = _extract_json(txt)
        if result is not None:
            return result

        # Final fallback: return the raw text wrapped in a dict
        logger.warning(f"generate_json: could not parse JSON from: {txt[:120]}")
        return {"clean_text": txt[:500] if txt else prompt[:100]}

    async def agenerate_json(self, system: str, prompt: str,
                             temperature: float = 0.1, top_p: float = 0.9,
                             max_tokens: Optional[int] = 200,
                             timeout: Optional[int] = None) -> Dict[str, Any]:
        """Async wrapper for generate_json() — does not block the event loop."""
        return await asyncio.to_thread(
            self.generate_json, system, prompt, temperature, top_p, max_tokens, timeout
        )


