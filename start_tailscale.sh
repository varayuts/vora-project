#!/bin/bash
# ═══════════════════════════════════════════════════════════════
#  VORA with Tailscale Serve (Valid HTTPS)
#  - ใช้ได้ทุก device ใน Tailscale network
#  - Valid certificate จาก Let's Encrypt
#  - ไม่ต้อง accept certificate
# ═══════════════════════════════════════════════════════════════

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

cleanup() {
    echo ""
    echo -e "${RED}🛑 Stopping VORA...${NC}"
    pkill -f "uvicorn app.main:app" 2>/dev/null
    tailscale serve reset 2>/dev/null
    echo -e "${GREEN}✅ Stopped${NC}"
    exit 0
}
trap cleanup SIGINT SIGTERM

echo -e "${CYAN}"
echo "╔═══════════════════════════════════════════════════════════╗"
echo "║      🔒 VORA - Tailscale Mode (Valid HTTPS)              ║"
echo "╚═══════════════════════════════════════════════════════════╝"
echo -e "${NC}"

cd /home/user/vora_project/VORA/VORA

# Get Tailscale DNS name
TS_DNS=$(tailscale status --json 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('Self',{}).get('DNSName','').rstrip('.'))" 2>/dev/null)
TS_IP=$(tailscale ip -4 2>/dev/null)

if [ -z "$TS_DNS" ]; then
    echo -e "${RED}❌ Tailscale not connected${NC}"
    echo -e "${YELLOW}   Run: tailscale up${NC}"
    exit 1
fi

echo -e "${GREEN}✅ Tailscale connected${NC}"
echo -e "   DNS: ${BLUE}$TS_DNS${NC}"
echo -e "   IP:  ${BLUE}$TS_IP${NC}"

# Kill existing
pkill -f "uvicorn app.main:app" 2>/dev/null
tailscale serve reset 2>/dev/null
sleep 1

# Activate venv
source vora_env/bin/activate

# ✅ ตั้งค่า Environment Variables
export VORA_API_PORT=8080
export VORA_FRONTEND_PORT=8080
export VORA_HTTPS=true  # Tailscale serve ใช้ HTTPS

# Start API Server (HTTP:8080 - Tailscale จะ proxy เป็น HTTPS)
echo ""
echo -e "${YELLOW}📡 Starting VORA Server (HTTP:8080)...${NC}"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8080 > /tmp/vora_api.log 2>&1 &
API_PID=$!
sleep 3

# Health check
if curl -s http://localhost:8080/health > /dev/null 2>&1; then
    echo -e "   ${GREEN}✅ API Server: OK${NC}"
else
    echo -e "   ${RED}❌ API Server: FAILED${NC}"
    cat /tmp/vora_api.log | tail -5
    exit 1
fi

# Setup Tailscale Serve
echo ""
echo -e "${YELLOW}🔒 Setting up Tailscale Serve...${NC}"

# Reset existing serve config
tailscale serve reset 2>/dev/null

# Serve HTTP:8080 as HTTPS:443
tailscale serve --bg 8080

sleep 2

# Verify Tailscale Serve
echo ""
echo -e "${YELLOW}🔍 Verifying Tailscale Serve...${NC}"
tailscale serve status

echo ""
echo -e "${GREEN}╔═══════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║                    🔒 VORA Ready!                         ║${NC}"
echo -e "${GREEN}╚═══════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "   🌐 ${CYAN}Access from ANY Tailscale device:${NC}"
echo -e "      ${BLUE}https://${TS_DNS}/app${NC}"
echo ""
echo -e "   📱 ${CYAN}Alternative (IP):${NC}"
echo -e "      ${BLUE}https://${TS_IP}/app${NC}  (may need cert accept)"
echo ""
echo -e "   🔧 ${CYAN}API Endpoints:${NC}"
echo -e "      Health:   ${BLUE}https://${TS_DNS}/health${NC}"
echo -e "      Docs:     ${BLUE}https://${TS_DNS}/docs${NC}"
echo -e "      WebSocket: ${BLUE}wss://${TS_DNS}/ws/stt${NC}"
echo ""
echo -e "${GREEN}╔═══════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║  ✅ Valid HTTPS Certificate (No warnings!)                ║${NC}"
echo -e "${GREEN}║  ✅ Works on Gateway + Mobile + Desktop                   ║${NC}"
echo -e "${GREEN}╚═══════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "   Press ${RED}Ctrl+C${NC} to stop"
echo ""

# Keep running
while kill -0 $API_PID 2>/dev/null; do
    sleep 5
done

cleanup
