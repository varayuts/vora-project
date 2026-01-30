#!/bin/bash
# ═══════════════════════════════════════════════════════════════
#  VORA Pre-Deployment Checklist
#  Run this before deploying to production
# ═══════════════════════════════════════════════════════════════

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}"
echo "╔════════════════════════════════════════════════════════════╗"
echo "║       🚀 VORA Pre-Deployment Checklist                     ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo -e "${NC}"

ERRORS=0

# ==============================================================================
# 1. VORA Server
# ==============================================================================
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${CYAN}1. Checking VORA Server...${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

# Check server running
if curl -sf https://user.tail87d9fe.ts.net/health > /dev/null 2>&1; then
    echo -e "   ${GREEN}✅ VORA Server is running${NC}"
    echo -e "      URL: https://user.tail87d9fe.ts.net"
else
    echo -e "   ${RED}❌ VORA Server not reachable${NC}"
    echo -e "      ${YELLOW}Fix: cd /home/user/vora_project/VORA/VORA && ./start_tailscale.sh${NC}"
    ((ERRORS++))
fi

# Check Tailscale
if tailscale status > /dev/null 2>&1; then
    echo -e "   ${GREEN}✅ Tailscale connected${NC}"
else
    echo -e "   ${YELLOW}⚠️  Tailscale not running (optional for local testing)${NC}"
fi

# ==============================================================================
# 2. Gateway
# ==============================================================================
echo ""
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${CYAN}2. Checking Gateway Configuration...${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

if [ -f "Gateway/.env" ]; then
    echo -e "   ${GREEN}✅ Gateway/.env exists${NC}"
    
    # Check SERVER_BASE
    if grep -q "SERVER_BASE=https://user.tail87d9fe.ts.net" Gateway/.env; then
        echo -e "   ${GREEN}✅ SERVER_BASE configured (Tailscale)${NC}"
    else
        echo -e "   ${YELLOW}⚠️  SERVER_BASE not using Tailscale${NC}"
    fi
    
    # Check ROSBRIDGE
    ROSBRIDGE=$(grep "^ROSBRIDGE=" Gateway/.env | cut -d'=' -f2)
    if [ -n "$ROSBRIDGE" ]; then
        echo -e "   ${GREEN}✅ ROSBRIDGE configured: ${ROSBRIDGE}${NC}"
        echo -e "      ${CYAN}Note: Make sure MyAGV has static IP!${NC}"
    else
        echo -e "   ${RED}❌ ROSBRIDGE not configured${NC}"
        ((ERRORS++))
    fi
else
    echo -e "   ${RED}❌ Gateway/.env not found${NC}"
    echo -e "      ${YELLOW}Fix: cp Gateway/.env.example Gateway/.env${NC}"
    ((ERRORS++))
fi

# Check Gateway dependencies
if [ -f "Gateway/gateway/requirements.txt" ]; then
    echo -e "   ${GREEN}✅ Gateway requirements.txt exists${NC}"
fi

# ==============================================================================
# 3. MyAGV Setup Instructions
# ==============================================================================
echo ""
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${CYAN}3. MyAGV (Jetson Nano) Setup Instructions...${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

echo -e "   ${YELLOW}📋 Before deployment on MyAGV:${NC}"
echo ""
echo -e "   ${CYAN}Step 1: Set Static IP${NC}"
echo -e "      ${BLUE}See: Myagv/SETUP_STATIC_IP.md${NC}"
echo -e "      Quick: sudo nmcli connection modify \"WiFi\" ipv4.method manual ipv4.addresses \"192.168.0.111/24\""
echo ""
echo -e "   ${CYAN}Step 2: Install ROS2 Package${NC}"
echo -e "      ${BLUE}cd ~/ros2_ws/src${NC}"
echo -e "      ${BLUE}cp -r /path/to/Myagv/vora_robot_bridge .${NC}"
echo -e "      ${BLUE}colcon build --packages-select vora_robot_bridge${NC}"
echo ""
echo -e "   ${CYAN}Step 3: Test Audio Device${NC}"
echo -e "      ${BLUE}python3 -c \"import sounddevice as sd; print(sd.query_devices())\"${NC}"
echo ""

# ==============================================================================
# 4. Network Connectivity
# ==============================================================================
echo ""
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${CYAN}4. Network Connectivity Tests...${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

# Check if we can resolve local network
if ip route show default > /dev/null 2>&1; then
    echo -e "   ${GREEN}✅ Network interface configured${NC}"
    
    # Get network info
    GATEWAY=$(ip route show default | awk '/default/ {print $3}')
    echo -e "      Gateway: ${GATEWAY}"
    
    # Ping gateway
    if ping -c 1 -W 1 $GATEWAY > /dev/null 2>&1; then
        echo -e "   ${GREEN}✅ Can reach gateway${NC}"
    else
        echo -e "   ${RED}❌ Cannot reach gateway${NC}"
        ((ERRORS++))
    fi
else
    echo -e "   ${RED}❌ No network connection${NC}"
    ((ERRORS++))
fi

# ==============================================================================
# 5. File Structure
# ==============================================================================
echo ""
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${CYAN}5. Checking File Structure...${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

FILES_TO_CHECK=(
    "start_tailscale.sh"
    "start_dev.sh"
    "Gateway/start_gateway.sh"
    "Gateway/find_myagv.py"
    "Gateway/gateway/main.py"
    "Myagv/send_audio_to_gateway.py"
    "Myagv/vora_robot_bridge/vora_robot_bridge/command_executor.py"
    "DEPLOYMENT.md"
    "NETWORK_SETUP.md"
)

for file in "${FILES_TO_CHECK[@]}"; do
    if [ -f "$file" ]; then
        echo -e "   ${GREEN}✅ $file${NC}"
    else
        echo -e "   ${RED}❌ Missing: $file${NC}"
        ((ERRORS++))
    fi
done

# ==============================================================================
# Summary
# ==============================================================================
echo ""
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${CYAN}Summary${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

if [ $ERRORS -eq 0 ]; then
    echo ""
    echo -e "${GREEN}╔════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║              ✅ ALL CHECKS PASSED!                         ║${NC}"
    echo -e "${GREEN}║              Ready for Deployment 🚀                       ║${NC}"
    echo -e "${GREEN}╚════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "${CYAN}Next Steps:${NC}"
    echo -e "  1. ${BLUE}Deploy Gateway:${NC}     cd Gateway && ./start_gateway.sh"
    echo -e "  2. ${BLUE}Setup MyAGV:${NC}        Follow Myagv/README_MYAGV_ONLY.md"
    echo -e "  3. ${BLUE}Read Full Guide:${NC}    DEPLOYMENT.md"
    echo ""
else
    echo ""
    echo -e "${RED}╔════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${RED}║              ⚠️  ${ERRORS} ISSUE(S) FOUND                          ║${NC}"
    echo -e "${RED}║              Please fix before deployment                  ║${NC}"
    echo -e "${RED}╚════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "${YELLOW}📚 Documentation:${NC}"
    echo -e "  - ${BLUE}DEPLOYMENT.md${NC}     - Full deployment guide"
    echo -e "  - ${BLUE}NETWORK_SETUP.md${NC}  - Fix IP address issues"
    echo ""
fi

exit $ERRORS
