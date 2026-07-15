#!/usr/bin/env python3
"""
Test STT WebSocket - จำลองการส่ง audio จาก Gateway
"""
import asyncio
import json
import websockets
import wave
import sys

async def test_stt():
    # Generate test audio (1 second of sine wave 440Hz)
    import numpy as np
    
    sample_rate = 16000
    duration = 2  # seconds
    frequency = 440  # Hz
    
    print(f"🔊 Generating {duration}s test audio at {sample_rate}Hz...")
    
    # Generate sine wave
    t = np.linspace(0, duration, int(sample_rate * duration), False)
    audio = np.sin(2 * np.pi * frequency * t) * 0.5
    
    # Convert to int16 PCM
    audio_int16 = (audio * 32767).astype(np.int16)
    audio_bytes = audio_int16.tobytes()
    
    print(f"✅ Generated {len(audio_bytes)} bytes")
    print(f"📡 Connecting to ws://localhost:8000/ws/stt...")
    
    try:
        async with websockets.connect("ws://localhost:8000/ws/stt") as ws:
            # Send init message
            init_msg = json.dumps({"rate": sample_rate})
            print(f"📨 Sending init: {init_msg}")
            await ws.send(init_msg)
            
            # Send audio in chunks (200ms per chunk)
            chunk_size = int(sample_rate * 0.2 * 2)  # 200ms, 2 bytes per sample
            chunks_sent = 0
            
            for i in range(0, len(audio_bytes), chunk_size):
                chunk = audio_bytes[i:i+chunk_size]
                await ws.send(chunk)
                chunks_sent += 1
                print(f"📨 Chunk {chunks_sent}: {len(chunk)} bytes")
                await asyncio.sleep(0.2)  # Match real-time
            
            # Wait for response
            print(f"⏳ Waiting for transcription...")
            try:
                response = await asyncio.wait_for(ws.recv(), timeout=30)
                data = json.loads(response)
                print(f"✅ Response: {data}")
                
                if data.get("type") == "final":
                    print(f"🎉 Final text: {data.get('text')}")
                elif data.get("type") == "partial":
                    print(f"📝 Partial: {data.get('text')}")
            except asyncio.TimeoutError:
                print(f"⏱️ Timeout waiting for response")
    
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_stt())




