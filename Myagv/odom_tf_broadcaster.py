#!/usr/bin/env python3
"""
Odom → TF Broadcaster for MyAGV 2023
=====================================
Subscribes to /odom (nav_msgs/Odometry) and broadcasts
the odom → base_footprint TF transform.

Needed because myagv_odometry_node publishes /odom topic
but does NOT broadcast the TF transform that Nav2 requires.

MyAGV hardware bug: /odom always has x=0, y=0 (only θ/yaw is valid).
Fix: integrate /cmd_vel velocity to estimate x,y (dead reckoning).
The odom→base_footprint TF uses integrated x,y + real θ from /odom.
Also publishes /odom_fused topic for Nav2 bt_navigator odom_topic.

Usage:
    python3 odom_tf_broadcaster.py
    # or
    ros2 run tf2_ros static_transform_publisher  ...  (for static only)
"""

import math
import sys
import rclpy
from rclpy.node import Node
from nav_msgs.msg import Odometry
from tf2_ros import TransformBroadcaster, StaticTransformBroadcaster
from geometry_msgs.msg import TransformStamped, Twist
from std_msgs.msg import Bool


class OdomTFBroadcaster(Node):
    def __init__(self, skip_odom_tf: bool = False):
        super().__init__("odom_tf_broadcaster")
        # When True: skip odom→base_footprint TF (EKF handles it), only publish /odom_fused
        self._skip_odom_tf = skip_odom_tf
        if skip_odom_tf:
            self.get_logger().info("--skip-odom-tf mode: /odom_fused only (EKF owns TF)")
        self._br = TransformBroadcaster(self)
        self._sub = self.create_subscription(
            Odometry, "/odom", self._odom_cb, 10
        )
        # Static identity TF: base_footprint → base_link
        # Nav2 costmaps/planners reference base_link; URDF has this joint,
        # but robot_state_publisher only runs it myagv_active.launch.py is up.
        # We publish it here as a safe fallback (identity = no physical offset).
        # NOTE: do NOT publish base_footprint/base_link → laser_frame here.
        #       ydlidar_launch.py already publishes base_footprint → laser_frame
        #       (x=0.065, z=0.08, yaw=π). A second source causes TF conflicts.
        self._static_br = StaticTransformBroadcaster(self)
        static_tf_msgs = []

        bf_to_bl = TransformStamped()
        bf_to_bl.header.stamp = self.get_clock().now().to_msg()
        bf_to_bl.header.frame_id = "base_footprint"
        bf_to_bl.child_frame_id = "base_link"
        bf_to_bl.transform.rotation.w = 1.0
        static_tf_msgs.append(bf_to_bl)

        self._static_br.sendTransform(static_tf_msgs)

        # odom→base_footprint TF is published continuously by _tf_timer (100 Hz).
        # Before first /odom: identity transform.  After: dead-reckoning x,y + real θ.
        # Skipped in --skip-odom-tf mode (EKF already owns this TF).
        self._got_odom = False
        self._latest_orientation_x = 0.0
        self._latest_orientation_y = 0.0
        self._latest_orientation_z = 0.0
        self._latest_orientation_w = 1.0
        if not self._skip_odom_tf:
            self._tf_timer = self.create_timer(0.01, self._publish_tf_cb)  # 100 Hz — reduces gap for AMCL getOdomPose() TF lookup

        self.get_logger().info("odom → base_footprint → base_link TF broadcaster started")

        # Dead-reckoning state — MyAGV /odom has x=y=0 always; integrate cmd_vel instead
        self._x = 0.0
        self._y = 0.0
        self._yaw = 0.0
        self._last_dr_time = self.get_clock().now()
        self._last_cmd = None  # most recent Twist (held between integration ticks)

        # Publish corrected odom for Nav2 bt_navigator odom_topic
        self._odom_fused_pub = self.create_publisher(Odometry, "/odom_fused", 10)

        # Integration timer at 20 Hz — runs independently of cmd_vel arrival rate
        self._dr_timer = self.create_timer(0.05, self._integrate_cb)

        # cmd_vel safety watchdog: sends zero if no cmd_vel for >1s
        self._cmd_vel_sub = self.create_subscription(
            Twist, "/cmd_vel", self._cmd_vel_cb, 10
        )
        self._cmd_vel_pub = self.create_publisher(Twist, "/cmd_vel", 10)
        self._last_cmd_vel_time = self.get_clock().now()
        self._cmd_vel_active = False
        self._stop_logged = False  # prevent repeated STOP logs
        self._watchdog_timer = self.create_timer(0.5, self._watchdog_cb)
        # Suppress watchdog STOP while Nav2 goal is active (recovery gaps can exceed 5s).
        self._nav_active = False
        self.create_subscription(Bool, '/vora/nav_active', self._nav_active_cb, 10)

    def _publish_tf_cb(self):
        """Publish odom→base_footprint at 100 Hz.
        Stamp uses current ROS time only to avoid future-dated TF entries that can
        trigger Nav2 / costmap extrapolation and message-filter failures."""
        t = TransformStamped()
        t.header.stamp = self.get_clock().now().to_msg()
        t.header.frame_id = "odom"
        t.child_frame_id = "base_footprint"
        t.transform.translation.x = self._x
        t.transform.translation.y = self._y
        t.transform.translation.z = 0.0
        t.transform.rotation.x = self._latest_orientation_x
        t.transform.rotation.y = self._latest_orientation_y
        t.transform.rotation.z = self._latest_orientation_z
        t.transform.rotation.w = self._latest_orientation_w
        self._br.sendTransform(t)

    def _cmd_vel_cb(self, msg: Twist):
        # Threshold 0.10 filters out Nav2 final-deceleration trailing commands
        # (DWB sends ~0.06 m/s on the last cycle before stopping, which at 0.05
        # threshold would re-arm the watchdog and cause a second stop).
        speed = abs(msg.linear.x) + abs(msg.linear.y) + abs(msg.angular.z)
        if speed > 0.10:
            self._last_cmd_vel_time = self.get_clock().now()
            self._cmd_vel_active = True
            self._stop_logged = False  # new real motion, allow logging again
        self._last_cmd = msg  # store for integration timer (all commands, including stop)

    def _integrate_cb(self):
        """Dead-reckoning: integrate cmd_vel at 20 Hz to estimate x,y displacement."""
        if self._last_cmd is None:
            return
        now = self.get_clock().now()
        dt = (now - self._last_dr_time).nanoseconds / 1e9
        self._last_dr_time = now

        vx = self._last_cmd.linear.x
        vy = self._last_cmd.linear.y

        # Only integrate if robot is actually moving and dt is sane
        # (dt > 0.5 means node was paused/stalled — skip to avoid position jump)
        if (abs(vx) + abs(vy) > 0.01) and (0.001 < dt < 0.5):
            # Transform robot-frame velocity to world frame using current yaw
            c = math.cos(self._yaw)
            s = math.sin(self._yaw)
            self._x += (c * vx - s * vy) * dt
            self._y += (s * vx + c * vy) * dt

    def _nav_active_cb(self, msg: Bool):
        self._nav_active = msg.data

    def _watchdog_cb(self):
        if not self._cmd_vel_active:
            return
        if self._nav_active:
            return  # Nav2 goal active — recovery/replanning gaps can exceed 5s; don't STOP
        elapsed = (self.get_clock().now() - self._last_cmd_vel_time).nanoseconds / 1e9
        if elapsed > 5.0:  # was 1.0 — Nav2 planning/recovery gaps can exceed 1s on Jetson Nano
            self._cmd_vel_pub.publish(Twist())
            self._cmd_vel_active = False
            if not self._stop_logged:
                self.get_logger().warn("cmd_vel watchdog: motion stopped -> sending STOP")
                self._stop_logged = True

    def _odom_cb(self, msg: Odometry):
        self._got_odom = True

        # Extract yaw from real /odom orientation — this IS valid on MyAGV (θ works)
        q = msg.pose.pose.orientation
        siny_cosp = 2.0 * (q.w * q.z + q.x * q.y)
        cosy_cosp = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
        self._yaw = math.atan2(siny_cosp, cosy_cosp)

        # Store latest orientation for _publish_tf_cb (100 Hz timer owns the TF broadcast).
        self._latest_orientation_x = q.x
        self._latest_orientation_y = q.y
        self._latest_orientation_z = q.z
        self._latest_orientation_w = q.w

        # Publish /odom_fused for Nav2 bt_navigator (progress checker needs x,y)
        fused = Odometry()
        fused.header.stamp = self.get_clock().now().to_msg()
        fused.header.frame_id = "odom"
        fused.child_frame_id = "base_footprint"
        fused.pose.pose.position.x = self._x
        fused.pose.pose.position.y = self._y
        fused.pose.pose.position.z = 0.0
        fused.pose.pose.orientation = msg.pose.pose.orientation
        fused.twist.twist.linear.x = msg.twist.twist.linear.x
        fused.twist.twist.angular.z = msg.twist.twist.angular.z
        self._odom_fused_pub.publish(fused)


def main():
    skip_tf = "--skip-odom-tf" in sys.argv
    rclpy.init(args=[a for a in sys.argv if a != "--skip-odom-tf"])
    node = OdomTFBroadcaster(skip_odom_tf=skip_tf)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()


