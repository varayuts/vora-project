#!/bin/bash
# VORA Full Stack - HTTP mode (ใช้กับ Cloudflare Tunnel)

cleanup() {
    echo ""
    echo "🛑 Stopping VORA services..."
    pkill -f "uvicorn app.main:app" 2>/dev/null
    pkill -f "http.server 9000" 2>/dev/null
    pkill -f "cloudflared tunnel" 2>/dev/null
    echo "✅ All services stopped"
    exit 0
}
trap cleanup SIGINT SIGTERM

echo "🚀 Starting VORA Full Stack (HTTP mode)..."
echo "============================================"

cd /home/user/vora_project/VORA/VORA

# Get local IP
LOCAL_IP=$(hostname -I | awk '{print $1}')
TAILSCALE_IP=$(tailscale ip -4 2>/dev/null || echo "N/A")

# Kill existing
pkill -f "uvicorn app.main:app" 2>/dev/null
pkill -f "http.server 9000" 2>/dev/null
pkill -f "cloudflared tunnel" 2>/dev/null
sleep 1

# Start FastAPI Server (HTTP on port 8080)
echo "📡 Starting FastAPI Server (HTTP) on port 8080..."
source vora_env/bin/activate
python -m uvicorn app.main:app --host 0.0.0.0 --port 8080 > /tmp/vora_server.log 2>&1 &
API_PID=$!
echo "   PID: $API_PID"

sleep 3

# Start Frontend Server (HTTP on port 9000)
echo "🌐 Starting Frontend Server (HTTP) on port 9000..."
cd /home/user/vora_project/VORA/VORA/app/frontend
python3 -m http.server 9000 --bind 0.0.0.0 > /tmp/vora_frontend.log 2>&1 &
FRONTEND_PID=$!
echo "   PID: $FRONTEND_PID"

sleep 2

# Health check
echo ""
echo "🔍 Health Check..."
if curl -s http://localhost:8080/health > /dev/null 2>&1; then
    echo "   ✅ API Server: OK"
else
    echo "   ❌ API Server: FAILED"
fi

if curl -s http://localhost:9000 > /dev/null 2>&1; then
    echo "   ✅ Frontend Server: OK"
else
    echo "   ❌ Frontend Server: FAILED"
fi

echo ""
echo "============================================"
echo "✅ VORA is running! (HTTP mode)"
echo ""
echo "🖥️  Local:     http://localhost:9000"
echo "📱 LAN:       http://$LOCAL_IP:9000"
echo "🌐 Tailscale: http://$TAILSCALE_IP:9000"
echo ""
echo "📡 API:  http://localhost:8080"
echo "🎤 STT:  ws://localhost:8080/ws/stt"
echo ""
echo "============================================"
echo ""
echo "📱 สำหรับ Mobile: ใช้ Cloudflare Tunnel"
echo "   รันคำสั่ง: cloudflared tunnel --url http://localhost:9000"
echo ""
echo "Press Ctrl+C to stop all services"
echo "============================================"

# Keep running
while kill -0 $API_PID 2>/dev/null && kill -0 $FRONTEND_PID 2>/dev/null; do
    sleep 5
done

echo "⚠️ Service stopped!"
cleanup
