import os, asyncio, time, json
from typing import Dict, Any, List
import roslibpy
from gateway.waypoint import pose_stamped

USE_MQTT = os.getenv("USE_MQTT", "0") == "1"
MQTT_BROKER = os.getenv("MQTT_BROKER", "127.0.0.1")
MQTT_TOPIC = os.getenv("MQTT_TOPIC", "myagv/cmd_vel")
USE_ACTION = os.getenv("USE_ACTION", "0") == "1"
GOAL_FRAME = os.getenv("GOAL_FRAME", "map")

class MotionPublisher:
    def __init__(self, rosbridge_url: str, cmd_vel_topic: str):
        self.rosbridge_url = rosbridge_url
        self.cmd_vel_topic = cmd_vel_topic
        self._ros = None
        self._topic = None
        self._mqtt = None
        self.post_motion_hook = None  # callable(linear_x, angular_z, actual_duration)
        self._cancel_checker = None   # callable() -> bool, set by search to allow cancel during motion
        if USE_MQTT:
            try:
                import paho.mqtt.client as mqtt
                self._mqtt = mqtt.Client()
                self._mqtt.connect(MQTT_BROKER, 1883, 60)
                self._mqtt.loop_start()
            except Exception as e:
                print("MQTT init failed:", e)

    async def _ensure_ros(self):
        if self._ros and self._ros.is_connected:
            return
        host_port = self.rosbridge_url.replace("ws://","").split("/")[0]
        host, port = host_port.split(":")
        self._ros = roslibpy.Ros(host=host, port=int(port))
        self._ros.run()
        for _ in range(50):
            if self._ros.is_connected:
                break
            await asyncio.sleep(0.1)
        if not self._ros.is_connected:
            raise RuntimeError("Cannot connect to rosbridge")
        self._topic = roslibpy.Topic(self._ros, self.cmd_vel_topic, "geometry_msgs/Twist")

    def _twist(self, lx, az):
        return {"linear": {"x": lx, "y": 0.0, "z": 0.0}, "angular": {"x": 0.0, "y": 0.0, "z": az}}

    async def send_to_command_executor(self, intent: str, query_id: str = None):
        """Send command to /vora/command topic for Command Executor to handle"""
        await self._ensure_ros()
        
        if query_id is None:
            import uuid
            query_id = str(uuid.uuid4())[:8]
        
        vora_topic = roslibpy.Topic(self._ros, "/vora/command", "std_msgs/String")
        command_json = json.dumps({
            "intent": intent,
            "query_id": query_id
        })
        message = {"data": command_json}
        vora_topic.publish(roslibpy.Message(message))
        print(f"✅ Sent to /vora/command: {command_json}")
        await asyncio.sleep(0.5)  # Wait for command to be processed

    async def exec_motion(self, cmd: Dict[str, Any], obstacle_checker=None, post_motion_hook=None):
        # NEW: Send to Command Executor instead of direct /cmd_vel
        if cmd.get("type") == "stop":
            await self.send_to_command_executor("stop")
            return
        
        # For now, other motions still use direct /cmd_vel
        # TODO: Implement forward/backward/turn in command_executor
        if cmd.get("type") == "move":
            lx = float(cmd.get("linear_x", 0.0))
            az = float(cmd.get("angular_z", 0.0))
            duration = max(0.0, float(cmd.get("duration", 0.0)))
            
            print(f"🎯 Motion detected: linear_x={lx:.2f}, angular_z={az:.2f}, duration={duration:.2f}s")
            
            if USE_MQTT and self._mqtt:
                self._mqtt.publish(MQTT_TOPIC, json.dumps({"lx": lx, "az": az, "duration": duration}), qos=0, retain=False)
                return True
            
            await self._ensure_ros()
            
            # Minimum rotation duration — small angles (<15°) produce <0.3s
            # which is too short for Mecanum wheels to respond via WebSocket.
            MIN_ROTATE_DUR = 0.5  # seconds (5 messages minimum for rotation)
            if abs(az) > 0.01 and abs(lx) < 0.01 and duration < MIN_ROTATE_DUR:
                print(f"⚡ Rotation boosted: {duration:.2f}s → {MIN_ROTATE_DUR}s (min)")
                duration = MIN_ROTATE_DUR
            
            # Publish at 10Hz (every 0.1s) for the full duration
            # round() แทน int() เพื่อไม่สูญเสีย fractional time (~0.05-0.09s)
            loop_count = max(1, round(duration / 0.1))
            print(f"📡 Publishing {loop_count} messages at 10Hz for {duration}s")
            
            interrupted = False
            for i in range(loop_count):
                # Real-time obstacle interrupt: check LiDAR EVERY iteration (~0.1s)
                # Only applies to forward motion (lx > 0) — don't interrupt rotations or backward
                if obstacle_checker and lx > 0:
                    if obstacle_checker():
                        print(f"🛑 LIDAR INTERRUPT at step {i}/{loop_count} — obstacle detected during motion!")
                        interrupted = True
                        break
                
                # Cancel check: allow search cancellation to interrupt motion
                if self._cancel_checker and self._cancel_checker():
                    print(f"🛑 CANCEL INTERRUPT at step {i}/{loop_count}")
                    interrupted = True
                    break
                
                self._topic.publish(self._twist(lx, az))
                await asyncio.sleep(0.1)
            
            # Stop command — ส่งซ้ำ 3 ครั้ง (WebSocket→ROSBridge อาจ drop ได้)
            for _ in range(3):
                self._topic.publish(self._twist(0.0, 0.0))
                await asyncio.sleep(0.05)
            
            if interrupted:
                print(f"⚠️ Motion interrupted early by LiDAR at step {i}/{loop_count}")
                actual_dur = i * 0.1
            else:
                print(f"✅ Motion completed: {loop_count} messages sent")
                actual_dur = duration
            
            # Report motion to dead-reckoning hook (if provided)
            hook = post_motion_hook or self.post_motion_hook
            if hook:
                hook(lx, az, actual_dur)
            
            return not interrupted
        return False

    async def stop(self):
        if USE_MQTT and self._mqtt:
            self._mqtt.publish(MQTT_TOPIC, json.dumps({"lx":0.0,"az":0.0,"duration":0}), qos=0, retain=False)
            return True
        await self._ensure_ros()
        self._topic.publish(self._twist(0.0, 0.0))
        return True

class WaypointSender:
    def __init__(self, ros: roslibpy.Ros = None, frame_id: str = GOAL_FRAME):
        self._ros = ros
        self.frame_id = frame_id
        self._goal_topic = None
        self._action_client = None

    async def _ensure(self, ros: roslibpy.Ros):
        if self._ros is None:
            self._ros = ros

    async def send_via_topic(self, ros: roslibpy.Ros, waypoints: List[Dict]):
        await self._ensure(ros)
        if self._goal_topic is None:
            self._goal_topic = roslibpy.Topic(self._ros, "/move_base_simple/goal", "geometry_msgs/PoseStamped")
        for wp in waypoints:
            pose = pose_stamped(wp.get("x",0.0), wp.get("y",0.0), wp.get("theta",0.0), self.frame_id)
            self._goal_topic.publish(pose)
            await asyncio.sleep(0.5)

    async def send_via_action(self, ros: roslibpy.Ros, waypoints: List[Dict]):
        await self._ensure(ros)
        from roslibpy.actionlib import ActionClient, Goal
        if self._action_client is None:
            self._action_client = ActionClient(self._ros, "/move_base", "move_base_msgs/MoveBaseAction")
        for wp in waypoints:
            pose = pose_stamped(wp.get("x",0.0), wp.get("y",0.0), wp.get("theta",0.0), self.frame_id)
            goal = Goal(self._action_client, {"target_pose": pose})
            goal.send()
            goal.wait(60)
            goal.cancel()
            await asyncio.sleep(0.2)

# Singleton ROS connection สำหรับ LLM Planner (ป้องกัน connection leak)
_shared_ros: roslibpy.Ros = None

async def ensure_ros(rosbridge_url: str) -> roslibpy.Ros:
    """Singleton ROS connection — ป้องกัน connection leak ที่ทำให้ delay สะสม"""
    global _shared_ros
    if _shared_ros and _shared_ros.is_connected:
        return _shared_ros
    host_port = rosbridge_url.replace("ws://","").split("/")[0]
    host, port = host_port.split(":")
    _shared_ros = roslibpy.Ros(host=host, port=int(port))
    _shared_ros.run()
    for _ in range(50):
        if _shared_ros.is_connected:
            break
        await asyncio.sleep(0.1)
    if not _shared_ros.is_connected:
        _shared_ros = None
        raise RuntimeError("Cannot connect to rosbridge")
    return _shared_ros
