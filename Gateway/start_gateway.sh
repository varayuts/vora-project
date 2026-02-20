#!/bin/bash
# ═══════════════════════════════════════════════════════════════
#  VORA Gateway Startup Script
#  Role: Proxy audio from Robot → VORA Server → Execute commands
# ═══════════════════════════════════════════════════════════════

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

cleanup() {
    echo ""
    echo -e "${RED}🛑 Stopping Gateway...${NC}"
    pkill -f "uvicorn.*gateway.main" 2>/dev/null
    echo -e "${GREEN}✅ Stopped${NC}"
    exit 0
}
trap cleanup SIGINT SIGTERM

echo -e "${CYAN}"
echo "╔═══════════════════════════════════════════════════════════╗"
echo "║           🤖 VORA GATEWAY - Starting...                   ║"
echo "╚═══════════════════════════════════════════════════════════╝"
echo -e "${NC}"

cd "$(dirname "$0")"

# Kill existing
pkill -f "uvicorn.*gateway.main" 2>/dev/null
timeout 1 2>/dev/null || sleep 1 2>/dev/null || ping 127.0.0.1 -n 2 > /dev/null

# Check .env file
if [ ! -f ".env" ]; then
    echo -e "${RED}❌ Error: .env file not found!${NC}"
    echo -e "${YELLOW}   Create .env file with:${NC}"
    echo -e "${YELLOW}   - SERVER_BASE=https://user.tail87d9fe.ts.net${NC}"
    echo -e "${YELLOW}   - SERVER_WS=wss://user.tail87d9fe.ts.net/ws/stt${NC}"
    echo -e "${YELLOW}   - ROSBRIDGE=ws://192.168.0.111:9090 (MyAGV Static IP)${NC}"
    exit 1
fi

# Display Network Info
echo -e "${BLUE}📍 Network Configuration:${NC}"
echo -e "${CYAN}   Gateway:  192.168.0.60 (This machine)${NC}"
echo -e "${CYAN}   MyAGV:    192.168.0.111 (ROSBridge)${NC}"
echo -e "${CYAN}   Server:   user.tail87d9fe.ts.net (Tailscale)${NC}"
echo ""

# Check if venv exists
if [ ! -d "../venv" ]; then
    echo -e "${YELLOW}⚠️  venv not found, trying to activate anyway...${NC}"
else
    # Windows uses Scripts/activate, Linux uses bin/activate
    if [ -f "../venv/Scripts/activate" ]; then
        source ../venv/Scripts/activate
        echo -e "${GREEN}✅ Activated venv (Windows)${NC}"
    elif [ -f "../venv/bin/activate" ]; then
        source ../venv/bin/activate
        echo -e "${GREEN}✅ Activated venv (Linux)${NC}"
    else
        echo -e "${YELLOW}⚠️  venv activation script not found${NC}"
    fi
fi

# Install dependencies if needed
if ! python -c "import fastapi" 2>/dev/null; then
    echo -e "${YELLOW}📦 Installing dependencies...${NC}"
    pip install -r gateway/requirements.txt
fi

# Show configuration
echo ""
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
source .env
echo -e "${CYAN}📡 Configuration:${NC}"
echo -e "   ${BLUE}VORA Server:${NC}  ${SERVER_BASE}"
echo -e "   ${BLUE}WebSocket:${NC}    ${SERVER_WS}"
echo -e "   ${BLUE}ROSBridge:${NC}    ${ROSBRIDGE}"
echo -e "   ${BLUE}Mock Robot:${NC}   ${MOCK_ROBOT:-0}"
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

# Start Gateway
echo ""
echo -e "${YELLOW}🚀 Starting Gateway on port 9001...${NC}"

python -m uvicorn gateway.main:app \
    --host 0.0.0.0 \
    --port 9001 \
    --reload &

GATEWAY_PID=$!
timeout 3 2>/dev/null || sleep 3 2>/dev/null || ping 127.0.0.1 -n 4 > /dev/null

# Health check
echo ""
if curl -s http://localhost:9001/health > /dev/null 2>&1; then
    echo -e "${GREEN}✅ Gateway is running!${NC}"
else
    echo -e "${RED}❌ Gateway failed to start${NC}"
    exit 1
fi

echo ""
echo -e "${GREEN}╔═══════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║              ✅ VORA GATEWAY READY!                       ║${NC}"
echo -e "${GREEN}╚═══════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "   📡 ${CYAN}Endpoints:${NC}"
echo -e "      Health:    ${BLUE}http://localhost:9001/health${NC}"
echo -e "      Camera:    ${BLUE}http://localhost:9001/camera/frame${NC}"
echo -e "      Audio WS:  ${BLUE}ws://localhost:9001/gw/audio${NC}"
echo -e "      Dashboard: ${BLUE}ws://localhost:9001/gw/dashboard${NC}"
echo ""
echo -e "   ${YELLOW}📱 To send audio from Robot:${NC}"
echo -e "      ${BLUE}python send_audio_to_gateway.py --gateway-ws ws://GATEWAY_IP:9001/gw/audio${NC}"
echo ""
echo -e "   Press ${RED}Ctrl+C${NC} to stop"
echo ""

# Keep running
while kill -0 $GATEWAY_PID 2>/dev/null; do
    timeout 5 2>/dev/null || sleep 5 2>/dev/null || ping 127.0.0.1 -n 6 > /dev/null
done

cleanup
