#!/usr/bin/env python3
"""
command_executor.py
===================
MyAGV ROS2 Node - Receives VORA commands and executes them

Subscribes: /vora/command (JSON commands from Gateway)
Publishes: /vora/status, /vora/result, /cmd_vel (safety stop)
"""

import json
import math
import random
import time
import threading
from typing import Any, Dict, Optional

import rclpy
from rclpy.node import Node
from rclpy.action import ActionClient as RclpyActionClient
from rclpy.executors import MultiThreadedExecutor
from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy
from std_msgs.msg import String
from geometry_msgs.msg import Twist, PoseStamped
from sensor_msgs.msg import LaserScan
from nav2_msgs.action import NavigateToPose


class CommandExecutor(Node):
    """
    MyAGV-side ROS2 Node for VORA Command Execution

    Responsibilities:
      - Receive high-level commands via /vora/command
      - Execute immediate actions (stop)
      - Publish status updates for long-running tasks
      - Provide safety emergency stop via /cmd_vel

    Note: This is a dispatcher node. For complex tasks like:
      - Navigation: Integrate with Nav2 action client
      - Object Finding: Integrate with vision + patrol pipeline
      - SLAM: Integrate with slam_toolbox lifecycle manager
    """

    def __init__(self):
        super().__init__('vora_command_executor')

        # Params (override via ROS2 params or environment in your launch)
        self.declare_parameter('command_topic', '/vora/command')
        self.declare_parameter('status_topic', '/vora/status')
        self.declare_parameter('result_topic', '/vora/result')
        self.declare_parameter('cmd_vel_topic', '/cmd_vel')

        self.declare_parameter('default_timeout_s', 120.0)

        self.command_topic = self.get_parameter('command_topic').get_parameter_value().string_value
        self.status_topic = self.get_parameter('status_topic').get_parameter_value().string_value
        self.result_topic = self.get_parameter('result_topic').get_parameter_value().string_value
        self.cmd_vel_topic = self.get_parameter('cmd_vel_topic').get_parameter_value().string_value
        self.default_timeout_s = self.get_parameter('default_timeout_s').get_parameter_value().double_value

        self.status_pub = self.create_publisher(String, self.status_topic, 10)
        self.result_pub = self.create_publisher(String, self.result_topic, 10)
        self.cmd_vel_pub = self.create_publisher(Twist, self.cmd_vel_topic, 10)

        self.sub = self.create_subscription(String, self.command_topic, self.on_command, 10)

        self.active_query_id: Optional[str] = None
        self.active_intent: Optional[str] = None
        self.active_started_at: float = 0.0
        self.active_timeout_s: float = self.default_timeout_s

        # Nav2 action client — created once, used lazily per navigate command.
        # Using the ROS2 native ActionClient avoids roslibpy's topic-name mismatch
        # (roslibpy expects /navigate_to_pose/status but ROS2 uses /_action/status).
        self._nav2_client = RclpyActionClient(self, NavigateToPose, 'navigate_to_pose')
        self._nav_thread: Optional[threading.Thread] = None
        self._active_nav_goal_handle = None  # set when Nav2 accepts goal; cleared on stop/done

        # Latest laser scan for escape nudge forward-clearance check
        # QoS fix: ydlidar_ros2_driver publishes /scan with BEST_EFFORT reliability.
        # Default depth=10 uses RELIABLE → incompatible QoS → no laser data received.
        _scan_qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            durability=DurabilityPolicy.VOLATILE,
            depth=10,
        )
        self._latest_scan: Optional[LaserScan] = None
        self._scan_sub = self.create_subscription(LaserScan, '/scan', self._scan_cb, _scan_qos)

        # watchdog: if an active command times out, publish a timeout result
        self.timer = self.create_timer(0.5, self.on_timer)

        self.get_logger().info("=" * 60)
        self.get_logger().info("🤖 VORA Command Executor - Ready")
        self.get_logger().info("=" * 60)
        self.get_logger().info(f"📥 Listening: {self.command_topic}")
        self.get_logger().info(f"📤 Status:    {self.status_topic}")
        self.get_logger().info(f"📤 Result:    {self.result_topic}")
        self.get_logger().info(f"🚨 Safety:    {self.cmd_vel_topic}")
        self.get_logger().info("=" * 60)

    def on_timer(self):
        if not self.active_query_id:
            return
        elapsed = time.time() - self.active_started_at
        if self.active_timeout_s > 0 and elapsed > self.active_timeout_s:
            qid = self.active_query_id
            intent = self.active_intent or "unknown"
            self.get_logger().warn(f"Command timed out: query_id={qid} intent={intent}")
            self.publish_result({
                "query_id": qid,
                "state": "done",
                "result": "timeout",
                "intent": intent,
                "message": "Command timed out on robot",
                "ts": time.time()
            })
            self.clear_active()

    def clear_active(self):
        self.active_query_id = None
        self.active_intent = None
        self.active_started_at = 0.0
        self.active_timeout_s = self.default_timeout_s
        self._active_nav_goal_handle = None

    def publish_status(self, payload: Dict[str, Any]):
        msg = String()
        msg.data = json.dumps(payload, ensure_ascii=False)
        self.status_pub.publish(msg)

    def publish_result(self, payload: Dict[str, Any]):
        msg = String()
        msg.data = json.dumps(payload, ensure_ascii=False)
        self.result_pub.publish(msg)

    def stop_robot(self):
        twist = Twist()
        twist.linear.x = 0.0
        twist.linear.y = 0.0
        twist.linear.z = 0.0
        twist.angular.x = 0.0
        twist.angular.y = 0.0
        twist.angular.z = 0.0
        self.cmd_vel_pub.publish(twist)

    def _scan_cb(self, msg: LaserScan):
        self._latest_scan = msg

    def _check_forward_clearance(self, min_dist: float = 0.25) -> bool:
        """Check if forward direction has at least min_dist clearance using latest /scan.
        Forward = rays around angle 0 (±15°). Returns True if clear, False if blocked."""
        scan = self._latest_scan
        if scan is None:
            self.get_logger().warn("[NAV] No scan data — assuming forward blocked")
            return False
        # Find indices covering ±15° around 0 (forward)
        half_cone = math.radians(15.0)
        forward_ranges = []
        for i, r in enumerate(scan.ranges):
            angle = scan.angle_min + i * scan.angle_increment
            # Normalize to [-pi, pi]
            while angle > math.pi:
                angle -= 2 * math.pi
            while angle < -math.pi:
                angle += 2 * math.pi
            if abs(angle) <= half_cone:
                if scan.range_min < r < scan.range_max:
                    forward_ranges.append(r)
        if not forward_ranges:
            return False
        closest = min(forward_ranges)
        self.get_logger().info(f"[NAV] Forward clearance: {closest:.2f}m (need {min_dist:.2f}m)")
        return closest >= min_dist

    def _escape_nudge(self) -> str:
        """Execute escape nudge: rotate ±25° then forward 8cm. Returns direction used."""
        direction = random.choice(["LEFT", "RIGHT"])
        rotate_angle = math.radians(25.0)
        rotate_speed = 0.5  # rad/s
        rotate_duration = rotate_angle / rotate_speed  # ~0.87s

        self.get_logger().info(f"[NAV] escape nudge triggered")
        self.get_logger().info(f"[NAV] nudge direction={direction}")

        # A. Rotate
        twist = Twist()
        twist.angular.z = rotate_speed if direction == "LEFT" else -rotate_speed
        start = time.time()
        while time.time() - start < rotate_duration and rclpy.ok():
            self.cmd_vel_pub.publish(twist)
            time.sleep(0.1)
        self.stop_robot()
        time.sleep(0.2)

        # B. Check forward clearance — if blocked, rotate opposite
        if not self._check_forward_clearance(0.25):
            opposite = "RIGHT" if direction == "LEFT" else "LEFT"
            self.get_logger().info(f"[NAV] forward blocked, rotating {opposite} instead")
            twist.angular.z = -twist.angular.z  # reverse rotation
            # Rotate double (undo original + rotate opposite)
            start = time.time()
            while time.time() - start < rotate_duration * 2.0 and rclpy.ok():
                self.cmd_vel_pub.publish(twist)
                time.sleep(0.1)
            self.stop_robot()
            time.sleep(0.2)
            direction = opposite

            # Re-check after opposite rotation
            if not self._check_forward_clearance(0.25):
                self.get_logger().warn("[NAV] still blocked after opposite rotation — skipping forward nudge")
                return direction

        # C. Forward nudge: 0.08 m/s × 1.0s = ~8cm
        twist = Twist()
        twist.linear.x = 0.08
        start = time.time()
        while time.time() - start < 1.0 and rclpy.ok():
            self.cmd_vel_pub.publish(twist)
            time.sleep(0.1)
        self.stop_robot()
        time.sleep(0.3)

        return direction

    def _navigate_thread(self, query_id: str, target: str,
                          x: float, y: float, z: float,
                          qx: float, qy: float, qz: float, qw: float):
        """Send NavigateToPose goal to Nav2 and relay outcome to /vora/result.

        Runs in a daemon thread. Uses threading.Event + Future.add_done_callback
        so we block the thread without touching the executor — the MultiThreadedExecutor
        keeps processing action client responses on its own threads and sets our Events.

        DO NOT use spin_until_future_complete here: adding self to a second executor
        while MultiThreadedExecutor already owns it raises RuntimeError.
        """
        # ── 1. Confirm Nav2 action server is reachable ──
        # subprocess "ros2 node list" was removed: it spawns a child process that may
        # not inherit the correct RMW/DDS environment, causing the check to fail even
        # when Nav2 is fully running. wait_for_server() is the canonical ROS2 check.
        _NAV2_SERVER_WAIT_S = 30.0
        self.get_logger().info(
            f"[NAVIGATE] waiting for /navigate_to_pose action server (max {_NAV2_SERVER_WAIT_S:.0f}s)...")
        if not self._nav2_client.wait_for_server(timeout_sec=_NAV2_SERVER_WAIT_S):
            self.get_logger().error(
                f"[NAVIGATE] nav_not_ready: /navigate_to_pose action server not available "
                f"after {_NAV2_SERVER_WAIT_S:.0f}s — is Nav2 running?")
            self.publish_result({
                "query_id": query_id, "state": "done", "result": "nav_not_ready",
                "intent": "navigate", "target": target,
                "message": (f"/navigate_to_pose action server not available after "
                            f"{_NAV2_SERVER_WAIT_S:.0f}s — start Nav2 first (start_nav2.sh)"),
                "ts": time.time(),
            })
            self.clear_active()
            return

        # ── 3. Build goal ──
        goal = NavigateToPose.Goal()
        goal.pose = PoseStamped()
        goal.pose.header.frame_id = "map"
        # stamp.sec = 0 → Nav2/tf2 uses latest available TF, avoids
        # "extrapolation into the future" when TF is slightly behind wall clock.
        goal.pose.header.stamp.sec = 0
        goal.pose.header.stamp.nanosec = 0
        goal.pose.pose.position.x = x
        goal.pose.pose.position.y = y
        goal.pose.pose.position.z = z
        goal.pose.pose.orientation.x = qx
        goal.pose.pose.orientation.y = qy
        goal.pose.pose.orientation.z = qz
        goal.pose.pose.orientation.w = qw

        # ── 3. Send goal — wait for acceptance via Event ──
        # Acceptance timeout: 30s. On Jetson Nano under load (Nav2 computing costmaps,
        # DDS discovery in progress), the action server may take >10s to accept a goal.
        # 10s was the prior value — it caused consistent "error" reports on every navigate.
        _ACCEPT_TIMEOUT_S = 30.0
        self.get_logger().info(
            f"[NAVIGATE] Nav2 ACTIVE confirmed, sending goal "
            f"x={x:.2f} y={y:.2f} qz={qz:.3f} qw={qw:.3f}")
        accepted_event = threading.Event()
        goal_handle_box: list = [None]

        def _on_goal_response(future):
            goal_handle_box[0] = future.result()
            accepted_event.set()

        send_future = self._nav2_client.send_goal_async(goal)
        send_future.add_done_callback(_on_goal_response)

        if not accepted_event.wait(timeout=_ACCEPT_TIMEOUT_S):
            self.get_logger().error(
                f"[NAVIGATE] Acceptance timeout after {_ACCEPT_TIMEOUT_S:.0f}s — "
                "Nav2 did not respond (costmap/planner still initialising?)")
            self.publish_result({
                "query_id": query_id, "state": "done", "result": "acceptance_timeout",
                "intent": "navigate", "target": target,
                "message": f"Nav2 goal acceptance timed out after {_ACCEPT_TIMEOUT_S:.0f}s",
                "ts": time.time(),
            })
            self.clear_active()
            return

        goal_handle = goal_handle_box[0]
        if not goal_handle.accepted:
            self.get_logger().warn("[NAVIGATE] Goal rejected by Nav2 (costmap/planner not ready?)")
            self.publish_result({
                "query_id": query_id, "state": "done", "result": "rejected",
                "intent": "navigate", "target": target,
                "message": "Nav2 rejected the goal (costmap/planner not ready?)",
                "ts": time.time(),
            })
            self.clear_active()
            return

        # ── 4. Wait for result via Event — with periodic progress logs ──
        self._active_nav_goal_handle = goal_handle
        self.get_logger().info("[NAVIGATE] Goal accepted — waiting for result...")
        self.publish_status({
            "query_id": query_id,
            "state": "running",
            "intent": "navigate",
            "target": target,
            "message": f"Nav2 goal accepted — navigating to ({x:.2f}, {y:.2f})",
            "nav2_accepted": True,
            "ts": time.time(),
        })
        result_event = threading.Event()
        result_box: list = [None]

        def _on_result(future):
            result_box[0] = future.result()
            result_event.set()

        result_future = goal_handle.get_result_async()
        result_future.add_done_callback(_on_result)

        _PROGRESS_INTERVAL_S = 15.0
        _total_waited = 0.0
        _nav_timed_out = True
        while _total_waited < self.default_timeout_s:
            _chunk = min(_PROGRESS_INTERVAL_S, self.default_timeout_s - _total_waited)
            if result_event.wait(timeout=_chunk):
                _nav_timed_out = False
                break
            _total_waited += _chunk
            if _total_waited < self.default_timeout_s:
                self.get_logger().info(
                    f"[NAVIGATE] Still navigating... ({_total_waited:.0f}s elapsed, "
                    f"limit={self.default_timeout_s:.0f}s)")

        if _nav_timed_out:
            self.get_logger().warn(
                f"[NAVIGATE] Navigation timed out after {self.default_timeout_s:.0f}s — cancelling")
            goal_handle.cancel_goal_async()
            self.publish_result({
                "query_id": query_id, "state": "done", "result": "timeout",
                "intent": "navigate", "target": target,
                "message": f"Nav2 navigation timed out after {self.default_timeout_s:.0f}s",
                "ts": time.time(),
            })
            self.clear_active()
            return

        # ── 5. Map GoalStatus to outcome string ──
        nav_result = result_box[0]
        status = nav_result.status  # action_msgs/GoalStatus constants
        # SUCCEEDED=4  CANCELED=5  ABORTED=6
        if status == 4:
            outcome, message = "success", f"Reached ({x:.2f}, {y:.2f})"
        elif status == 6:
            outcome, message = "aborted", "Nav2 aborted (obstacle / planner failure)"
        elif status == 5:
            outcome, message = "cancelled", "Navigation cancelled"
        else:
            outcome, message = "error", f"Nav2 finished with unexpected status {status}"

        # ── 6. Escape nudge + single retry on ABORTED ──
        if status == 6:
            self.get_logger().warn(f"[NAVIGATE] Nav2 aborted — attempting escape nudge")
            self.stop_robot()
            nudge_dir = self._escape_nudge()
            self.get_logger().info(f"[NAV] retry after escape (nudge={nudge_dir})")

            # Resend same goal once
            self.get_logger().info(f"[NAVIGATE] Retry: sending same goal x={x:.2f} y={y:.2f}")
            accepted_event2 = threading.Event()
            goal_handle_box2: list = [None]

            def _on_goal_response2(future):
                goal_handle_box2[0] = future.result()
                accepted_event2.set()

            send_future2 = self._nav2_client.send_goal_async(goal)
            send_future2.add_done_callback(_on_goal_response2)

            if not accepted_event2.wait(timeout=_ACCEPT_TIMEOUT_S):
                self.get_logger().error(
                    f"[NAVIGATE] Retry: acceptance timeout after {_ACCEPT_TIMEOUT_S:.0f}s")
                self.publish_result({
                    "query_id": query_id, "state": "done", "result": "aborted",
                    "intent": "navigate", "target": target,
                    "message": "Escape nudge done but retry goal acceptance timed out",
                    "ts": time.time(),
                })
                self.clear_active()
                return

            goal_handle2 = goal_handle_box2[0]
            if not goal_handle2.accepted:
                self.get_logger().warn("[NAVIGATE] Retry: goal rejected")
                self.publish_result({
                    "query_id": query_id, "state": "done", "result": "aborted",
                    "intent": "navigate", "target": target,
                    "message": "Escape nudge done but retry goal rejected",
                    "ts": time.time(),
                })
                self.clear_active()
                return

            result_event2 = threading.Event()
            result_box2: list = [None]

            def _on_result2(future):
                result_box2[0] = future.result()
                result_event2.set()

            result_future2 = goal_handle2.get_result_async()
            result_future2.add_done_callback(_on_result2)

            if not result_event2.wait(timeout=self.default_timeout_s):
                self.get_logger().warn("[NAVIGATE] Retry: timed out — cancelling")
                goal_handle2.cancel_goal_async()
                self.publish_result({
                    "query_id": query_id, "state": "done", "result": "timeout",
                    "intent": "navigate", "target": target,
                    "message": "Retry navigation timed out after escape nudge",
                    "ts": time.time(),
                })
                self.clear_active()
                return

            nav_result2 = result_box2[0]
            status2 = nav_result2.status
            if status2 == 4:
                outcome, message = "success", f"Reached ({x:.2f}, {y:.2f}) after escape nudge"
            elif status2 == 6:
                outcome, message = "aborted", "Nav2 aborted on retry after escape nudge"
            elif status2 == 5:
                outcome, message = "cancelled", "Retry navigation cancelled"
            else:
                outcome, message = "error", f"Retry finished with unexpected status {status2}"
            self.get_logger().info(f"[NAVIGATE] Retry {outcome}: {message}")

        self.get_logger().info(f"[NAVIGATE] {outcome}: {message}")
        self.publish_result({
            "query_id": query_id,
            "state": "done",
            "result": outcome,
            "intent": "navigate",
            "target": target,
            "message": message,
            "ts": time.time(),
        })
        self.clear_active()

    def execute_motion(self, intent: str, duration: float, speed: float, angular_speed: float):
        """Execute motion command for specified duration"""
        twist = Twist()
        
        # Set velocities based on intent
        if intent == "move_forward":
            twist.linear.x = speed
        elif intent == "move_backward":
            twist.linear.x = -speed
        elif intent == "strafe_left":
            twist.linear.y = speed
        elif intent == "strafe_right":
            twist.linear.y = -speed
        elif intent == "turn_left":
            twist.angular.z = angular_speed
        elif intent == "turn_right":
            twist.angular.z = -angular_speed
        
        # Publish at 10Hz for the specified duration
        rate = self.create_rate(10)
        start_time = time.time()
        end_time = start_time + duration
        
        self.get_logger().info(f"Publishing /cmd_vel for {duration}s: linear.x={twist.linear.x}, linear.y={twist.linear.y}, angular.z={twist.angular.z}")
        
        while time.time() < end_time and rclpy.ok():
            self.cmd_vel_pub.publish(twist)
            rate.sleep()
        
        # Stop after duration
        self.stop_robot()
        elapsed = time.time() - start_time
        self.get_logger().info(f"Motion completed in {elapsed:.2f}s")

    def on_command(self, msg: String):
        raw = msg.data.strip()
        try:
            cmd = json.loads(raw) if raw else {}
        except Exception as e:
            self.get_logger().error(f"Invalid JSON on {self.command_topic}: {e} | raw={raw[:200]}")
            self.publish_status({
                "state": "error",
                "error": "invalid_json",
                "detail": str(e),
                "raw": raw[:200],
                "ts": time.time()
            })
            return

        query_id = str(cmd.get("query_id") or cmd.get("command_id") or "").strip()
        intent = str(cmd.get("intent") or "").strip().lower()
        target = str(cmd.get("target") or "").strip()
        params = cmd.get("params") or {}

        if not intent:
            self.get_logger().warn("Command missing intent; ignoring.")
            self.publish_status({
                "query_id": query_id,
                "state": "error",
                "error": "missing_intent",
                "ts": time.time()
            })
            return

        timeout_s = float(params.get("timeout_s", self.default_timeout_s))
        self.active_query_id = query_id or f"noid-{int(time.time())}"
        self.active_intent = intent
        self.active_started_at = time.time()
        self.active_timeout_s = timeout_s

        # Acknowledge
        self.publish_status({
            "query_id": self.active_query_id,
            "state": "accepted",
            "intent": intent,
            "target": target,
            "params": params,
            "ts": time.time()
        })

        # Dispatch
        if intent == "stop":
            self.get_logger().info(f"[STOP] query_id={self.active_query_id}")
            if self._active_nav_goal_handle is not None:
                self.get_logger().info("[STOP] cancelling active Nav2 goal")
                try:
                    self._active_nav_goal_handle.cancel_goal_async()
                except Exception:
                    pass
                self._active_nav_goal_handle = None
            self.stop_robot()
            self.publish_result({
                "query_id": self.active_query_id,
                "state": "done",
                "result": "stopped",
                "intent": intent,
                "message": "Robot stopped",
                "ts": time.time()
            })
            self.clear_active()
            return

        # Motion commands with duration
        if intent in ("move_forward", "move_backward", "turn_left", "turn_right", 
                      "strafe_left", "strafe_right"):
            duration = float(params.get("duration", 2.0))
            speed = float(params.get("speed", 0.15))  # Elephant MyAGV 2023 default
            angular_speed = float(params.get("angular_speed", 0.50))  # MyAGV 2023 factory default
            
            self.get_logger().info(f"[{intent.upper()}] query_id={self.active_query_id} duration={duration}s speed={speed}")
            
            # Execute motion
            self.execute_motion(intent, duration, speed, angular_speed)
            
            # Publish result
            self.publish_result({
                "query_id": self.active_query_id,
                "state": "done",
                "result": "completed",
                "intent": intent,
                "message": f"Moved for {duration}s",
                "ts": time.time()
            })
            self.clear_active()
            return

        if intent == "navigate":
            x = float(params.get("x", 0.0))
            y = float(params.get("y", 0.0))
            z = float(params.get("z", 0.0))
            qw = float(params.get("qw", 1.0))
            qx = float(params.get("qx", 0.0))
            qy = float(params.get("qy", 0.0))
            qz = float(params.get("qz", 0.0))
            self.get_logger().info(f"[NAVIGATE] query_id={self.active_query_id} target={target} x={x} y={y}")
            self.publish_status({
                "query_id": self.active_query_id,
                "state": "running",
                "intent": intent,
                "target": target,
                "message": f"Navigating to ({x:.2f}, {y:.2f})",
                "ts": time.time()
            })
            # Disable the generic watchdog for navigate — the navigate thread manages
            # its own timeouts (30s acceptance + default_timeout_s result wait).
            # If watchdog were left enabled with a short timeout_s from params, it would
            # fire and publish a conflicting "timeout" result mid-navigation.
            self.active_timeout_s = 0  # 0 → watchdog condition `> 0` is False → disabled
            # Run Nav2 action in a background thread so spin() keeps processing.
            snap = self.active_query_id  # capture before thread races
            self._nav_thread = threading.Thread(
                target=self._navigate_thread,
                args=(snap, target, x, y, z, qx, qy, qz, qw),
                daemon=True,
            )
            self._nav_thread.start()
            return

        if intent == "find_object":
            # Placeholder: integrate your vision+patrol pipeline here.
            self.get_logger().info(f"[FIND_OBJECT] query_id={self.active_query_id} target={target}")
            self.publish_status({
                "query_id": self.active_query_id,
                "state": "running",
                "intent": intent,
                "target": target,
                "message": "Find-object requested (vision/patrol pipeline should execute and publish /vora/result)",
                "ts": time.time()
            })
            return

        if intent in ("start_slam", "stop_slam", "save_map"):
            self.get_logger().info(f"[MAPPING] query_id={self.active_query_id} intent={intent} params={params}")
            self.publish_status({
                "query_id": self.active_query_id,
                "state": "running",
                "intent": intent,
                "target": target,
                "message": "Mapping command received (slam_toolbox/map_saver should be handled by mapping manager node)",
                "ts": time.time()
            })
            return

        # Unknown intent -> finish with error
        self.get_logger().warn(f"Unknown intent: {intent}")
        self.publish_result({
            "query_id": self.active_query_id,
            "state": "done",
            "result": "error",
            "intent": intent,
            "message": f"Unknown intent: {intent}",
            "ts": time.time()
        })
        self.clear_active()


def main():
    rclpy.init()
    node = CommandExecutor()
    # MultiThreadedExecutor required: _navigate_thread uses aux SingleThreadedExecutors
    # to spin_until_future_complete while this executor keeps running action client callbacks.
    executor = MultiThreadedExecutor()
    executor.add_node(node)
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


