"""
search_planner.py — Zone-ranked search planning for VORA.

Given a target object, ranks zones by likelihood and returns a SearchPlan
indicating where the robot should go first.

Scoring:
  +5  static prior: zone.expected_objects contains target
  +8  memory sighting: object_memory has prior hit in this zone (decayed by age)
  +3  co-location: spatial_memory saw target-related landmark in zone
  -10 recently searched: zone scanned < 5 min ago (suppression)
  -0.5/m distance penalty from robot's current position
"""

import math
import re
import time
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("gateway")


@dataclass
class SearchPlan:
    target: str
    ranked_zones: List[Tuple[str, float, str]]  # (zone_id, score, reason)
    approach_zone_id: Optional[str] = None
    approach_x: float = 0.0
    approach_y: float = 0.0


# Minimum score to trigger zone navigation (below this → skip to agent loop)
_MIN_ZONE_SCORE = 3.0

# Suppression window: don't revisit a zone within this many seconds
_SUPPRESSION_SEC = 300.0  # 5 min

# Strong bonuses/penalties for explicit zone intent (must dominate baseline scoring)
_EXPLICIT_ZONE_BONUS = 1000.0
_NON_TARGET_PENALTY = 100.0
_EXCLUDED_ZONE_PENALTY = 1000.0

# Common aliases not present in semantic_map labels: alias → canonical zone id.
# Used by extract_zone_intent() to recognize zone names in free-form queries.
_ZONE_ALIASES: Dict[str, str] = {
    "toilet": "bathroom",
    "restroom": "bathroom",
    "wc": "bathroom",
    "ห้องส้วม": "bathroom",
    "ส้วม": "bathroom",
    "lounge": "living_room",
    "living": "living_room",
    "living room": "living_room",
    "livingroom": "living_room",
    "ห้องรับแขก": "living_room",
    "ห้องนั่งเล่น": "living_room",
    "นั่งเล่น": "living_room",  # catches "ที่นั่งเล่น" via substring
    "รับแขก": "living_room",
    "door": "entrance",
    "front door": "entrance",
    "หน้าประตู": "entrance",
    "ที่ประตู": "entrance",
    "ประตูทางเข้า": "entrance",
    "ทางเข้า": "entrance",
    "ห้องนอน": "bedroom",
    "ที่นอน": "bedroom",
    "ห้องน้ำ": "bathroom",
    "ที่ห้องน้ำ": "bathroom",
}

# Phrases that indicate "leave/exit this zone" (the matched zone becomes EXCLUDED)
_EXIT_PATTERNS = (
    "exit ", "leave ", "out of ", "away from ",
    "ออกจาก", "ออกไปจาก", "ออก ",
)


def extract_zone_intent(text: str, smap) -> Tuple[Optional[str], Optional[str]]:
    """Parse a free-form user query for explicit zone intent.

    Returns ``(target_zone_id, excluded_zone_id)``. Either may be None.

    Handles substring matches against ``zone.id`` / ``label_en`` / ``label_th``,
    common aliases (e.g. ``toilet`` → ``bathroom``), and exit-style phrases
    (``"exit bedroom"`` / ``"ออกจากห้องนอน"``) which mark the matched zone as
    excluded so the planner picks somewhere else.
    """
    if not text:
        return None, None
    t = " " + text.lower().strip() + " "
    zones = smap.get_all_zones()
    if not zones:
        return None, None
    zone_ids = {z.id for z in zones}

    def _match_any_zone(segment: str) -> Optional[str]:
        # Direct id / label substring
        for z in zones:
            for needle in (z.id, (z.label_en or "").lower(), (z.label_th or "").lower()):
                if needle and len(needle) >= 3 and needle in segment:
                    return z.id
        # Alias substring
        for alias, zid in _ZONE_ALIASES.items():
            if zid in zone_ids and alias in segment:
                return zid
        return None

    # 1) Detect excluded zone via exit phrases
    excluded_id: Optional[str] = None
    for pat in _EXIT_PATTERNS:
        idx = t.find(pat)
        if idx < 0:
            continue
        # Look at the ~30 chars after the exit phrase for a zone name
        rest = t[idx + len(pat): idx + len(pat) + 30]
        zid = _match_any_zone(rest)
        if zid:
            excluded_id = zid
            # Erase the exit phrase + zone name from t so step 2 can't pick it as target
            t = t.replace(pat + rest.split(zid, 1)[0] + zid, " ", 1) if zid in rest else t
            break

    # 2) Detect target zone in remaining text
    target_id: Optional[str] = None
    candidate = _match_any_zone(t)
    if candidate and candidate != excluded_id:
        target_id = candidate

    return target_id, excluded_id


def find_memory_anchor(
    omem,
    smap,
    target: str,
    zone_id: str,
    max_age_hours: float = 48.0,
) -> Optional[Tuple[float, float, float]]:
    """Return ``(x, y, age_h)`` of the freshest sighting of ``target``
    inside ``zone_id``, or None.

    Used to refine approach: instead of navigating to the zone *center*,
    the robot can hop to the precise spot where the object was last seen
    inside that zone. Sightings outside the zone radius (+0.5m slop) are
    ignored to keep stale records from yanking the robot to nowhere.
    """
    if not target or not zone_id:
        return None
    # Authoritative reload so deleted/edited records can never resurrect
    # via stale in-process state.
    if hasattr(omem, "reload"):
        try:
            omem.reload()
        except Exception:
            pass
    # Strict filter: only fresh, in-zone, non-origin records.
    if hasattr(omem, "get_valid_history"):
        history = omem.get_valid_history(
            target, zone_id=zone_id, max_age_hours=max_age_hours, limit=10,
        )
    else:
        history = omem.get_history(target, limit=10)
    if not history:
        return None
    z = smap.get_zone(zone_id)
    best: Optional[Tuple[float, float, float]] = None
    for loc in history:
        if loc.section != zone_id:
            continue
        age_h = loc.age_hours()
        if age_h > max_age_hours:
            continue
        if z is not None:
            dx = loc.estimated_x - z.center_x
            dy = loc.estimated_y - z.center_y
            if math.sqrt(dx * dx + dy * dy) > (z.radius + 0.5):
                continue  # estimated point fell outside the zone
        if best is None or age_h < best[2]:
            best = (loc.estimated_x, loc.estimated_y, age_h)
    return best


class SearchPlanner:
    """Computes a zone-ranked search plan for a target object."""

    def __init__(self, smap, omem, smem):
        """
        Args:
            smap: SemanticMap instance
            omem: ObjectMemory instance
            smem: SpatialMemory instance
        """
        self._smap = smap
        self._omem = omem
        self._smem = smem
        self._recently_searched: Dict[str, float] = {}

    def plan(
        self,
        target: str,
        robot_x: float,
        robot_y: float,
        explicit_zone_id: Optional[str] = None,
        excluded_zone_id: Optional[str] = None,
        current_zone_id: Optional[str] = None,
    ) -> SearchPlan:
        """Rank zones by likelihood of containing the target object.

        Returns a SearchPlan with ranked_zones sorted by descending score.
        If the top zone scores above threshold, approach_zone_id is set.

        When ``explicit_zone_id`` is set, that zone gets a dominating bonus and
        all other zones get a strong penalty — so co-location, distance, and
        sighting history can refine ordering but never override the target.
        """
        # Authoritative reload — deleted/edited records take effect now,
        # not whenever the singleton was last constructed.
        if hasattr(self._omem, "reload"):
            try:
                self._omem.reload()
            except Exception as _e:
                logger.warning(f"[MEM] reload in planner failed: {_e}")

        zones = self._smap.get_all_zones()
        if not zones:
            return SearchPlan(target=target, ranked_zones=[])

        if explicit_zone_id or excluded_zone_id:
            logger.info(
                f"[ZONE] explicit={explicit_zone_id} "
                f"current={current_zone_id} excluded={excluded_zone_id}"
            )

        # ── HARD OVERRIDE ──
        # User named a target zone → that zone is the only valid candidate.
        # Memory may refine *where in the zone* but cannot rewrite the zone.
        # No ranking, no fallbacks, no "smart" reordering.
        if explicit_zone_id:
            z = self._smap.get_zone(explicit_zone_id)
            if z is not None:
                logger.info(f"[ZONE] forcing explicit target zone={explicit_zone_id} (hard override)")
                return SearchPlan(
                    target=target,
                    ranked_zones=[(z.id, _EXPLICIT_ZONE_BONUS, "explicit_target_zone(forced)")],
                    approach_zone_id=z.id,
                    approach_x=z.center_x,
                    approach_y=z.center_y,
                )
            logger.warning(f"[ZONE] explicit zone '{explicit_zone_id}' not in semantic_map — falling through")

        scored: List[Tuple[str, float, str]] = []

        # Get recently searched zones from spatial memory.
        # spatial_memory may be None now (Gateway disabled it as a planner
        # input because stale cross-session observations were bleeding into
        # ranking even after the user cleared object memory).
        if self._smem is not None:
            recently_searched_zones = self._smem.get_recently_searched_zones(
                max_age_min=_SUPPRESSION_SEC / 60.0
            )
        else:
            recently_searched_zones = set()

        for zone in zones:
            score = 0.0
            reasons = []

            # 1. Static prior: expected_objects match
            obj_lower = target.lower()
            for eo in zone.expected_objects:
                if obj_lower in eo.lower() or eo.lower() in obj_lower:
                    score += 5.0
                    reasons.append(f"expected({eo})")
                    break

            # 2. Memory sighting: past observations in this zone
            history = self._omem.get_history(target, limit=3)
            for loc in history:
                if loc.section == zone.id:
                    age_hours = loc.age_hours()
                    decay = max(0.0, 1.0 - age_hours / 24.0)
                    bonus = 8.0 * decay
                    score += bonus
                    reasons.append(f"sighting({age_hours:.1f}h ago, +{bonus:.1f})")
                    break  # only count best sighting per zone

            # 3. Co-location: spatial_memory saw related landmark in this zone
            related = (
                self._smem.find_related_locations(target, max_age_min=30)
                if self._smem is not None else []
            )
            for rel in related:
                rel_zone = self._smap.get_zone_at(rel["x"], rel["y"])
                if rel_zone and rel_zone.id == zone.id:
                    score += 3.0
                    reasons.append("co-location")
                    break

            # 4. Recently searched suppression
            if zone.id in self._recently_searched:
                elapsed = time.time() - self._recently_searched[zone.id]
                if elapsed < _SUPPRESSION_SEC:
                    score -= 10.0
                    reasons.append(f"suppressed({elapsed:.0f}s ago)")
            if zone.id in recently_searched_zones:
                score -= 5.0
                reasons.append("spatial_suppressed")

            # 5. Distance penalty
            dx = zone.center_x - robot_x
            dy = zone.center_y - robot_y
            dist = math.sqrt(dx * dx + dy * dy)
            dist_penalty = 0.5 * dist
            score -= dist_penalty
            if dist_penalty > 0.5:
                reasons.append(f"dist({dist:.1f}m)")

            # 6. Explicit target-zone override (HARD priority)
            if explicit_zone_id:
                if zone.id == explicit_zone_id:
                    score += _EXPLICIT_ZONE_BONUS
                    reasons.append("forced_zone_priority(explicit_target_zone)")
                    if current_zone_id and current_zone_id != explicit_zone_id:
                        reasons.append(f"wrong_zone({current_zone_id})->move_to_target_zone")
                    elif current_zone_id == explicit_zone_id:
                        reasons.append("local_search_in_zone")
                else:
                    score -= _NON_TARGET_PENALTY
                    reasons.append("non_target_zone")

            # 7. Excluded zone (e.g. "exit bedroom")
            if excluded_zone_id and zone.id == excluded_zone_id:
                score -= _EXCLUDED_ZONE_PENALTY
                reasons.append("excluded_zone")

            reason_str = ", ".join(reasons) if reasons else "baseline"
            scored.append((zone.id, round(score, 1), reason_str))

        # Sort by score descending
        scored.sort(key=lambda x: x[1], reverse=True)

        # Determine top pick
        approach_zone_id = None
        approach_x = 0.0
        approach_y = 0.0
        if scored and scored[0][1] >= _MIN_ZONE_SCORE:
            top_zone = self._smap.get_zone(scored[0][0])
            if top_zone:
                approach_zone_id = top_zone.id
                approach_x = top_zone.center_x
                approach_y = top_zone.center_y

        plan = SearchPlan(
            target=target,
            ranked_zones=scored,
            approach_zone_id=approach_zone_id,
            approach_x=approach_x,
            approach_y=approach_y,
        )

        logger.info(f"🗺️ SearchPlan for '{target}': {scored[:4]}")
        if approach_zone_id:
            logger.info(f"   → Navigate to {approach_zone_id} at ({approach_x:.2f}, {approach_y:.2f})")
        else:
            logger.info(f"   → No strong zone match (top score={scored[0][1] if scored else 0}), use agent loop")

        return plan

    def memory_backed_fallback_zones(
        self,
        plan: SearchPlan,
        exclude_ids: Optional[set] = None,
        max_n: int = 2,
    ) -> List[Tuple[str, float, float]]:
        """Return up to ``max_n`` next zones from ``plan.ranked_zones`` that
        are backed by memory or a static prior, skipping the chosen approach
        zone and any caller-supplied excluded ids.

        A zone qualifies if its score reason mentions ``sighting`` (memory),
        ``expected`` (static prior) or ``co-location`` (spatial memory).
        Suppressed / excluded / non_target zones (negative-scoring) are
        rejected. Result: ``[(zone_id, x, y), ...]`` in rank order.
        """
        skip = set(exclude_ids or set())
        if plan.approach_zone_id:
            skip.add(plan.approach_zone_id)
        out: List[Tuple[str, float, float]] = []
        for zid, score, reason in plan.ranked_zones:
            if zid in skip:
                continue
            if score <= 0:
                continue
            if not any(tag in reason for tag in ("sighting", "expected", "co-location")):
                continue
            z = self._smap.get_zone(zid)
            if not z:
                continue
            out.append((zid, z.center_x, z.center_y))
            if len(out) >= max_n:
                break
        return out

    def mark_searched(self, zone_id: str) -> None:
        """Mark a zone as recently searched (suppresses it for _SUPPRESSION_SEC)."""
        self._recently_searched[zone_id] = time.time()
        logger.info(f"🗺️ Zone '{zone_id}' marked as searched (suppressed for {_SUPPRESSION_SEC:.0f}s)")

    @staticmethod
    def local_scan_angles() -> List[float]:
        """Return compact scan angles for local zone inspection.
        3 positions, 120 degrees apart — covers full 360 with camera FOV overlap.
        """
        return [0.0, 120.0, -120.0]
