#!/usr/bin/env python3
"""
WebSocket Test Client for STT Endpoint
Tests that the server accepts WebSocket connections and processes audio frames
"""
import asyncio
import websockets
import json
import sys

async def test_stt_websocket():
    """Connect to WebSocket and test STT endpoint"""
    uri = "ws://localhost:8000/ws/stt"
    
    try:
        async with websockets.connect(uri) as websocket:
            print(f"✅ Connected to {uri}")
            
            # Send initial session message
            session_msg = {
                "type": "start_session",
                "session_id": "test-session-001",
                "language": "th"
            }
            await websocket.send(json.dumps(session_msg))
            print(f"📨 Sent: {session_msg}")
            
            # Simulate audio frame (PCM 16-bit, 16kHz)
            # This is just a test frame - real audio would come from ReSpeaker
            test_audio_frame = b'\x00' * 3200  # 100ms of silence at 16kHz
            
            await websocket.send(test_audio_frame)
            print(f"🔊 Sent test audio frame (3200 bytes)")
            
            # Wait for response with timeout
            try:
                response = await asyncio.wait_for(websocket.recv(), timeout=5.0)
                msg = json.loads(response)
                print(f"📥 Received: {msg}")
                
                if msg.get("type") == "transcript":
                    print(f"✅ STT Working! Text: {msg.get('text')}")
                elif msg.get("type") == "error":
                    print(f"❌ Error: {msg.get('message')}")
            except asyncio.TimeoutError:
                print("⏱️  No response (timeout) - server might be processing")
            except json.JSONDecodeError:
                print("⚠️ Received non-JSON response (might be audio processing)")
                
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    print("🧪 Testing STT WebSocket Endpoint")
    print("-" * 50)
    asyncio.run(test_stt_websocket())


