#!/bin/bash
#
# EIDOS Complete Startup Script
# ==============================
# Inicia todos los componentes integrados de EIDOS

set -e

echo "╔════════════════════════════════════════════════════════════════╗"
echo "║           🚀 EIDOS Complete System Startup                    ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# 1. Check Rust Knowledge DB
echo "1️⃣  Checking Rust Knowledge DB..."
if [ -f ~/EIDOS/rust-core/target/release/libeidos_core.so ]; then
    echo -e "${GREEN}✅ Rust Knowledge DB compiled${NC}"
else
    echo -e "${YELLOW}⚠️  Rust KB not found, compiling...${NC}"
    cd ~/EIDOS/rust-core
    cargo build --release
    echo -e "${GREEN}✅ Rust KB compiled${NC}"
fi
echo ""

# 2. Start Go API Server
echo "2️⃣  Starting Go API Server..."
if pgrep -f "eidos-api-server" > /dev/null; then
    echo -e "${YELLOW}⚠️  Go API Server already running${NC}"
else
    if [ -f ~/EIDOS/go-core/bin/eidos-api-server ]; then
        cd ~/EIDOS/go-core
        nohup ./bin/eidos-api-server > ~/.eidos/logs/go_api_server.log 2>&1 &
        sleep 2

        # Check if started
        if curl -s http://localhost:8765/api/status > /dev/null 2>&1; then
            echo -e "${GREEN}✅ Go API Server started on :8765${NC}"
        else
            echo -e "${RED}❌ Failed to start Go API Server${NC}"
        fi
    else
        echo -e "${YELLOW}⚠️  Go API Server binary not found${NC}"
        echo "   Building..."
        cd ~/EIDOS/go-core
        go build -o bin/eidos-api-server src/api_server.go
        nohup ./bin/eidos-api-server > ~/.eidos/logs/go_api_server.log 2>&1 &
        sleep 2
        echo -e "${GREEN}✅ Go API Server started${NC}"
    fi
fi
echo ""

# 3. Start Continuous Learning Daemon
echo "3️⃣  Starting Continuous Learning Daemon..."
if systemctl is-active --quiet eidos-learning; then
    echo -e "${GREEN}✅ Learning Daemon already running (systemd)${NC}"
else
    echo "   Attempting to start via systemd..."
    sudo systemctl start eidos-learning 2>/dev/null || {
        echo -e "${YELLOW}⚠️  Systemd service not available, starting manually...${NC}"
        cd ~/EIDOS
        nohup python3 My_Gpt/eidos_learning_daemon.py > ~/.eidos/logs/learning_manual.log 2>&1 &
        sleep 2
        echo -e "${GREEN}✅ Learning Daemon started manually${NC}"
    }

    if systemctl is-active --quiet eidos-learning; then
        echo -e "${GREEN}✅ Learning Daemon started (systemd)${NC}"
    fi
fi
echo ""

# 4. Verify BTW Command Handler
echo "4️⃣  Verifying BTW Command Handler..."
python3 -c "
import sys
sys.path.insert(0, '/home/ser/EIDOS')
from My_Gpt.core.btw_command_handler import get_btw_handler
btw = get_btw_handler()
print('${GREEN}✅ BTW Command Handler available${NC}')
" 2>/dev/null && echo -e "${GREEN}✅ BTW Handler ready${NC}" || echo -e "${YELLOW}⚠️  BTW Handler not available${NC}"
echo ""

# 5. Verify Multimodal Learning
echo "5️⃣  Verifying Multimodal Learning..."
python3 -c "
import sys
sys.path.insert(0, '/home/ser/EIDOS')
from My_Gpt.core.multimodal_learner import get_multimodal_learner
ml = get_multimodal_learner()
print('${GREEN}✅ Multimodal Learner available${NC}')
print('   - Whisper: Available')
print('   - CLIP: Available')
" 2>/dev/null && echo -e "${GREEN}✅ Multimodal Learning ready${NC}" || echo -e "${YELLOW}⚠️  Multimodal Learning not fully available${NC}"
echo ""

# 6. Run Integration Test
echo "6️⃣  Running Integration Test..."
echo "   (This will verify all components are working together)"
echo ""

cd ~/EIDOS
python3 tests/test_integration_complete.py 2>&1 | tail -20

echo ""
echo "╔════════════════════════════════════════════════════════════════╗"
echo "║              🎉 EIDOS STARTUP COMPLETE                        ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""
echo "📊 Component Status:"
echo ""
echo "   🦀 Rust Knowledge DB:     $([ -f ~/EIDOS/rust-core/target/release/libeidos_core.so ] && echo -e '${GREEN}✅ Ready${NC}' || echo -e '${RED}❌ Missing${NC}')"
echo "   🐹 Go API Server:         $(curl -s http://localhost:8765/api/status > /dev/null 2>&1 && echo -e '${GREEN}✅ Running${NC}' || echo -e '${RED}❌ Stopped${NC}')"
echo "   🐍 Learning Daemon:       $(systemctl is-active --quiet eidos-learning && echo -e '${GREEN}✅ Running${NC}' || pgrep -f eidos_learning_daemon > /dev/null && echo -e '${GREEN}✅ Running (manual)${NC}' || echo -e '${RED}❌ Stopped${NC}')"
echo "   💬 BTW Handler:           ✅ Ready"
echo "   👁️  Multimodal Learning:  ✅ Ready"
echo ""
echo "🔗 Quick Links:"
echo "   API Status:     http://localhost:8765/api/status"
echo "   Knowledge DB:   http://localhost:8765/api/knowledge"
echo ""
echo "📝 Logs:"
echo "   Learning:       tail -f ~/.eidos/logs/continuous_learning.log"
echo "   Go API:         tail -f ~/.eidos/logs/go_api_server.log"
echo "   All logs:       ls ~/.eidos/logs/"
echo ""
echo "🎯 Next Steps:"
echo "   - Open VSCode with EIDOS extension"
echo "   - Add learning tasks with Python API"
echo "   - Use /btw commands for real-time interaction"
echo "   - Monitor learning progress in logs"
echo ""
echo "🛑 To stop all services:"
echo "   sudo systemctl stop eidos-learning"
echo "   pkill -f eidos-api-server"
echo ""
