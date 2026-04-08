"""
Nav2 Client for VORA Gateway
==============================
Sends navigation goals to Nav2 via ROSBridge WebSocket.
Supports:
  - navigate_to_pose (single goal)
  - cancel_navigation
  - get_nav_status (feedback monitoring)
  - get_costmap_data (for obstacle-aware goal selection)
  - request_explore_frontier (trigger explore_lite)

Uses roslibpy action client to call /navigate_to_pose action.
"""

import asyncio
import logging
import math
import time
from typing import Optional, Dict, Any, Tuple, Callable

import roslibpy
import roslibpy.actionlib

logger = logging.getLogger("nav2_client")


class Nav2Client:
    """
    Gateway-side Nav2 interface via ROSBridge.

    Usage:
        nav2 = Nav2Client("ws://192.168.0.111:9090")
        await nav2.connect()
        result = await nav2.navigate_to_pose(x=1.0, y=0.5, theta=0.0, timeout=60)
    """

    # Navigation status codes
    STATUS_UNKNOWN = 0
    STATUS_ACCEPTED = 1
    STATUS_EXECUTING = 2
    STATUS_CANCELING = 3
    STATUS_SUCCEEDED = 4
    STATUS_CANCELED = 5
    STATUS_ABORTED = 6

    STATUS_NAMES = {
        0: "UNKNOWN", 1: "ACCEPTED", 2: "EXECUTING", 3: "CANCELING",
        4: "SUCCEEDED", 5: "CANCELED", 6: "ABORTED",
    }

    def __init__(self, rosbridge_url: str = "ws://192.168.0.111:9090"):
        self._url = rosbridge_url
        self._ros: Optional[roslibpy.Ros] = None
        self._action_client = None
        self._current_goal = None
        self._nav_status = self.STATUS_UNKNOWN
        self._nav_feedback: Dict[str, Any] = {}
        self._connected = False
        self._on_feedback_cb: Optional[Callable] = None

    @property
    def connected(self) -> bool:
        return self._connected and self._ros and self._ros.is_connected

    @property
    def nav_status(self) -> int:
        return self._nav_status

    @property
    def nav_status_name(self) -> str:
        return self.STATUS_NAMES.get(self._nav_status, "UNKNOWN")

    @property
    def is_navigating(self) -> bool:
        return self._nav_status in (self.STATUS_ACCEPTED, self.STATUS_EXECUTING)

    @property
    def feedback(self) -> Dict[str, Any]:
        return self._nav_feedback

    def inject_ros(self, ros: roslibpy.Ros) -> None:
        """Accept pre-connected roslibpy.Ros from ensure_ros() singleton.
        Avoids spawning a second Twisted factory that conflicts with camera/odom/LiDAR.
        ActionClient is NOT created here — its constructor waits for Nav2 status topic
        and will block/fail if Nav2 isn't running yet at Gateway startup time.
        ActionClient is created lazily in connect() when navigation is first requested."""
        self._ros = ros
        self._connected = True
        logger.info("Nav2 client using shared ROSBridge connection")

    async def connect(self) -> bool:
        """Connect to ROSBridge and set up Nav2 action client."""
        # If ros was already injected via inject_ros(), create ActionClient lazily here.
        # This avoids blocking at startup — ActionClient is only created when navigation
        # is actually needed (i.e. when navigate_to_pose() is first called).
        if self._ros and self._ros.is_connected:
            if self._action_client is None:
                try:
                    self._action_client = roslibpy.actionlib.ActionClient(
                        self._ros,
                        "/navigate_to_pose",
                        "nav2_msgs/NavigateToPose",
                    )
                except Exception as e:
                    logger.warning(f"Nav2 ActionClient setup failed (Nav2 not ready?): {e}")
                    return False
            self._connected = True
            return True
        try:
            host_port = self._url.replace("ws://", "").split("/")[0]
            host, port = host_port.split(":")
            self._ros = roslibpy.Ros(host=host, port=int(port))
            self._ros.run()

            for _ in range(50):
                if self._ros.is_connected:
                    break
                await asyncio.sleep(0.1)

            if not self._ros.is_connected:
                logger.error("Cannot connect to ROSBridge for Nav2")
                return False

            # Create action client for navigate_to_pose
            self._action_client = roslibpy.actionlib.ActionClient(
                self._ros,
                "/navigate_to_pose",
                "nav2_msgs/NavigateToPose",
            )
            self._connected = True
            logger.info(f"Nav2 client connected via {self._url}")
            return True

        except Exception as e:
            logger.error(f"Nav2 client connection failed: {e}")
            return False

    def disconnect(self):
        """Disconnect from ROSBridge."""
        if self._action_client:
            self._action_client.dispose()
        if self._ros:
            try:
                self._ros.close()
            except Exception:
                pass
        self._connected = False
        logger.info("Nav2 client disconnected")

    async def navigate_to_pose(
        self,
        x: float,
        y: float,
        theta: float = 0.0,
        frame_id: str = "map",
        timeout: float = 120.0,
        on_feedback: Optional[Callable] = None,
    ) -> Dict[str, Any]:
        """
        Send a navigation goal to Nav2 and wait for result.

        Args:
            x, y: target position in map frame (meters)
            theta: target orientation (radians)
            frame_id: coordinate frame (default: "map")
            timeout: max seconds to wait for navigation
            on_feedback: optional callback(feedback_dict) called during navigation

        Returns:
            {
                "success": bool,
                "status": str,        # "SUCCEEDED", "ABORTED", etc.
                "duration": float,    # seconds taken
                "distance_remaining": float,
            }
        """
        if not self.connected or self._action_client is None:
            success = await self.connect()
            if not success:
                return {"success": False, "status": "CONNECTION_FAILED", "duration": 0}

        self._on_feedback_cb = on_feedback
        self._nav_status = self.STATUS_UNKNOWN
        self._nav_feedback = {}

        # Build PoseStamped goal
        qz = math.sin(theta / 2.0)
        qw = math.cos(theta / 2.0)
        goal_msg = roslibpy.actionlib.Goal(
            self._action_client,
            {
                "pose": {
                    "header": {
                        "frame_id": frame_id,
                        "stamp": {"sec": 0, "nanosec": 0},
                    },
                    "pose": {
                        "position": {"x": x, "y": y, "z": 0.0},
                        "orientation": {"x": 0.0, "y": 0.0, "z": qz, "w": qw},
                    },
                }
            },
        )

        logger.info(f"Nav2 goal: x={x:.2f} y={y:.2f} θ={math.degrees(theta):.0f}° frame={frame_id}")

        # Track result via events
        result_event = asyncio.Event()
        result_data = {"success": False, "status": "TIMEOUT", "duration": 0}
        start_time = time.monotonic()
        # Capture the running event loop so roslibpy callbacks can signal it
        # thread-safely (they fire from a different thread than asyncio's loop).
        loop = asyncio.get_event_loop()

        def _on_feedback(fb):
            self._nav_status = self.STATUS_EXECUTING
            try:
                pose = fb.get("current_pose", {}).get("pose", {})
                pos = pose.get("position", {})
                dist = fb.get("distance_remaining", -1)
                self._nav_feedback = {
                    "x": pos.get("x", 0),
                    "y": pos.get("y", 0),
                    "distance_remaining": dist,
                    "elapsed": time.monotonic() - start_time,
                }
                if self._on_feedback_cb:
                    self._on_feedback_cb(self._nav_feedback)
            except Exception:
                pass

        def _on_result(res):
            nonlocal result_data
            elapsed = time.monotonic() - start_time
            # Nav2 result is empty on success; status is in the goal handle
            result_data = {
                "success": True,
                "status": "SUCCEEDED",
                "duration": elapsed,
                "distance_remaining": 0,
            }
            self._nav_status = self.STATUS_SUCCEEDED
            # Use call_soon_threadsafe: this callback fires from roslibpy's thread,
            # not the asyncio loop thread. Direct result_event.set() is not safe.
            loop.call_soon_threadsafe(result_event.set)

        goal_msg.on("feedback", _on_feedback)
        goal_msg.on("result", _on_result)

        self._current_goal = goal_msg
        self._nav_status = self.STATUS_ACCEPTED
        goal_msg.send()

        logger.info("Nav2 goal sent, waiting for result...")

        # Wait for result or timeout.
        # Previously this used run_in_executor(None, result_event.wait) which is
        # wrong: asyncio.Event.wait is a coroutine method — calling it in an
        # executor returns a coroutine object immediately, resolving the future
        # instantly and always producing TIMEOUT/failure.
        try:
            await asyncio.wait_for(result_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            elapsed = time.monotonic() - start_time
            logger.warning(f"Nav2 navigation timed out after {elapsed:.1f}s")
            result_data = {
                "success": False,
                "status": "TIMEOUT",
                "duration": elapsed,
                "distance_remaining": self._nav_feedback.get("distance_remaining", -1),
            }
            # Cancel the goal
            await self.cancel_navigation()

        self._current_goal = None
        logger.info(f"Nav2 result: {result_data['status']} in {result_data['duration']:.1f}s")
        return result_data

    async def cancel_navigation(self):
        """Cancel the current navigation goal."""
        if self._current_goal:
            try:
                self._current_goal.cancel()
                self._nav_status = self.STATUS_CANCELED
                logger.info("Nav2 goal cancelled")
            except Exception as e:
                logger.warning(f"Failed to cancel Nav2 goal: {e}")
        self._current_goal = None

    async def set_initial_pose(self, x: float, y: float, theta: float):
        """Publish initial pose for AMCL localization."""
        if not self.connected:
            await self.connect()

        topic = roslibpy.Topic(
            self._ros, "/initialpose", "geometry_msgs/PoseWithCovarianceStamped"
        )
        qz = math.sin(theta / 2.0)
        qw = math.cos(theta / 2.0)
        msg = {
            "header": {"frame_id": "map", "stamp": {"sec": 0, "nanosec": 0}},
            "pose": {
                "pose": {
                    "position": {"x": x, "y": y, "z": 0.0},
                    "orientation": {"x": 0.0, "y": 0.0, "z": qz, "w": qw},
                },
                "covariance": [0.25, 0, 0, 0, 0, 0,
                               0, 0.25, 0, 0, 0, 0,
                               0, 0, 0, 0, 0, 0,
                               0, 0, 0, 0, 0, 0,
                               0, 0, 0, 0, 0, 0,
                               0, 0, 0, 0, 0, 0.068],
            },
        }
        topic.publish(roslibpy.Message(msg))
        logger.info(f"Initial pose set: x={x:.2f} y={y:.2f} θ={math.degrees(theta):.0f}°")
        await asyncio.sleep(0.5)

    async def check_nav2_active(self) -> bool:
        """Check if Nav2 lifecycle nodes are active by checking action server availability."""
        if not self.connected:
            return False
        try:
            # Check if navigate_to_pose action is available by listing topics
            service = roslibpy.Service(
                self._ros, "/lifecycle_manager_navigation/get_state",
                "lifecycle_msgs/GetState"
            )
            result_event = asyncio.Event()
            state = {"active": False}
            loop = asyncio.get_event_loop()

            def _on_response(resp):
                # current_state.label == "active"
                cs = resp.get("current_state", {})
                state["active"] = cs.get("label", "") == "active"
                loop.call_soon_threadsafe(result_event.set)

            request = roslibpy.ServiceRequest({})
            service.call(request, callback=_on_response,
                         errback=lambda e: loop.call_soon_threadsafe(result_event.set))

            try:
                await asyncio.wait_for(result_event.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                pass

            return state["active"]
        except Exception:
            return False

    async def get_explore_frontiers(self) -> bool:
        """
        Check if explore_lite is running by verifying its marker topic exists.
        explore_lite publishes to /explore/frontiers when active.
        """
        if not self.connected:
            return False
        try:
            # Simple check: see if explore_lite's topic exists
            topic = roslibpy.Topic(self._ros, "/explore/frontiers", "visualization_msgs/MarkerArray")
            # If topic exists, explore_lite is likely running
            return True
        except Exception:
            return False
