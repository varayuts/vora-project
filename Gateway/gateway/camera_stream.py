"""
Camera Stream Module for VORA Gateway
======================================
Subscribe ROS2 /image_raw via ROSBridge and serve HTTP endpoints.

Endpoints:
- GET /camera/frame   → Single JPEG frame (for polling)
- GET /camera/mjpeg   → MJPEG stream (for <img> autorefresh)
- GET /camera/status  → Camera status

ROS Image Message (sensor_msgs/Image):
- encoding: "rgb8", "bgr8", or "jpeg"
- data: raw pixels or compressed
"""

import os
import io
import time
import asyncio
import base64
import logging
import threading
from typing import Optional
import roslibpy
from PIL import Image
import numpy as np

logger = logging.getLogger("camera")

# Configuration
ROSBRIDGE_URL = os.getenv("ROSBRIDGE", "ws://192.168.0.111:9090")
IMAGE_TOPIC = os.getenv("IMAGE_TOPIC", "/image_raw")
COMPRESSED_TOPIC = os.getenv("COMPRESSED_TOPIC", "/image_raw/compressed")
CAMERA_COMPRESSED_TOPIC = "/camera/compressed"  # ros_camera_pub.py primary topic
FRAME_WIDTH = 640
FRAME_HEIGHT = 480
JPEG_QUALITY = 80


class CameraStream:
    """
    Subscribe to ROS camera topic and provide frames for HTTP endpoints.
    """
    
    def __init__(self, rosbridge_url: str = ROSBRIDGE_URL):
        self.rosbridge_url = rosbridge_url
        self._ros: Optional[roslibpy.Ros] = None
        self._image_sub: Optional[roslibpy.Topic] = None
        self._compressed_sub: Optional[roslibpy.Topic] = None
        self._camera_compressed_sub: Optional[roslibpy.Topic] = None
        
        # Latest frame (JPEG bytes)
        self._latest_frame: Optional[bytes] = None
        self._frame_timestamp: float = 0
        self._frame_count: int = 0
        self._connected: bool = False
        self._running: bool = False
        
        # Lock for thread-safe frame access
        self._lock = threading.Lock()
    
    @property
    def is_connected(self) -> bool:
        return self._connected and self._ros is not None and self._ros.is_connected
    
    @property
    def frame_count(self) -> int:
        return self._frame_count
    
    def _connect_ros(self):
        """Connect to ROSBridge."""
        try:
            host_port = self.rosbridge_url.replace("ws://", "").split("/")[0]
            host, port = host_port.split(":")
            
            self._ros = roslibpy.Ros(host=host, port=int(port))
            self._ros.run()
            
            # Wait for connection
            for _ in range(50):
                if self._ros.is_connected:
                    break
                time.sleep(0.1)
            
            if self._ros.is_connected:
                self._connected = True
                logger.info(f"✅ Connected to ROSBridge: {self.rosbridge_url}")
                return True
            else:
                logger.error("❌ Failed to connect to ROSBridge")
                return False
                
        except Exception as e:
            logger.error(f"❌ ROSBridge connection error: {e}")
            return False
    
    def _on_image(self, msg: dict):
        """Callback for sensor_msgs/Image messages."""
        try:
            encoding = msg.get("encoding", "rgb8")
            width = msg.get("width", FRAME_WIDTH)
            height = msg.get("height", FRAME_HEIGHT)
            data = msg.get("data", "")
            
            # Decode base64 data
            if isinstance(data, str):
                raw_bytes = base64.b64decode(data)
            else:
                raw_bytes = bytes(data)
            
            jpeg_bytes = None
            
            # Convert to JPEG based on encoding
            if encoding in ["rgb8", "bgr8"]:
                # Raw RGB/BGR pixels (3 bytes per pixel)
                np_arr = np.frombuffer(raw_bytes, dtype=np.uint8)
                np_arr = np_arr.reshape((height, width, 3))
                
                if encoding == "bgr8":
                    np_arr = np_arr[:, :, ::-1]  # BGR to RGB
                
                img = Image.fromarray(np_arr, mode="RGB")
                buffer = io.BytesIO()
                img.save(buffer, format="JPEG", quality=JPEG_QUALITY)
                jpeg_bytes = buffer.getvalue()
                
            elif encoding in ["yuyv", "yuv422_yuy2", "yuyv422"]:
                # YUYV/YUY2 format (2 bytes per pixel on average)
                # Convert YUYV to RGB using PIL/numpy
                np_arr = np.frombuffer(raw_bytes, dtype=np.uint8)
                np_arr = np_arr.reshape((height, width, 2))
                
                # Extract Y, U, V components
                y = np_arr[:, :, 0].astype(np.float32)
                u = np_arr[:, 0::2, 1].repeat(2, axis=1).astype(np.float32) - 128
                v = np_arr[:, 1::2, 1].repeat(2, axis=1).astype(np.float32) - 128
                
                # YUV to RGB conversion
                r = np.clip(y + 1.402 * v, 0, 255).astype(np.uint8)
                g = np.clip(y - 0.344 * u - 0.714 * v, 0, 255).astype(np.uint8)
                b = np.clip(y + 1.772 * u, 0, 255).astype(np.uint8)
                
                rgb = np.stack([r, g, b], axis=-1)
                img = Image.fromarray(rgb, mode="RGB")
                buffer = io.BytesIO()
                img.save(buffer, format="JPEG", quality=JPEG_QUALITY)
                jpeg_bytes = buffer.getvalue()
                
            elif encoding in ["jpeg", "compressed", "mjpeg"]:
                # Already compressed
                jpeg_bytes = raw_bytes
                
            else:
                if self._frame_count == 0:
                    logger.warning(f"⚠️ Unsupported encoding: {encoding}, trying raw conversion...")
                # Try to handle as raw RGB anyway
                try:
                    np_arr = np.frombuffer(raw_bytes, dtype=np.uint8)
                    np_arr = np_arr.reshape((height, width, 3))
                    img = Image.fromarray(np_arr, mode="RGB")
                    buffer = io.BytesIO()
                    img.save(buffer, format="JPEG", quality=JPEG_QUALITY)
                    jpeg_bytes = buffer.getvalue()
                except Exception:
                    return
            
            if jpeg_bytes is None:
                return
            
            # Store frame
            with self._lock:
                self._latest_frame = jpeg_bytes
                self._frame_timestamp = time.time()
                self._frame_count += 1
            
            if self._frame_count == 1:
                logger.info(f"📷 First frame received! encoding={encoding}, size={len(jpeg_bytes)} bytes")
            elif self._frame_count % 300 == 0:
                logger.info(f"📷 Frame #{self._frame_count} ({len(jpeg_bytes)} bytes, {encoding})")
                
        except Exception as e:
            logger.error(f"❌ Frame processing error: {e}")
    
    def _on_compressed_image(self, msg: dict):
        """Callback for sensor_msgs/CompressedImage messages."""
        try:
            data = msg.get("data", "")
            format_type = msg.get("format", "jpeg")
            
            if isinstance(data, str):
                jpeg_bytes = base64.b64decode(data)
            else:
                jpeg_bytes = bytes(data)
            
            if len(jpeg_bytes) == 0:
                return
            
            with self._lock:
                self._latest_frame = jpeg_bytes
                self._frame_timestamp = time.time()
                self._frame_count += 1
            
            if self._frame_count == 1:
                logger.info(f"📷 First compressed frame! size={len(jpeg_bytes)} bytes, format={format_type}")
            elif self._frame_count % 300 == 0:
                logger.info(f"📷 Frame #{self._frame_count} ({len(jpeg_bytes)} bytes, {format_type})")
                
        except Exception as e:
            logger.error(f"❌ Compressed frame error: {e}")
    
    def start(self):
        """Start camera subscription in background thread with auto-retry."""
        if self._running:
            return
        
        self._running = True
        
        def _run():
            retry_delay = 5  # seconds
            max_retry_delay = 30
            
            while self._running:
                # Try to connect
                if not self._connect_ros():
                    logger.warning(f"⏳ Camera will retry in {retry_delay}s...")
                    time.sleep(retry_delay)
                    retry_delay = min(retry_delay * 1.5, max_retry_delay)
                    continue
                
                # Reset retry delay on success
                retry_delay = 5
                
                # Subscribe to BOTH topics - use whichever works
                # Raw topic (handles yuyv, rgb8, bgr8)
                try:
                    self._image_sub = roslibpy.Topic(
                        self._ros, IMAGE_TOPIC, "sensor_msgs/Image"
                    )
                    self._image_sub.subscribe(self._on_image)
                    logger.info(f"📷 Subscribed to {IMAGE_TOPIC} (raw)")
                except Exception as e:
                    logger.warning(f"⚠️ Could not subscribe to raw topic: {e}")
                
                # Compressed topic: /image_raw/compressed
                try:
                    self._compressed_sub = roslibpy.Topic(
                        self._ros, COMPRESSED_TOPIC, "sensor_msgs/CompressedImage"
                    )
                    self._compressed_sub.subscribe(self._on_compressed_image)
                    logger.info(f"📷 Subscribed to {COMPRESSED_TOPIC} (compressed)")
                except Exception as e:
                    logger.warning(f"⚠️ Could not subscribe to compressed topic: {e}")
                
                # Also subscribe to /camera/compressed (ros_camera_pub.py publishes here)
                try:
                    self._camera_compressed_sub = roslibpy.Topic(
                        self._ros, CAMERA_COMPRESSED_TOPIC, "sensor_msgs/CompressedImage"
                    )
                    self._camera_compressed_sub.subscribe(self._on_compressed_image)
                    logger.info(f"📷 Subscribed to {CAMERA_COMPRESSED_TOPIC} (camera compressed)")
                except Exception as e:
                    logger.warning(f"⚠️ Could not subscribe to camera compressed topic: {e}")
                
                # Keep thread alive and monitor connection
                while self._running and self.is_connected:
                    time.sleep(1)
                
                # Connection lost - cleanup and retry
                if self._running:
                    logger.warning("📷 Camera connection lost, reconnecting...")
                    self._cleanup_ros()
                    time.sleep(2)
        
        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        logger.info("🎥 Camera stream started (with auto-retry)")
    
    def _cleanup_ros(self):
        """Cleanup ROS connections for reconnect."""
        try:
            if self._image_sub:
                self._image_sub.unsubscribe()
                self._image_sub = None
            if self._compressed_sub:
                self._compressed_sub.unsubscribe()
                self._compressed_sub = None
            if self._camera_compressed_sub:
                self._camera_compressed_sub.unsubscribe()
                self._camera_compressed_sub = None
            if self._ros:
                try:
                    self._ros.close()
                except:
                    pass
                self._ros = None
            self._connected = False
        except Exception as e:
            logger.debug(f"Cleanup error: {e}")
    
    def stop(self):
        """Stop camera subscription."""
        self._running = False
        
        if self._image_sub:
            self._image_sub.unsubscribe()
        if self._compressed_sub:
            self._compressed_sub.unsubscribe()
        if self._camera_compressed_sub:
            self._camera_compressed_sub.unsubscribe()
        if self._ros and self._ros.is_connected:
            self._ros.close()
        
        self._connected = False
        logger.info("🛑 Camera stream stopped")
    
    def get_frame(self) -> Optional[bytes]:
        """Get latest frame as JPEG bytes."""
        with self._lock:
            return self._latest_frame
    
    def get_frame_age(self) -> float:
        """Get age of latest frame in seconds."""
        with self._lock:
            if self._frame_timestamp == 0:
                return float('inf')
            return time.time() - self._frame_timestamp
    
    def get_status(self) -> dict:
        """Get camera status."""
        with self._lock:
            return {
                "connected": self.is_connected,
                "running": self._running,
                "frame_count": self._frame_count,
                "frame_age_ms": round(self.get_frame_age() * 1000, 1) if self._frame_timestamp > 0 else None,
                "has_frame": self._latest_frame is not None,
                "frame_size": len(self._latest_frame) if self._latest_frame else 0,
                "rosbridge": self.rosbridge_url,
                "topic": IMAGE_TOPIC,
            }


# Global camera instance
_camera: Optional[CameraStream] = None


def get_camera() -> CameraStream:
    """Get or create global camera instance."""
    global _camera
    if _camera is None:
        _camera = CameraStream()
    return _camera


def start_camera():
    """Start camera stream."""
    cam = get_camera()
    if not cam._running:
        cam.start()
    return cam


def stop_camera():
    """Stop camera stream."""
    global _camera
    if _camera:
        _camera.stop()
        _camera = None
