#!/usr/bin/env python3
"""
Odom → TF Broadcaster for MyAGV 2023
=====================================
Subscribes to /odom (nav_msgs/Odometry) and broadcasts
the odom → base_link TF transform.

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
from tf2_ros import TransformBroadcaster
from geometry_msgs.msg import TransformStamped


class OdomTFBroadcaster(Node):
    def __init__(self):
        super().__init__("odom_tf_broadcaster")
        self._br = TransformBroadcaster(self)
        self._sub = self.create_subscription(
            Odometry, "/odom", self._odom_cb, 10
        )
        self.get_logger().info("odom → base_link TF broadcaster started")

    def _odom_cb(self, msg: Odometry):
        t = TransformStamped()
        t.header.stamp = msg.header.stamp
        t.header.frame_id = "odom"
        t.child_frame_id = "base_link"
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
