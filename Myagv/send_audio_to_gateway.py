#!/usr/bin/env python3
"""
send_audio_to_gateway.py
========================
Stream microphone audio from Robot (ReSpeaker/USB Mic) to VORA Gateway
WITH SILENCE DETECTION and LOW LATENCY OPTIMIZATION

Features:
- Auto-detect device sample rate (16kHz/48kHz)
- Resample if needed
- Silence detection (auto-stop after 60s silence)
- Latency logging
- Smaller chunks for lower latency

Usage:
    python3 send_audio_to_gateway.py \\
        --gateway-ws ws://GATEWAY_IP:9001/gw/audio \\
        --lang th \\
        --device "ReSpeaker"

Requirements:
    pip3 install sounddevice numpy websockets scipy
"""

import argparse
import asyncio
import json
import sys
import time
from typing import Optional

import numpy as np
import sounddevice as sd
from websockets.client import connect as ws_connect
from scipy import signal


def choose_device(name_substr: Optional[str]):
    """Find audio input device by name substring"""
    if not name_substr:
        return None
    devices = sd.query_devices()
    for idx, d in enumerate(devices):
        if d.get("max_input_channels", 0) > 0 and name_substr.lower() in d.get("name", "").lower():
            print(f"[INFO] Found device #{idx}: {d.get('name')}")
            return idx
    return None


def get_device_sample_rate(device_index, preferred_rate=16000):
    """Get best sample rate for device (prefer 16kHz for ReSpeaker, 48kHz for others)"""
    try:
        dev_info = sd.query_devices(device_index)
        default_sr = int(dev_info['default_samplerate'])
        
        # Common sample rates to try
        test_rates = [preferred_rate, 16000, 48000, 44100, 32000, 22050, 8000]
        
        for rate in test_rates:
            try:
                # Try to open stream with this rate
                sd.check_input_settings(device=device_index, samplerate=rate, channels=1)
                print(f"[INFO] Device supports {rate}Hz")
                return rate
            except:
                continue
        
        # Fallback to device default
        print(f"[WARN] Using device default sample rate: {default_sr}Hz")
        return default_sr
    except Exception as e:
        print(f"[WARN] Could not detect sample rate, using {preferred_rate}Hz: {e}")
        return preferred_rate


async def main():
    parser = argparse.ArgumentParser(
        description="Stream mic audio to Gateway with silence detection (VORA Robot Audio Client)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Auto-detect sample rate, auto-stop on silence
  python3 send_audio_to_gateway.py \\
      --gateway-ws ws://192.168.0.60:9001/gw/audio \\
      --device "ReSpeaker"

  # Custom silence settings
  python3 send_audio_to_gateway.py \\
      --gateway-ws ws://192.168.0.60:9001/gw/audio \\
      --device "Yeti GX" \\
      --silence-threshold 300 \\
      --silence-duration 60.0
        """
    )
    parser.add_argument("--gateway-ws", required=True, help="Gateway WebSocket URL (e.g. ws://GATEWAY_IP:9001/gw/audio)")
    parser.add_argument("--lang", default="th", help="Language hint for STT (default: th)")
    parser.add_argument("--rate", type=int, default=16000, help="Target sample rate in Hz (default: 16000)")
    parser.add_argument("--frames", type=int, default=512, help="Frames per chunk - SMALLER = LOWER LATENCY (default: 512)")
    parser.add_argument("--device", default=None, help='Input device name substring (e.g. "ReSpeaker", "Yeti GX")')
    parser.add_argument("--silence-threshold", type=int, default=400, help="RMS threshold for silence detection (default: 400)")
    parser.add_argument("--silence-duration", type=float, default=60.0, help="Seconds of silence before auto-stop (default: 60.0)")
    parser.add_argument("--no-silence-detection", action="store_true", help="Disable auto-stop on silence")
    parser.add_argument("--verbose", action="store_true", help="Show detailed latency logs")
    args = parser.parse_args()

    # Find device
    device_index = choose_device(args.device)

    if args.device and device_index is None:
        print(f"[WARN] Device containing '{args.device}' not found. Using default.")
    
    # Auto-detect best sample rate for device
    device_sample_rate = get_device_sample_rate(device_index, args.rate)
    target_rate = args.rate  # Rate expected by Gateway (16000)
    need_resample = device_sample_rate != target_rate
    
    # Silence detection settings
    silence_threshold = args.silence_threshold
    silence_duration = args.silence_duration
    silence_enabled = not args.no_silence_detection
    silence_chunks_max = int(silence_duration * target_rate / args.frames)
    
    # Print device info
    try:
        if device_index is not None:
            dev_info = sd.query_devices(device_index)
            print(f"[INFO] Using input device #{device_index}: {dev_info['name']}")
        else:
            di = sd.default.device[0]
            dev_info = sd.query_devices(di)
            device_index = di
            print(f"[INFO] Using default input device #{di}: {dev_info['name']}")
        
        print(f"[INFO] Device sample rate: {device_sample_rate}Hz")
        if need_resample:
            print(f"[INFO] Will resample to: {target_rate}Hz for Gateway")
        else:
            print(f"[INFO] No resampling needed")
        print(f"[INFO] Channels: 1 (mono)")
        print(f"[INFO] Chunk size: {args.frames} frames ({args.frames / device_sample_rate * 1000:.1f}ms)")
        if silence_enabled:
            print(f"[INFO] Silence detection: ENABLED (threshold={silence_threshold}, duration={silence_duration}s)")
        else:
            print(f"[INFO] Silence detection: DISABLED")
        print(f"[INFO] Latency logging: {'VERBOSE' if args.verbose else 'MINIMAL'}")
    except Exception as e:
        print(f"[WARN] Could not get device info: {e}")
        print(f"[INFO] Using default input device")

    audio_q: asyncio.Queue = asyncio.Queue(maxsize=100)
    silent_chunks = 0
    total_chunks = 0

    def cb(indata, frames, time_info, status):
        """Audio callback - called for each audio chunk"""
        nonlocal silent_chunks, total_chunks
        
        if status:
            # Uncomment for debugging audio issues
            # print(f"[AUDIO STATUS] {status}")
            pass
        
        # Get mono audio
        mono_audio = indata[:, 0]
        
        # Resample if needed
        if need_resample:
            # Calculate target number of samples
            num_samples_out = int(len(mono_audio) * target_rate / device_sample_rate)
            # Resample using scipy
            mono_audio = signal.resample(mono_audio, num_samples_out)
        
        # Silence detection
        if silence_enabled:
            rms = np.sqrt(np.mean(mono_audio**2))
            if rms < (silence_threshold / 32767.0):  # Normalize to float range
                silent_chunks += 1
            else:
                silent_chunks = 0
        
        # Convert to PCM16
        pcm16 = (np.clip(mono_audio, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()
        total_chunks += 1
        
        try:
            audio_q.put_nowait((pcm16, silent_chunks))
        except asyncio.QueueFull:
            # Drop frames if queue is full (prevents lag)
            pass

    print(f"\n{'='*60}")
    print(f"[INFO] Connecting to Gateway: {args.gateway_ws}")
    print(f"{'='*60}\n")

    try:
        async with ws_connect(args.gateway_ws, max_size=8 * 1024 * 1024, ping_interval=20, ping_timeout=10) as ws:
            # ✅ IMPORTANT: Send config TEXT first (Gateway expects this)
            init_cfg = {"rate": target_rate, "lang": args.lang}
            await ws.send(json.dumps(init_cfg))
            print(f"[INFO] Sent init config: {init_cfg}")
            print(f"[INFO] 🎤 Recording... Press Ctrl+C to stop")
            print(f"{'='*60}\n")

            with sd.InputStream(
                samplerate=device_sample_rate,
                channels=1,
                dtype="float32",
                blocksize=args.frames,
                device=device_index,
                callback=cb,
            ):
                chunks_sent = 0
                last_log_time = time.time()
                session_start = time.time()
                
                try:
                    while True:
                        chunk_data, silent_count = await audio_q.get()
                        
                        # Check for silence timeout
                        if silence_enabled and silent_count >= silence_chunks_max:
                            print(f"\n[INFO] 🔇 Silence detected for {silence_duration}s - auto-stopping")
                            print(f"[INFO] Session duration: {time.time() - session_start:.1f}s")
                            print(f"[INFO] Total chunks sent: {chunks_sent}")
                            break
                        
                        # Send chunk with latency tracking
                        send_start = time.time()
                        await ws.send(chunk_data)
                        send_latency = (time.time() - send_start) * 1000  # ms
                        
                        chunks_sent += 1
                        
                        # Logging
                        if args.verbose and chunks_sent % 50 == 0:
                            print(f"[LATENCY] Chunk #{chunks_sent}: send={send_latency:.1f}ms, silent={silent_count}/{silence_chunks_max}")
                        elif not args.verbose and time.time() - last_log_time > 5.0:
                            audio_time = chunks_sent * args.frames / device_sample_rate
                            print(f"[INFO] Sent {chunks_sent} chunks ({audio_time:.1f}s audio) | silent: {silent_count}/{silence_chunks_max}")
                            last_log_time = time.time()
                            
                except KeyboardInterrupt:
                    print(f"\n[INFO] Stopped by user")
                    print(f"[INFO] Total audio sent: {chunks_sent * args.frames / device_sample_rate:.1f}s")
    except ConnectionRefusedError:
        print(f"\n[ERROR] ❌ Could not connect to Gateway at {args.gateway_ws}")
        print(f"[ERROR] Make sure Gateway is running: cd Gateway && ./start_gateway.sh")
        sys.exit(1)
    except Exception as e:
        print(f"\n[ERROR] ❌ Connection error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
