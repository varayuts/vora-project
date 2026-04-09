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

    def plan(self, target: str, robot_x: float, robot_y: float) -> SearchPlan:
        """Rank zones by likelihood of containing the target object.

        Returns a SearchPlan with ranked_zones sorted by descending score.
        If the top zone scores above threshold, approach_zone_id is set.
        """
        zones = self._smap.get_all_zones()
        if not zones:
            return SearchPlan(target=target, ranked_zones=[])

        scored: List[Tuple[str, float, str]] = []

        # Get recently searched zones from spatial memory
        recently_searched_zones = self._smem.get_recently_searched_zones(
            max_age_min=_SUPPRESSION_SEC / 60.0
        )

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
            related = self._smem.find_related_locations(target, max_age_min=30)
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
