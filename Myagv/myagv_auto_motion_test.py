#!/usr/bin/env python3
import time
import math

import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist


class AutoMotionTest(Node):
    def __init__(self):
        super().__init__("myagv_auto_motion_test")
        self.pub = self.create_publisher(Twist, "/cmd_vel", 10)

        # ปรับได้ตามต้องการ
        self.linear_speed = 0.20   # m/s
        self.angular_speed = 0.80  # rad/s
        self.hz = 20.0             # publish rate
        self.dt = 1.0 / self.hz

    def publish_cmd(self, vx: float, wz: float):
        msg = Twist()
        msg.linear.x = float(vx)
        msg.angular.z = float(wz)
        self.pub.publish(msg)

    def run_for(self, vx: float, wz: float, seconds: float):
        end = time.time() + seconds
        while rclpy.ok() and time.time() < end:
            self.publish_cmd(vx, wz)
            rclpy.spin_once(self, timeout_sec=0.0)
            time.sleep(self.dt)

    def stop(self):
        for _ in range(10):
            self.publish_cmd(0.0, 0.0)
            rclpy.spin_once(self, timeout_sec=0.0)
            time.sleep(self.dt)

    def spin_360(self, direction: int = +1):
        # duration = 2*pi / |w|
        w = self.angular_speed * (1 if direction >= 0 else -1)
        duration = (2.0 * math.pi) / abs(w)
        self.run_for(0.0, w, duration)

    def sequence(self):
        self.get_logger().info("AUTO TEST START")

        self.get_logger().info("Forward 3s")
        self.run_for(self.linear_speed, 0.0, 3.0)
        self.stop()

        self.get_logger().info("Backward 3s")
        self.run_for(-self.linear_speed, 0.0, 3.0)
        self.stop()

        self.get_logger().info("Turn left 2s (in place)")
        self.run_for(0.0, +self.angular_speed, 2.0)
        self.stop()

        self.get_logger().info("Turn right 2s (in place)")
        self.run_for(0.0, -self.angular_speed, 2.0)
        self.stop()

        self.get_logger().info("Spin 360 deg")
        self.spin_360(direction=+1)
        self.stop()

        self.get_logger().info("AUTO TEST DONE")


def main():
    rclpy.init()
    node = AutoMotionTest()
    try:
        node.sequence()
    except KeyboardInterrupt:
        pass
    finally:
        node.stop()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
