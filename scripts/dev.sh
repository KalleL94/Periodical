#!/bin/bash

# ==============================================================================
# Development Server Script for Periodical
# Usage: ./scripts/dev.sh [--port PORT] [--host HOST] [--docker]
# Example: ./scripts/dev.sh --port 8001 --host 127.0.0.1
# Example: ./scripts/dev.sh --docker --port 8001
# ==============================================================================

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

HOST="192.168.0.190"
PORT="8001"
USE_DOCKER=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --port)
            PORT="$2"
            shift 2
            ;;
        --host)
            HOST="$2"
            shift 2
            ;;
        --docker)
            USE_DOCKER=true
            shift
            ;;
        *)
            echo -e "${YELLOW}Unknown option: $1${NC}"
            exit 1
            ;;
    esac
done

if [ ! -f "app/main.py" ]; then
    echo -e "${YELLOW}Error: app/main.py not found. Run this script from the project root.${NC}"
    exit 1
fi

echo -e "${GREEN}╔════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║          Periodical Development Server Starting...          ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════════════════════════════╝${NC}"
echo ""

if [ "$USE_DOCKER" = true ]; then
    echo -e "${BLUE}Mode:${NC}     Docker (hot reload, PRODUCTION=false)"
    echo -e "${BLUE}Port:${NC}     $PORT"
    echo -e "${BLUE}URL:${NC}      http://localhost:$PORT"
    echo -e "${BLUE}Docs:${NC}     http://localhost:$PORT/docs"
    echo ""
    echo -e "${YELLOW}Press CTRL+C to quit${NC}"
    echo ""

    export DEV_PORT="$PORT"
    docker compose up --build
else
    echo -e "${BLUE}Mode:${NC}     Local venv"
    echo -e "${BLUE}Host:${NC}     $HOST"
    echo -e "${BLUE}Port:${NC}     $PORT"
    echo -e "${BLUE}URL:${NC}      http://$HOST:$PORT"
    echo -e "${BLUE}Docs:${NC}     http://$HOST:$PORT/docs"
    echo -e "${BLUE}Watching:${NC} app/ and tests/ directories only"
    echo ""
    echo -e "${YELLOW}Note: Only app/ and tests/ are watched to avoid file watch limit issues${NC}"
    echo -e "${YELLOW}Press CTRL+C to quit${NC}"
    echo ""

    if [ -z "$VIRTUAL_ENV" ]; then
        echo -e "${YELLOW}Warning: Virtual environment not activated.${NC}"
        echo -e "${BLUE}Attempting to activate venv...${NC}"
        if [ -f "venv/bin/activate" ]; then
            source venv/bin/activate
        else
            echo -e "${YELLOW}Error: venv not found. Please create and activate a virtual environment.${NC}"
            exit 1
        fi
    fi

    uvicorn app.main:app \
        --reload \
        --reload-dir app \
        --reload-dir tests \
        --host "$HOST" \
        --port "$PORT"
fi
