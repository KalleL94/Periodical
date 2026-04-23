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
SERVICE_NAME="ica-schedule"
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

# 2. Backup Database
DB_PATH="app/database/schedule.db"
BACKUP_DIR="backups"
TIMESTAMP=$(date +'%Y%m%d_%H%M%S')
BACKUP_FILE="$BACKUP_DIR/schedule_${TIMESTAMP}.db"

if [ -f "$DB_PATH" ]; then
    log "💾 Skapar databas-backup..."
    
    # Skapa backup-katalog om den inte finns
    mkdir -p "$BACKUP_DIR"
    
    # Kopiera databas
    if cp "$DB_PATH" "$BACKUP_FILE"; then
        log "✅ Backup skapad: $BACKUP_FILE"
        
        # Behåll endast de 10 senaste backuperna
        log "🧹 Rensar gamla backups (behåller 10 senaste)..."
        ls -t "$BACKUP_DIR"/schedule_*.db | tail -n +11 | xargs -r rm
    else
        error_exit "Kunde inte skapa databas-backup"
    fi
else
    warn "Databas hittades inte på $DB_PATH - hoppar över backup"
fi

# 3. Git Pull
log "📥 Hämtar senaste koden..."
git fetch origin

# Ta bort ospårade filer som konfliktar med inkommande commits
# (händer t.ex. när filer flyttas i repot men redan existerar lokalt)
CONFLICTING=$(git diff --name-status HEAD origin/main 2>/dev/null \
    | awk '$1=="A" || $1=="R100" {print $NF}' \
    | while read -r f; do [ -f "$f" ] && echo "$f"; done)
if [ -n "$CONFLICTING" ]; then
    warn "Tar bort lokala ospårade filer som konfliktar med origin/main:"
    echo "$CONFLICTING" | while read -r f; do warn "  - $f"; done
    echo "$CONFLICTING" | xargs rm -f
fi

if ! git merge --ff-only origin/main; then
    error_exit "Git merge misslyckades. Kontrollera nätverk eller konflikter."
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
# log "🔄 Letar efter migrations-script..."
# shopt -s nullglob # Gör att loopen inte körs om inga filer hittas
# migrations=(migrate_*.py)

# if [ ${#migrations[@]} -gt 0 ]; then
#     for migration in "${migrations[@]}"; do
#         log "   -> Kör migration: $migration"
#         if ! python "$migration"; then
#             error_exit "Migration misslyckades: $migration"
#         fi
#     done
# else
#     log "   -> Inga migrations-filer hittades (migrate_*.py). Hoppar över."
# fi

# 6. Starta om tjänsten
log "🔄 Startar om systemd-tjänsten ($SERVICE_NAME)..."
# Detta kräver sudo-rättigheter utan lösenord, vilket du konfigurerat tidigare
if ! sudo /usr/bin/systemctl restart "$SERVICE_NAME"; then
    error_exit "Misslyckades att starta om tjänsten. Kontrollera sudo-rättigheter eller systemctl status."
fi

# 7. Vänta på uppstart
log "⏳ Väntar 10 sekunder på att tjänsten ska starta..."
sleep 10

# 8. Health Check
log "🏥 Kör health check mot $HEALTH_URL..."
HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$HEALTH_URL")

if [ "$HTTP_STATUS" -eq 200 ]; then
    log "✅ Deployment slutförd! Health check svarade 200 OK."
    exit 0
else
    log "⏳ Väntar 30 sekunder på att tjänsten ska starta..."
    sleep 30
    HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$HEALTH_URL")
    if [ "$HTTP_STATUS" -eq 200 ]; then
        log "✅ Deployment slutförd efter väntan! Health check svarade 200 OK."
        exit 0
    fi
    # Hämta loggar för att se vad som gick fel
    log "❌ Health check misslyckades med status: $HTTP_STATUS"
    echo "--- Senaste loggarna ---"
    sudo journalctl -u "$SERVICE_NAME" -n 20 --no-pager
    exit 1
fi