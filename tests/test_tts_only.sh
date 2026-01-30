#!/bin/bash
# Test TTS only

cd /home/user/vora_project/VORA/VORA
source vora_env/bin/activate

echo "=== Starting server ==="
python -m uvicorn app.main:app --host 127.0.0.1 --port 8080 &
SERVER_PID=$!

sleep 5

echo ""
echo "=== Testing health ==="
curl -s http://127.0.0.1:8080/health
echo ""

echo ""
echo "=== Testing TTS ==="
timeout 30 curl -s -X POST http://127.0.0.1:8080/api/server/tts/speak \
  -H "Content-Type: application/json" \
  -d '{"text":"สวัสดีครับ","speed":1.0}' \
  -o /tmp/test_tts.wav

if [ -f /tmp/test_tts.wav ]; then
  echo "✅ TTS Success!"
  file /tmp/test_tts.wav
  ls -lh /tmp/test_tts.wav
else
  echo "❌ TTS Failed - no output file"
fi

echo ""
echo "=== Stopping server ==="
kill $SERVER_PID 2>/dev/null
sleep 2
