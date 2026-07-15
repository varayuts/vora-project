"""
semantic_map.py — Semantic zone + landmark data model for VORA Gateway.

Manages Gateway/data/semantic_map.json with CRUD operations.
Replaces the hardcoded ZONE_DEFS that were previously in object_memory.py.

Zones represent room sections (bedroom, entrance, etc.) with expected objects.
Landmarks represent fixed reference points (sofa, table, door, etc.).
"""

import json
import logging
import math
import os
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("gateway")

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_SEMANTIC_MAP_FILE = _DATA_DIR / "semantic_map.json"


@dataclass
class Zone:
    id: str
    label_th: str
    label_en: str
    center_x: float
    center_y: float
    radius: float
    expected_objects: List[str] = field(default_factory=list)
    notes: str = ""
    color: str = "#3b82f6"
    source: str = "seed"  # "seed" = pre-loaded default, "manual" = user-created


@dataclass
class Landmark:
    id: str
    label: str
    x: float
    y: float
    zone_id: str = ""
    category: str = ""
    notes: str = ""


class SemanticMap:
    """Manages zones and landmarks with JSON persistence."""

    def __init__(self, path: Path = _SEMANTIC_MAP_FILE):
        self._path = path
        self._zones: Dict[str, Zone] = {}
        self._landmarks: Dict[str, Landmark] = {}
        self._load()

    # ── CRUD: Zones ──────────────────────────────────────────────

    def add_zone(self, zone: Zone) -> bool:
        """Add or replace a zone (upsert by id). Returns True on success."""
        old = self._zones.get(zone.id)
        self._zones[zone.id] = zone
        if not self._save():
            if old:
                self._zones[zone.id] = old
            else:
                self._zones.pop(zone.id, None)
            return False
        return True

    def update_zone(self, zone_id: str, updates: dict) -> Optional[Zone]:
        z = self._zones.get(zone_id)
        if not z:
            return None
        for k, v in updates.items():
            if hasattr(z, k):
                setattr(z, k, v)
        self._save()
        return z

    def delete_zone(self, zone_id: str) -> bool:
        if zone_id not in self._zones:
            return False
        removed = self._zones.pop(zone_id)
        if not self._save():
            # Rollback: restore zone if file write failed
            self._zones[zone_id] = removed
            logger.error(f"❌ delete_zone rollback: restored '{zone_id}' after save failure")
            return False
        return True

    def get_zone(self, zone_id: str) -> Optional[Zone]:
        return self._zones.get(zone_id)

    def get_all_zones(self) -> List[Zone]:
        return list(self._zones.values())

    # ── CRUD: Landmarks ──────────────────────────────────────────

    def add_landmark(self, lm: Landmark) -> None:
        """Add or replace a landmark (upsert by id)."""
        self._landmarks[lm.id] = lm
        self._save()

    def update_landmark(self, lm_id: str, updates: dict) -> Optional[Landmark]:
        lm = self._landmarks.get(lm_id)
        if not lm:
            return None
        for k, v in updates.items():
            if hasattr(lm, k):
                setattr(lm, k, v)
        self._save()
        return lm

    def delete_landmark(self, lm_id: str) -> bool:
        if lm_id in self._landmarks:
            del self._landmarks[lm_id]
            self._save()
            return True
        return False

    def get_all_landmarks(self) -> List[Landmark]:
        return list(self._landmarks.values())

    # ── Queries ──────────────────────────────────────────────────

    def get_zone_at(self, x: float, y: float) -> Optional[Zone]:
        """Find which zone a map coordinate falls into (point-in-circle)."""
        best: Optional[Zone] = None
        best_dist = float("inf")
        for z in self._zones.values():
            dx = x - z.center_x
            dy = y - z.center_y
            dist = math.sqrt(dx * dx + dy * dy)
            if dist <= z.radius and dist < best_dist:
                best = z
                best_dist = dist
        return best

    def get_zones_for_object(self, object_name: str) -> List[Zone]:
        """Get zones whose expected_objects contain the target (fuzzy match)."""
        obj_lower = object_name.lower()
        matches = []
        for z in self._zones.values():
            for eo in z.expected_objects:
                if obj_lower in eo.lower() or eo.lower() in obj_lower:
                    matches.append(z)
                    break
        return matches

    def get_zone_anchor(self, zone_id: str) -> Optional[Tuple[float, float]]:
        z = self._zones.get(zone_id)
        if z:
            return (z.center_x, z.center_y)
        return None

    # ── Serialization ────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "zones": [asdict(z) for z in self._zones.values()],
            "landmarks": [asdict(lm) for lm in self._landmarks.values()],
        }

    def _load(self) -> None:
        if not self._path.exists():
            logger.info(f"Semantic map file not found, starting empty: {self._path}")
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for zd in data.get("zones", []):
                z = Zone(
                    id=zd["id"],
                    label_th=zd.get("label_th", ""),
                    label_en=zd.get("label_en", ""),
                    center_x=float(zd.get("center_x", 0)),
                    center_y=float(zd.get("center_y", 0)),
                    radius=float(zd.get("radius", 1.0)),
                    expected_objects=zd.get("expected_objects", []),
                    notes=zd.get("notes", ""),
                    color=zd.get("color", "#3b82f6"),
                    source=zd.get("source", "seed"),
                )
                self._zones[z.id] = z
            for ld in data.get("landmarks", []):
                lm = Landmark(
                    id=ld["id"],
                    label=ld.get("label", ""),
                    x=float(ld.get("x", 0)),
                    y=float(ld.get("y", 0)),
                    zone_id=ld.get("zone_id", ""),
                    category=ld.get("category", ""),
                    notes=ld.get("notes", ""),
                )
                self._landmarks[lm.id] = lm
            logger.info(
                f"Loaded semantic map: {len(self._zones)} zones, "
                f"{len(self._landmarks)} landmarks from {self._path.name}"
            )
        except Exception as e:
            logger.error(f"Failed to load semantic map: {e}")

    def _save(self) -> bool:
        """Persist to JSON. Returns True on verified write, False on failure."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            data = self.to_dict()
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            # Verify: re-read and check zone count matches
            with open(self._path, "r", encoding="utf-8") as f:
                saved = json.load(f)
            expected_zones = len(data["zones"])
            actual_zones = len(saved.get("zones", []))
            if expected_zones != actual_zones:
                logger.error(f"❌ semantic_map save VERIFICATION FAILED: "
                             f"expected {expected_zones} zones, file has {actual_zones}")
                return False
            zone_ids = [z["id"] for z in saved["zones"]]
            logger.info(f"💾 semantic_map saved: {actual_zones} zones {zone_ids}")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to save semantic map: {e}")
            return False


# ── Singleton ────────────────────────────────────────────────────
semantic_map = SemanticMap()


