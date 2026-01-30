#!/usr/bin/env python3
"""
find_myagv.py
=============
Auto-discover MyAGV (Jetson Nano) on local network

Scans for ROSBridge WebSocket (port 9090) and updates Gateway .env
"""

import socket
import sys
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed


def check_rosbridge(ip: str, port: int = 9090, timeout: float = 0.5) -> bool:
    """Check if ROSBridge is running on this IP"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((ip, port))
        sock.close()
        return result == 0
    except:
        return False


def get_network_range():
    """Get current network range (e.g., 192.168.0)"""
    try:
        # Get default gateway
        result = subprocess.run(['ip', 'route', 'show', 'default'], 
                              capture_output=True, text=True, timeout=2)
        if result.returncode != 0:
            return None
        
        # Extract IP from "default via 192.168.0.1"
        for line in result.stdout.split('\n'):
            if 'default via' in line:
                gateway = line.split()[2]
                # Get network prefix (e.g., 192.168.0)
                parts = gateway.split('.')
                return '.'.join(parts[:3])
    except:
        pass
    
    # Fallback: try common ranges
    return None


def scan_network(network_prefix: str, start: int = 1, end: int = 254):
    """Scan network range for ROSBridge"""
    print(f"🔍 Scanning {network_prefix}.{start}-{end} for ROSBridge (port 9090)...")
    
    found_devices = []
    
    with ThreadPoolExecutor(max_workers=50) as executor:
        futures = {}
        for i in range(start, end + 1):
            ip = f"{network_prefix}.{i}"
            futures[executor.submit(check_rosbridge, ip)] = ip
        
        for future in as_completed(futures):
            ip = futures[future]
            try:
                if future.result():
                    print(f"✅ Found ROSBridge at {ip}:9090")
                    found_devices.append(ip)
            except:
                pass
    
    return found_devices


def update_env_file(ip: str, env_path: str = "Gateway/.env"):
    """Update ROSBRIDGE in .env file"""
    try:
        with open(env_path, 'r') as f:
            lines = f.readlines()
        
        updated = False
        for i, line in enumerate(lines):
            if line.startswith('ROSBRIDGE='):
                lines[i] = f'ROSBRIDGE=ws://{ip}:9090\n'
                updated = True
                break
        
        if updated:
            with open(env_path, 'w') as f:
                f.writelines(lines)
            print(f"✅ Updated {env_path}: ROSBRIDGE=ws://{ip}:9090")
            return True
        else:
            print(f"⚠️  ROSBRIDGE not found in {env_path}")
            return False
    except Exception as e:
        print(f"❌ Error updating .env: {e}")
        return False


def main():
    print("╔════════════════════════════════════════════════════════╗")
    print("║     🔍 VORA - Auto-discover MyAGV on Network          ║")
    print("╚════════════════════════════════════════════════════════╝")
    print()
    
    # Get network range
    network_prefix = get_network_range()
    
    if not network_prefix:
        print("❌ Could not detect network range")
        print("📝 Common ranges: 192.168.0, 192.168.1, 10.0.0")
        network_prefix = input("Enter network prefix (e.g., 192.168.0): ").strip()
        
        if not network_prefix:
            print("❌ No network prefix provided. Exiting.")
            sys.exit(1)
    
    print(f"📡 Detected network: {network_prefix}.0/24")
    print()
    
    # Scan network
    devices = scan_network(network_prefix)
    
    print()
    print("─" * 60)
    
    if not devices:
        print("❌ No ROSBridge found on network")
        print()
        print("Troubleshooting:")
        print("  1. Make sure MyAGV is powered on")
        print("  2. Check MyAGV is connected to same WiFi")
        print("  3. Verify ROSBridge is running:")
        print("     ros2 launch rosbridge_server rosbridge_websocket_launch.xml")
        print("  4. Check firewall on MyAGV (port 9090)")
        sys.exit(1)
    
    if len(devices) == 1:
        # Only one device found - auto update
        ip = devices[0]
        print(f"✅ Found 1 device: {ip}")
        print()
        
        if update_env_file(ip):
            print()
            print("╔════════════════════════════════════════════════════════╗")
            print("║              ✅ Configuration Updated!                ║")
            print("╚════════════════════════════════════════════════════════╝")
            print()
            print(f"MyAGV IP: {ip}")
            print()
            print("Next steps:")
            print("  1. Restart Gateway: cd Gateway && ./start_gateway.sh")
            print("  2. Gateway will now connect to MyAGV automatically!")
    else:
        # Multiple devices found - let user choose
        print(f"✅ Found {len(devices)} devices with ROSBridge:")
        print()
        for i, ip in enumerate(devices, 1):
            print(f"  {i}. {ip}")
        print()
        
        choice = input("Select device number (or 'q' to quit): ").strip()
        
        if choice.lower() == 'q':
            print("Cancelled.")
            sys.exit(0)
        
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(devices):
                ip = devices[idx]
                if update_env_file(ip):
                    print()
                    print("╔════════════════════════════════════════════════════════╗")
                    print("║              ✅ Configuration Updated!                ║")
                    print("╚════════════════════════════════════════════════════════╝")
                    print()
                    print(f"MyAGV IP: {ip}")
                    print()
                    print("Restart Gateway to apply changes.")
            else:
                print("❌ Invalid selection")
                sys.exit(1)
        except ValueError:
            print("❌ Invalid input")
            sys.exit(1)


if __name__ == "__main__":
    main()
