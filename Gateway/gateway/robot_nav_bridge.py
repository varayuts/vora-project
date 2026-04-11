"""
robot_nav_bridge.py — Gateway → Robot navigation bridge.
========================================================
Replaces the legacy Gateway-side roslibpy Nav2 ActionClient
(see nav2_client.py), which never worked reliably because:

  • roslibpy's ActionClient expects ROS1-style action topic names,
    while the MyAGV Jetson Nano runs ROS2 Galactic where the action
    topics live under /navigate_to_pose/_action/* — the roslibpy
    ActionClient handshake silently fails with
    "Action client failed to connect, no status received".
  • Even when it connects, action feedback plumbing is fragile
    across the rosbridge_suite / roslibpy version gap.

The authoritative navigation path is now:

    Gateway ── publish /vora/command (std_msgs/String JSON) ──►
        MyAGV command_executor.py ── native rclpy ActionClient ──►
            Nav2 /navigate_to_pose

The robot-side executor publishes a final outcome to /vora/result
keyed by ``query_id`` — we subscribe once and await the matching id.

This class keeps the exact surface used by main.py:
    inject_ros(), connect(), connected, navigate_to_pose(),
    navigate_to_zone(), cancel_navigation(), set_initial_pose(),
    is_navigating, nav_status_name, feedback
so no call sites need to change beyond the import.
"""

import asyncio
import json
import logging
import math
import time
import uuid
from typing import Any, Callable, Dict, Optional

import roslibpy

logger = logging.getLogger("robot_nav_bridge")


class RobotNavBridge:
    """Publishes navigation intents to the robot-side Command Executor.

    All heavy lifting (Nav2 action call, feedback, outcome mapping) runs on
    the robot. Gateway only dispatches intents and awaits results — no
    ActionClient handshake happens here, so the old
    "ActionClient failed to connect" path is physically impossible.
    """

    COMMAND_TOPIC = "/vora/command"
    RESULT_TOPIC = "/vora/result"
    STATUS_TOPIC = "/vora/status"

    def __init__(self, rosbridge_url: str = "ws://192.168.0.111:9090"):
        self._url = rosbridge_url
        self._ros: Optional[roslibpy.Ros] = None
        self._cmd_topic: Optional[roslibpy.Topic] = None
        self._result_topic: Optional[roslibpy.Topic] = None
        self._status_topic: Optional[roslibpy.Topic] = None
        self._initial_pose_topic: Optional[roslibpy.Topic] = None

        # In-flight goals: query_id → (asyncio.Event, result_box, loop)
        self._pending: Dict[str, Dict[str, Any]] = {}
        self._last_status: str = "IDLE"
        self._last_feedback: Dict[str, Any] = {}
        self._navigating: bool = False
        self._current_qid: Optional[str] = None

    # ── surface compat ────────────────────────────────────────────────
    @property
    def connected(self) -> bool:
        return (
            self._ros is not None
            and self._ros.is_connected
            and self._cmd_topic is not None
            and self._result_topic is not None
        )

    @property
    def nav_status(self) -> str:
        return self._last_status

    @property
    def nav_status_name(self) -> str:
        return self._last_status

    @property
    def is_navigating(self) -> bool:
        return self._navigating

    @property
    def feedback(self) -> Dict[str, Any]:
        return self._last_feedback

    # ── wiring ────────────────────────────────────────────────────────
    def inject_ros(self, ros: roslibpy.Ros) -> None:
        """Attach shared ROSBridge connection + subscribe to /vora/result.

        Called once at startup from main.py's ensure_ros path. Idempotent:
        re-injection just re-subscribes. Never raises — logs and returns on
        failure so that a dead rosbridge can't cascade into Gateway startup.
        """
        self._ros = ros
        try:
            self._cmd_topic = roslibpy.Topic(ros, self.COMMAND_TOPIC, "std_msgs/String")
            self._result_topic = roslibpy.Topic(ros, self.RESULT_TOPIC, "std_msgs/String")
            self._status_topic = roslibpy.Topic(ros, self.STATUS_TOPIC, "std_msgs/String")
            self._result_topic.subscribe(self._on_result)
            self._status_topic.subscribe(self._on_status)
            logger.info(
                "[NAV] RobotNavBridge ready — command=%s result=%s",
                self.COMMAND_TOPIC, self.RESULT_TOPIC,
            )
        except Exception as e:
            logger.warning(f"[NAV] RobotNavBridge subscribe failed: {e}")

    async def connect(self) -> bool:
        """No-op preflight: returns True iff we already have a live rosbridge
        connection with /vora/command publishable. Does NOT open a second
        Twisted factory — main.py's ensure_ros() owns the one true ros."""
        if self.connected:
            return True
        # If ros was injected but subscription failed earlier, try again.
        if self._ros is not None and self._ros.is_connected:
            self.inject_ros(self._ros)
            return self.connected
        logger.warning("[NAV] preflight failed — rosbridge not connected")
        return False

    # ── callbacks ─────────────────────────────────────────────────────
    def _on_result(self, msg: Dict[str, Any]) -> None:
        try:
            payload = json.loads(msg.get("data", "{}"))
        except Exception:
            return
        qid = str(payload.get("query_id") or "")
        if not qid or qid not in self._pending:
            return
        entry = self._pending[qid]
        entry["result"] = payload
        loop: asyncio.AbstractEventLoop = entry["loop"]
        event: asyncio.Event = entry["event"]
        try:
            loop.call_soon_threadsafe(event.set)
        except Exception:
            pass

    def _on_status(self, msg: Dict[str, Any]) -> None:
        try:
            payload = json.loads(msg.get("data", "{}"))
        except Exception:
            return
        state = str(payload.get("state") or "").lower()
        self._last_status = state.upper() or self._last_status
        if state in ("accepted", "running"):
            self._navigating = True

    # ── publish helpers ──────────────────────────────────────────────
    def _publish_command(self, payload: Dict[str, Any]) -> bool:
        if not self.connected or self._cmd_topic is None:
            return False
        try:
            self._cmd_topic.publish(roslibpy.Message({"data": json.dumps(payload)}))
            return True
        except Exception as e:
            logger.warning(f"[NAV] publish to {self.COMMAND_TOPIC} failed: {e}")
            return False

    # ── public API used by main.py ───────────────────────────────────
    async def navigate_to_pose(
        self,
        x: float,
        y: float,
        theta: float = 0.0,
        frame_id: str = "map",
        timeout: float = 60.0,
        on_feedback: Optional[Callable] = None,
    ) -> Dict[str, Any]:
        """Send a navigate intent to the robot and await the matching result.

        Returns a dict with the same shape main.py already expects:
            {"success": bool, "status": str, "duration": float,
             "distance_remaining": float}
        """
        if not await self.connect():
            return {"success": False, "status": "NAV_UNAVAILABLE", "duration": 0.0,
                    "distance_remaining": -1}

        qid = uuid.uuid4().hex[:8]
        qz = math.sin(theta / 2.0)
        qw = math.cos(theta / 2.0)
        cmd = {
            "query_id": qid,
            "intent": "navigate",
            "target": "zone",
            "params": {
                "x": float(x), "y": float(y), "z": 0.0,
                "qx": 0.0, "qy": 0.0, "qz": float(qz), "qw": float(qw),
                "timeout_s": float(timeout),
                "frame_id": frame_id,
            },
        }

        loop = asyncio.get_event_loop()
        event = asyncio.Event()
        self._pending[qid] = {"event": event, "result": None, "loop": loop}
        self._current_qid = qid
        self._navigating = True
        self._last_status = "SENT"

        start = time.monotonic()
        if not self._publish_command(cmd):
            self._pending.pop(qid, None)
            self._navigating = False
            self._last_status = "PUBLISH_FAILED"
            return {"success": False, "status": "PUBLISH_FAILED", "duration": 0.0,
                    "distance_remaining": -1}

        logger.info(f"[NAV] → /vora/command navigate qid={qid} x={x:.2f} y={y:.2f}")

        try:
            # Slight grace over the robot-side timeout so we never cut off a
            # result that's already on the wire. Robot's default_timeout_s is
            # 120s; our per-call timeout dominates unless it's larger.
            await asyncio.wait_for(event.wait(), timeout=timeout + 5.0)
        except asyncio.TimeoutError:
            elapsed = time.monotonic() - start
            self._pending.pop(qid, None)
            self._navigating = False
            self._last_status = "TIMEOUT"
            logger.warning(f"[NAV] qid={qid} timed out after {elapsed:.1f}s")
            # Best-effort cancel on robot side
            await self.cancel_navigation()
            return {"success": False, "status": "TIMEOUT", "duration": elapsed,
                    "distance_remaining": -1}

        entry = self._pending.pop(qid, {})
        self._navigating = False
        elapsed = time.monotonic() - start
        res = entry.get("result") or {}
        outcome = str(res.get("result") or "").lower()
        ok = outcome == "success"
        status_map = {
            "success": "SUCCEEDED",
            "aborted": "ABORTED",
            "cancelled": "CANCELED",
            "timeout": "TIMEOUT",
            "error": "ERROR",
            "rejected": "REJECTED",
        }
        status = status_map.get(outcome, outcome.upper() or "UNKNOWN")
        self._last_status = status
        logger.info(f"[NAV] qid={qid} → {status} in {elapsed:.1f}s")
        return {"success": ok, "status": status, "duration": elapsed,
                "distance_remaining": 0.0 if ok else -1}

    async def navigate_to_zone(self, x: float, y: float, timeout: float = 60.0) -> bool:
        result = await self.navigate_to_pose(
            x=x, y=y, theta=0.0, frame_id="map", timeout=timeout,
        )
        ok = bool(result.get("success")) and result.get("status") == "SUCCEEDED"
        logger.info(
            f"[NAV] navigate_to_zone({x:.2f},{y:.2f}) → "
            f"{'OK' if ok else 'FAIL'} ({result.get('status')})"
        )
        return ok

    async def cancel_navigation(self) -> None:
        """Publish a stop intent so the robot aborts the active Nav2 goal."""
        if not self.connected:
            return
        try:
            self._publish_command({
                "query_id": uuid.uuid4().hex[:8],
                "intent": "stop",
                "target": "nav",
                "params": {},
            })
            self._last_status = "CANCELED"
            self._navigating = False
            logger.info("[NAV] sent stop intent to /vora/command")
        except Exception as e:
            logger.warning(f"[NAV] cancel publish failed: {e}")

    async def set_initial_pose(self, x: float, y: float, theta: float) -> None:
        """Publish /initialpose directly — AMCL still consumes it on the robot."""
        if not self.connected:
            return
        if self._initial_pose_topic is None:
            self._initial_pose_topic = roslibpy.Topic(
                self._ros, "/initialpose", "geometry_msgs/PoseWithCovarianceStamped",
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
        self._initial_pose_topic.publish(roslibpy.Message(msg))
        logger.info(f"[NAV] /initialpose published: x={x:.2f} y={y:.2f} θ={math.degrees(theta):.0f}°")
        await asyncio.sleep(0.3)

    def disconnect(self) -> None:
        try:
            if self._result_topic:
                self._result_topic.unsubscribe()
            if self._status_topic:
                self._status_topic.unsubscribe()
        except Exception:
            pass
