"""
Qwen3-VL:8B Integration for VORA (via Ollama)
==============================================
Vision Language Model for Thai object detection + understanding

Uses Ollama API with Qwen3-VL:8B (6.1GB):
- 256K context window
- 32 languages including Thai
- Spatial understanding + visual agent capabilities
- No torch/transformers dependency — lightweight HTTP calls
"""

import json
import base64
import asyncio
import urllib.request
import urllib.error
import socket
import logging
from typing import Optional, Dict, Any, List
from pathlib import Path

from ...core.settings import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Low-level Ollama HTTP helpers (same pattern as ollama.py)
# ---------------------------------------------------------------------------

def _post_json(url: str, payload: dict, timeout: int) -> dict:
    """POST JSON to Ollama and return parsed response."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
        return json.loads(body)


def _encode_image_base64(image_path: str) -> str:
    """Read an image file and return its base64 string."""
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


# ---------------------------------------------------------------------------
# Core VLM functions
# ---------------------------------------------------------------------------

def _vlm_host() -> str:
    return settings.OLLAMA_HOST.rstrip("/")


def _vlm_model() -> str:
    return settings.OLLAMA_VLM_MODEL


def _vlm_timeout() -> int:
    return int(settings.OLLAMA_TIMEOUT)


async def understand_image(
    image_path: str,
    prompt: str,
    lang: str = "th",
    *,
    temperature: float = 0.4,
    max_tokens: int = 8192,
) -> Dict[str, Any]:
    """
    Understand image content using Qwen3-VL via Ollama.

    Args:
        image_path: Absolute path to the image file.
        prompt:     What to ask about the image (Thai or English).
        lang:       Response language hint ('th' or 'en').
        temperature: Sampling temperature.
        max_tokens:  Maximum response tokens.

    Returns:
        {
            "text": "คำตอบภาษาไทย",
            "model": "qwen3-vl:8b",
            "lang": "th"
        }
    """
    try:
        # Encode image
        img_b64 = await asyncio.to_thread(_encode_image_base64, image_path)

        # Build prompt (no system prompt — Qwen3-VL works better with direct prompts)
        # Use question form to avoid Qwen3-VL empty response on imperative prompts
        full_prompt = prompt

        url = f"{_vlm_host()}/api/generate"
        payload = {
            "model": _vlm_model(),
            "prompt": full_prompt,
            "images": [img_b64],
            "stream": False,
            "keep_alive": settings.OLLAMA_KEEP_ALIVE,
            "options": {
                "temperature": temperature,
                "top_p": 0.9,
                "num_predict": max_tokens,
            },
        }

        logger.info(f"📸 VLM request: model={_vlm_model()}, prompt={prompt[:80]}...")

        res = await asyncio.to_thread(_post_json, url, payload, _vlm_timeout())
        response_text = (res.get("response") or "").strip()

        # Strip Qwen3 <think>...</think> blocks (thinking mode artifacts)
        import re
        response_text = re.sub(r"<think>.*?</think>", "", response_text, flags=re.DOTALL).strip()

        logger.info(f"✅ VLM response ({len(response_text)} chars): {response_text[:120]}...")

        return {
            "text": response_text,
            "model": _vlm_model(),
            "lang": lang,
            "eval_duration_ms": res.get("eval_duration", 0) // 1_000_000,
        }

    except (urllib.error.URLError, socket.timeout) as e:
        logger.error(f"❌ VLM timeout/connection error: {e}")
        return {"text": "", "error": f"VLM connection error: {e}", "model": _vlm_model()}
    except FileNotFoundError:
        logger.error(f"❌ Image not found: {image_path}")
        return {"text": "", "error": f"Image not found: {image_path}", "model": _vlm_model()}
    except Exception as e:
        logger.error(f"❌ VLM error: {e}")
        return {"text": "", "error": str(e), "model": _vlm_model()}


async def find_object(
    image_path: str,
    object_name: str,
    lang: str = "th",
) -> Dict[str, Any]:
    """
    Find a specific object in an image.

    Example:
        find_object("room.jpg", "ไขควง", lang="th")
        → {"found": True, "location": "บนโต๊ะ ด้านซ้าย", "description": "..."}
    """
    if lang == "th":
        prompt = (
            f"ในภาพนี้มี '{object_name}' หรือไม่? "
            f"ถ้ามี บอกตำแหน่งว่าอยู่ตรงไหนของภาพ (ซ้าย/ขวา/กลาง/บน/ล่าง) "
            f"ถ้าไม่มี ตอบว่าไม่พบ"
        )
    else:
        prompt = (
            f"Is there a '{object_name}' in this image? "
            f"If yes, describe its position (left/right/center/top/bottom). "
            f"If no, say not found."
        )

    result = await understand_image(image_path, prompt, lang)

    # Heuristic: determine if object was found
    txt = result.get("text", "").lower()
    found = not any(neg in txt for neg in ["ไม่พบ", "ไม่มี", "ไม่เจอ", "not found", "no ", "cannot"])
    if not txt:
        found = False

    return {
        **result,
        "object": object_name,
        "found": found,
        "description": result.get("text", ""),
    }


async def describe_scene(
    image_path: str,
    lang: str = "th",
) -> Dict[str, Any]:
    """Generate a complete scene description."""
    if lang == "th":
        prompt = "ภาพนี้มีสิ่งของอะไรบ้าง แต่ละอย่างอยู่ตำแหน่งไหน?"
    else:
        prompt = "What objects are in this image and where is each one positioned?"
    return await understand_image(image_path, prompt, lang)


async def analyze_obstacle(
    image_path: str,
    current_goal: str = "",
    lang: str = "th",
) -> Dict[str, Any]:
    """
    Analyze an obstacle scene and recommend a strategy.

    Used when LiDAR detects an obstacle — the VLM sees the image and decides:
    - What the obstacle is
    - Whether to go around it, wait, or reroute

    Returns:
        {
            "text": "...",
            "obstacle_type": "chair",
            "strategy": "go_around_left" | "go_around_right" | "wait" | "reroute" | "stop",
            "confidence": "high" | "medium" | "low"
        }
    """
    if lang == "th":
        goal_part = f" (หุ่นยนต์กำลังไปหา '{current_goal}')" if current_goal else ""
        prompt = (
            f"มีอะไรขวางทางอยู่ในภาพนี้ไหม?{goal_part} "
            "ถ้ามี คืออะไร? ควรเลี่ยงไปทางซ้ายหรือขวา หรือรอ หรือหยุด?"
        )
    else:
        goal_part = f" (robot is heading to '{current_goal}')" if current_goal else ""
        prompt = (
            f"Is there an obstacle in this image?{goal_part} "
            "If yes, what is it? Should the robot go left, right, wait, or stop?"
        )

    result = await understand_image(image_path, prompt, lang, temperature=0.4, max_tokens=8192)

    # Try to parse JSON from response
    txt = result.get("text", "")
    strategy_data = {}
    try:
        # Find JSON in response
        start = txt.find("{")
        end = txt.rfind("}") + 1
        if start != -1 and end > start:
            strategy_data = json.loads(txt[start:end])
    except (json.JSONDecodeError, ValueError):
        # Fallback: extract strategy from keywords
        txt_lower = txt.lower()
        if "stop" in txt_lower or "หยุด" in txt_lower:
            strategy_data = {"strategy": "stop"}
        elif "wait" in txt_lower or "รอ" in txt_lower:
            strategy_data = {"strategy": "wait"}
        elif "left" in txt_lower or "ซ้าย" in txt_lower:
            strategy_data = {"strategy": "go_around_left"}
        elif "right" in txt_lower or "ขวา" in txt_lower:
            strategy_data = {"strategy": "go_around_right"}
        else:
            strategy_data = {"strategy": "stop"}  # Safe default

    return {
        **result,
        "obstacle_type": strategy_data.get("obstacle", "unknown"),
        "strategy": strategy_data.get("strategy", "stop"),
        "reason": strategy_data.get("reason", ""),
    }


async def check_vlm_health() -> Dict[str, Any]:
    """Check if VLM model is available in Ollama."""
    try:
        url = f"{_vlm_host()}/api/tags"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        
        models = [m.get("name", "") for m in data.get("models", [])]
        vlm_name = _vlm_model()
        
        # Check if model is available (exact or prefix match)
        available = any(vlm_name in m for m in models)
        
        return {
            "ok": available,
            "model": vlm_name,
            "available_models": models,
            "message": f"✅ {vlm_name} ready" if available else f"❌ {vlm_name} not found. Run: ollama pull {vlm_name}",
        }
    except Exception as e:
        return {"ok": False, "model": _vlm_model(), "error": str(e)}


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import asyncio
    import sys

    async def test():
        # Check health first
        health = await check_vlm_health()
        print("VLM Health:", json.dumps(health, ensure_ascii=False, indent=2))

        if not health["ok"]:
            print("⚠️ VLM model not loaded. Run: ollama pull qwen3-vl:8b")
            return

        # Test with real image if provided
        if len(sys.argv) > 1:
            img = sys.argv[1]
            print(f"\n--- describe_scene({img}) ---")
            r = await describe_scene(img)
            print(json.dumps(r, ensure_ascii=False, indent=2))

            print(f"\n--- find_object({img}, 'ปากกา') ---")
            r = await find_object(img, "ปากกา")
            print(json.dumps(r, ensure_ascii=False, indent=2))
        else:
            print("Usage: python -m app.providers.llm.qwen_vlm <image_path>")

    asyncio.run(test())
