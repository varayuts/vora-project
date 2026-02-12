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

        # ===== Elephant MyAGV 2023 (Jetson Nano) Default Values =====
        # จาก Elephant Robotics specification:
        #   - Max linear speed: 0.9 m/s (แต่ใช้จริงไม่ควรเกิน 0.3)
        #   - Max angular speed: ~1.5 rad/s (แต่ใช้จริง 0.5 เหมาะสมที่สุด)
        #   - Wheel base: 0.105 m (mecanum 4-wheel)
        self.linear_speed = 0.15   # m/s (ค่าปลอดภัยสำหรับทดสอบ)
        self.angular_speed = 0.50  # rad/s (ค่า default ที่แม่นยำสำหรับ MyAGV 2023)
        self.rotation_calibration = 1.0   # จูนใหม่ที่ 0.50 rad/s (cal เดิม 0.85 วัดที่ 0.30)
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
        # duration = (2*pi / |w|) * calibration
        # calibration ชดเชยการหมุนเกินจาก momentum ของ Mecanum wheel
        w = self.angular_speed * (1 if direction >= 0 else -1)
        duration = (2.0 * math.pi) / abs(w) * self.rotation_calibration
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
