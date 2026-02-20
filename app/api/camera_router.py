"""
Camera Router for VORA Server
==============================
Gateway PUSHES frames to Server (because Server cannot reach Gateway's LAN).
Webapp fetches frames from Server's in-memory store.

Flow:  MyAGV → ROSBridge → Gateway → POST /camera/push → Server memory
       Webapp ← GET /camera/frame ← Server memory

Endpoints:
- POST /camera/push    → Gateway pushes JPEG frame here
- GET  /camera/status  → Camera status
- GET  /camera/frame   → Latest JPEG frame (from memory)
- GET  /camera/mjpeg   → MJPEG stream (from memory)
- POST /camera/capture → Save current frame to Images/
"""

import time
import logging
import asyncio
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import Response, StreamingResponse

logger = logging.getLogger("camera")

router = APIRouter(prefix="/camera", tags=["camera"])

# ── In-memory frame store (Gateway pushes here) ──────────────────────
_frame: bytes | None = None
_frame_ts: float = 0.0
_frame_count: int = 0
_push_count: int = 0          # total pushes received
_first_frame_logged: bool = False


@router.post("/push")
async def push_frame(request: Request):
    """
    Accept a JPEG frame pushed by Gateway.
    Gateway calls this every ~200ms (~5 fps).
    """
    global _frame, _frame_ts, _frame_count, _push_count, _first_frame_logged

    body = await request.body()
    if not body:
        return Response(status_code=400)

    _frame = body
    _frame_ts = time.time()
    _frame_count += 1
    _push_count += 1

    if not _first_frame_logged:
        _first_frame_logged = True
        logger.info(f"📷 First frame received from Gateway! size={len(body)} bytes")

    # Log every 100 pushes
    if _push_count % 100 == 0:
        logger.info(f"📷 Frame push #{_push_count} ({len(body)} bytes)")

    return Response(status_code=200)


@router.get("/status")
async def camera_status():
    """Camera status from in-memory store."""
    has_frame = _frame is not None
    age_ms = round((time.time() - _frame_ts) * 1000, 1) if _frame_ts > 0 else None
    return {
        "connected": has_frame and (age_ms is not None and age_ms < 5000),
        "has_frame": has_frame,
        "frame_count": _frame_count,
        "push_count": _push_count,
        "frame_age_ms": age_ms,
        "frame_size": len(_frame) if _frame else 0,
    }


@router.get("/frame")
async def camera_frame():
    """
    Serve latest frame from in-memory store.
    Returns 204 if no frame available.
    """
    if _frame is None:
        return Response(content=b"", status_code=204)

    return Response(
        content=_frame,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "X-Frame-Age-Ms": str(round((time.time() - _frame_ts) * 1000, 1)),
            "X-Frame-Count": str(_frame_count),
        },
    )


@router.get("/mjpeg")
async def camera_mjpeg():
    """MJPEG stream from in-memory store (~10 fps)."""

    async def generate():
        last_count = 0
        while True:
            if _frame and _frame_count > last_count:
                last_count = _frame_count
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n"
                    + _frame
                    + b"\r\n"
                )
            await asyncio.sleep(0.1)  # ~10 fps

    return StreamingResponse(
        generate(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


@router.post("/capture")
async def capture_frame():
    """Save the current in-memory frame to Images/ folder."""
    if _frame is None:
        return {"success": False, "error": "No frame available"}

    # __file__ = app/api/camera_router.py → .parent.parent.parent = project root
    images_dir = Path(__file__).parent.parent.parent / "Images"
    images_dir.mkdir(exist_ok=True)

    timestamp = int(time.time() * 1000)
    filename = f"capture_{timestamp}.jpg"
    filepath = images_dir / filename

    with open(filepath, "wb") as f:
        f.write(_frame)

    logger.info(f"📸 Captured {filename} ({len(_frame)} bytes)")
    return {
        "success": True,
        "filename": filename,
        "path": str(filepath),
        "size": len(_frame),
    }
