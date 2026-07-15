#!/usr/bin/env python3
"""
ros_camera_pub.py
=================
อ่าน /dev/video0 โดยตรงด้วย OpenCV แล้ว publish เป็น CompressedImage
ให้ Gateway subscribe ได้ผ่าน ROSBridge

ทำไม? เพราะ usb_cam node segfault บน Jetson Nano นี้

Usage:
    python3 ros_camera_pub.py

Topics ที่ publish:
    /camera/compressed  (sensor_msgs/CompressedImage)

Gateway subscribe:
    /camera/compressed  type: sensor_msgs/CompressedImage
"""

import sys
import os
import time

# ── cv2 path fix ─────────────────────────────────────────────────────────────
# After 'source /opt/ros/galactic/setup.bash', ROS2 overrides PYTHONPATH and
# shadows ~/.local/lib/python3.8/site-packages where opencv-python-headless
# is installed.  Inject user-site explicitly so 'import cv2' works.
try:
    import site as _site
    _user_site = _site.getusersitepackages()
    if _user_site not in sys.path:
        sys.path.insert(0, _user_site)
except Exception:
    pass
# ─────────────────────────────────────────────────────────────────────────────

import cv2
import numpy as np
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import CompressedImage
from builtin_interfaces.msg import Time


class DirectCameraPublisher(Node):
    def __init__(self):
        super().__init__("vora_camera_pub")

        # ─── Parameters ──────────────────────────────────────────────
        self.declare_parameter("device",       "/dev/video0")
        self.declare_parameter("width",        640)
        self.declare_parameter("height",       480)
        self.declare_parameter("fps",          15.0)
        self.declare_parameter("jpeg_quality", 60)
        self.declare_parameter("topic",        "/camera/compressed")
        self.declare_parameter("use_mjpeg",    True)   # ask camera for MJPEG directly

        dev  = self.get_parameter("device").value
        w    = self.get_parameter("width").value
        h    = self.get_parameter("height").value
        fps  = self.get_parameter("fps").value
        self.quality = self.get_parameter("jpeg_quality").value
        topic        = self.get_parameter("topic").value
        use_mjpeg    = self.get_parameter("use_mjpeg").value

        # ─── Open camera ─────────────────────────────────────────────
        # V4L2 backend can't open by device name in some subprocess contexts.
        # Parse device index from path (e.g. /dev/video0 → 0), fall back to 0.
        try:
            dev_index = int(dev.replace("/dev/video", "").strip())
        except (ValueError, AttributeError):
            dev_index = 0

        # V4L2 by-name and by-index both fail in some subprocess/ROS2 contexts.
        # Strategy: try multiple open methods until one works.
        self.cap = None
        open_attempts = [
            (dev,       cv2.CAP_V4L2),   # string + V4L2
            (dev_index, None),            # index, auto backend
            (dev_index, cv2.CAP_V4L2),   # index + V4L2
            (dev,       None),            # string, auto backend
        ]
        for cam_id, backend in open_attempts:
            try:
                cap = cv2.VideoCapture(cam_id, backend) if backend is not None \
                      else cv2.VideoCapture(cam_id)
                if cap.isOpened():
                    self.cap = cap
                    self.get_logger().info(
                        f"Camera opened: id={cam_id!r}, backend={backend}"
                    )
                    break
                cap.release()
            except Exception as e:
                self.get_logger().warn(f"Open attempt ({cam_id!r}, {backend}) failed: {e}")

        if self.cap is None or not self.cap.isOpened():
            self.get_logger().fatal(f"Cannot open camera: {dev}")
            raise RuntimeError(f"Cannot open {dev}")

        # Request MJPEG directly from hardware (smaller buffer, no CPU decode)
        if use_mjpeg:
            self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
            self.get_logger().info("Requested MJPEG from hardware")

        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH,  w)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
        self.cap.set(cv2.CAP_PROP_FPS,          fps)

        actual_w   = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h   = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        actual_fps = self.cap.get(cv2.CAP_PROP_FPS)

        # ─── Publishers ──────────────────────────────────────────────
        # Publish on both topics so Gateway works regardless of which it subscribes to:
        #   /camera/compressed       (our primary topic)
        #   /image_raw/compressed    (Gateway currently subscribes to this)
        self.pub = self.create_publisher(CompressedImage, topic, 10)
        self.pub_raw_compressed = self.create_publisher(
            CompressedImage, "/image_raw/compressed", 10
        )

        # ─── Timer (at requested fps) ─────────────────────────────────
        self.timer = self.create_timer(1.0 / fps, self.publish_frame)

        # Stats
        self.frame_count = 0
        self.error_count = 0
        self.consecutive_errors = 0
        self.reopen_count = 0
        self.t0 = time.time()
        self.stats_timer = self.create_timer(10.0, self.print_stats)

        # Camera device info for reopening
        self._dev = dev
        self._dev_index = dev_index
        self._use_mjpeg = use_mjpeg
        self._width = w
        self._height = h
        self._fps = fps

        self.get_logger().info("=" * 60)
        self.get_logger().info("📷 VORA Direct Camera Publisher - Ready")
        self.get_logger().info("=" * 60)
        self.get_logger().info(f"📥 Device:    {dev}  (index={dev_index})")
        self.get_logger().info(f"📐 Actual:    {actual_w}x{actual_h} @ {actual_fps:.0f}fps")
        self.get_logger().info(f"📤 Topics:")
        self.get_logger().info(f"     {topic}            (sensor_msgs/CompressedImage)")
        self.get_logger().info(f"     /image_raw/compressed  (sensor_msgs/CompressedImage)")
        self.get_logger().info(f"🖼️  JPEG Q:    {self.quality}")
        self.get_logger().info("=" * 60)
        self.get_logger().info("✅ Gateway should subscribe to: /image_raw/compressed")
        self.get_logger().info("=" * 60)

    def _reopen_camera(self):
        """Reopen the camera device after persistent read failures."""
        self.reopen_count += 1
        self.get_logger().warn(
            f"🔄 Reopening camera (attempt #{self.reopen_count})..."
        )

        # Release old handle
        try:
            if self.cap:
                self.cap.release()
        except Exception:
            pass
        self.cap = None

        # ── USB reset: re-enumerate USB devices so /dev/video0 reappears ──
        # On Jetson Nano, USB camera sometimes disappears under load.
        # Unbind/rebind the USB hub forces the kernel to re-detect devices.
        import subprocess, glob
        try:
            # Try to reset the specific USB device
            usb_paths = glob.glob("/sys/bus/usb/devices/*/product")
            for p in usb_paths:
                try:
                    with open(p, 'r') as f:
                        product = f.read().strip()
                    # Look for camera-related USB devices
                    if any(kw in product.lower() for kw in ['camera', 'video', 'webcam', 'uvc']):
                        dev_path = os.path.dirname(p)
                        dev_id = os.path.basename(dev_path)
                        unbind = f"/sys/bus/usb/drivers/usb/unbind"
                        bind = f"/sys/bus/usb/drivers/usb/bind"
                        self.get_logger().info(f"🔌 USB reset: {dev_id} ({product})")
                        subprocess.run(f"echo '{dev_id}' | sudo tee {unbind}", shell=True, timeout=5)
                        time.sleep(2)
                        subprocess.run(f"echo '{dev_id}' | sudo tee {bind}", shell=True, timeout=5)
                        time.sleep(3)
                        break
                except Exception:
                    continue
        except Exception as e:
            self.get_logger().warn(f"USB reset failed: {e}")
        
        # Wait extra time for device to re-appear after USB reset
        wait_time = min(2.0 + self.reopen_count * 2, 15.0)  # escalating wait, max 15s
        self.get_logger().info(f"⏳ Waiting {wait_time:.0f}s for camera device...")
        time.sleep(wait_time)

        open_attempts = [
            (self._dev,       cv2.CAP_V4L2),
            (self._dev_index, None),
            (self._dev_index, cv2.CAP_V4L2),
            (self._dev,       None),
        ]
        for cam_id, backend in open_attempts:
            try:
                cap = cv2.VideoCapture(cam_id, backend) if backend is not None \
                      else cv2.VideoCapture(cam_id)
                if cap.isOpened():
                    if self._use_mjpeg:
                        cap.set(cv2.CAP_PROP_FOURCC,
                                cv2.VideoWriter_fourcc(*"MJPG"))
                    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  self._width)
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._height)
                    cap.set(cv2.CAP_PROP_FPS,          self._fps)
                    self.cap = cap
                    self.consecutive_errors = 0
                    self.get_logger().info(
                        f"✅ Camera reopened successfully (attempt #{self.reopen_count})"
                    )
                    return True
                cap.release()
            except Exception:
                pass

        self.get_logger().error("❌ Failed to reopen camera")
        return False

    def publish_frame(self):
        if self.cap is None:
            # Camera not available, try reopen every ~2s (timer fires at fps)
            self.consecutive_errors += 1
            if self.consecutive_errors % 30 == 0:
                self._reopen_camera()
            return

        ret, frame = self.cap.read()
        if not ret:
            self.error_count += 1
            self.consecutive_errors += 1
            if self.consecutive_errors <= 5 or self.consecutive_errors % 30 == 0:
                self.get_logger().warn(
                    f"Camera read failed (consecutive: {self.consecutive_errors})"
                )
            # After 30 consecutive failures (~2s at 15fps), reopen camera
            if self.consecutive_errors >= 30:
                self._reopen_camera()
            return

        # Reset consecutive counter on success
        self.consecutive_errors = 0

        # Encode to JPEG
        ok, buf = cv2.imencode(".jpg", frame,
                               [cv2.IMWRITE_JPEG_QUALITY, self.quality])
        if not ok:
            self.get_logger().warn("JPEG encode failed")
            return

        # Build message
        msg = CompressedImage()
        now = self.get_clock().now().to_msg()
        msg.header.stamp    = now
        msg.header.frame_id = "camera"
        msg.format          = "jpeg"
        msg.data            = buf.tobytes()

        self.pub.publish(msg)
        self.pub_raw_compressed.publish(msg)
        self.frame_count += 1

    def print_stats(self):
        elapsed = time.time() - self.t0
        fps = self.frame_count / elapsed if elapsed > 0 else 0
        self.get_logger().info(
            f"📊 Published: {self.frame_count} frames "
            f"({fps:.1f} fps avg), {self.error_count} errors"
        )

    def destroy_node(self):
        if self.cap:
            self.cap.release()
        super().destroy_node()


def main():
    rclpy.init()
    try:
        node = DirectCameraPublisher()
    except RuntimeError as e:
        print(f"[FATAL] {e}")
        sys.exit(1)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.get_logger().info("Shutting down camera publisher")
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()


