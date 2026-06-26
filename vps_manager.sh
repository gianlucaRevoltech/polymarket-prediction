#!/bin/bash

# Polymarket Paper Trading Bot - VPS Management Script
# Usage: ./vps_manager.sh {start|stop|restart|status|logs}

BOT_NAME="polymarket-bot"
BOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$BOT_DIR/data/bot.pid"
LOG_FILE="$BOT_DIR/logs/bot.log"
SRC_DIR="$BOT_DIR/src"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if bot is running
is_running() {
    if [ -f "$PID_FILE" ]; then
        pid=$(cat "$PID_FILE")
        if ps -p "$pid" > /dev/null 2>&1; then
            return 0
        fi
    fi
    return 1
}

# Start bot
start() {
    if is_running; then
        echo -e "${YELLOW}Bot is already running (PID: $(cat $PID_FILE))${NC}"
        return 1
    fi
    
    echo -e "${GREEN}Starting $BOT_NAME...${NC}"
    
    # Create directories
    mkdir -p "$BOT_DIR/data" "$BOT_DIR/logs"
    
    # Install dependencies quietly
    pip3 install -q -r "$BOT_DIR/requirements.txt" 2>/dev/null
    
    # Start bot in background with nohup
    cd "$SRC_DIR"
    nohup python3 main.py >> "$LOG_FILE" 2>&1 &
    pid=$!
    
    # Save PID
    echo $pid > "$PID_FILE"
    
    # Wait a bit and check if still running
    sleep 2
    if is_running; then
        echo -e "${GREEN}Bot started successfully (PID: $pid)${NC}"
        echo -e "Logs: $LOG_FILE"
    else
        echo -e "${RED}Failed to start bot. Check logs: $LOG_FILE${NC}"
        rm -f "$PID_FILE"
        return 1
    fi
}

# Stop bot
stop() {
    if ! is_running; then
        echo -e "${YELLOW}Bot is not running${NC}"
        rm -f "$PID_FILE"
        return 0
    fi
    
    pid=$(cat "$PID_FILE")
    echo -e "${YELLOW}Stopping $BOT_NAME (PID: $pid)...${NC}"
    
    # Kill process
    kill "$pid" 2>/dev/null
    
    # Wait for process to stop
    for i in {1..10}; do
        if ! is_running; then
            break
        fi
        sleep 1
    done
    
    # Force kill if still running
    if is_running; then
        echo -e "${YELLOW}Force killing...${NC}"
        kill -9 "$pid" 2>/dev/null
        sleep 1
    fi
    
    rm -f "$PID_FILE"
    echo -e "${GREEN}Bot stopped${NC}"
}

# Restart bot
restart() {
    echo -e "${YELLOW}Restarting $BOT_NAME...${NC}"
    stop
    sleep 2
    start
}

# Show status
status() {
    if is_running; then
        pid=$(cat "$PID_FILE")
        echo -e "${GREEN}$BOT_NAME is running (PID: $pid)${NC}"
        
        # Show uptime
        if [ -f "$LOG_FILE" ]; then
            echo "Log file: $LOG_FILE"
            echo "Log size: $(du -h "$LOG_FILE" | cut -f1)"
        fi
        
        # Show portfolio summary if available
        if [ -f "$BOT_DIR/data/portfolio_state.json" ]; then
            echo ""
            echo "Portfolio state: OK"
        fi
    else
        echo -e "${RED}$BOT_NAME is not running${NC}"
    fi
}

# Show logs
logs() {
    if [ ! -f "$LOG_FILE" ]; then
        echo -e "${RED}No log file found${NC}"
        return 1
    fi
    
    lines=${1:-50}
    echo -e "${GREEN}Last $lines lines from $LOG_FILE:${NC}"
    echo ""
    tail -n "$lines" "$LOG_FILE"
}

# Follow logs in real-time
follow() {
    if [ ! -f "$LOG_FILE" ]; then
        echo -e "${RED}No log file found${NC}"
        return 1
    fi
    
    echo -e "${GREEN}Following logs (Ctrl+C to exit):${NC}"
    echo ""
    tail -f "$LOG_FILE"
}

# Main
case "$1" in
    start)
        start
        ;;
    stop)
        stop
        ;;
    restart)
        restart
        ;;
    status)
        status
        ;;
    logs)
        logs "$2"
        ;;
    follow)
        follow
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status|logs [lines]|follow}"
        echo ""
        echo "Commands:"
        echo "  start   - Start the bot in background"
        echo "  stop    - Stop the bot"
        echo "  restart - Restart the bot"
        echo "  status  - Check if bot is running"
        echo "  logs    - Show last N lines of logs (default: 50)"
        echo "  follow  - Follow logs in real-time"
        exit 1
        ;;
esac

exit 0
