"""
VLM Router — Vision Language Model API endpoints
=================================================
Endpoints for Qwen3-VL:8B image understanding via Ollama.

Prefix: /vlm
"""

import os
import logging
from typing import Optional, List
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from pydantic import BaseModel

from ..providers.llm.qwen_vlm import (
    understand_image,
    find_object,
    describe_scene,
    analyze_obstacle,
    check_vlm_health,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/vlm", tags=["VLM"])

# ---------------------------------------------------------------------------
# Path to Images directory (test images uploaded via FileZilla)
# ---------------------------------------------------------------------------
APP_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
IMAGES_DIR = os.path.join(APP_ROOT, "Images")


def _resolve_image(filename: str) -> str:
    """Resolve filename to absolute path inside Images/."""
    path = os.path.join(IMAGES_DIR, filename)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail=f"Image not found: {filename}")
    return path


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class UnderstandRequest(BaseModel):
    image: str                          # filename in Images/ folder
    prompt: str                         # question about the image
    lang: str = "th"
    temperature: float = 0.4
    max_tokens: int = 8192


class FindObjectRequest(BaseModel):
    image: str                          # filename in Images/ folder
    object_name: str                    # e.g. "ปากกา", "wallet"
    lang: str = "th"


class DescribeRequest(BaseModel):
    image: str                          # filename in Images/ folder
    lang: str = "th"


class ObstacleRequest(BaseModel):
    image: str                          # filename in Images/ folder
    current_goal: str = ""              # e.g. "ไปหาไขควง"
    lang: str = "th"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/health")
async def vlm_health():
    """Check if Qwen3-VL model is loaded in Ollama."""
    return await check_vlm_health()


@router.get("/images")
async def list_images():
    """List all test images in the Images/ folder."""
    if not os.path.isdir(IMAGES_DIR):
        return {"images": [], "error": f"Images directory not found: {IMAGES_DIR}"}

    files = []
    for f in sorted(os.listdir(IMAGES_DIR)):
        fpath = os.path.join(IMAGES_DIR, f)
        if os.path.isfile(fpath) and f.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".gif")):
            files.append({
                "filename": f,
                "size_kb": round(os.path.getsize(fpath) / 1024, 1),
                "url": f"/vlm/image/{f}",
            })
    return {"images": files, "count": len(files), "directory": IMAGES_DIR}


@router.get("/image/{filename}")
async def serve_image(filename: str):
    """Serve a single image file (for webapp display)."""
    from fastapi.responses import FileResponse
    path = _resolve_image(filename)
    return FileResponse(path, media_type="image/jpeg")


@router.post("/understand")
async def vlm_understand(req: UnderstandRequest):
    """
    Ask a question about an image.
    
    Example:
        POST /vlm/understand
        {"image": "pen.jpg", "prompt": "ในภาพมีอะไรบ้าง?"}
    """
    path = _resolve_image(req.image)
    logger.info(f"📸 /vlm/understand: image={req.image}, prompt={req.prompt[:60]}")
    result = await understand_image(
        path, req.prompt, req.lang,
        temperature=req.temperature, max_tokens=req.max_tokens,
    )
    return {"status": "ok", **result}


@router.post("/find-object")
async def vlm_find_object(req: FindObjectRequest):
    """
    Find a specific object in an image.
    
    Example:
        POST /vlm/find-object
        {"image": "all.jpg", "object_name": "ปากกา"}
    """
    path = _resolve_image(req.image)
    logger.info(f"🔍 /vlm/find-object: image={req.image}, object={req.object_name}")
    result = await find_object(path, req.object_name, req.lang)
    return {"status": "ok", **result}


@router.post("/describe")
async def vlm_describe(req: DescribeRequest):
    """
    Describe all objects and their positions in an image.
    
    Example:
        POST /vlm/describe
        {"image": "all.jpg"}
    """
    path = _resolve_image(req.image)
    logger.info(f"📝 /vlm/describe: image={req.image}")
    result = await describe_scene(path, req.lang)
    return {"status": "ok", **result}


@router.post("/obstacle")
async def vlm_obstacle(req: ObstacleRequest):
    """
    Analyze an obstacle and recommend avoidance strategy.
    
    Returns:
        {
            "obstacle_type": "chair",
            "strategy": "go_around_left",
            "reason": "..."
        }
    
    Example:
        POST /vlm/obstacle
        {"image": "obstacle.jpg", "current_goal": "ไปหาไขควง"}
    """
    path = _resolve_image(req.image)
    logger.info(f"🚧 /vlm/obstacle: image={req.image}, goal={req.current_goal}")
    result = await analyze_obstacle(path, req.current_goal, req.lang)
    return {"status": "ok", **result}


@router.post("/upload")
async def upload_image(file: UploadFile = File(...)):
    """
    Upload a new image to the Images/ folder.
    Not for realtime camera — for testing with pre-captured images.
    """
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")

    os.makedirs(IMAGES_DIR, exist_ok=True)
    dest = os.path.join(IMAGES_DIR, file.filename)

    content = await file.read()
    with open(dest, "wb") as f:
        f.write(content)

    size_kb = round(len(content) / 1024, 1)
    logger.info(f"📤 Uploaded: {file.filename} ({size_kb} KB)")

    return {
        "status": "ok",
        "filename": file.filename,
        "size_kb": size_kb,
        "url": f"/vlm/image/{file.filename}",
    }
