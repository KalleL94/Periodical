#!/bin/bash

# ==============================================================================
# Development Server Script for Periodical
# Usage: ./scripts/dev.sh [--port PORT] [--host HOST]
# Example: ./scripts/dev.sh --port 8001 --host 127.0.0.1
# ==============================================================================

# Konfigurera färger för output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Standard värden
HOST="127.0.0.1"
PORT="8001"

# Parse arguments
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
        *)
            echo -e "${YELLOW}Unknown option: $1${NC}"
            exit 1
            ;;
    esac
done

# Kontrollera att vi är i rätt katalog
if [ ! -f "app/main.py" ]; then
    echo -e "${YELLOW}Error: app/main.py not found. Run this script from the project root.${NC}"
    exit 1
fi

# Kontrollera att venv är aktiverat
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

echo -e "${GREEN}╔════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║          Periodical Development Server Starting...          ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${BLUE}Host:${NC}     $HOST"
echo -e "${BLUE}Port:${NC}     $PORT"
echo -e "${BLUE}URL:${NC}      http://$HOST:$PORT"
echo -e "${BLUE}Docs:${NC}     http://$HOST:$PORT/docs"
echo -e "${BLUE}Watching:${NC} app/ and tests/ directories only"
echo ""
echo -e "${YELLOW}Note: Only app/ and tests/ are watched to avoid file watch limit issues${NC}"
echo -e "${YELLOW}Press CTRL+C to quit${NC}"
echo ""

# Starta uvicorn med --reload-dir för att undvika file watch limit problem
uvicorn app.main:app \
    --reload \
    --reload-dir app \
    --reload-dir tests \
    --host "$HOST" \
    --port "$PORT"
