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
import urllib.request
import urllib.error
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field, asdict
from pathlib import Path

from gateway.semantic_map import semantic_map

logger = logging.getLogger("object_memory")

# Legacy file — NO LONGER source of truth. Kept only so older on-disk
# data is visible for debugging; planning MUST ignore it. Server is the
# single authority for runtime object memory.
MEMORY_FILE = Path(__file__).parent.parent / "data" / "object_memory.json"

# Server endpoint for authoritative memory fetch. Set by Gateway main.py
# at startup via set_server_base(); until then reload() is a no-op.
_server_base: Optional[str] = None


def set_server_base(url: str) -> None:
    """Inject the Server base URL so ObjectMemory.reload()/clear() can
    talk to the authoritative store. Called once from Gateway startup."""
    global _server_base
    _server_base = (url or "").rstrip("/")
    logger.info(f"[MEM] server base set → {_server_base}")


def _server_get_full(timeout: float = 2.5) -> Optional[Dict[str, List[dict]]]:
    """Blocking HTTP GET of the authoritative memory dict from Server.
    Returns None on any failure — callers must handle that by falling
    back to the current in-process cache (not to local disk)."""
    if not _server_base:
        return None
    url = f"{_server_base}/map/objects/full"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
        data = json.loads(body.decode("utf-8"))
        return data if isinstance(data, dict) else None
    except Exception as e:
        logger.warning(f"[MEM] server GET {url} failed: {e}")
        return None


def _server_post_full(payload: Dict[str, List[dict]], timeout: float = 2.5) -> bool:
    if not _server_base:
        return False
    url = f"{_server_base}/map/objects/full"
    try:
        req = urllib.request.Request(
            url, method="POST",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=timeout).read()
        return True
    except Exception as e:
        logger.warning(f"[MEM] server POST {url} failed: {e}")
        return False


def _server_delete(object_name: Optional[str] = None, timeout: float = 2.5) -> bool:
    if not _server_base:
        return False
    path = f"/map/objects/{object_name}" if object_name else "/map/objects"
    url = f"{_server_base}{path}"
    try:
        req = urllib.request.Request(url, method="DELETE")
        urllib.request.urlopen(req, timeout=timeout).read()
        return True
    except Exception as e:
        logger.warning(f"[MEM] server DELETE {url} failed: {e}")
        return False

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
        """No-op. Local ``object_memory.json`` is NOT a source of truth.

        The Server is the single authority; planning data is fetched via
        :meth:`reload` before each search. Reading the file here would
        resurrect objects the UI already deleted on the Server, which
        was the exact bug we're closing. Logged once so operators can
        audit the decision.
        """
        logger.info("[MEM] local file ignored — Server is the source of truth")

    def _save(self):
        """No-op. Gateway never writes ``object_memory.json`` anymore —
        the authoritative store lives on the Server. ``remember()`` is
        responsible for pushing new records forward via
        ``POST /map/objects/full`` (driven by the Gateway push task)."""
        return

    # ── Server-backed load/push helpers ─────────────────────────────
    def _apply_server_dict(self, data: Dict[str, List[dict]]) -> int:
        """Replace the in-process memory from a Server payload.

        The payload shape matches ``get_all_as_dict()`` minus the
        derived ``zone_label``. Records are migrated through
        ``_migrate_record`` so older-on-wire records still deserialize.
        Returns the total number of records loaded.
        """
        new_memory: Dict[str, List[ObjectLocation]] = {}
        total = 0
        for obj_name, records in (data or {}).items():
            if not isinstance(records, list):
                continue
            locs: List[ObjectLocation] = []
            for r in records:
                if not isinstance(r, dict):
                    continue
                r = dict(r)  # don't mutate caller
                r.pop("zone_label", None)  # derived field, not a ctor arg
                try:
                    r = _migrate_record(r)
                    locs.append(ObjectLocation(**r))
                except Exception as e:
                    logger.debug(f"[MEM] skip bad record for {obj_name}: {e}")
            if locs:
                new_memory[obj_name] = locs
                total += len(locs)
        self._memory = new_memory
        return total

    def push_to_server(self) -> bool:
        """Publish the current in-process memory as the authoritative
        Server state. Called from the Gateway push task after every
        ``remember()`` / ``clear()`` so Server and Gateway stay in
        sync without relying on disk."""
        payload = {
            name: [asdict(loc) for loc in locs]
            for name, locs in self._memory.items()
        }
        return _server_post_full(payload)

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
        # ── Hard reject untrustworthy writes ──
        # Origin pose = AMCL not yet converged or _robot_pose still default.
        # Empty source = caller has no localization signal at all.
        # In both cases the estimated_x/estimated_y projection is meaningless
        # and would mark the object in the wrong room.
        if abs(observer_x) < 0.01 and abs(observer_y) < 0.01:
            logger.warning(
                f"📕 REJECT remember('{object_name}'): observer at origin (0,0) "
                f"— pose uninitialized (source={localization_source!r})"
            )
            return
        if not localization_source or localization_source.lower() in ("none", "unknown"):
            logger.warning(
                f"📕 REJECT remember('{object_name}'): no trustworthy "
                f"localization_source (got {localization_source!r})"
            )
            return

        # ── Compute derived fields ──
        bearing = location_to_bearing(location)
        est_x, est_y = estimate_object_position(
            observer_x, observer_y, observer_theta, bearing,
        )
        zone_name = get_zone_name(est_x, est_y)
        logger.info(
            f"📥 remember('{object_name}') ACCEPTED: src={localization_source} "
            f"observer=({observer_x:.2f},{observer_y:.2f},{observer_theta:.2f}) "
            f"estimated=({est_x:.2f},{est_y:.2f}) zone={zone_name}"
        )

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

        # Immediate push to Server (authoritative). If it fails the
        # next reload() will still see the previous Server state, so
        # the in-process record is effectively local-only until the
        # background push task catches up.
        self.push_to_server()
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

    def reload(self) -> int:
        """Fetch the authoritative memory state from the Server.

        Returns the total number of records now held in-process. If the
        Server is unreachable we keep the existing in-process cache
        (never fall back to the local file — that was the bug).
        """
        before_objs = len(self._memory)
        before_recs = sum(len(v) for v in self._memory.values())
        data = _server_get_full()
        if data is None:
            logger.warning(
                f"[MEM] reload skipped — server unavailable, "
                f"keeping cache objs={before_objs} recs={before_recs}"
            )
            return before_recs
        after_recs = self._apply_server_dict(data)
        logger.info(
            f"[MEM] source=server objects={len(self._memory)} records={after_recs} "
            f"(was {before_objs}/{before_recs})"
        )
        return after_recs

    def get_valid_history(
        self,
        object_name: str,
        zone_id: Optional[str] = None,
        max_age_hours: float = 48.0,
        limit: int = 10,
    ) -> List[ObjectLocation]:
        """Return only fresh, in-zone, currently-on-disk records for planning.

        - Drops entries older than ``max_age_hours`` (stale).
        - If ``zone_id`` is given, drops entries whose ``section`` doesn't match.
        - Drops entries pinned at the (0,0) origin (uninitialized pose writes).
        Logs how many were dropped so [MEM] decisions are auditable.
        """
        history = self.get_history(object_name, limit=limit)
        if not history:
            return []
        kept: List[ObjectLocation] = []
        dropped_age = dropped_zone = dropped_origin = 0
        for loc in history:
            if loc.age_hours() > max_age_hours:
                dropped_age += 1
                continue
            if zone_id and loc.section != zone_id:
                dropped_zone += 1
                continue
            if abs(loc.observer_x) < 0.01 and abs(loc.observer_y) < 0.01:
                dropped_origin += 1
                continue
            kept.append(loc)
        if dropped_age or dropped_zone or dropped_origin:
            logger.info(
                f"[MEM] valid_history '{object_name}' "
                f"zone={zone_id or '*'}: kept={len(kept)} "
                f"dropped(stale={dropped_age},out_of_zone={dropped_zone},origin={dropped_origin})"
            )
        return kept

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
        """Clear memory for one object or all. Authoritative on Server —
        we clear the in-process cache AND propagate the delete so the
        next planning cycle can't resurrect it via ``reload()``."""
        if object_name:
            if object_name in self._memory:
                del self._memory[object_name]
            _server_delete(object_name)
            logger.info(f"[MEM] deleted '{object_name}' — server acked")
        else:
            self._memory = {}
            _server_delete(None)
            logger.info("[MEM] deleted all — server acked")


# Global singleton
object_memory = ObjectMemory()
