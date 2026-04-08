"""
Map Router for VORA Server
===========================
Serves SLAM map image + real-time robot position + object memory markers.
Gateway PUSHES robot pose periodically. Webapp fetches state to render on canvas.

Flow:
  Gateway subscribes /odom via ROSBridge → POST /map/pose → Server memory
  Gateway pushes object memory            → POST /map/objects → Server memory
  Webapp ← GET /map/image   ← static PNG of SLAM map
  Webapp ← GET /map/state   ← robot pose + objects (polled ~5Hz)

Endpoints:
- GET  /map/image    → SLAM map as PNG
- GET  /map/info     → Map metadata (resolution, origin, size)
- GET  /map/state    → Robot pose + object markers (for webapp canvas)
- POST /map/pose     → Gateway pushes robot pose here
- POST /map/objects   → Gateway pushes object memory here
"""

import io
import time
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import Response, JSONResponse

logger = logging.getLogger("map")

router = APIRouter(prefix="/map", tags=["map"])

# ── Map file paths ────────────────────────────────────────────────────
# Try multiple locations for map files
_SEARCH_DIRS = [
    Path(__file__).parent.parent.parent / "Myagv" / "maps",    # workspace
    Path(__file__).parent.parent / "maps",                       # app/maps
    Path.home() / "maps",                                        # ~/maps
]


def _find_map_file(name: str) -> Optional[Path]:
    for d in _SEARCH_DIRS:
        p = d / name
        if p.exists():
            return p
    return None


# ── In-memory state (Gateway pushes here) ─────────────────────────────
_map_png_cache: Optional[bytes] = None
_map_info: dict = {}

_robot_pose = {
    "x": 0.0,
    "y": 0.0,
    "theta": 0.0,
    "source": "none",
    "timestamp": 0.0,
}

_object_markers: list = []   # [{name, x, y, zone, last_seen, confidence}, ...]

# Trail history: accumulated from pose pushes (survives page refresh)
_trail_history: list = []    # [{x, y, t}, ...]
_TRAIL_MAX = 2000
_trail_last_x = 0.0
_trail_last_y = 0.0

# Semantic map annotations (pushed from Gateway)
_annotations: dict = {"zones": [], "landmarks": []}

# Raw occupancy grid (grayscale, cropped) for navigation checks
_occupancy_grid = None  # numpy array: 0=wall, 254=free, 205=unknown


def _load_map():
    """Load PGM map, auto-crop to room bounds, enhance colors, cache as PNG."""
    global _map_png_cache, _map_info, _occupancy_grid

    yaml_path = _find_map_file("lab_room.yaml")
    pgm_path = _find_map_file("lab_room.pgm")

    if not yaml_path or not pgm_path:
        logger.warning(f"⚠️ Map files not found. Searched: {[str(d) for d in _SEARCH_DIRS]}")
        return

    # Parse YAML (simple — no PyYAML dependency needed)
    info = {"resolution": 0.05, "origin": [-10, -10, 0], "width": 0, "height": 0}
    for line in yaml_path.read_text().splitlines():
        line = line.strip()
        if line.startswith("resolution:"):
            info["resolution"] = float(line.split(":")[1].strip())
        elif line.startswith("origin:"):
            parts = line.split("[")[1].split("]")[0].split(",")
            info["origin"] = [float(p.strip()) for p in parts]

    try:
        from PIL import Image
        import numpy as np

        img = Image.open(str(pgm_path))
        arr = np.array(img)
        orig_h, orig_w = arr.shape
        res = info["resolution"]
        ox, oy = info["origin"][0], info["origin"][1]

        # Auto-crop: find bounding box of non-unknown (!=205) pixels
        non_unknown = arr != 205
        rows_any = np.any(non_unknown, axis=1)
        cols_any = np.any(non_unknown, axis=0)

        if rows_any.any() and cols_any.any():
            rmin, rmax = int(np.where(rows_any)[0][0]), int(np.where(rows_any)[0][-1])
            cmin, cmax = int(np.where(cols_any)[0][0]), int(np.where(cols_any)[0][-1])

            # Add generous padding
            pad = 20
            rmin = max(0, rmin - pad)
            rmax = min(orig_h - 1, rmax + pad)
            cmin = max(0, cmin - pad)
            cmax = min(orig_w - 1, cmax + pad)

            cropped = arr[rmin:rmax + 1, cmin:cmax + 1]

            # Update origin for cropped coordinate system
            info["origin"] = [
                ox + cmin * res,
                oy + (orig_h - 1 - rmax) * res,
                0,
            ]
        else:
            cropped = arr

        new_h, new_w = cropped.shape
        info["width"] = int(new_w)
        info["height"] = int(new_h)

        # Keep raw occupancy grid for navigation checks
        _occupancy_grid = cropped.copy()

        # Enhance colors → RGBA for webapp dark theme
        rgba = np.zeros((new_h, new_w, 4), dtype=np.uint8)

        # Unknown → dark background matching webapp
        rgba[cropped == 205] = [26, 26, 46, 255]

        # Free space (>=240) → light floor
        rgba[cropped >= 240] = [200, 210, 230, 255]

        # Walls / occupied (<=10) → bright for visibility
        rgba[cropped <= 10] = [255, 90, 90, 255]

        # Near-unknown (196-210 excluding 205) → slightly visible border
        near = (cropped >= 196) & (cropped <= 210) & (cropped != 205)
        rgba[near] = [50, 50, 80, 255]

        out_img = Image.fromarray(rgba, mode="RGBA")
        buf = io.BytesIO()
        out_img.save(buf, format="PNG")
        _map_png_cache = buf.getvalue()
        _map_info = info

        logger.info(f"✅ Map cropped: {orig_w}×{orig_h} → {new_w}×{new_h}px, "
                     f"res={res}m/px, origin={info['origin']}")
    except ImportError:
        logger.error("❌ Pillow/numpy not installed — cannot process map")
    except Exception as e:
        logger.error(f"❌ Failed to load map: {e}")


# ── Endpoints ─────────────────────────────────────────────────────────

@router.post("/reload")
async def reload_map():
    """Force reload map from disk (after saving new SLAM map)."""
    global _map_png_cache, _map_info, _occupancy_grid
    _map_png_cache = None
    _map_info = {}
    _occupancy_grid = None
    _load_map()
    if _map_png_cache:
        return JSONResponse({"ok": True, "width": _map_info.get("width"), "height": _map_info.get("height")})
    return JSONResponse({"ok": False, "error": "Map files not found"}, status_code=404)


@router.get("/image")
async def get_map_image():
    """Serve SLAM map as PNG image."""
    if not _map_png_cache:
        _load_map()
    if not _map_png_cache:
        return Response(status_code=404, content="Map not found")
    return Response(content=_map_png_cache, media_type="image/png",
                    headers={"Cache-Control": "no-cache"})


@router.get("/info")
async def get_map_info():
    """Map metadata: resolution, origin, pixel dimensions."""
    if not _map_info:
        _load_map()
    return JSONResponse({
        "available": bool(_map_info),
        **_map_info,
    })


@router.get("/state")
async def get_map_state():
    """Real-time state for webapp canvas: robot pose + object markers + trail."""
    return JSONResponse({
        "robot": _robot_pose,
        "objects": _object_markers,
        "trail": _trail_history[-500:],  # last 500 points for rendering
        "annotations": _annotations,
        "map": {
            "width": _map_info.get("width", 0),
            "height": _map_info.get("height", 0),
            "resolution": _map_info.get("resolution", 0.05),
            "origin": _map_info.get("origin", [-10, -10, 0]),
        },
    })


@router.post("/pose")
async def push_robot_pose(request: Request):
    """Gateway pushes robot pose from /odom."""
    global _robot_pose, _trail_last_x, _trail_last_y
    try:
        data = await request.json()
        x = float(data.get("x", 0))
        y = float(data.get("y", 0))
        _robot_pose = {
            "x": x,
            "y": y,
            "theta": float(data.get("theta", 0)),
            "source": data.get("source", "unknown"),
            "timestamp": time.time(),
        }
        # Accumulate trail if robot moved >2cm
        dx = x - _trail_last_x
        dy = y - _trail_last_y
        if dx * dx + dy * dy > 0.0004:  # 0.02m squared
            _trail_history.append({"x": round(x, 3), "y": round(y, 3), "t": time.time()})
            if len(_trail_history) > _TRAIL_MAX:
                _trail_history.pop(0)
            _trail_last_x = x
            _trail_last_y = y
        return {"ok": True}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@router.post("/objects")
async def push_object_markers(request: Request):
    """Gateway pushes object memory entries for map display."""
    global _object_markers
    try:
        data = await request.json()
        _object_markers = data if isinstance(data, list) else data.get("objects", [])
        return {"ok": True, "count": len(_object_markers)}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


# ── Navigation helpers ────────────────────────────────────────────────

def _world_to_pixel(wx: float, wy: float) -> tuple:
    """Convert world coords (meters) to pixel coords in cropped map."""
    if not _map_info:
        return (-1, -1)
    res = _map_info["resolution"]
    ox, oy = _map_info["origin"][0], _map_info["origin"][1]
    h = _map_info["height"]
    px = int((wx - ox) / res)
    py = h - 1 - int((wy - oy) / res)
    return (px, py)


def _is_occupied(px: int, py: int, margin: int = 2) -> bool:
    """Check if pixel (and surrounding margin) is wall/unknown."""
    if _occupancy_grid is None:
        return False  # No map = assume free
    h, w = _occupancy_grid.shape
    for dy in range(-margin, margin + 1):
        for dx in range(-margin, margin + 1):
            nx, ny = px + dx, py + dy
            if nx < 0 or nx >= w or ny < 0 or ny >= h:
                return True  # Out of bounds = occupied
            val = int(_occupancy_grid[ny, nx])
            if val <= 10:  # Wall
                return True
    return False


@router.post("/check_path")
async def check_path(request: Request):
    """
    Check if a straight-line path from (x1,y1) to (x2,y2) is free on the SLAM map.
    Gateway calls this before moving forward to verify the map doesn't show a wall.
    
    Body: {"x1": float, "y1": float, "x2": float, "y2": float}
    Returns: {"free": bool, "blocked_at": [wx, wy] or null, "cells_checked": int}
    """
    import math as m
    if _occupancy_grid is None:
        return JSONResponse({"free": True, "reason": "no_map", "cells_checked": 0})
    
    try:
        data = await request.json()
        x1, y1 = float(data["x1"]), float(data["y1"])
        x2, y2 = float(data["x2"]), float(data["y2"])
    except (KeyError, TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "need x1,y1,x2,y2"}, status_code=400)
    
    res = _map_info.get("resolution", 0.05)
    dist = m.sqrt((x2 - x1)**2 + (y2 - y1)**2)
    steps = max(1, int(dist / (res * 0.5)))  # check every half-pixel
    
    for i in range(steps + 1):
        t = i / steps
        wx = x1 + t * (x2 - x1)
        wy = y1 + t * (y2 - y1)
        px, py = _world_to_pixel(wx, wy)
        if _is_occupied(px, py, margin=3):  # 3px margin ≈ 15cm safety buffer
            return JSONResponse({
                "free": False,
                "blocked_at": [round(wx, 3), round(wy, 3)],
                "cells_checked": i + 1,
            })
    
    return JSONResponse({"free": True, "blocked_at": None, "cells_checked": steps + 1})


@router.post("/is_free")
async def is_position_free(request: Request):
    """
    Check if a single world position is free (not wall/unknown).
    Body: {"x": float, "y": float}
    """
    if _occupancy_grid is None:
        return JSONResponse({"free": True, "reason": "no_map"})
    
    try:
        data = await request.json()
        wx, wy = float(data["x"]), float(data["y"])
    except (KeyError, TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "need x, y"}, status_code=400)
    
    px, py = _world_to_pixel(wx, wy)
    occupied = _is_occupied(px, py, margin=3)
    return JSONResponse({"free": not occupied, "pixel": [px, py]})


@router.post("/relocalize")
async def relocalize(request: Request):
    """
    Manual relocalization: set robot pose on AMCL.
    Forwards to Gateway via pipeline WebSocket.
    Body: {"x": float, "y": float, "theta": float}
    """
    global _robot_pose, _trail_last_x, _trail_last_y
    try:
        data = await request.json()
        x = float(data.get("x", 0))
        y = float(data.get("y", 0))
        theta = float(data.get("theta", 0))
    except (KeyError, TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "need x, y, theta"}, status_code=400)

    # Immediately update server-side pose for responsive UI
    _robot_pose = {
        "x": x, "y": y, "theta": theta,
        "source": "manual", "timestamp": time.time(),
    }
    # Reset trail tracking anchor
    _trail_last_x = x
    _trail_last_y = y

    # Forward to Gateway via pipeline WebSocket
    sent = False
    try:
        from .pipeline_router import gateway_connections
        for robot_id, ws in list(gateway_connections.items()):
            try:
                await ws.send_json({
                    "type": "command",
                    "cmd": "relocalize",
                    "params": {"x": x, "y": y, "theta": theta},
                    "priority": 1,
                })
                sent = True
                logger.info(f"📍 Relocalize sent to {robot_id}: x={x:.2f} y={y:.2f}")
            except Exception as e:
                logger.warning(f"⚠️ Failed to send relocalize to {robot_id}: {e}")
    except ImportError:
        logger.warning("⚠️ pipeline_router not available — pose updated locally only")

    return JSONResponse({
        "ok": True,
        "sent_to_gateway": sent,
        "pose": {"x": x, "y": y, "theta": theta},
    })


# ── Semantic Map Annotations ─────────────────────────────────────────

@router.post("/annotations/push")
async def push_annotations(request: Request):
    """Gateway pushes semantic map annotations here."""
    global _annotations
    try:
        _annotations = await request.json()
        return {"ok": True}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@router.get("/annotations")
async def get_annotations():
    """Return cached annotations (pushed from Gateway)."""
    return JSONResponse(_annotations)


async def _forward_to_gateway(path: str, json_data=None, method: str = "POST"):
    """Forward a request to Gateway and return the response."""
    import httpx
    from app.core.settings import Settings
    gw_url = Settings().GATEWAY_URL
    url = f"{gw_url}{path}"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            if method == "DELETE":
                r = await client.delete(url)
            else:
                r = await client.post(url, json=json_data)
        return JSONResponse(r.json(), status_code=r.status_code)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=502)


@router.post("/annotations/zone")
async def proxy_upsert_zone(request: Request):
    """Proxy: create/update zone on Gateway."""
    return await _forward_to_gateway("/annotations/zone", await request.json())


@router.delete("/annotations/zone/{zone_id}")
async def proxy_delete_zone(zone_id: str):
    """Proxy: delete zone on Gateway."""
    return await _forward_to_gateway(f"/annotations/zone/{zone_id}", method="DELETE")


@router.post("/annotations/landmark")
async def proxy_upsert_landmark(request: Request):
    """Proxy: create/update landmark on Gateway."""
    return await _forward_to_gateway("/annotations/landmark", await request.json())


@router.delete("/annotations/landmark/{lm_id}")
async def proxy_delete_landmark(lm_id: str):
    """Proxy: delete landmark on Gateway."""
    return await _forward_to_gateway(f"/annotations/landmark/{lm_id}", method="DELETE")


# ── Load map on import ────────────────────────────────────────────────
_load_map()
