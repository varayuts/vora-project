"""
Object Memory System for VORA
==============================
Remembers where objects were last observed to enable smart search priority.

Position estimation:
  Since the robot has only a monocular camera (no depth sensor), exact object
  distance is unknown. We ESTIMATE the object's map position by projecting
  a fixed distance (DEFAULT_OBJECT_DIST_M) from the robot pose in the
  direction of (heading + bearing). This is approximate but much more useful
  than storing the robot's own position.

  estimated_x = observer_x + dist * cos(observer_theta + bearing_rad)
  estimated_y = observer_y + dist * sin(observer_theta + bearing_rad)

Map Zone System:
  Zones are loaded dynamically from semantic_map.json via the SemanticMap module.
  Each zone has an id, Thai/English labels, center+radius, and expected objects.
  The search system uses zones to prioritize where to look.
"""

import os
import json
import math
import time
import logging
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field, asdict
from pathlib import Path

from gateway.semantic_map import semantic_map

logger = logging.getLogger("object_memory")

# Memory file location
MEMORY_FILE = Path(__file__).parent.parent / "data" / "object_memory.json"

# Default projection distance for object position estimation (meters)
# With a webcam at ~0.5m height, objects on tables/floor are typically 0.5-1.5m away.
# Using 0.8m as a reasonable estimate for indoor lab objects.
DEFAULT_OBJECT_DIST_M = 0.8


# ═══════════════════════════════════════════════════════════════════
#  ZONE FUNCTIONS — delegate to semantic_map module
# ═══════════════════════════════════════════════════════════════════
# Zones are loaded from Gateway/data/semantic_map.json via semantic_map.py.
# These functions provide backward-compatible wrappers.


def get_zone(x: float, y: float) -> Optional[Dict[str, Any]]:
    """Find which zone a map coordinate falls into.
    Returns a dict with 'name' and 'label' keys, or None."""
    z = semantic_map.get_zone_at(x, y)
    if z:
        return {"name": z.id, "label": z.label_th, "description": z.notes,
                "typical_objects": z.expected_objects}
    return None


def get_zone_name(x: float, y: float) -> str:
    """Get the zone name for a map coordinate. Returns '?' if outside all zones."""
    z = semantic_map.get_zone_at(x, y)
    return z.id if z else "?"


def get_zone_label(zone_name: str) -> str:
    """Get Thai label for a zone name."""
    z = semantic_map.get_zone(zone_name)
    return z.label_th if z else f"โซน {zone_name}"


def get_zone_for_object(object_name: str) -> List[Dict[str, Any]]:
    """Get zones where an object is likely to be found."""
    zones = semantic_map.get_zones_for_object(object_name)
    return [{"name": z.id, "label": z.label_th, "description": z.notes,
             "typical_objects": z.expected_objects} for z in zones]


# ── Legacy aliases ──
def get_section(x: float, y: float) -> str:
    """Legacy alias: map coordinates to zone name."""
    return get_zone_name(x, y)


def get_section_label(section: str) -> str:
    """Legacy alias: get Thai label for a zone/section name."""
    return get_zone_label(section)


def location_to_bearing(location: str) -> float:
    """Convert VLM location code to estimated bearing in degrees (+left, -right).

    VLM outputs camera-frame positions like 'center_left', 'top_right'.
    We estimate bearing: left≈+25°, center≈0°, right≈-25°.
    """
    loc = location.lower()
    if "left" in loc:
        return 25.0
    elif "right" in loc:
        return -25.0
    return 0.0


def estimate_object_position(
    observer_x: float,
    observer_y: float,
    observer_theta: float,
    bearing_deg: float,
    distance_m: float = DEFAULT_OBJECT_DIST_M,
) -> tuple:
    """Estimate object's map position by projecting from robot pose.

    Args:
        observer_x, observer_y: Robot position in map frame (meters)
        observer_theta: Robot heading (radians)
        bearing_deg: Bearing from heading to object (+left, -right)
        distance_m: Estimated distance to object (default 0.8m)

    Returns:
        (estimated_x, estimated_y) in map frame
    """
    bearing_rad = math.radians(bearing_deg)
    total_angle = observer_theta + bearing_rad
    est_x = observer_x + distance_m * math.cos(total_angle)
    est_y = observer_y + distance_m * math.sin(total_angle)
    return round(est_x, 3), round(est_y, 3)


@dataclass
class ObjectLocation:
    """Single object observation record."""
    object_name: str              # Normalized name (card, pen, etc.)
    display_name: str             # Original Thai/English name used
    location: str                 # VLM camera-frame code ("center_left", "top_right")
    location_description: str     # Human description (e.g., "บนโต๊ะ ทางซ้าย")
    timestamp: float = field(default_factory=time.time)
    confidence: float = 0.8
    image_url: Optional[str] = None
    # Observer (robot) pose at detection time
    observer_x: float = 0.0      # Robot X in map frame (meters)
    observer_y: float = 0.0      # Robot Y in map frame (meters)
    observer_theta: float = 0.0  # Robot heading (radians)
    # Estimated object position (projected from robot pose + bearing)
    estimated_x: float = 0.0     # Estimated object X in map frame
    estimated_y: float = 0.0     # Estimated object Y in map frame
    # Derived spatial context
    bearing_deg: float = 0.0     # Estimated bearing from heading to object (+left, -right)
    section: str = ""            # Map zone name ("bedroom", "living_room", etc.)
    localization_source: str = ""  # "amcl" | "odom_hybrid" | "dead_reckoning" | ""
    last_verified_at: float = 0.0  # Timestamp of last confirmation at this location

    def age_hours(self) -> float:
        """How many hours ago this was recorded"""
        return (time.time() - self.timestamp) / 3600


# Priority zones are now computed dynamically from semantic_map.
# No hardcoded OBJECT_PRIORITY_ZONES needed.


def _migrate_record(r: dict) -> dict:
    """Migrate old JSON records to new format."""
    # Rename robot_x/y/theta → observer_x/y/theta
    if "robot_x" in r and "observer_x" not in r:
        r["observer_x"] = r.pop("robot_x")
        r["observer_y"] = r.pop("robot_y")
        r["observer_theta"] = r.pop("robot_theta")
    # Fill missing new fields with defaults
    r.setdefault("bearing_deg", location_to_bearing(r.get("location", "")))
    r.setdefault("section", get_zone_name(r.get("observer_x", 0.0), r.get("observer_y", 0.0)))
    r.setdefault("localization_source", "")
    r.setdefault("last_verified_at", 0.0)
    # Compute estimated position if missing
    if "estimated_x" not in r:
        ex, ey = estimate_object_position(
            r.get("observer_x", 0.0),
            r.get("observer_y", 0.0),
            r.get("observer_theta", 0.0),
            r.get("bearing_deg", 0.0),
        )
        r["estimated_x"] = ex
        r["estimated_y"] = ey
    return r


class ObjectMemory:
    """Object memory system — remembers where objects were observed."""

    def __init__(self):
        self._memory: Dict[str, List[ObjectLocation]] = {}
        self._load()

    def _load(self):
        """Load memory from file, migrating old format if needed."""
        if MEMORY_FILE.exists():
            try:
                with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for obj_name, records in data.items():
                        locs = []
                        for r in records:
                            r = _migrate_record(r)
                            locs.append(ObjectLocation(**r))
                        self._memory[obj_name] = locs
                logger.info(f"📚 Loaded object memory: {len(self._memory)} objects")
            except Exception as e:
                logger.warning(f"Failed to load object memory: {e}")
                self._memory = {}
        else:
            logger.info("📚 Object memory: Starting fresh")

    def _save(self):
        """Save memory to file (atomic: write tmp then rename)"""
        try:
            MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            data = {
                obj_name: [asdict(loc) for loc in locations]
                for obj_name, locations in self._memory.items()
            }
            tmp = MEMORY_FILE.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            tmp.replace(MEMORY_FILE)
        except Exception as e:
            logger.warning(f"Failed to save object memory: {e}")

    def remember(
        self,
        object_name: str,
        display_name: str,
        location: str,
        location_description: str = "",
        confidence: float = 0.8,
        image_url: Optional[str] = None,
        observer_x: float = 0.0,
        observer_y: float = 0.0,
        observer_theta: float = 0.0,
        localization_source: str = "",
    ):
        """Remember where an object was observed.

        Computes estimated_x/estimated_y by projecting from robot pose + bearing.
        """
        # ── Suspicious-write detection ──
        if abs(observer_x) < 0.01 and abs(observer_y) < 0.01:
            logger.warning(
                f"⚠️ SUSPICIOUS WRITE: '{object_name}' stored at origin (0,0) "
                f"— pose likely uninitialized (source={localization_source})"
            )

        # ── Compute derived fields ──
        bearing = location_to_bearing(location)
        est_x, est_y = estimate_object_position(
            observer_x, observer_y, observer_theta, bearing,
        )
        zone_name = get_zone_name(est_x, est_y)

        loc = ObjectLocation(
            object_name=object_name,
            display_name=display_name,
            location=location,
            location_description=location_description,
            confidence=confidence,
            image_url=image_url,
            observer_x=observer_x,
            observer_y=observer_y,
            observer_theta=observer_theta,
            estimated_x=est_x,
            estimated_y=est_y,
            bearing_deg=bearing,
            section=zone_name,
            localization_source=localization_source,
            last_verified_at=time.time(),
        )

        if object_name not in self._memory:
            self._memory[object_name] = []

        # Keep only last 10 locations per object
        self._memory[object_name].insert(0, loc)
        self._memory[object_name] = self._memory[object_name][:10]

        self._save()
        logger.info(
            f"📝 Remembered: {display_name} at estimated ({est_x:.2f}, {est_y:.2f}) "
            f"zone={zone_name}({get_zone_label(zone_name)}) "
            f"bearing={bearing:+.0f}° from robot@({observer_x:.2f},{observer_y:.2f}) "
            f"source={localization_source}"
        )

    def recall(self, object_name: str) -> Optional[ObjectLocation]:
        """Recall the most recent observation of an object."""
        if object_name not in self._memory or not self._memory[object_name]:
            return None
        return self._memory[object_name][0]

    def get_history(self, object_name: str, limit: int = 5) -> List[ObjectLocation]:
        """Get observation history for an object"""
        if object_name not in self._memory:
            return []
        return self._memory[object_name][:limit]

    def get_priority_zones(self, object_name: str) -> Dict[str, int]:
        """Get search priority zones for an object type.

        Merges two sources:
        1. Static priors from semantic_map expected_objects (+3 per zone match)
        2. Sighting history from object memory (+5, decayed by age)
        """
        priorities: Dict[str, int] = {}

        # Static priors from semantic map
        for zone in semantic_map.get_zones_for_object(object_name):
            priorities[zone.id] = 3

        # Default fallback: give all zones a baseline score of 1
        if not priorities:
            for zone in semantic_map.get_all_zones():
                priorities[zone.id] = 1

        # Boost priority for zones where we've found it before
        history = self.get_history(object_name, limit=3)
        for loc in history:
            zone = loc.section
            if zone and zone != "?":
                age_penalty = min(loc.age_hours() / 24, 1.0)
                boost = int(5 * (1 - age_penalty))
                priorities[zone] = priorities.get(zone, 0) + boost

        return priorities

    def get_search_hint(self, object_name: str) -> Optional[str]:
        """Get a search hint based on memory.

        Returns a Thai sentence using zone + estimated position.
        Example: "เคยเจอปากกา ห้องนั่งเล่น (ตำแหน่งประมาณ 2.1, 1.8) เมื่อกี้"
        """
        last = self.recall(object_name)
        if not last:
            return None

        age = last.age_hours()
        if age < 1:
            age_text = "เมื่อกี้"
        elif age < 24:
            age_text = f"เมื่อ {int(age)} ชั่วโมงที่แล้ว"
        else:
            days = int(age / 24)
            age_text = f"เมื่อ {days} วันที่แล้ว"

        zone_text = get_zone_label(last.section) if last.section and last.section != "?" else ""
        dir_text = last.location_description or last.location

        hint = f"เคยเจอ{last.display_name}"
        if zone_text:
            hint += f" {zone_text}"
        if dir_text:
            hint += f" ({dir_text})"
        hint += f" {age_text}"
        if last.localization_source and last.localization_source not in ("amcl", ""):
            hint += " (ตำแหน่งอาจไม่แม่น)"
        return hint

    def get_all_objects(self) -> List[str]:
        """Get list of all remembered object names"""
        return list(self._memory.keys())

    def get_all_as_dict(self) -> Dict[str, Any]:
        """Get all memory as a serializable dict (for API endpoints)."""
        result = {}
        for obj_name, locs in self._memory.items():
            result[obj_name] = []
            for loc in locs:
                d = asdict(loc)
                # Add zone info
                d["zone_label"] = get_zone_label(loc.section)
                result[obj_name].append(d)
        return result

    def get_zone_summary(self) -> str:
        """Get a Thai summary of all zones and what's been found in each.
        Useful for LLM context."""
        lines = ["แผนผังห้อง:"]
        for z in semantic_map.get_all_zones():
            objs_in_zone = []
            for obj_name, locs in self._memory.items():
                for loc in locs[:1]:  # most recent only
                    if loc.section == z.id:
                        objs_in_zone.append(loc.display_name)
            obj_text = ", ".join(objs_in_zone) if objs_in_zone else "ยังไม่เคยเจอของ"
            lines.append(f"  {z.label_th}: {z.notes or z.label_en} — พบ: {obj_text}")
        return "\n".join(lines)

    def clear(self, object_name: Optional[str] = None):
        """Clear memory for one object or all"""
        if object_name:
            if object_name in self._memory:
                del self._memory[object_name]
        else:
            self._memory = {}
        self._save()


# Global singleton
object_memory = ObjectMemory()
