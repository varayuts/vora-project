#!/bin/bash
# VORA Full Stack Startup Script with HTTPS + SSL

# Trap Ctrl+C to kill all child processes
cleanup() {
    echo ""
    echo "🛑 Stopping VORA services..."
    pkill -f "uvicorn app.main:app" 2>/dev/null
    pkill -f "https_server.py" 2>/dev/null
    echo "✅ All services stopped"
    exit 0
}
trap cleanup SIGINT SIGTERM

echo "🚀 Starting VORA Full Stack..."
echo "================================"

# Get local IP
LOCAL_IP=$(hostname -I | awk '{print $1}')
TAILSCALE_IP=$(tailscale ip -4 2>/dev/null || echo "N/A")

# Check if certificates exist
cd /home/user/vora_project/VORA/VORA
if [ ! -f "cert.pem" ] || [ ! -f "key.pem" ]; then
    echo "⚠️ Certificates not found! Creating self-signed certificates..."
    openssl req -x509 -newkey rsa:2048 -keyout key.pem -out cert.pem -days 365 -nodes -subj "/CN=localhost" > /dev/null 2>&1
fi

# Kill any existing processes
pkill -f "uvicorn app.main:app" 2>/dev/null
pkill -f "https_server.py" 2>/dev/null
sleep 1

# Start FastAPI Server with SSL (STT + LLM)
echo "📡 Starting FastAPI Server (HTTPS) on port 8000..."
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 \
    --ssl-keyfile=key.pem --ssl-certfile=cert.pem > /tmp/vora_server.log 2>&1 &
UVICORN_PID=$!
echo "   PID: $UVICORN_PID"

sleep 3
 
# Start HTTPS Frontend Server
echo "🔒 Starting Frontend Server (HTTPS) on port 9000..."
cd /home/user/vora_project/VORA/VORA/app/frontend
python3 https_server.py > /tmp/vora_frontend.log 2>&1 &
FRONTEND_PID=$!
echo "   PID: $FRONTEND_PID"

sleep 2

# Health check
echo ""
echo "🔍 Health Check..."
if curl -sk https://localhost:8000/health > /dev/null 2>&1; then
    echo "   ✅ API Server: OK"
else
    echo "   ❌ API Server: FAILED (check /tmp/vora_server.log)"
fi

if curl -sk https://localhost:9000 > /dev/null 2>&1; then
    echo "   ✅ Frontend Server: OK"
else
    echo "   ❌ Frontend Server: FAILED (check /tmp/vora_frontend.log)"
fi

echo ""
echo "================================"
echo "✅ VORA is running!"
echo ""
echo "🖥️  Local:     https://localhost:9000"
echo "📱 LAN:       https://$LOCAL_IP:9000"
echo "🌐 Tailscale: https://$TAILSCALE_IP:9000"
echo ""
echo "📡 API (HTTPS):  https://localhost:8000"
echo "🎤 STT (WSS):    wss://localhost:8000/ws/stt"
echo ""
echo "⚠️  First time: Accept certificate at https://$TAILSCALE_IP:8000 first!"
echo ""
echo "Press Ctrl+C to stop all services"
echo "================================"

# Keep script running and wait for child processes
while kill -0 $UVICORN_PID 2>/dev/null && kill -0 $FRONTEND_PID 2>/dev/null; do
    sleep 5
done

echo "⚠️ One of the services stopped unexpectedly!"
cleanup

