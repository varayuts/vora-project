"""
Semantic Spatial Memory for VORA
=================================
Persistent spatial memory that remembers WHAT was seen WHERE.

Inspired by scene-graph approaches (DAAAM, ConceptGraphs):
- Every VLM observation is stored with odom pose + heading
- Before search: LLM summarizes explored vs unexplored areas
- Skip re-observation if same position was scanned recently
- Object co-location: "saw table at X → pen likely near X"

Data model:
    Observation = {
        id, timestamp,
        x, y, theta,              # odom pose in meters/radians
        heading_deg,               # human-readable heading
        description,               # VLM English scene description (trimmed)
        objects_mentioned: [],     # nouns extracted from description
        phase,                     # e.g. "phase1_look_left"
        search_target,             # what we were looking for
    }

Persistence: Gateway/data/spatial_memory.json
Retention: configurable (default 1 hour)
"""

import json
import math
import time
import logging
import re
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass, field, asdict
from pathlib import Path

logger = logging.getLogger("spatial_memory")

# ── Config ──
MEMORY_FILE = Path(__file__).parent.parent / "data" / "spatial_memory.json"
RETENTION_SECONDS = 3600        # keep observations for 1 hour
NEAR_RADIUS_M = 0.12           # observations within 12cm = "same place" (step=25cm, must be < step)
NEAR_ANGLE_DEG = 30.0          # observations within 30° heading = "same view"
SKIP_TTL_SECONDS = 120          # skip re-scan if observed within 2 min
MAX_OBSERVATIONS = 200          # cap to prevent unbounded growth

# ── Common English nouns to extract from VLM descriptions ──
_OBJECT_NOUNS = re.compile(
    r'\b(bottle|box|bag|pen|pencil|wallet|card|coil|key|cup|mug|phone|book|'
    r'paper|table|desk|chair|shelf|cabinet|door|wall|panel|floor|logo|sign|'
    r'screen|monitor|laptop|keyboard|mouse|cable|wire|container|basket|bin|'
    r'clock|lamp|light|plant|toy|ball|tool|hammer|screwdriver|tape|folder|'
    r'notebook|backpack|hat|shoe|glass|plate|fork|spoon|knife|remote|charger)\b',
    re.IGNORECASE
)

# ── Furniture / landmark nouns (co-location anchors) ──
_LANDMARK_NOUNS = frozenset({
    "table", "desk", "chair", "shelf", "cabinet", "door", "entrance",
    "sofa", "bench", "counter", "drawer", "rack", "stand", "window",
    "monitor", "screen", "laptop", "keyboard",
})


@dataclass
class Observation:
    """A single VLM observation tied to a physical location."""
    id: int
    timestamp: float
    x: float                    # meters (odom frame)
    y: float
    theta: float                # radians
    heading_deg: float          # degrees, human-readable
    description: str            # VLM scene description (English, trimmed)
    objects_mentioned: List[str]  # extracted nouns
    phase: str                  # pipeline phase label
    search_target: str          # what we were looking for

    def age_seconds(self) -> float:
        return time.time() - self.timestamp

    def age_minutes(self) -> float:
        return self.age_seconds() / 60.0

    def is_expired(self, ttl: float = RETENTION_SECONDS) -> bool:
        return self.age_seconds() > ttl


def _extract_objects(description: str) -> List[str]:
    """Extract mentioned object nouns from an English VLM description."""
    return list(set(m.lower() for m in _OBJECT_NOUNS.findall(description)))


def _angle_diff(a: float, b: float) -> float:
    """Shortest angular distance in degrees between two headings."""
    d = (a - b) % 360
    if d > 180:
        d -= 360
    return abs(d)


class SpatialMemory:
    """
    Persistent semantic spatial memory for the VORA robot.
    
    Stores VLM observations with their physical pose so the robot
    builds a cumulative "semantic map" over time.
    """

    def __init__(self):
        self._observations: List[Observation] = []
        self._next_id: int = 1
        self._load()

    # ═══════════════════════════════════════════
    #  Persistence
    # ═══════════════════════════════════════════

    def _load(self):
        if MEMORY_FILE.exists():
            try:
                with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                obs_list = data.get("observations", [])
                self._next_id = data.get("next_id", 1)
                self._observations = [Observation(**o) for o in obs_list]
                # Prune expired on load
                self._prune()
                logger.info(
                    f"🧠 Spatial memory loaded: {len(self._observations)} observations"
                )
            except Exception as e:
                logger.warning(f"Failed to load spatial memory: {e}")
                self._observations = []
                self._next_id = 1
        else:
            logger.info("🧠 Spatial memory: starting fresh")

    def _save(self):
        try:
            MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "next_id": self._next_id,
                "observations": [asdict(o) for o in self._observations],
            }
            with open(MEMORY_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save spatial memory: {e}")

    def _prune(self):
        """Remove expired observations and cap total count."""
        before = len(self._observations)
        self._observations = [
            o for o in self._observations if not o.is_expired()
        ]
        # Cap size — keep most recent
        if len(self._observations) > MAX_OBSERVATIONS:
            self._observations = self._observations[-MAX_OBSERVATIONS:]
        pruned = before - len(self._observations)
        if pruned > 0:
            logger.info(f"🧹 Pruned {pruned} expired observations")

    # ═══════════════════════════════════════════
    #  1. Record observations
    # ═══════════════════════════════════════════

    def record(
        self,
        x: float,
        y: float,
        theta: float,
        description: str,
        phase: str,
        search_target: str,
    ) -> Observation:
        """
        Store a VLM observation at the current robot pose.

        Args:
            x, y: odom position in meters
            theta: heading in radians
            description: VLM English scene description
            phase: pipeline phase label (e.g. "phase1_look_left")
            search_target: what we were looking for
        
        Returns the created Observation.
        """
        heading_deg = math.degrees(theta) % 360
        objects = _extract_objects(description)

        obs = Observation(
            id=self._next_id,
            timestamp=time.time(),
            x=round(x, 3),
            y=round(y, 3),
            theta=round(theta, 4),
            heading_deg=round(heading_deg, 1),
            description=description[:500],  # increased for richer scene memory
            objects_mentioned=objects,
            phase=phase,
            search_target=search_target,
        )
        self._next_id += 1
        self._observations.append(obs)

        # Periodic prune + save
        if len(self._observations) % 10 == 0:
            self._prune()
        self._save()

        logger.debug(
            f"🧠 Recorded obs #{obs.id} at ({obs.x:.2f},{obs.y:.2f}) "
            f"heading={obs.heading_deg:.0f}° objects={objects}"
        )
        return obs

    # ═══════════════════════════════════════════
    #  2. Pre-search summary for LLM Navigator
    # ═══════════════════════════════════════════

    def get_exploration_summary(self, max_age_min: float = 30.0) -> str:
        """
        Build a textual summary of what the robot has seen recently.
        
        Returns a multi-line string suitable for LLM context, e.g.:
            "📍 (0.08,-0.20) heading 312°: white panels, floor, box [5 min ago]"
            "📍 (0.06,-0.36) heading 346°: gray floor, partition walls [7 min ago]"
        
        If no observations exist, returns empty string.
        """
        recent = [
            o for o in self._observations
            if o.age_minutes() <= max_age_min
        ]
        if not recent:
            return ""

        lines = []
        for o in recent[-15:]:  # last 15 observations
            objs = ", ".join(o.objects_mentioned[:5]) if o.objects_mentioned else "—"
            desc_short = o.description[:80].replace("\n", " ")
            lines.append(
                f"📍 ({o.x:.2f},{o.y:.2f}) heading={o.heading_deg:.0f}° "
                f"[{o.age_minutes():.0f}m ago] objects=[{objs}]: {desc_short}"
            )
        return "\n".join(lines)

    def get_explored_regions(self, max_age_min: float = 10.0) -> List[Dict[str, Any]]:
        """
        Return a list of explored "regions" — clustered by proximity.
        
        Each region: {center_x, center_y, count, last_seen_min, objects}
        """
        recent = [
            o for o in self._observations
            if o.age_minutes() <= max_age_min
        ]
        if not recent:
            return []

        # Simple clustering: merge observations within NEAR_RADIUS_M
        clusters: List[Dict[str, Any]] = []
        for obs in recent:
            merged = False
            for c in clusters:
                dx = obs.x - c["center_x"]
                dy = obs.y - c["center_y"]
                if math.sqrt(dx * dx + dy * dy) < NEAR_RADIUS_M:
                    # Merge into cluster
                    n = c["count"]
                    c["center_x"] = (c["center_x"] * n + obs.x) / (n + 1)
                    c["center_y"] = (c["center_y"] * n + obs.y) / (n + 1)
                    c["count"] += 1
                    c["last_seen_min"] = min(c["last_seen_min"], obs.age_minutes())
                    c["objects"].update(obs.objects_mentioned)
                    merged = True
                    break
            if not merged:
                clusters.append({
                    "center_x": obs.x,
                    "center_y": obs.y,
                    "count": 1,
                    "last_seen_min": obs.age_minutes(),
                    "objects": set(obs.objects_mentioned),
                })

        # Convert sets to lists for JSON
        for c in clusters:
            c["objects"] = list(c["objects"])
        return clusters

    # ═══════════════════════════════════════════
    #  3. Skip duplicate scans
    # ═══════════════════════════════════════════

    def was_recently_observed(
        self,
        x: float,
        y: float,
        heading_deg: float,
        ttl: float = SKIP_TTL_SECONDS,
    ) -> bool:
        """
        Check if this position + heading was already observed recently.
        
        Returns True if there's a non-expired observation within NEAR_RADIUS_M
        and NEAR_ANGLE_DEG of the given pose, recorded less than `ttl` seconds ago.
        """
        now = time.time()
        for obs in reversed(self._observations):  # newest first
            age = now - obs.timestamp
            if age > ttl:
                continue  # too old
            dx = x - obs.x
            dy = y - obs.y
            dist = math.sqrt(dx * dx + dy * dy)
            if dist > NEAR_RADIUS_M:
                continue  # too far
            if _angle_diff(heading_deg, obs.heading_deg) > NEAR_ANGLE_DEG:
                continue  # different heading
            return True
        return False

    # ═══════════════════════════════════════════
    #  4. Object co-location
    # ═══════════════════════════════════════════

    def find_related_locations(
        self,
        target_object: str,
        max_age_min: float = 60.0,
    ) -> List[Dict[str, Any]]:
        """
        Find observations where landmarks related to the target were seen.
        
        E.g., if target is "pen" and we saw "desk" at (0.5, 0.3),
        return that observation as a "likely place to look."
        
        Returns list of {x, y, heading_deg, landmark, age_min, description}.
        """
        target_lower = target_object.lower()

        # Direct hits — we literally saw the target name before
        direct = []
        landmark_hits = []

        for obs in self._observations:
            if obs.age_minutes() > max_age_min:
                continue

            # Direct: VLM description mentions the exact target
            if target_lower in obs.description.lower():
                direct.append({
                    "x": obs.x,
                    "y": obs.y,
                    "heading_deg": obs.heading_deg,
                    "landmark": target_lower,
                    "age_min": round(obs.age_minutes(), 1),
                    "description": obs.description[:100],
                    "type": "direct",
                })

            # Landmark co-location: VLM mentioned furniture/landmark
            for obj in obs.objects_mentioned:
                if obj in _LANDMARK_NOUNS:
                    landmark_hits.append({
                        "x": obs.x,
                        "y": obs.y,
                        "heading_deg": obs.heading_deg,
                        "landmark": obj,
                        "age_min": round(obs.age_minutes(), 1),
                        "description": obs.description[:100],
                        "type": "landmark",
                    })

        # Direct hits first, then landmarks (deduplicated by position)
        results = direct
        seen_positions = set((round(d["x"], 1), round(d["y"], 1)) for d in direct)
        for lm in landmark_hits:
            key = (round(lm["x"], 1), round(lm["y"], 1))
            if key not in seen_positions:
                results.append(lm)
                seen_positions.add(key)

        return results[:10]  # cap at 10

    def build_colocation_context(
        self,
        target_object: str,
        max_age_min: float = 60.0,
    ) -> str:
        """
        Build a textual context string about related locations for the LLM.
        
        Returns something like:
            "🗺️ ขวดน้ำ เคยเห็นใกล้ desk ที่ (0.50, 0.30) เมื่อ 5 นาทีที่แล้ว"
        """
        hits = self.find_related_locations(target_object, max_age_min)
        if not hits:
            return ""

        lines = []
        for h in hits[:5]:
            tag = "⭐ เคยเห็นวัตถุนี้" if h["type"] == "direct" else f"📌 เคยเห็น {h['landmark']}"
            lines.append(
                f"{tag} ที่ ({h['x']:.2f},{h['y']:.2f}) heading={h['heading_deg']:.0f}° "
                f"[{h['age_min']:.0f} นาทีที่แล้ว]"
            )
        return "\n".join(lines)

    # ═══════════════════════════════════════════
    #  Utilities
    # ═══════════════════════════════════════════

    def get_stats(self) -> Dict[str, Any]:
        """Return memory statistics."""
        self._prune()
        all_objects = set()
        for obs in self._observations:
            all_objects.update(obs.objects_mentioned)
        return {
            "total_observations": len(self._observations),
            "unique_objects_seen": len(all_objects),
            "objects": sorted(all_objects),
            "oldest_min": round(self._observations[0].age_minutes(), 1) if self._observations else 0,
            "newest_min": round(self._observations[-1].age_minutes(), 1) if self._observations else 0,
        }

    def clear(self):
        """Clear all observations."""
        self._observations = []
        self._next_id = 1
        self._save()
        logger.info("🧹 Spatial memory cleared")


# ── Singleton ──
spatial_memory = SpatialMemory()
