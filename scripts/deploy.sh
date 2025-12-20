#!/bin/bash

# ==============================================================================
# Deployment Script for Periodical
# Usage: ./deploy.sh [APP_PATH]
# Example: ./deploy.sh /opt/Periodical
# ==============================================================================

# Konfigurera färger för output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 1. Hantera argument (APP_PATH)
APP_PATH="${1:-.}"
SERVICE_NAME="periodical"
HEALTH_URL="http://127.0.0.1:8000/health"

# Funktion för loggning med timestamp
log() {
    echo -e "${GREEN}[$(date +'%Y-%m-%d %H:%M:%S')]${NC} $1"
}

warn() {
    echo -e "${YELLOW}[$(date +'%Y-%m-%d %H:%M:%S')] VARNING: $1${NC}"
}

error_exit() {
    echo -e "${RED}[$(date +'%Y-%m-%d %H:%M:%S')] FEL: $1${NC}"
    exit 1
}

# Starta deployment
log "🚀 Startar deployment för $SERVICE_NAME i $APP_PATH"

# Navigera till katalogen
if [ -d "$APP_PATH" ]; then
    cd "$APP_PATH" || error_exit "Kunde inte navigera till $APP_PATH"
else
    error_exit "Katalogen $APP_PATH existerar inte."
fi

# 2. Git Pull
log "📥 Hämtar senaste koden..."
if ! git pull; then
    error_exit "Git pull misslyckades. Kontrollera nätverk eller konflikter."
fi

# Aktivera virtual environment (Kritiskt steg)
if [ -f "venv/bin/activate" ]; then
    log "🐍 Aktiverar virtual environment..."
    source venv/bin/activate
else
    warn "Hittade inget venv/bin/activate. Kör med system-python (RISKABELT)."
fi

# 3. Pip Install
log "📦 Installerar/uppdaterar dependencies..."
if ! pip install .; then
    error_exit "Pip install misslyckades."
fi

# 4. Kör migrationer
# Letar efter filer som matchar mönstret migrate_*.py
log "🔄 Letar efter migrations-script..."
shopt -s nullglob # Gör att loopen inte körs om inga filer hittas
migrations=(migrate_*.py)

if [ ${#migrations[@]} -gt 0 ]; then
    for migration in "${migrations[@]}"; do
        log "   -> Kör migration: $migration"
        if ! python "$migration"; then
            error_exit "Migration misslyckades: $migration"
        fi
    done
else
    log "   -> Inga migrations-filer hittades (migrate_*.py). Hoppar över."
fi

# 5. Starta om tjänsten
log "reStartar om systemd-tjänsten ($SERVICE_NAME)..."
# Detta kräver sudo-rättigheter utan lösenord, vilket du konfigurerat tidigare
if ! sudo systemctl restart "$SERVICE_NAME"; then
    error_exit "Misslyckades att starta om tjänsten. Kontrollera sudo-rättigheter eller systemctl status."
fi

# 6. Vänta på uppstart
log "⏳ Väntar 5 sekunder på att tjänsten ska starta..."
sleep 5

# 7. Health Check
log "🏥 Kör health check mot $HEALTH_URL..."
HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$HEALTH_URL")

if [ "$HTTP_STATUS" -eq 200 ]; then
    log "✅ Deployment slutförd! Health check svarade 200 OK."
    exit 0
else
    # Hämta loggar för att se vad som gick fel
    log "❌ Health check misslyckades med status: $HTTP_STATUS"
    echo "--- Senaste loggarna ---"
    sudo journalctl -u "$SERVICE_NAME" -n 20 --no-pager
    exit 1
fi