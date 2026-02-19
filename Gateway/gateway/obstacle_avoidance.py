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
from typing import Dict, Any, Optional, List
import httpx

logger = logging.getLogger("obstacle")

# Configuration
FRONT_ANGLE_DEG = 60       # ±30° from front = 60° cone
OBSTACLE_WARN_M = 0.8      # Warning distance (slow down)
OBSTACLE_STOP_M = 0.3      # Emergency stop distance
SCAN_TOPIC = "/scan"        # ROS LaserScan topic
AVOIDANCE_SPEED = 0.10      # Slow avoidance speed (m/s)
AVOIDANCE_ANGULAR = 0.40    # Avoidance turn speed (rad/s)

# Server VLM endpoint
SERVER_BASE = os.getenv("SERVER_BASE", "https://user.tail87d9fe.ts.net")


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
        self._avoiding = False
        
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
        
        LaserScan fields:
        - angle_min, angle_max, angle_increment (radians)
        - ranges: list of distances (meters)
        - range_min, range_max: valid range limits
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
        
        self._last_scan_ranges = ranges
        
        # Extract front-zone readings (±30° from forward)
        front_half_rad = math.radians(FRONT_ANGLE_DEG / 2)
        front_distances = []
        
        for i, r in enumerate(ranges):
            angle = angle_min + i * angle_increment
            # Front zone: angle near 0 (or near ±π for rear-mounted LiDAR)
            if -front_half_rad <= angle <= front_half_rad:
                if range_min <= r <= range_max:
                    front_distances.append(r)
        
        if not front_distances:
            self._obstacle_detected = False
            self._min_front_distance = float('inf')
            return
        
        self._min_front_distance = min(front_distances)
        self._obstacle_detected = self._min_front_distance < OBSTACLE_WARN_M
    
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
                })
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
                })
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
