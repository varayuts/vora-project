#!/usr/bin/env python3
"""
zone_mapper.py — VORA Zone Mapping Tool
========================================

Interactive tool to define semantic zones in the robot's environment.
Zones are used by object_memory to prioritize visual search directions.

Two modes:
  interactive — Connect to robot via ROSBridge, drive to each zone, mark it
  manual      — Define zones by typing names + heading angles (no robot needed)

Output:
  maps/zone_map.json   — Zone definitions (for object_memory & search planner)
  maps/zones/*.jpg     — Reference images per zone (interactive mode only)
  maps/ZONE_MAP.md     — Markdown visualization of the room map

Usage:
  python3 zone_mapper.py                                       # Interactive
  python3 zone_mapper.py --manual                              # Manual (no robot)
  python3 zone_mapper.py --rosbridge ws://192.168.0.111:9090   # Custom ROSBridge
  python3 zone_mapper.py --load                                # View/edit existing map

Requirements (interactive mode only):
  pip install roslibpy

Convention:
  - 0° = robot's forward direction at home position
  - Positive angle = counterclockwise (left)
  - Negative angle = clockwise (right)
  - Heading is always normalized to 0–360°
"""

import json
import os
import sys
import time
import math
import base64
import argparse
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict

# ═══════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════
ROSBRIDGE_DEFAULT = os.getenv("ROSBRIDGE", "ws://192.168.0.111:9090")
MAPS_DIR = Path(__file__).parent / "maps"
ZONES_IMG_DIR = MAPS_DIR / "zones"
ZONE_MAP_FILE = MAPS_DIR / "zone_map.json"
ZONE_MAP_MD = MAPS_DIR / "ZONE_MAP.md"

# Robot motion (same as Gateway/gateway/main.py)
ANGULAR_SPEED = 0.50     # rad/s
LINEAR_SPEED  = 0.10     # m/s
ROTATION_CAL  = 0.87     # calibration factor for MyAGV 2023 Mecanum

# ═══════════════════════════════════════════════════════
# Terminal Colors
# ═══════════════════════════════════════════════════════
class C:
    RED    = "\033[91m"
    GREEN  = "\033[92m"
    YELLOW = "\033[93m"
    BLUE   = "\033[94m"
    CYAN   = "\033[96m"
    BOLD   = "\033[1m"
    DIM    = "\033[2m"
    RESET  = "\033[0m"

def _ok(msg):   print(f"{C.GREEN}✅ {msg}{C.RESET}")
def _warn(msg): print(f"{C.YELLOW}⚠️  {msg}{C.RESET}")
def _err(msg):  print(f"{C.RED}❌ {msg}{C.RESET}")
def _info(msg): print(f"{C.BLUE}ℹ️  {msg}{C.RESET}")
def _head(msg):
    bar = "═" * 56
    print(f"\n{C.CYAN}{bar}{C.RESET}")
    print(f"{C.CYAN}{C.BOLD}  {msg}{C.RESET}")
    print(f"{C.CYAN}{bar}{C.RESET}")


# ═══════════════════════════════════════════════════════
# Zone Mapper Core
# ═══════════════════════════════════════════════════════
class ZoneMapper:
    """Manages zone definitions and robot interaction."""

    def __init__(self, rosbridge_url: str = ROSBRIDGE_DEFAULT):
        self.rosbridge_url = rosbridge_url
        self.ros = None
        self.cmd_vel_topic = None
        self.connected = False

        # Heading tracking
        self.current_heading_deg = 0.0   # degrees from home (0–360)
        self.heading_source = "estimated" # "odom" if /odom available
        self._odom_initial_yaw = None    # first odom yaw (to compute relative heading)

        # Camera
        self.last_frame_b64: Optional[str] = None
        self.last_frame_time: float = 0.0

        # Map data
        self.zones: List[Dict] = []
        self.room_name = "Lab"
        self.room_size = ""          # กรอกเองตอน setup (ไม่บังคับ)
        self.room_description = ""
        self.home_description = "ตำแหน่งเริ่มต้นของหุ่นยนต์"

    # ─── ROSBridge Connection ────────────────────────
    def connect(self) -> bool:
        """Connect to robot via ROSBridge."""
        try:
            import roslibpy
        except ImportError:
            _err("roslibpy not installed — run: pip install roslibpy")
            _info("Or use --manual mode to define zones without robot")
            return False

        url = self.rosbridge_url
        try:
            host_port = url.replace("ws://", "").replace("wss://", "").split("/")[0]
            host, port = host_port.split(":")
        except ValueError:
            _err(f"Invalid ROSBridge URL: {url}  (expected ws://host:port)")
            return False

        _info(f"Connecting to ROSBridge at {url} ...")
        try:
            self.ros = roslibpy.Ros(host=host, port=int(port))
            self.ros.run()

            for _ in range(50):
                if self.ros.is_connected:
                    break
                time.sleep(0.1)

            if not self.ros.is_connected:
                _err("Timeout — ROSBridge did not respond")
                return False

            _ok("Connected to ROSBridge")
            self.connected = True

            # /cmd_vel publisher
            self.cmd_vel_topic = roslibpy.Topic(
                self.ros, "/cmd_vel", "geometry_msgs/Twist")
            _ok("Publisher ready: /cmd_vel")

            # /odom subscriber (optional — for accurate heading)
            self._subscribe_odom(roslibpy)

            # /camera/compressed subscriber (optional — for reference images)
            self._subscribe_camera(roslibpy)

            return True

        except Exception as e:
            _err(f"Connection failed: {e}")
            return False

    def _subscribe_odom(self, roslibpy):
        """Try to subscribe to /odom for heading tracking."""
        try:
            topic = roslibpy.Topic(self.ros, "/odom", "nav_msgs/Odometry")
            topic.subscribe(self._on_odom)
            self.heading_source = "odom"
            _ok("Subscribed to /odom (heading from odometry)")
        except Exception as e:
            _warn(f"/odom not available ({e}), using estimated heading from cmd_vel")
            self.heading_source = "estimated"

    def _subscribe_camera(self, roslibpy):
        """Try to subscribe to /camera/compressed for reference images."""
        try:
            topic = roslibpy.Topic(
                self.ros, "/camera/compressed", "sensor_msgs/CompressedImage")
            topic.subscribe(self._on_camera)
            _ok("Subscribed to /camera/compressed (reference images)")
        except Exception as e:
            _warn(f"Camera not available ({e}), no reference images")

    def _on_odom(self, msg):
        """Extract yaw from /odom quaternion → update heading."""
        try:
            q = msg["pose"]["pose"]["orientation"]
            siny = 2.0 * (q["w"] * q["z"] + q["x"] * q["y"])
            cosy = 1.0 - 2.0 * (q["y"] ** 2 + q["z"] ** 2)
            yaw_rad = math.atan2(siny, cosy)
            yaw_deg = math.degrees(yaw_rad)

            if self._odom_initial_yaw is None:
                self._odom_initial_yaw = yaw_deg

            # Relative heading from start
            self.current_heading_deg = (yaw_deg - self._odom_initial_yaw) % 360
        except (KeyError, TypeError):
            pass

    def _on_camera(self, msg):
        """Store latest compressed camera frame."""
        try:
            self.last_frame_b64 = msg.get("data", "")
            self.last_frame_time = time.time()
        except Exception:
            pass

    def disconnect(self):
        """Disconnect from ROSBridge."""
        if self.ros and self.ros.is_connected:
            try:
                self.ros.terminate()
            except Exception:
                pass
        self.connected = False

    # ─── Robot Motion ────────────────────────────────
    def _publish_twist(self, linear_x: float, angular_z: float, duration: float):
        """Publish cmd_vel for a duration at ~10Hz, then stop."""
        if not self.connected:
            _warn("Not connected — motion skipped")
            return

        import roslibpy
        twist = {
            "linear":  {"x": linear_x, "y": 0.0, "z": 0.0},
            "angular": {"x": 0.0, "y": 0.0, "z": angular_z},
        }

        loops = max(1, round(duration / 0.1))
        for _ in range(loops):
            self.cmd_vel_topic.publish(roslibpy.Message(twist))
            time.sleep(0.1)

        # Stop (send 3× for reliability over WebSocket)
        stop_msg = {
            "linear":  {"x": 0.0, "y": 0.0, "z": 0.0},
            "angular": {"x": 0.0, "y": 0.0, "z": 0.0},
        }
        for _ in range(3):
            self.cmd_vel_topic.publish(roslibpy.Message(stop_msg))
            time.sleep(0.05)

        # If not using odom, estimate heading from angular_z
        if self.heading_source == "estimated":
            angle_deg = math.degrees(angular_z) * duration
            self.current_heading_deg = (self.current_heading_deg + angle_deg) % 360

    def rotate(self, degrees: float):
        """Rotate by a given number of degrees (positive=left/CCW)."""
        angle_rad = math.radians(abs(degrees))
        duration = (angle_rad / ANGULAR_SPEED) * ROTATION_CAL
        direction = 1.0 if degrees > 0 else -1.0
        az = ANGULAR_SPEED * direction

        print(f"  🔄 Rotating {degrees:+.1f}° "
              f"(ω={az:+.2f} rad/s, duration={duration:.2f}s, cal={ROTATION_CAL})")
        self._publish_twist(0.0, az, duration)
        time.sleep(0.3)  # let frame settle

    def move(self, meters: float):
        """Move forward (positive) or backward (negative)."""
        duration = abs(meters) / LINEAR_SPEED
        lx = LINEAR_SPEED if meters > 0 else -LINEAR_SPEED
        direction = "forward" if meters > 0 else "backward"
        print(f"  🚗 Moving {direction} ~{abs(meters):.2f}m "
              f"(speed={LINEAR_SPEED} m/s, duration={duration:.2f}s)")
        self._publish_twist(lx, 0.0, duration)
        time.sleep(0.3)

    def stop(self):
        """Emergency stop."""
        if self.connected:
            import roslibpy
            stop_msg = {
                "linear":  {"x": 0.0, "y": 0.0, "z": 0.0},
                "angular": {"x": 0.0, "y": 0.0, "z": 0.0},
            }
            for _ in range(5):
                self.cmd_vel_topic.publish(roslibpy.Message(stop_msg))
                time.sleep(0.05)
        print("  🛑 STOP")

    # ─── Camera ──────────────────────────────────────
    def capture_image(self, zone_name: str) -> Optional[str]:
        """Save latest camera frame as reference image for a zone.
        Returns relative path from maps/ dir, or None."""
        if not self.last_frame_b64:
            _warn("No camera frame available")
            return None
        if (time.time() - self.last_frame_time) > 10.0:
            _warn("Camera frame is stale (>10s old)")

        ZONES_IMG_DIR.mkdir(parents=True, exist_ok=True)
        safe_name = zone_name.replace(" ", "_").replace("/", "_")
        img_path = ZONES_IMG_DIR / f"{safe_name}.jpg"

        try:
            img_bytes = base64.b64decode(self.last_frame_b64)
            img_path.write_bytes(img_bytes)
            rel_path = f"zones/{safe_name}.jpg"
            _ok(f"Reference image saved: {img_path}")
            return rel_path
        except Exception as e:
            _warn(f"Failed to save image: {e}")
            return None

    # ─── Zone Management ─────────────────────────────
    def add_zone(self, name: str, heading_deg: float,
                 description: str = "", ref_image: str = "",
                 expected_objects: List[str] = None):
        """Add a zone to the map."""
        heading_deg = round(heading_deg % 360, 1)

        zone = {
            "name": name,
            "heading_deg": heading_deg,
            "description": description,
            "ref_image": ref_image,
            "expected_objects": expected_objects or [],
            "created": datetime.now().isoformat(),
        }
        self.zones.append(zone)
        _ok(f"Zone added: {name} @ {heading_deg}°"
            + (f" — {description}" if description else ""))

    def remove_zone(self, name: str) -> bool:
        """Remove a zone by name."""
        before = len(self.zones)
        self.zones = [z for z in self.zones if z["name"] != name]
        removed = len(self.zones) < before
        if removed:
            _ok(f"Removed zone: {name}")
        else:
            _warn(f"Zone not found: {name}")
        return removed

    def list_zones(self):
        """Print all defined zones."""
        if not self.zones:
            _warn("No zones defined yet")
            return
        print(f"\n{'#':<4} {'Zone':<16} {'Heading':>8} {'Description':<30} {'Objects'}")
        print("─" * 80)
        for i, z in enumerate(self.zones, 1):
            objs = ", ".join(z.get("expected_objects", []))
            print(f"{i:<4} {z['name']:<16} {z['heading_deg']:>7.1f}° "
                  f"{z.get('description', ''):<30} {objs}")
        print()

    # ─── Save / Load ─────────────────────────────────
    def save(self):
        """Save zone map to JSON + generate markdown."""
        MAPS_DIR.mkdir(parents=True, exist_ok=True)

        zone_map = {
            "version": 1,
            "created": datetime.now().isoformat(),
            "room": {
                "name": self.room_name,
                "size": self.room_size,
                "description": self.room_description,
            },
            "home": {
                "heading_deg": 0.0,
                "description": self.home_description,
            },
            "robot": {
                "angular_speed": ANGULAR_SPEED,
                "linear_speed": LINEAR_SPEED,
                "rotation_cal": ROTATION_CAL,
            },
            "zones": self.zones,
        }

        ZONE_MAP_FILE.write_text(
            json.dumps(zone_map, indent=2, ensure_ascii=False), encoding="utf-8")
        _ok(f"Saved zone map: {ZONE_MAP_FILE}")

        # Generate markdown
        md = generate_zone_markdown(zone_map)
        ZONE_MAP_MD.write_text(md, encoding="utf-8")
        _ok(f"Saved markdown map: {ZONE_MAP_MD}")

        return zone_map

    def load(self) -> bool:
        """Load existing zone map from JSON."""
        if not ZONE_MAP_FILE.exists():
            _warn(f"No zone map found at {ZONE_MAP_FILE}")
            return False

        try:
            data = json.loads(ZONE_MAP_FILE.read_text(encoding="utf-8"))
            room = data.get("room", {})
            self.room_name = room.get("name", "Lab")
            self.room_size = room.get("size", "")
            self.room_description = room.get("description", "")
            self.home_description = data.get("home", {}).get("description", "")
            self.zones = data.get("zones", [])
            _ok(f"Loaded {len(self.zones)} zones from {ZONE_MAP_FILE}")
            return True
        except Exception as e:
            _err(f"Failed to load zone map: {e}")
            return False


# ═══════════════════════════════════════════════════════
# Markdown Map Generator
# ═══════════════════════════════════════════════════════
def _heading_to_quadrant(deg: float) -> str:
    """Map heading to compass quadrant label."""
    deg = deg % 360
    if deg < 22.5 or deg >= 337.5:
        return "Front"
    elif deg < 67.5:
        return "Front-Left"
    elif deg < 112.5:
        return "Left"
    elif deg < 157.5:
        return "Back-Left"
    elif deg < 202.5:
        return "Back"
    elif deg < 247.5:
        return "Back-Right"
    elif deg < 292.5:
        return "Right"
    else:
        return "Front-Right"


def _place_zone_on_grid(zones: List[Dict], grid_radius: int = 6):
    """Place zones on a 2D text grid based on heading."""
    # Grid is (2*radius+1) x (2*radius+1), center = robot
    size = 2 * grid_radius + 1
    grid = [[" " for _ in range(size)] for _ in range(size)]
    cx, cy = grid_radius, grid_radius

    # Robot at center
    grid[cy][cx] = "🤖"

    for z in zones:
        h = math.radians(z["heading_deg"])
        # ROS convention: 0°=forward(+X), 90°=left(+Y)
        # Grid convention: row 0=top=forward, col increases right
        dx = -math.sin(h)  # grid col offset (right = positive)
        dy = -math.cos(h)  # grid row offset (forward/up = negative row)

        r = grid_radius - 1  # distance from center on grid
        col = cx + round(dx * r)
        row = cy + round(dy * r)

        col = max(1, min(size - 2, col))
        row = max(1, min(size - 2, row))

        # Place zone label (first 6 chars)
        label = z["name"][:6]
        for i, ch in enumerate(label):
            c = col - len(label) // 2 + i
            if 0 <= c < size:
                grid[row][c] = ch

    return grid


def generate_zone_markdown(zone_map: dict) -> str:
    """Generate a complete markdown document for the zone map."""
    room = zone_map.get("room", {})
    zones = zone_map.get("zones", [])
    robot = zone_map.get("robot", {})

    room_name = room.get('name', 'Unknown')
    room_size = room.get('size', '')
    size_str = f" ({room_size}m)" if room_size else ""

    lines = []
    lines.append(f"# 🗺️ VORA Zone Map — {room_name}")
    lines.append("")
    lines.append(f"**Room:** {room_name}{size_str}")
    if room.get("description"):
        lines.append(f"**Description:** {room['description']}")
    lines.append(f"**Created:** {zone_map.get('created', '?')}")
    lines.append(f"**Rotation Calibration:** {robot.get('rotation_cal', ROTATION_CAL)}")
    lines.append(f"**Angular Speed:** {robot.get('angular_speed', ANGULAR_SPEED)} rad/s")
    lines.append("")

    # ─── ASCII compass map ───────────────────────────
    lines.append("---")
    lines.append("")
    lines.append("## Zone Layout (Top-down View)")
    lines.append("")
    lines.append("```")
    lines.append(f"  Room: {room_name}{size_str}")
    lines.append(f"  Robot heading convention: 0°=Forward, 90°=Left, 180°=Back, 270°=Right")
    lines.append("")

    # Build compass-style map
    # Sort zones by heading
    sorted_zones = sorted(zones, key=lambda z: z["heading_deg"])

    # Simple compass layout
    # Categorize zones into 8 sectors
    sectors = {q: [] for q in [
        "Front", "Front-Left", "Left", "Back-Left",
        "Back", "Back-Right", "Right", "Front-Right"
    ]}
    for z in sorted_zones:
        q = _heading_to_quadrant(z["heading_deg"])
        sectors[q].append(z)

    def _sector_label(sector_zones):
        if not sector_zones:
            return "·  ·  ·"
        return " | ".join(f"{z['name']}({z['heading_deg']:.0f}°)"
                         for z in sector_zones)

    w = 44  # box width
    lines.append(f"{'Front (0°)':^{w}}")
    lines.append(f"{'│':^{w}}")

    fl = _sector_label(sectors["Front-Left"])
    fr = _sector_label(sectors["Front-Right"])
    f_label = _sector_label(sectors["Front"])
    lines.append(f"  ┌{'─' * (w - 4)}┐")

    if sectors["Front"]:
        lines.append(f"  │{f_label:^{w - 4}}│")
    lines.append(f"  │{' ' * ((w - 4) // 2 - len(fl) // 2)}{fl}"
                 f"{' ' * max(1, (w - 4) - len(fl) - (w - 4) // 2 + len(fl) // 2 - len(fr))}{fr}"
                 f"{'│':>1}" if (fl or fr) else f"  │{' ' * (w - 4)}│")

    lines.append(f"  │{' ' * (w - 4)}│")

    l_label = _sector_label(sectors["Left"])
    r_label = _sector_label(sectors["Right"])
    robot_line = f"  │{l_label}{'':>{max(1, (w - 4) // 2 - len(l_label))}}" \
                 f"🤖" \
                 f"{'':>{max(1, (w - 4) // 2 - len(r_label) - 1)}}{r_label}│"
    lines.append(f"{'Left (90°)':>14}  ──{robot_line}── {'Right (270°)'}")

    lines.append(f"  │{' ' * (w - 4)}│")

    bl = _sector_label(sectors["Back-Left"])
    br = _sector_label(sectors["Back-Right"])
    b_label = _sector_label(sectors["Back"])

    if bl or br:
        lines.append(f"  │  {bl}{' ' * max(1, (w - 6) - len(bl) - len(br))}{br}  │")
    if sectors["Back"]:
        lines.append(f"  │{b_label:^{w - 4}}│")

    lines.append(f"  └{'─' * (w - 4)}┘")
    lines.append(f"{'│':^{w}}")
    lines.append(f"{'Back (180°)':^{w}}")
    lines.append("```")
    lines.append("")

    # ─── Zone Table ──────────────────────────────────
    lines.append("---")
    lines.append("")
    lines.append("## Zone Details")
    lines.append("")
    lines.append("| # | Zone | Heading | Sector | Description | Expected Objects | Ref Image |")
    lines.append("|---|------|---------|--------|-------------|-----------------|-----------|")
    for i, z in enumerate(sorted_zones, 1):
        q = _heading_to_quadrant(z["heading_deg"])
        objs = ", ".join(z.get("expected_objects", [])) or "—"
        img = f"![{z['name']}]({z['ref_image']})" if z.get("ref_image") else "—"
        desc = z.get("description", "") or "—"
        lines.append(f"| {i} | **{z['name']}** | {z['heading_deg']:.1f}° | "
                     f"{q} | {desc} | {objs} | {img} |")
    lines.append("")

    # ─── Search Priority ─────────────────────────────
    lines.append("---")
    lines.append("")
    lines.append("## Search Scan Order (from home)")
    lines.append("")
    lines.append("When looking for an object, robot should scan zones in this order")
    lines.append("(sorted by heading from current direction):")
    lines.append("")
    for i, z in enumerate(sorted_zones, 1):
        objs = z.get("expected_objects", [])
        obj_str = f" → likely: {', '.join(objs)}" if objs else ""
        lines.append(f"{i}. **{z['name']}** ({z['heading_deg']:.0f}°){obj_str}")
    lines.append("")

    # ─── Rotation Commands ───────────────────────────
    lines.append("---")
    lines.append("")
    lines.append("## Rotation Commands (for reference)")
    lines.append("")
    lines.append("| From → To | Rotation | Duration (cal={:.2f}) |".format(
        robot.get("rotation_cal", ROTATION_CAL)))
    lines.append("|-----------|----------|----------------------|")

    prev_heading = 0.0
    for z in sorted_zones:
        diff = z["heading_deg"] - prev_heading
        if diff > 180:
            diff -= 360
        elif diff < -180:
            diff += 360
        dur = (math.radians(abs(diff)) / ANGULAR_SPEED) * ROTATION_CAL
        direction = "← (left)" if diff > 0 else "→ (right)"
        lines.append(f"| Home → **{z['name']}** | {diff:+.1f}° {direction} | {dur:.2f}s |")
        prev_heading = z["heading_deg"]
    lines.append("")

    # ─── Config for object_memory ────────────────────
    lines.append("---")
    lines.append("")
    lines.append("## Zone Config for object_memory.py")
    lines.append("")
    lines.append("Copy this into your memory/search config:")
    lines.append("")
    lines.append("```python")
    lines.append("# Auto-generated by zone_mapper.py")
    lines.append(f"# Room: {room_name}{size_str}")
    lines.append(f"# Created: {zone_map.get('created')}")
    lines.append("")
    lines.append("ZONE_HEADINGS = {")
    for z in sorted_zones:
        lines.append(f'    "{z["name"]}": {z["heading_deg"]:.1f},  '
                     f'# {z.get("description", "")}')
    lines.append("}")
    lines.append("")
    lines.append("ZONE_EXPECTED_OBJECTS = {")
    for z in sorted_zones:
        objs = z.get("expected_objects", [])
        if objs:
            lines.append(f'    "{z["name"]}": {objs},')
    lines.append("}")
    lines.append("```")
    lines.append("")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════
# Interactive Session (with robot)
# ═══════════════════════════════════════════════════════
def interactive_session(mapper: ZoneMapper):
    """Drive the robot, mark zones interactively."""
    _head("🤖 Interactive Zone Mapping")
    print("""
Controls:
  a / d      — Rotate left / right  15°
  A / D      — Rotate left / right  45°
  q / e      — Rotate left / right  90°
  w / s      — Move forward / backward  0.15m
  W / S      — Move forward / backward  0.30m
  r <deg>    — Rotate exact degrees (e.g. r 35)
  SPACE      — Stop robot
  ---
  m          — Mark current heading as a new zone
  l          — List all zones
  x <name>   — Remove a zone
  h          — Show current heading
  ---
  save       — Save and exit
  quit       — Quit without saving
""")

    _setup_room(mapper)
    _info(f"Heading source: {mapper.heading_source}")
    print()

    while True:
        heading = mapper.current_heading_deg
        try:
            cmd = input(f"{C.BOLD}[{heading:6.1f}°] ▶ {C.RESET}").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not cmd:
            continue

        # Motion commands
        if cmd == "a":
            mapper.rotate(15)
        elif cmd == "d":
            mapper.rotate(-15)
        elif cmd == "A":
            mapper.rotate(45)
        elif cmd == "D":
            mapper.rotate(-45)
        elif cmd == "q":
            mapper.rotate(90)
        elif cmd == "e":
            mapper.rotate(-90)
        elif cmd == "w":
            mapper.move(0.15)
        elif cmd == "s":
            mapper.move(-0.15)
        elif cmd == "W":
            mapper.move(0.30)
        elif cmd == "S":
            mapper.move(-0.30)
        elif cmd.startswith("r "):
            try:
                deg = float(cmd.split(None, 1)[1])
                mapper.rotate(deg)
            except ValueError:
                _warn("Usage: r <degrees>  (e.g. r 35)")
        elif cmd == " " or cmd == "stop":
            mapper.stop()

        # Zone commands
        elif cmd == "m":
            _mark_zone(mapper)
        elif cmd == "l":
            mapper.list_zones()
        elif cmd.startswith("x "):
            name = cmd.split(None, 1)[1]
            mapper.remove_zone(name)
        elif cmd == "h":
            _info(f"Current heading: {mapper.current_heading_deg:.1f}° "
                  f"({_heading_to_quadrant(mapper.current_heading_deg)}) "
                  f"[source: {mapper.heading_source}]")

        # Save/Quit
        elif cmd == "save":
            mapper.save()
            break
        elif cmd in ("quit", "exit"):
            ans = input("Quit without saving? (y/N) ").strip().lower()
            if ans == "y":
                break
        else:
            _warn(f"Unknown command: {cmd}")


def _mark_zone(mapper: ZoneMapper):
    """Prompt user to define a zone at current heading."""
    heading = mapper.current_heading_deg
    print(f"\n  📍 Marking zone at heading {heading:.1f}° "
          f"({_heading_to_quadrant(heading)})")

    name = input("  Zone name (e.g. DeskA, Shelf, DoorArea): ").strip()
    if not name:
        _warn("Cancelled — no name given")
        return

    # Check duplicate
    for z in mapper.zones:
        if z["name"].lower() == name.lower():
            _warn(f"Zone '{name}' already exists. Remove it first with: x {name}")
            return

    desc = input("  Description (Thai/English): ").strip()
    objects_str = input("  Expected objects (comma-separated, e.g. pen,card): ").strip()
    expected_objects = [o.strip() for o in objects_str.split(",") if o.strip()] \
        if objects_str else []

    # Try to capture reference image
    ref_image = None
    if mapper.connected and mapper.last_frame_b64:
        ans = input("  📷 Capture reference image? (Y/n) ").strip().lower()
        if ans != "n":
            ref_image = mapper.capture_image(name)

    mapper.add_zone(
        name=name,
        heading_deg=heading,
        description=desc,
        ref_image=ref_image or "",
        expected_objects=expected_objects,
    )


# ═══════════════════════════════════════════════════════
# Manual Session (no robot)
# ═══════════════════════════════════════════════════════
def manual_session(mapper: ZoneMapper):
    """Define zones by typing names + angles (no robot needed)."""
    _head("📝 Manual Zone Mapping")
    print("""
Define zones by entering name + heading angle.
Heading convention: 0°=Forward, 90°=Left, 180°=Back, 270°=Right

Commands:
  add        — Add a new zone
  list       — List all zones  
  remove     — Remove a zone
  save       — Save and exit
  quit       — Quit without saving
""")

    _setup_room(mapper)
    print()

    while True:
        try:
            cmd = input(f"{C.BOLD}[manual] ▶ {C.RESET}").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not cmd:
            continue

        if cmd == "add":
            name = input("  Zone name: ").strip()
            if not name:
                continue
            try:
                heading = float(input(f"  Heading angle (0-360°): ").strip())
            except ValueError:
                _warn("Invalid angle")
                continue
            desc = input("  Description: ").strip()
            objects_str = input("  Expected objects (comma-separated): ").strip()
            expected_objects = [o.strip() for o in objects_str.split(",") if o.strip()] \
                if objects_str else []
            mapper.add_zone(name, heading, desc, "", expected_objects)

        elif cmd == "list":
            mapper.list_zones()

        elif cmd in ("remove", "rm", "delete"):
            name = input("  Zone name to remove: ").strip()
            mapper.remove_zone(name)

        elif cmd == "save":
            mapper.save()
            break

        elif cmd in ("quit", "exit"):
            ans = input("Quit without saving? (y/N) ").strip().lower()
            if ans == "y":
                break
        else:
            _warn(f"Unknown command: {cmd}  (try: add, list, remove, save, quit)")


# ═══════════════════════════════════════════════════════
# Shared Helpers
# ═══════════════════════════════════════════════════════
def _setup_room(mapper: ZoneMapper):
    """Prompt for room info (or keep defaults)."""
    size_display = f" ({mapper.room_size}m)" if mapper.room_size else ""
    print(f"\n  Current room: {mapper.room_name}{size_display}")
    ans = input("  Change room info? (y/N) ").strip().lower()
    if ans == "y":
        name = input(f"  Room name [{mapper.room_name}]: ").strip()
        if name:
            mapper.room_name = name
        size = input(f"  Room size in meters (e.g. 5x7)  [{mapper.room_size or 'skip'}]: ").strip()
        if size:
            mapper.room_size = size
        desc = input(f"  Room description (optional): ").strip()
        if desc:
            mapper.room_description = desc
        home = input(f"  Home position description [{mapper.home_description}]: ").strip()
        if home:
            mapper.home_description = home


def view_session(mapper: ZoneMapper):
    """View existing zone map."""
    if not mapper.load():
        _info("Create one with: python3 zone_mapper.py --manual")
        return

    _head(f"🗺️ Zone Map — {mapper.room_name}")
    mapper.list_zones()

    zone_map = json.loads(ZONE_MAP_FILE.read_text(encoding="utf-8"))
    md = generate_zone_markdown(zone_map)
    print(md)

    # Offer to edit
    ans = input("\nEdit zones? (y/N) ").strip().lower()
    if ans == "y":
        manual_session(mapper)


# ═══════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(
        description="VORA Zone Mapping Tool — define semantic zones for visual search")
    parser.add_argument("--manual", action="store_true",
                        help="Manual mode (no robot connection needed)")
    parser.add_argument("--load", action="store_true",
                        help="View/edit existing zone map")
    parser.add_argument("--rosbridge", type=str, default=ROSBRIDGE_DEFAULT,
                        help=f"ROSBridge WebSocket URL (default: {ROSBRIDGE_DEFAULT})")
    parser.add_argument("--room", type=str, default="Lab",
                        help="Room name (default: Lab)")
    parser.add_argument("--room-size", type=str, default="",
                        help="Room dimensions e.g. 5x7m (optional)")
    args = parser.parse_args()

    mapper = ZoneMapper(rosbridge_url=args.rosbridge)
    mapper.room_name = args.room
    mapper.room_size = args.room_size

    _head("🗺️ VORA Zone Mapper")
    size_display = f" ({mapper.room_size}m)" if mapper.room_size else ""
    print(f"  Room: {mapper.room_name}{size_display}")
    print(f"  Output: {MAPS_DIR}/")
    print()

    # Load existing if available
    if ZONE_MAP_FILE.exists():
        mapper.load()
        print()

    if args.load:
        view_session(mapper)
    elif args.manual:
        manual_session(mapper)
    else:
        # Interactive mode — needs robot
        if mapper.connect():
            try:
                interactive_session(mapper)
            finally:
                mapper.disconnect()
        else:
            _warn("Cannot connect to robot — falling back to manual mode")
            manual_session(mapper)


if __name__ == "__main__":
    main()


