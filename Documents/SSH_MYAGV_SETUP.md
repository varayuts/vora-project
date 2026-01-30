# 🔐 SSH to MyAGV - Complete Setup Guide

**Date:** 30 มกราคม 2026  
**MyAGV IP:** `192.168.0.111`  
**User:** `er` (or your username)

---

## 📡 SSH Connection

```bash
# From your laptop/PC
ssh er@192.168.0.111

# If password required, enter MyAGV password
```

---

## 📦 Current Status

✅ **Working:**
- ROSBridge: ws://192.168.0.111:9090
- Command Executor: Listening on /vora/command
- Audio Stream: Connected to Gateway (192.168.0.60:9001)
- Static IP: 192.168.0.111

❌ **Issue:**
- MyAGV hardware driver (myagv_odometry) has no launch file
- Robot receives /cmd_vel but doesn't move → need motor controller

---

## 🎯 Session Goals

### Option 1: Test if /cmd_vel already works (Quick Test)

```bash
# After SSH to MyAGV:

# Test 1: Publish velocity manually
ros2 topic pub --rate 10 /cmd_vel geometry_msgs/Twist \
  "{linear: {x: 0.1, y: 0.0, z: 0.0}, angular: {x: 0.0, y: 0.0, z: 0.0}}"

# Press Ctrl+C after robot moves
```

**If robot moves:** Problem solved! Command Executor + /cmd_vel works!  
**If robot doesn't move:** Need to find/install motor controller

---

### Option 2: Find MyAGV Motor Controller

```bash
# Check what packages are available
ros2 pkg list | grep -i myagv
ros2 pkg list | grep -i motor
ros2 pkg list | grep -i base

# Check for serial devices (motor controller usually uses serial)
ls /dev/ttyUSB* /dev/ttyACM*

# Check if any node publishes to /cmd_vel
ros2 topic info /cmd_vel

# Find launch files in myagv_ros2
find ~/myagv_ros2 -name "*.launch.py" 2>/dev/null
```

---

### Option 3: Create Simple Motor Controller in vora_robot_bridge

**If MyAGV uses serial port:**

```bash
# Install dependencies
pip3 install pyserial

# Create new node: motor_controller.py
cd ~/vora_ws/src/vora_robot_bridge/vora_robot_bridge/
nano motor_controller.py
```

**Content:**
```python
#!/usr/bin/env python3
import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist
import serial
import struct

class MotorController(Node):
    def __init__(self):
        super().__init__('myagv_motor_controller')
        
        # Try to open serial port
        try:
            self.serial_port = serial.Serial('/dev/ttyUSB0', 115200, timeout=1)
            self.get_logger().info('✅ Connected to motor controller')
        except:
            self.get_logger().error('❌ Cannot open /dev/ttyUSB0')
            self.serial_port = None
        
        # Subscribe to /cmd_vel
        self.subscription = self.create_subscription(
            Twist,
            '/cmd_vel',
            self.cmd_vel_callback,
            10
        )
        
    def cmd_vel_callback(self, msg):
        if not self.serial_port:
            return
            
        # Convert Twist to motor commands
        linear_x = msg.linear.x  # Forward/backward
        angular_z = msg.angular.z  # Turn left/right
        
        # Send to motor controller (format depends on your MyAGV)
        # This is example - adjust for your robot!
        try:
            # Example format: send as bytes
            cmd = f"VEL:{linear_x:.2f},{angular_z:.2f}\n"
            self.serial_port.write(cmd.encode())
            self.get_logger().info(f'Sent: {cmd.strip()}')
        except Exception as e:
            self.get_logger().error(f'Error: {e}')

def main(args=None):
    rclpy.init(args=args)
    node = MotorController()
    rclpy.spin(node)
    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()
```

**Update setup.py:**
```bash
nano ~/vora_ws/src/vora_robot_bridge/setup.py
```

Add to `entry_points`:
```python
'console_scripts': [
    'command_executor = vora_robot_bridge.command_executor:main',
    'motor_controller = vora_robot_bridge.motor_controller:main',  # ADD THIS
],
```

**Build and test:**
```bash
cd ~/vora_ws
colcon build --packages-select vora_robot_bridge
source install/setup.bash
ros2 run vora_robot_bridge motor_controller
```

---

### Option 4: Use existing myagv_ros2 node

```bash
# Check executable in myagv_odometry
cd ~/myagv_ros2
source install/setup.bash
ros2 pkg executables myagv_odometry

# Try running directly
ros2 run myagv_odometry <node_name>

# Or check source code
ls ~/myagv_ros2/src/myagv_odometry/
cat ~/myagv_ros2/src/myagv_odometry/package.xml
```

---

## 🧪 Quick Test Commands

```bash
# 1. Check ROS topics
ros2 topic list

# 2. Check nodes
ros2 node list

# 3. Monitor /cmd_vel
ros2 topic echo /cmd_vel

# 4. Test manual velocity
ros2 topic pub --once /cmd_vel geometry_msgs/Twist \
  "{linear: {x: 0.1, y: 0.0, z: 0.0}, angular: {x: 0.0, y: 0.0, z: 0.0}}"

# 5. Check serial devices
ls -la /dev/ttyUSB* /dev/ttyACM* 2>/dev/null

# 6. Check permissions
groups  # Should see dialout group for serial access
```

---

## 📝 Information to Collect

Please check and provide:

```bash
# 1. MyAGV model/version
cat ~/myagv_ros2/README.md 2>/dev/null | head -20

# 2. Available packages
ros2 pkg list | grep myagv

# 3. Serial devices
ls /dev/tty* | grep -E "USB|ACM"

# 4. Launch files
find ~/myagv_ros2 -name "*.launch.py" -o -name "*.launch"

# 5. Executables
ros2 pkg executables myagv_odometry

# 6. Running nodes (after starting everything)
ros2 node list

# 7. Topic info
ros2 topic info /cmd_vel
```

---

## 🎯 Expected Outcome

**After Session:**
1. ✅ Identify motor controller method
2. ✅ Test /cmd_vel → robot movement
3. ✅ Update start_myagv.sh with correct startup sequence
4. ✅ Document MyAGV-specific configuration

---

## 📞 Quick Commands for Copy-Paste

```bash
# SSH to MyAGV
ssh er@192.168.0.111

# Quick diagnostic
echo "=== ROS2 Topics ===" && ros2 topic list && \
echo "=== ROS2 Nodes ===" && ros2 node list && \
echo "=== Serial Devices ===" && ls /dev/tty* | grep -E "USB|ACM" && \
echo "=== MyAGV Packages ===" && ros2 pkg list | grep myagv

# Test movement
ros2 topic pub --rate 10 /cmd_vel geometry_msgs/Twist \
  "{linear: {x: 0.1, y: 0.0, z: 0.0}, angular: {x: 0.0, y: 0.0, z: 0.0}}"
```

---

## 🚀 Start Current Working System

```bash
# Terminal 1: ROSBridge
ros2 launch rosbridge_server rosbridge_websocket_launch.xml

# Terminal 2: Command Executor
source ~/vora_ws/install/setup.bash
ros2 run vora_robot_bridge command_executor

# Terminal 3: Audio Stream
cd ~/Desktop/VORA_myAGV_only_ros2_package/new
python3 send_audio_to_gateway.py \
  --gateway-ws ws://192.168.0.60:9001/gw/audio \
  --device "ReSpeaker" \
  --rate 16000
```

**Or use script:**
```bash
cd ~/Desktop/VORA_myAGV_only_ros2_package/new
./start_myagv.sh 192.168.0.60
```

---

**Ready for SSH session!** 🔐
