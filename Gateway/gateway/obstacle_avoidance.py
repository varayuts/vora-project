"""
Obstacle Avoidance Module for VORA Gateway
============================================
Combines LiDAR reactive avoidance with VLM-assisted decision making.

Architecture:
1. LiDAR (YDLidar on MyAGV) → detects obstacles via /scan topic
2. If obstacle detected → capture snapshot → send to Server VLM → get strategy
3. Execute avoidance strategy: go_around_left, go_around_right, wait, reroute, stop

Professor's Question:
"ถ้ามี obstacle ล่ะเจอสิ่งกีดขวางหุ่นจะตัดสินใจอะไรต่อเพื่อไปยังจุดหมายหรือเพื่อหาของสิ่งๆนั้น"

Answer: The robot uses a 3-layer approach:
  Layer 1: LiDAR reactive → immediate stop if too close (< 0.3m)
  Layer 2: VLM analysis → identify obstacle type and decide strategy
  Layer 3: Replanning → adjust navigation goal based on VLM recommendation
"""

import os
import json
import math
import asyncio
import logging
import tempfile
from typing import Dict, Any, Optional, List, Tuple
import httpx

logger = logging.getLogger("obstacle")

# Configuration
FRONT_ANGLE_DEG = 60       # ±30° from front = 60° cone
OBSTACLE_WARN_M = 0.8      # Warning distance (slow down)
OBSTACLE_STOP_M = 0.3      # Emergency stop distance
SCAN_TOPIC = "/scan"        # ROS LaserScan topic
AVOIDANCE_SPEED = 0.10      # Slow avoidance speed (m/s)
AVOIDANCE_ANGULAR = 0.40    # Avoidance turn speed (rad/s)

# ── Robot Body Model ──────────────────────────────────────────────────
# Elephant MyAGV 2023 physical dimensions (measured)
ROBOT_WIDTH_M = 0.21        # 21 cm body width
ROBOT_LENGTH_M = 0.26       # 26 cm body length (front to back)
ROBOT_HALF_WIDTH_M = ROBOT_WIDTH_M / 2  # 10.5 cm half-width for clearance
ROBOT_CLEARANCE_M = 0.05    # 5 cm minimum gap on each side to pass
ROBOT_MIN_PASSAGE_M = ROBOT_WIDTH_M + 2 * ROBOT_CLEARANCE_M  # 31 cm minimum passage

# Camera specs (Logitech C920-like webcam mounted on top of robot)
CAMERA_FOV_H_DEG = 78       # Horizontal field of view
CAMERA_MOUNT_HEIGHT_M = 0.18  # Camera height from ground (on top of LiDAR motor)
CAMERA_MOUNT_OFFSET_X_M = 0.0  # Camera is centered on robot body

# Server VLM endpoint
SERVER_BASE = os.getenv("SERVER_BASE", "https://user.tail87d9fe.ts.net")

# ── Sector Analysis Config ────────────────────────────────────────────
NUM_SECTORS = 24            # Divide 360° into 24 sectors (15° each)
SECTOR_DEG = 360 / NUM_SECTORS  # 15° per sector

# ── LiDAR Scan Arc ────────────────────────────────────────────────────
# YDLidar G2 on Elephant MyAGV: physical scan arc measured from base_footprint
# is approximately 230° (±115° from front). The rear ~130° blind spot has no
# LiDAR returns at all — do NOT treat those sectors as "open space".
SCAN_ARC_DEG = 230
SCAN_HALF_ARC_DEG = SCAN_ARC_DEG / 2   # ±115° from front

# ── LiDAR Mounting Offset ─────────────────────────────────────────────
# YDLidar G2 on Elephant MyAGV: sensor 0° points FORWARD (cable side = rear).
# Previous 180° offset was INVERTED — caused robot to think open space was
# behind it and walls were in front, leading to spin-in-place behavior.
# Set to 0 by default; override via LIDAR_OFFSET_DEG env var if mounting differs.
_lidar_offset_deg = float(os.getenv("LIDAR_OFFSET_DEG", "0"))
LIDAR_ANGLE_OFFSET_RAD = math.radians(_lidar_offset_deg)

# ── LiDAR Left/Right Mirror ───────────────────────────────────────────
# YDLidar G2 on Elephant MyAGV: LIDAR_MIRROR=1 (default ON).
# With MIRROR=ON  → forward clearance after turning matches sector prediction.
# With MIRROR=OFF → robot turns toward walls (verified 23-Mar-2026).
# Override via LIDAR_MIRROR env var if needed.
LIDAR_MIRROR = os.getenv("LIDAR_MIRROR", "1") == "1"


class ObstacleAvoidance:
    """
    Obstacle avoidance controller that combines LiDAR + VLM.
    
    Workflow:
    1. Subscribe to /scan (LiDAR) via ROSBridge
    2. When obstacle detected in front zone:
       a. Immediate stop (safety)
       b. If camera available → capture → send to VLM
       c. VLM returns strategy: go_around_left/right, wait, reroute, stop
       d. Execute strategy
    3. Resume navigation after obstacle cleared
    """
    
    def __init__(self, motion_publisher, rosbridge_url: str):
        self.motion = motion_publisher
        self.rosbridge_url = rosbridge_url
        self._ros = None
        self._scan_sub = None
        self._enabled = True
        self._current_goal = ""
        self._obstacle_detected = False
        self._min_front_distance = float('inf')
        self._last_scan_ranges = []
        self._last_angle_min = -math.pi
        self._last_angle_increment = 0.01
        self._last_range_min = 0.05
        self._last_range_max = 12.0
        self._avoiding = False
        # Sector analysis cache (updated every _on_scan)
        self._sector_distances = [float('inf')] * NUM_SECTORS  # avg dist per sector
        self._sector_min_distances = [float('inf')] * NUM_SECTORS  # min dist per sector
        self._scan_diag_logged = False  # one-time diagnostic logging
        
    async def start(self, ros_connection):
        """Start listening to LiDAR scan topic."""
        self._ros = ros_connection
        
        try:
            import roslibpy
            self._scan_sub = roslibpy.Topic(
                self._ros, SCAN_TOPIC, "sensor_msgs/LaserScan"
            )
            self._scan_sub.subscribe(self._on_scan)
            logger.info(f"✅ Obstacle avoidance started (LiDAR: {SCAN_TOPIC})")
            logger.info(f"   Warning: {OBSTACLE_WARN_M}m | Stop: {OBSTACLE_STOP_M}m | Cone: ±{FRONT_ANGLE_DEG//2}°")
            logger.info(f"   🔀 LIDAR_MIRROR={'ON' if LIDAR_MIRROR else 'OFF'}")
        except Exception as e:
            logger.error(f"❌ Failed to subscribe to {SCAN_TOPIC}: {e}")
    
    def stop(self):
        """Stop obstacle avoidance."""
        self._enabled = False
        if self._scan_sub:
            self._scan_sub.unsubscribe()
            logger.info("🛑 Obstacle avoidance stopped")
    
    def set_goal(self, goal: str):
        """Set current navigation goal (for VLM context)."""
        self._current_goal = goal
        logger.info(f"🎯 Obstacle avoidance goal: {goal}")
    
    @property
    def is_obstacle_detected(self) -> bool:
        return self._obstacle_detected
    
    @property
    def min_distance(self) -> float:
        return self._min_front_distance
    
    def _on_scan(self, msg: dict):
        """
        Callback for /scan topic (sensor_msgs/LaserScan).
        
        Updates front obstacle detection AND full 360° sector analysis.
        """
        if not self._enabled:
            return
        
        ranges = msg.get("ranges", [])
        angle_min = msg.get("angle_min", -math.pi)
        angle_increment = msg.get("angle_increment", 0.01)
        range_min = msg.get("range_min", 0.05)
        range_max = msg.get("range_max", 12.0)
        
        if not ranges:
            return
        
        # One-time diagnostic: log raw scan parameters for debugging angle mapping
        if not self._scan_diag_logged:
            self._scan_diag_logged = True
            n = len(ranges)
            angle_max = angle_min + (n - 1) * angle_increment
            logger.info(
                f"📐 LiDAR DIAG: {n} rays, angle_min={math.degrees(angle_min):.1f}°, "
                f"angle_max={math.degrees(angle_max):.1f}°, "
                f"increment={math.degrees(angle_increment):.3f}°, "
                f"MIRROR={'ON' if LIDAR_MIRROR else 'OFF'}, "
                f"OFFSET={math.degrees(LIDAR_ANGLE_OFFSET_RAD):.1f}°"
            )
        
        self._last_scan_ranges = ranges
        self._last_angle_min = angle_min
        self._last_angle_increment = angle_increment
        self._last_range_min = range_min
        self._last_range_max = range_max
        
        # ── Front-zone obstacle detection (existing) ──
        front_half_rad = math.radians(FRONT_ANGLE_DEG / 2)
        front_distances = []
        
        # ── ~230° Sector analysis (actual scan arc, rear blind spot has no data) ──
        # Divide the measured circle into NUM_SECTORS; sectors outside ±SCAN_HALF_ARC_DEG
        # will naturally have zero counts and be flagged as dead zones.
        sector_sums = [0.0] * NUM_SECTORS
        sector_counts = [0] * NUM_SECTORS
        sector_mins = [float('inf')] * NUM_SECTORS
        
        for i, r in enumerate(ranges):
            raw_angle = angle_min + i * angle_increment
            # Apply mounting offset
            angle = raw_angle + LIDAR_ANGLE_OFFSET_RAD
            # Mirror left/right if scan direction is inverted
            if LIDAR_MIRROR:
                angle = -angle
            # Normalize to [-π, π]
            while angle > math.pi:
                angle -= 2 * math.pi
            while angle < -math.pi:
                angle += 2 * math.pi
            
            # Front zone check
            if -front_half_rad <= angle <= front_half_rad:
                if range_min <= r <= range_max:
                    front_distances.append(r)
            
            # Sector analysis — normalize angle to [0, 360)
            if range_min <= r <= range_max:
                angle_deg = math.degrees(angle) % 360
                sector_idx = int(angle_deg / SECTOR_DEG) % NUM_SECTORS
                sector_sums[sector_idx] += r
                sector_counts[sector_idx] += 1
                if r < sector_mins[sector_idx]:
                    sector_mins[sector_idx] = r
        
        # Update front obstacle status
        if not front_distances:
            self._obstacle_detected = False
            self._min_front_distance = float('inf')
        else:
            self._min_front_distance = min(front_distances)
            self._obstacle_detected = self._min_front_distance < OBSTACLE_WARN_M
        
        # Update sector caches
        for s in range(NUM_SECTORS):
            if sector_counts[s] > 0:
                self._sector_distances[s] = sector_sums[s] / sector_counts[s]
            else:
                self._sector_distances[s] = float('inf')
            self._sector_min_distances[s] = sector_mins[s]
    
    def get_obstacle_checker(self):
        """Return a synchronous callable for exec_motion's obstacle_checker param.
        Uses get_forward_clearance() to skip the LiDAR dead zone (±0-15°) which
        can return junk near-zero values and cause false positives on forward motion."""
        def _check():
            fwd = self.get_forward_clearance()
            return fwd < OBSTACLE_STOP_M
        return _check
    
    def find_best_direction(self) -> Dict[str, Any]:
        """
        Analyze 360° LiDAR data to find the best direction for the robot to travel.
        
        Uses sector analysis to score each direction based on:
        1. Average distance (farther = more open space)
        2. Minimum distance (must be > robot width for clearance)
        3. Neighboring sectors (wide corridor = better than narrow gap)
        
        NOTE: Only sectors within ±SCAN_HALF_ARC_DEG (±115°) carry real LiDAR data.
        Rear sectors (beyond ±115°) are always marked as dead zones — no data ≠ clear.

        Returns:
        {
            "best_angle_deg": float,   # Best direction relative to current heading (0=front)
            "best_distance": float,    # Average distance in best direction
            "passable": bool,          # Can robot physically fit?
            "all_sectors": [...],      # Summary of all 24 sectors
            "open_directions": [...]   # List of passable directions sorted by score
        }
        """
        sectors = []
        for s in range(NUM_SECTORS):
            center_deg = s * SECTOR_DEG + SECTOR_DEG / 2  # e.g. 15°, 45°, 75°, ...
            # Normalize to [-180, 180) relative to front (0°)
            rel_deg = center_deg if center_deg <= 180 else center_deg - 360
            
            avg_dist = self._sector_distances[s]
            min_dist = self._sector_min_distances[s]
            
            # Dead zone detection: sectors with NO LiDAR readings (e.g. webcam blocking ±15°,
            # or outside the physical ~230° scan arc) must be treated as impassable,
            # NOT as "infinitely open" — no data ≠ clear path.
            is_dead_zone = (
                (avg_dist == float('inf') and min_dist == float('inf'))
                or abs(rel_deg) > SCAN_HALF_ARC_DEG  # outside scan arc
            )
            
            # Check corridor width: look at perpendicular sectors for clearance
            # For a sector facing direction D, check if sectors at D±90° have enough
            # distance to indicate the robot can physically fit
            left_sector = (s + NUM_SECTORS // 4) % NUM_SECTORS  # +90°
            right_sector = (s - NUM_SECTORS // 4) % NUM_SECTORS  # -90°
            left_clearance = self._sector_min_distances[left_sector]
            right_clearance = self._sector_min_distances[right_sector]
            
            # Passable = min distance > robot half-length AND sides > robot half-width + margin
            # Dead zones (no readings) are NOT passable — unknown = unsafe
            passable = (
                not is_dead_zone and
                min_dist > (ROBOT_LENGTH_M / 2 + ROBOT_CLEARANCE_M) and
                left_clearance > ROBOT_HALF_WIDTH_M and
                right_clearance > ROBOT_HALF_WIDTH_M
            )
            
            # Score: weighted combination of distance + passability + angle penalty
            # Prefer closer-to-forward directions (human-like: don't do 180° turn if not needed)
            forward_bias = max(0, 1.0 - abs(rel_deg) / 180.0)  # 1.0 at front, 0 at back
            # Cap distances for scoring — infinity makes forward unbeatable,
            # causing robot to ALWAYS go the same direction.
            _MAX_S = 2.5  # meters
            _s_avg = min(avg_dist, _MAX_S)
            _s_min = min(min_dist, _MAX_S)
            # Explicit angle penalty: penalise large turns so robot prefers
            # corridors close to its current heading over equidistant far ones.
            angle_penalty = abs(rel_deg) / 180.0
            open_score = (_s_avg * 0.5 + _s_min * 0.2 + forward_bias * 1.0)
            score = (open_score - 0.4 * angle_penalty) if passable else 0.0
            
            sectors.append({
                "sector": s,
                "angle_deg": round(rel_deg, 1),
                "avg_dist_m": round(avg_dist, 2) if avg_dist != float('inf') else None,
                "min_dist_m": round(min_dist, 2) if min_dist != float('inf') else None,
                "passable": passable,
                "score": round(score, 3),
            })
        
        # Sort by score descending
        open_dirs = sorted([s for s in sectors if s["passable"]], key=lambda x: x["score"], reverse=True)
        
        if open_dirs:
            best = open_dirs[0]
            return {
                "best_angle_deg": best["angle_deg"],
                "best_distance": best["avg_dist_m"],
                "passable": True,
                "all_sectors": sectors,
                "open_directions": open_dirs[:5],  # top 5 candidates
            }
        else:
            # No passable direction found — stuck
            return {
                "best_angle_deg": 0.0,
                "best_distance": 0.0,
                "passable": False,
                "all_sectors": sectors,
                "open_directions": [],
            }
    
    def can_robot_fit(self, direction_deg: float = 0.0) -> Tuple[bool, float]:
        """
        Quick check: can the robot physically fit if it moves in the given direction?
        
        Returns (passable, min_clearance_m).
        """
        # Find the sector closest to the requested direction
        norm_deg = direction_deg % 360
        sector_idx = int(norm_deg / SECTOR_DEG) % NUM_SECTORS
        
        front_dist = self._sector_min_distances[sector_idx]
        left_sector = (sector_idx + NUM_SECTORS // 4) % NUM_SECTORS
        right_sector = (sector_idx - NUM_SECTORS // 4) % NUM_SECTORS
        
        side_clearance = min(
            self._sector_min_distances[left_sector],
            self._sector_min_distances[right_sector],
        )
        
        passable = (
            front_dist > ROBOT_LENGTH_M and
            side_clearance > ROBOT_HALF_WIDTH_M
        )
        
        min_clearance = min(front_dist, side_clearance)
        return passable, round(min_clearance, 3)
    
    def get_forward_clearance(self) -> float:
        """
        Forward clearance using nearest non-dead-zone rays.
        
        YDLidar G2 has ~30° dead zone at front (±15° where motor is).
        Sector 0 (+15°) is DEAD, so can_robot_fit(0.0) returns side clearance
        instead of front clearance — causing false "blocked" readings.
        
        This method checks individual rays from ±16° to ±60° as the best
        available proxy for what's ahead of the robot.
        Returns minimum distance, or float('inf') if no valid readings.
        """
        if not self._last_scan_ranges:
            return float('inf')
        
        min_dist = float('inf')
        check_lo = math.radians(16)   # just past dead zone edge
        check_hi = math.radians(60)   # up to 60° — cos-projection removed so 60° is safe (was 40° as workaround)

        for i, r in enumerate(self._last_scan_ranges):
            if not (self._last_range_min <= r <= self._last_range_max):
                continue
            raw_angle = self._last_angle_min + i * self._last_angle_increment
            angle = raw_angle + LIDAR_ANGLE_OFFSET_RAD
            if LIDAR_MIRROR:
                angle = -angle
            while angle > math.pi:
                angle -= 2 * math.pi
            while angle < -math.pi:
                angle += 2 * math.pi

            abs_angle = abs(angle)
            if check_lo <= abs_angle <= check_hi:
                min_dist = min(min_dist, r)

        return min_dist

    def get_rear_obstacle_checker(self, threshold_m: float = 0.15):
        """Return callable that checks rear+side sectors for obstacles.
        Clamped to ±SCAN_HALF_ARC_DEG (±115°) — the rear blind spot has no rays
        so checking beyond that would always return False and skip real obstacles
        right at the arc edge.
        Used during backward motion and rotation to prevent hitting rear walls."""
        def _check():
            if not self._last_scan_ranges:
                return False
            check_lo = math.radians(90)
            # Stay within valid scan arc — blind spot starts at ±SCAN_HALF_ARC_DEG
            check_hi = math.radians(min(165, SCAN_HALF_ARC_DEG - 5))  # ~110°
            for i, r in enumerate(self._last_scan_ranges):
                if not (self._last_range_min <= r <= self._last_range_max):
                    continue
                raw_angle = self._last_angle_min + i * self._last_angle_increment
                angle = raw_angle + LIDAR_ANGLE_OFFSET_RAD
                if LIDAR_MIRROR:
                    angle = -angle
                while angle > math.pi:
                    angle -= 2 * math.pi
                while angle < -math.pi:
                    angle += 2 * math.pi
                if check_lo <= abs(angle) <= check_hi and r < threshold_m:
                    return True
            return False
        return _check
    
    def get_side_clearance(self) -> Tuple[float, float]:
        """
        Side clearance (left, right) using rays at ±60° to ±120°.
        
        Returns (left_min, right_min) in meters.
        Used during approach phase to detect walls beside the robot.
        Left = positive angles, Right = negative angles (robot frame).
        """
        if not self._last_scan_ranges:
            return float('inf'), float('inf')
        
        left_min = float('inf')
        right_min = float('inf')
        side_lo = math.radians(45)
        # Cap to valid scan arc — rays beyond ±SCAN_HALF_ARC_DEG don't exist
        side_hi = math.radians(min(120, SCAN_HALF_ARC_DEG - 5))  # ~110°
        
        for i, r in enumerate(self._last_scan_ranges):
            if not (self._last_range_min <= r <= self._last_range_max):
                continue
            raw_angle = self._last_angle_min + i * self._last_angle_increment
            angle = raw_angle + LIDAR_ANGLE_OFFSET_RAD
            if LIDAR_MIRROR:
                angle = -angle
            while angle > math.pi:
                angle -= 2 * math.pi
            while angle < -math.pi:
                angle += 2 * math.pi
            
            if side_lo <= angle <= side_hi:
                left_min = min(left_min, r)
            elif -side_hi <= angle <= -side_lo:
                right_min = min(right_min, r)
        
        return left_min, right_min

    def _directional_clearance(
        self, center_deg: float, window_deg: float = 7.5
    ) -> Tuple[float, float, float]:
        """
        Compute (forward, left_side, right_side) clearance for a given direction.

        Uses raw LiDAR rays at sub-sector resolution.
        center_deg is relative to robot front (+ = left, - = right).
        Returns float('inf') for each axis if no rays fall in that window.
        """
        if not self._last_scan_ranges:
            return float('inf'), float('inf'), float('inf')

        # Outside physical scan arc — no rays exist, treat as dead zone
        if abs(center_deg) > SCAN_HALF_ARC_DEG:
            return float('inf'), float('inf'), float('inf')

        center_rad = math.radians(center_deg)
        window_rad = math.radians(window_deg)
        perp_left_rad  = center_rad + math.pi / 2
        perp_right_rad = center_rad - math.pi / 2

        fwd_min   = float('inf')
        left_min  = float('inf')
        right_min = float('inf')
        fwd_count = 0

        for i, r in enumerate(self._last_scan_ranges):
            if not (self._last_range_min <= r <= self._last_range_max):
                continue

            raw_angle = self._last_angle_min + i * self._last_angle_increment
            angle = raw_angle + LIDAR_ANGLE_OFFSET_RAD
            if LIDAR_MIRROR:
                angle = -angle
            while angle > math.pi:
                angle -= 2 * math.pi
            while angle < -math.pi:
                angle += 2 * math.pi

            def _angular_diff(a: float, b: float) -> float:
                d = abs(a - b)
                return d if d <= math.pi else 2 * math.pi - d

            if _angular_diff(angle, center_rad) <= window_rad:
                fwd_min = min(fwd_min, r)
                fwd_count += 1
            if _angular_diff(angle, perp_left_rad) <= window_rad:
                left_min = min(left_min, r)
            if _angular_diff(angle, perp_right_rad) <= window_rad:
                right_min = min(right_min, r)

        if fwd_count == 0:
            fwd_min = float('inf')   # dead zone — no data

        return fwd_min, left_min, right_min

    def refine_direction(
        self,
        coarse_deg: float,
        search_half_deg: float = 20.0,
        step_deg: float = 5.0,
    ) -> float:
        """
        Sub-sector refinement: scan ±search_half_deg around coarse_deg at step_deg
        resolution using raw LiDAR rays instead of aggregated sectors.

        Scoring per candidate:
            score = clearance * 0.6 + side_width * 0.2 - 0.4 * angle_penalty

        Returns the refined angle (degrees, + = left, - = right).
        Logs the adjustment when the direction changes.
        """
        if not self._last_scan_ranges:
            return coarse_deg

        MIN_FWD = ROBOT_LENGTH_M / 2 + ROBOT_CLEARANCE_M  # must clear front
        _MAX_S = 2.5
        WINDOW_DEG = step_deg / 2  # ray-window = half a step (~2.5°)

        best_angle = coarse_deg
        best_score = -float('inf')

        cand = coarse_deg - search_half_deg
        while cand <= coarse_deg + search_half_deg + 0.01:
            # Skip candidates outside the physical scan arc
            if abs(cand) > SCAN_HALF_ARC_DEG - WINDOW_DEG:
                cand += step_deg
                continue

            fwd, left, right = self._directional_clearance(cand, WINDOW_DEG)

            if fwd == float('inf'):
                # Dead zone — skip, don't treat as open space
                cand += step_deg
                continue

            if fwd < MIN_FWD:
                cand += step_deg
                continue

            side_score = min(left, right, _MAX_S)
            angle_penalty = abs(cand) / 180.0
            score = min(fwd, _MAX_S) * 0.6 + side_score * 0.2 - 0.4 * angle_penalty

            if score > best_score:
                best_score = score
                best_angle = cand

            cand += step_deg

        best_angle = round(best_angle, 1)
        if abs(best_angle - coarse_deg) >= step_deg / 2:
            logger.info(
                f"🔍 Refined angle: {coarse_deg:+.0f}° → {best_angle:+.0f}° "
                f"(score={best_score:.3f})"
            )

        return best_angle

    def get_sector_summary(self) -> str:
        """Human-readable summary of 360° LiDAR for logging."""
        lines = []
        for s in range(NUM_SECTORS):
            center_deg = s * SECTOR_DEG + SECTOR_DEG / 2
            rel_deg = center_deg if center_deg <= 180 else center_deg - 360
            d = self._sector_min_distances[s]
            avg_d = self._sector_distances[s]
            is_dead = (avg_d == float('inf') and d == float('inf'))
            if is_dead:
                d_str = "DEAD"
                passable = "⛔"
            else:
                d_str = f"{d:.2f}m" if d != float('inf') else "∞"
                passable = "✅" if d > ROBOT_MIN_PASSAGE_M else "❌"
            lines.append(f"  {rel_deg:+6.0f}°: {d_str:>6s} {passable}")
        return "\n".join(lines)
    
    async def check_and_avoid(self) -> Optional[Dict[str, Any]]:
        """
        Check for obstacles and return avoidance strategy.
        
        Called by gateway main loop during active navigation.
        
        Returns None if path is clear, or:
        {
            "obstacle_detected": True,
            "distance": 0.45,
            "strategy": "go_around_left",
            "vlm_used": True,
            "obstacle_type": "chair",
            "reason": "..."
        }
        """
        if not self._obstacle_detected:
            return None
        
        dist = self._min_front_distance
        logger.warning(f"🚧 Obstacle detected! Distance: {dist:.2f}m")
        
        result = {
            "obstacle_detected": True,
            "distance": round(dist, 2),
            "strategy": "stop",  # Safe default
            "vlm_used": False,
        }
        
        # Layer 1: Emergency stop if too close
        if dist < OBSTACLE_STOP_M:
            logger.critical(f"🛑 EMERGENCY STOP — obstacle at {dist:.2f}m (< {OBSTACLE_STOP_M}m)")
            await self.motion.stop()
            result["strategy"] = "stop"
            result["reason"] = f"Too close ({dist:.2f}m)"
            return result
        
        # Layer 2: Try VLM analysis (if server reachable)
        vlm_result = await self._ask_vlm_strategy()
        
        if vlm_result and vlm_result.get("strategy"):
            result["strategy"] = vlm_result["strategy"]
            result["vlm_used"] = True
            result["obstacle_type"] = vlm_result.get("obstacle_type", "unknown")
            result["reason"] = vlm_result.get("reason", "")
            logger.info(f"🧠 VLM strategy: {result['strategy']} ({result['obstacle_type']})")
        else:
            # Layer 2 fallback: Use LiDAR-only heuristic
            result["strategy"] = self._lidar_heuristic()
            result["reason"] = "LiDAR heuristic (VLM unavailable)"
            logger.info(f"📊 LiDAR heuristic: {result['strategy']}")
        
        return result
    
    async def execute_avoidance(self, strategy: str) -> bool:
        """
        Execute the avoidance strategy.
        
        Returns True if avoidance was executed successfully.
        """
        if self._avoiding:
            logger.warning("⚠️ Already avoiding, skipping")
            return False
        
        self._avoiding = True
        
        try:
            if strategy == "stop":
                await self.motion.stop()
                logger.info("🛑 Stopped")
                
            elif strategy == "go_around_left":
                logger.info("⬅️ Avoiding left...")
                # Turn left 45°, move forward, turn right 45° to resume heading
                await self.motion.exec_motion({
                    "type": "move", "linear_x": 0.0,
                    "angular_z": AVOIDANCE_ANGULAR,
                    "duration": 0.8,  # ~20° turn
                })
                await asyncio.sleep(0.2)
                await self.motion.exec_motion({
                    "type": "move", "linear_x": AVOIDANCE_SPEED,
                    "angular_z": 0.0,
                    "duration": 2.0,  # Move forward past obstacle
                }, obstacle_checker=self.get_obstacle_checker())
                await asyncio.sleep(0.2)
                await self.motion.exec_motion({
                    "type": "move", "linear_x": 0.0,
                    "angular_z": -AVOIDANCE_ANGULAR,
                    "duration": 0.8,  # Turn back right
                })
                logger.info("✅ Left avoidance complete")
                
            elif strategy == "go_around_right":
                logger.info("➡️ Avoiding right...")
                await self.motion.exec_motion({
                    "type": "move", "linear_x": 0.0,
                    "angular_z": -AVOIDANCE_ANGULAR,
                    "duration": 0.8,
                })
                await asyncio.sleep(0.2)
                await self.motion.exec_motion({
                    "type": "move", "linear_x": AVOIDANCE_SPEED,
                    "angular_z": 0.0,
                    "duration": 2.0,
                }, obstacle_checker=self.get_obstacle_checker())
                await asyncio.sleep(0.2)
                await self.motion.exec_motion({
                    "type": "move", "linear_x": 0.0,
                    "angular_z": AVOIDANCE_ANGULAR,
                    "duration": 0.8,
                })
                logger.info("✅ Right avoidance complete")
                
            elif strategy == "wait":
                logger.info("⏸️ Waiting for obstacle to clear...")
                await self.motion.stop()
                # Wait up to 10 seconds
                for _ in range(20):
                    await asyncio.sleep(0.5)
                    if not self._obstacle_detected:
                        logger.info("✅ Path cleared, resuming")
                        break
                else:
                    logger.warning("⚠️ Obstacle still present after 10s")
                    
            elif strategy == "reroute":
                logger.info("🗺️ Rerouting — backing up and trying alternative path")
                # Back up
                await self.motion.exec_motion({
                    "type": "move", "linear_x": -AVOIDANCE_SPEED,
                    "angular_z": 0.0,
                    "duration": 1.5,
                })
                await asyncio.sleep(0.2)
                # Turn 90° and proceed
                await self.motion.exec_motion({
                    "type": "move", "linear_x": 0.0,
                    "angular_z": AVOIDANCE_ANGULAR,
                    "duration": 2.0,  # ~45°
                })
                logger.info("✅ Reroute initiated")
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Avoidance execution error: {e}")
            # Safety: always try to stop on error
            try:
                await self.motion.stop()
            except:
                pass
            return False
        finally:
            self._avoiding = False
    
    def _lidar_heuristic(self) -> str:
        """
        Simple LiDAR-only heuristic when VLM is unavailable.
        
        Checks which side has more clearance and navigates that way.
        """
        if not self._last_scan_ranges:
            return "stop"
        
        n = len(self._last_scan_ranges)
        if n < 10:
            return "stop"
        
        # Split into left half and right half
        mid = n // 2
        quarter = n // 4
        
        # Left side: ranges around 45-90° left
        left_ranges = [
            r for r in self._last_scan_ranges[mid:mid+quarter]
            if 0.05 < r < 12.0
        ]
        
        # Right side: ranges around 45-90° right
        right_ranges = [
            r for r in self._last_scan_ranges[mid-quarter:mid]
            if 0.05 < r < 12.0
        ]
        
        left_avg = sum(left_ranges) / len(left_ranges) if left_ranges else 0
        right_avg = sum(right_ranges) / len(right_ranges) if right_ranges else 0
        
        logger.debug(f"LiDAR heuristic: left_avg={left_avg:.2f}m, right_avg={right_avg:.2f}m")
        
        if left_avg > right_avg and left_avg > 0.5:
            return "go_around_left"
        elif right_avg > 0.5:
            return "go_around_right"
        else:
            return "stop"
    
    async def _ask_vlm_strategy(self) -> Optional[Dict[str, Any]]:
        """
        Ask the VLM server for obstacle avoidance strategy.
        
        NOTE: In the current setup, we don't have a live camera feed.
        This function will be activated when robot camera integration is ready.
        For now, it returns None (falling back to LiDAR heuristic).
        
        When camera is available:
        1. Capture frame from robot camera
        2. Upload to server
        3. Call /vlm/obstacle endpoint
        4. Return strategy
        """
        # TODO: Implement camera capture when MyAGV camera is integrated
        # For now, return None to use LiDAR heuristic
        #
        # Future implementation:
        # 1. Capture image from ROS topic /camera/image_raw
        # 2. Save to temp file
        # 3. Upload to server via /vlm/upload
        # 4. Call /vlm/obstacle with uploaded image
        
        logger.debug("VLM strategy: camera not yet integrated, using LiDAR heuristic")
        return None
    
    def get_status(self) -> Dict[str, Any]:
        """Get current obstacle avoidance status."""
        return {
            "enabled": self._enabled,
            "obstacle_detected": self._obstacle_detected,
            "min_front_distance_m": round(self._min_front_distance, 2) if self._min_front_distance != float('inf') else None,
            "current_goal": self._current_goal,
            "avoiding": self._avoiding,
            "config": {
                "warn_distance_m": OBSTACLE_WARN_M,
                "stop_distance_m": OBSTACLE_STOP_M,
                "front_angle_deg": FRONT_ANGLE_DEG,
            }
        }


# ---------------------------------------------------------------------------
# Convenience: test obstacle avoidance with a static image against server VLM
# ---------------------------------------------------------------------------

async def test_obstacle_with_image(image_filename: str, goal: str = "") -> Dict[str, Any]:
    """
    Test obstacle analysis using server VLM (for demo/testing).
    
    Uses test images in the Images/ folder on the server.
    """
    try:
        async with httpx.AsyncClient(timeout=60.0, verify=False) as client:
            resp = await client.post(
                f"{SERVER_BASE}/vlm/obstacle",
                json={"image": image_filename, "current_goal": goal}
            )
            if resp.status_code == 200:
                return resp.json()
            else:
                return {"error": f"HTTP {resp.status_code}", "detail": resp.text}
    except Exception as e:
        return {"error": str(e)}
