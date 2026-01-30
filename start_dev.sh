#!/bin/bash
# ═══════════════════════════════════════════════════════════════
#  VORA Development Mode (HTTP)
#  - Frontend: http://localhost:9000
#  - API:      http://localhost:8080
#  - ไมค์ใช้ได้เฉพาะ localhost
# ═══════════════════════════════════════════════════════════════

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

cleanup() {
    echo ""
    echo -e "${RED}🛑 Stopping VORA...${NC}"
    pkill -f "uvicorn app.main:app" 2>/dev/null
    echo -e "${GREEN}✅ Stopped${NC}"
    exit 0
}
trap cleanup SIGINT SIGTERM

echo -e "${BLUE}"
echo "╔═══════════════════════════════════════════════════════════╗"
echo "║           🤖 VORA - Development Mode (HTTP)               ║"
echo "╚═══════════════════════════════════════════════════════════╝"
echo -e "${NC}"

cd /home/user/vora_project/VORA/VORA

# Kill existing
pkill -f "uvicorn app.main:app" 2>/dev/null
pkill -f "http.server 9000" 2>/dev/null
sleep 1

# Activate venv
source vora_env/bin/activate

# ✅ ตั้งค่า Environment Variables สำหรับ Runtime Config
export VORA_API_PORT=8080
export VORA_FRONTEND_PORT=9000
export VORA_HTTPS=false

# Start API Server (HTTP:8080) - FastAPI จะ serve frontend ด้วย
echo -e "${YELLOW}📡 Starting VORA Server (HTTP:8080)...${NC}"
echo -e "${YELLOW}   API:      http://0.0.0.0:8080${NC}"
echo -e "${YELLOW}   Frontend: http://0.0.0.0:8080/app${NC}"
echo -e "${YELLOW}   WebSocket: ws://0.0.0.0:8080/ws/stt${NC}"

python -m uvicorn app.main:app --host 0.0.0.0 --port 8080 > /tmp/vora_api.log 2>&1 &
API_PID=$!
sleep 3

# Health check
echo ""
echo -e "${YELLOW}🔍 Health Check...${NC}"
if curl -s http://localhost:8080/health > /dev/null 2>&1; then
    echo -e "   ${GREEN}✅ API Server: OK${NC}"
else
    echo -e "   ${RED}❌ API Server: FAILED${NC}"
    cat /tmp/vora_api.log | tail -5
fi

echo ""
echo -e "${GREEN}╔═══════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║                    ✅ VORA Ready!                         ║${NC}"
echo -e "${GREEN}╚═══════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "   🖥️  Web App:   ${BLUE}http://localhost:8080/app${NC}"
echo -e "   📡 API:       ${BLUE}http://localhost:8080${NC}"
echo -e "   📚 API Docs:  ${BLUE}http://localhost:8080/docs${NC}"
echo ""
echo -e "   ${YELLOW}⚠️  ไมค์ใช้ได้เฉพาะ localhost เท่านั้น${NC}"
echo -e "   ${YELLOW}📱 สำหรับ Mobile ใช้: ./start_mobile.sh${NC}"
echo ""
echo -e "   Press ${RED}Ctrl+C${NC} to stop"
echo ""

# Keep running
while kill -0 $API_PID 2>/dev/null; do
    sleep 5
done

cleanup
