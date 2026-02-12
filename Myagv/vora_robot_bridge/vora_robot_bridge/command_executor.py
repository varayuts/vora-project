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
import time
from typing import Any, Dict, Optional

import rclpy
from rclpy.node import Node
from std_msgs.msg import String
from geometry_msgs.msg import Twist


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
            # Placeholder: integrate Nav2 action here in the future.
            self.get_logger().info(f"[NAVIGATE] query_id={self.active_query_id} target={target}")
            self.publish_status({
                "query_id": self.active_query_id,
                "state": "running",
                "intent": intent,
                "target": target,
                "message": "Navigate requested (integrate Nav2 action in robot pipeline)",
                "ts": time.time()
            })
            # For now, we don't complete automatically; expect external node to publish /vora/result.
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
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()
