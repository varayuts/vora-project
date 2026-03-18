#!/usr/bin/env python3
"""
Odom → TF Broadcaster for MyAGV 2023
=====================================
Subscribes to /odom (nav_msgs/Odometry) and broadcasts
the odom → base_footprint TF transform.

Needed because myagv_odometry_node publishes /odom topic
but does NOT broadcast the TF transform that Nav2 requires.

Usage:
    python3 odom_tf_broadcaster.py
    # or
    ros2 run tf2_ros static_transform_publisher  ...  (for static only)
"""

import rclpy
from rclpy.node import Node
from nav_msgs.msg import Odometry
from tf2_ros import TransformBroadcaster, StaticTransformBroadcaster
from geometry_msgs.msg import TransformStamped


class OdomTFBroadcaster(Node):
    def __init__(self):
        super().__init__("odom_tf_broadcaster")
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

        # Publish initial odom → base_footprint immediately (identity)
        # so tf2_echo can see the frame before first /odom arrives
        init_t = TransformStamped()
        init_t.header.stamp = self.get_clock().now().to_msg()
        init_t.header.frame_id = "odom"
        init_t.child_frame_id = "base_footprint"
        init_t.transform.rotation.w = 1.0
        self._br.sendTransform(init_t)

        # Also publish at 10Hz until first /odom arrives
        self._got_odom = False
        self._init_timer = self.create_timer(0.1, self._publish_init_tf)

        self.get_logger().info("odom → base_footprint → base_link TF broadcaster started")

    def _publish_init_tf(self):
        """Keep publishing identity TF until real /odom data arrives."""
        if self._got_odom:
            self._init_timer.cancel()
            return
        t = TransformStamped()
        t.header.stamp = self.get_clock().now().to_msg()
        t.header.frame_id = "odom"
        t.child_frame_id = "base_footprint"
        t.transform.rotation.w = 1.0
        self._br.sendTransform(t)

    def _odom_cb(self, msg: Odometry):
        self._got_odom = True
        t = TransformStamped()
        t.header.stamp = msg.header.stamp
        t.header.frame_id = "odom"
        t.child_frame_id = "base_footprint"
        t.transform.translation.x = msg.pose.pose.position.x
        t.transform.translation.y = msg.pose.pose.position.y
        t.transform.translation.z = msg.pose.pose.position.z
        t.transform.rotation = msg.pose.pose.orientation
        self._br.sendTransform(t)


def main():
    rclpy.init()
    node = OdomTFBroadcaster()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
