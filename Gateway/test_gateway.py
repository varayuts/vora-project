#!/usr/bin/env python3
"""
VORA Gateway Test Script
ทดสอบ Gateway Pipeline โดยไม่ต้องมี Robot จริง

Usage:
    python test_gateway.py
"""

import asyncio
import json
import httpx
import websockets

GATEWAY_URL = "http://localhost:9001"
GATEWAY_WS = "ws://localhost:9001/gw/audio"

# Test cases
TEST_COMMANDS = [
    ("โวร่า หยุด", {"type": "stop"}),
    ("โวร่า เดินหน้า", {"type": "move", "linear_x": 0.1}),
    ("โวร่า ถอยหลัง 50 เซน", {"type": "move", "linear_x": -0.1}),
    ("โวร่า หันซ้าย 90 องศา", {"type": "move", "angular_z": 0.5}),
    ("โวร่า เลี้ยวขวา", {"type": "move", "angular_z": -0.5}),
    ("สวัสดีครับ", None),  # No wake word - should be ignored
]

def print_header(text):
    print("\n" + "=" * 60)
    print(f"  {text}")
    print("=" * 60)

async def test_health():
    """Test Gateway health endpoint"""
    print_header("Testing /health")
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{GATEWAY_URL}/health")
            data = r.json()
            
            print(f"✅ Status: {data.get('status')}")
            print(f"   Server Connected: {data.get('server_connected')}")
            print(f"   Mock Robot: {data.get('mock_robot')}")
            print(f"   Debug: {data.get('debug')}")
            return data.get('status') == 'ok'
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

async def test_server_connection():
    """Test connection to VORA Server"""
    print_header("Testing Server Connection")
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{GATEWAY_URL}/test/server")
            data = r.json()
            
            if data.get('ok'):
                print(f"✅ Connected to VORA Server")
                print(f"   Health: {data.get('data')}")
                return True
            else:
                print(f"❌ Failed: {data.get('error')}")
                return False
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

async def test_intent_parser():
    """Test intent parser with various commands"""
    print_header("Testing Intent Parser")
    
    passed = 0
    failed = 0
    
    async with httpx.AsyncClient() as client:
        for cmd_text, expected in TEST_COMMANDS:
            try:
                r = await client.post(
                    f"{GATEWAY_URL}/test/intent",
                    json={"text": cmd_text}
                )
                data = r.json()
                
                if expected is None:
                    # Should NOT detect wake word
                    if not data.get('wake_word_found'):
                        print(f"✅ \"{cmd_text}\" → Correctly ignored (no wake word)")
                        passed += 1
                    else:
                        print(f"❌ \"{cmd_text}\" → Should have been ignored!")
                        failed += 1
                else:
                    # Should detect wake word and parse intent
                    if data.get('wake_word_found'):
                        intent = data.get('motion_intent') or {}
                        intent_type = intent.get('type')
                        
                        if intent_type == expected.get('type'):
                            print(f"✅ \"{cmd_text}\"")
                            print(f"   → Command: \"{data.get('command_after_wake')}\"")
                            print(f"   → Intent: {intent}")
                            passed += 1
                        else:
                            print(f"❌ \"{cmd_text}\" → Wrong intent type: {intent_type}")
                            failed += 1
                    else:
                        print(f"❌ \"{cmd_text}\" → Wake word not detected!")
                        failed += 1
                        
            except Exception as e:
                print(f"❌ \"{cmd_text}\" → Error: {e}")
                failed += 1
    
    print(f"\n📊 Results: {passed} passed, {failed} failed")
    return failed == 0

async def test_text_command():
    """Test sending text command directly"""
    print_header("Testing /gw/text Endpoint")
    
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{GATEWAY_URL}/gw/text",
                json={"text": "เดินหน้า 30 เซน", "lang": "th"}
            )
            data = r.json()
            
            if data.get('ok'):
                print(f"✅ Command accepted")
                print(f"   Mode: {data.get('mode')}")
                if data.get('motion'):
                    print(f"   Motion: {data.get('motion')}")
                return True
            else:
                print(f"❌ Command rejected: {data.get('reason')}")
                return False
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

async def test_websocket():
    """Test WebSocket connection (brief test)"""
    print_header("Testing WebSocket /gw/audio")
    
    try:
        async with websockets.connect(GATEWAY_WS) as ws:
            # Send init config
            await ws.send(json.dumps({"rate": 16000, "lang": "th"}))
            print("✅ WebSocket connected")
            print("   Config sent successfully")
            
            # Wait briefly for any response
            try:
                response = await asyncio.wait_for(ws.recv(), timeout=2.0)
                print(f"   Received: {response[:100]}...")
            except asyncio.TimeoutError:
                print("   (No immediate response - normal if not sending audio)")
            
            return True
    except Exception as e:
        print(f"❌ WebSocket Error: {e}")
        return False

async def main():
    print("\n" + "🤖" * 30)
    print("        VORA Gateway Test Suite")
    print("🤖" * 30)
    
    results = []
    
    # Run tests
    results.append(("Health Check", await test_health()))
    results.append(("Server Connection", await test_server_connection()))
    results.append(("Intent Parser", await test_intent_parser()))
    results.append(("Text Command", await test_text_command()))
    results.append(("WebSocket", await test_websocket()))
    
    # Summary
    print_header("Test Summary")
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    
    for name, ok in results:
        status = "✅" if ok else "❌"
        print(f"  {status} {name}")
    
    print(f"\n📊 Total: {passed}/{total} tests passed")
    
    if passed == total:
        print("\n🎉 All tests passed! Gateway is ready.")
    else:
        print("\n⚠️ Some tests failed. Check the logs above.")

if __name__ == "__main__":
    asyncio.run(main())


