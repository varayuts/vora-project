#!/usr/bin/env python3
"""
camera_bridge.py
================
Compress raw camera images for efficient ROSBridge WebSocket streaming.

Problem:
  - usb_cam publishes /image_raw as sensor_msgs/Image (~614KB-921KB per frame)
  - ROSBridge WebSocket drops large binary messages
  - Gateway never receives camera frames

Solution:
  - Subscribe to /image_raw
  - Compress to JPEG (~30-100KB)
  - Publish as sensor_msgs/CompressedImage on /camera/compressed
  - Throttle framerate to avoid overwhelming ROSBridge

Usage:
  ros2 run vora_robot_bridge camera_bridge

  # With custom parameters:
  ros2 run vora_robot_bridge camera_bridge --ros-args \
      -p jpeg_quality:=50 \
      -p max_fps:=10.0 \
      -p input_topic:=/image_raw \
      -p output_topic:=/camera/compressed

Requirements:
  pip3 install opencv-python numpy
  (cv2 is usually pre-installed on Jetson Nano)
"""

import time
import traceback
import sys
import os

# Ensure user site-packages are on path (cv2 may be installed there
# but ROS2's PYTHONPATH prepend can shadow it on some setups)
import site
for _p in site.getusersitepackages() if isinstance(site.getusersitepackages(), list) else [site.getusersitepackages()]:
    if _p not in sys.path:
        sys.path.append(_p)

import numpy as np
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image, CompressedImage

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False


# Encoding -> OpenCV conversion code mapping
ENCODING_TO_CV2 = {
    "rgb8": cv2.COLOR_RGB2BGR if HAS_CV2 else None,
    "rgba8": cv2.COLOR_RGBA2BGR if HAS_CV2 else None,
    "bgra8": cv2.COLOR_BGRA2BGR if HAS_CV2 else None,
    "bgr8": None,  # already BGR, no conversion needed
    "mono8": None,  # grayscale, encode directly
    "8UC1": None,   # grayscale
    "8UC3": None,   # assume BGR
}

# Bytes per pixel for common encodings
ENCODING_BPP = {
    "rgb8": 3,
    "rgba8": 4,
    "bgr8": 3,
    "bgra8": 4,
    "mono8": 1,
    "8UC1": 1,
    "8UC3": 3,
    "yuyv": 2,
    "uyvy": 2,
}


class CameraBridge(Node):
    """
    ROS2 Node that compresses raw images for ROSBridge streaming.

    Subscribes: /image_raw (sensor_msgs/Image)
    Publishes:  /camera/compressed (sensor_msgs/CompressedImage)
    """

    def __init__(self):
        super().__init__("vora_camera_bridge")

        # Parameters
        self.declare_parameter("input_topic", "/image_raw")
        self.declare_parameter("output_topic", "/camera/compressed")
        self.declare_parameter("jpeg_quality", 60)
        self.declare_parameter("max_fps", 10.0)
        self.declare_parameter("resize_width", 0)   # 0 = no resize
        self.declare_parameter("resize_height", 0)   # 0 = no resize

        self.input_topic = self.get_parameter("input_topic").value
        self.output_topic = self.get_parameter("output_topic").value
        self.jpeg_quality = self.get_parameter("jpeg_quality").value
        self.max_fps = self.get_parameter("max_fps").value
        self.resize_width = self.get_parameter("resize_width").value
        self.resize_height = self.get_parameter("resize_height").value

        self.min_interval = 1.0 / self.max_fps if self.max_fps > 0 else 0.0
        self.last_publish_time = 0.0
        self.frame_count = 0
        self.drop_count = 0
        self.error_count = 0
        self.stats_time = time.time()

        # Check OpenCV
        if not HAS_CV2:
            self.get_logger().fatal(
                "OpenCV (cv2) is not installed! "
                "Install with: pip3 install opencv-python-headless"
            )
            raise RuntimeError("cv2 not available")

        # Publisher & Subscriber
        self.pub = self.create_publisher(CompressedImage, self.output_topic, 10)
        self.sub = self.create_subscription(
            Image, self.input_topic, self.on_image, 1  # queue_size=1 to drop old frames
        )

        # Stats timer (every 10 seconds)
        self.stats_timer = self.create_timer(10.0, self.print_stats)

        self.get_logger().info("=" * 60)
        self.get_logger().info("📷 VORA Camera Bridge - Ready")
        self.get_logger().info("=" * 60)
        self.get_logger().info(f"📥 Input:   {self.input_topic} (sensor_msgs/Image)")
        self.get_logger().info(f"📤 Output:  {self.output_topic} (sensor_msgs/CompressedImage)")
        self.get_logger().info(f"🖼️  JPEG Q:  {self.jpeg_quality}")
        self.get_logger().info(f"⏱️  Max FPS: {self.max_fps}")
        if self.resize_width > 0 and self.resize_height > 0:
            self.get_logger().info(f"📐 Resize:  {self.resize_width}x{self.resize_height}")
        else:
            self.get_logger().info(f"📐 Resize:  disabled (original size)")
        self.get_logger().info("=" * 60)

    def print_stats(self):
        """Print periodic statistics."""
        elapsed = time.time() - self.stats_time
        if elapsed <= 0:
            return
        fps = self.frame_count / elapsed
        self.get_logger().info(
            f"📊 Camera Bridge: {self.frame_count} frames "
            f"({fps:.1f} fps), {self.drop_count} dropped, "
            f"{self.error_count} errors"
        )
        self.frame_count = 0
        self.drop_count = 0
        self.error_count = 0
        self.stats_time = time.time()

    def on_image(self, msg: Image):
        """Convert raw image to compressed JPEG."""
        now = time.time()

        # Throttle: skip if too soon since last publish
        if (now - self.last_publish_time) < self.min_interval:
            self.drop_count += 1
            return

        try:
            frame = self._image_msg_to_cv2(msg)
            if frame is None:
                return

            # Optional resize
            if self.resize_width > 0 and self.resize_height > 0:
                frame = cv2.resize(
                    frame,
                    (self.resize_width, self.resize_height),
                    interpolation=cv2.INTER_LINEAR,
                )

            # Encode to JPEG
            encode_params = [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality]
            success, jpeg_data = cv2.imencode(".jpg", frame, encode_params)
            if not success:
                self.get_logger().warn("JPEG encoding failed")
                self.error_count += 1
                return

            # Build CompressedImage message
            compressed = CompressedImage()
            compressed.header = msg.header
            compressed.format = "jpeg"
            compressed.data = jpeg_data.tobytes()

            self.pub.publish(compressed)
            self.last_publish_time = now
            self.frame_count += 1

        except Exception as e:
            self.error_count += 1
            if self.error_count <= 5:
                self.get_logger().error(f"Compression error: {e}")
                if self.error_count == 5:
                    self.get_logger().warn("Suppressing further error logs")

    def _image_msg_to_cv2(self, msg: Image) -> "np.ndarray | None":
        """Convert sensor_msgs/Image to OpenCV BGR numpy array."""
        encoding = msg.encoding.lower()
        width = msg.width
        height = msg.height

        if width == 0 or height == 0:
            self.get_logger().warn("Received image with 0 dimensions")
            return None

        raw = bytes(msg.data)

        # Handle YUYV / UYVY (common USB camera formats, 2 bytes/pixel)
        if encoding in ("yuyv", "yuyv422"):
            expected = width * height * 2
            if len(raw) < expected:
                self.get_logger().warn(
                    f"YUYV data too short: {len(raw)} < {expected}"
                )
                return None
            yuv = np.frombuffer(raw[:expected], dtype=np.uint8).reshape(
                (height, width, 2)
            )
            return cv2.cvtColor(yuv, cv2.COLOR_YUV2BGR_YUYV)

        if encoding in ("uyvy", "uyvy422"):
            expected = width * height * 2
            if len(raw) < expected:
                return None
            yuv = np.frombuffer(raw[:expected], dtype=np.uint8).reshape(
                (height, width, 2)
            )
            return cv2.cvtColor(yuv, cv2.COLOR_YUV2BGR_UYVY)

        # Handle standard encodings
        bpp = ENCODING_BPP.get(encoding)
        if bpp is None:
            # Try to guess from data length
            total_pixels = width * height
            data_len = len(raw)
            if data_len >= total_pixels * 3:
                bpp = 3
            elif data_len >= total_pixels:
                bpp = 1
            else:
                self.get_logger().warn(
                    f"Unknown encoding '{encoding}' and can't guess BPP "
                    f"(data={data_len}, pixels={total_pixels})"
                )
                return None

        expected = width * height * bpp
        if len(raw) < expected:
            self.get_logger().warn(
                f"Image data too short for {encoding}: {len(raw)} < {expected}"
            )
            return None

        if bpp == 1:
            frame = np.frombuffer(raw[:expected], dtype=np.uint8).reshape(
                (height, width)
            )
        else:
            frame = np.frombuffer(raw[:expected], dtype=np.uint8).reshape(
                (height, width, bpp)
            )

        # Apply color conversion if needed
        conversion = ENCODING_TO_CV2.get(encoding)
        if conversion is not None:
            frame = cv2.cvtColor(frame, conversion)

        return frame


def main():
    rclpy.init()
    node = CameraBridge()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.get_logger().info("Camera bridge shutting down")
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
