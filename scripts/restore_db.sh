#!/bin/bash

# ==============================================================================
# Database Restore Script for Periodical
# Usage: ./restore_db.sh [BACKUP_FILE]
# Example: ./restore_db.sh backups/schedule_20251221_183000.db
# ==============================================================================

# Konfigurera färger för output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

DB_PATH="app/database/schedule.db"
SERVICE_NAME="ica-schedule"

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

# Kontrollera argument
if [ -z "$1" ]; then
    echo "Användning: $0 [BACKUP_FILE]"
    echo ""
    echo "Tillgängliga backups:"
    ls -lh backups/schedule_*.db 2>/dev/null || echo "  Inga backups hittades"
    exit 1
fi

BACKUP_FILE="$1"

# Kontrollera att backup-filen finns
if [ ! -f "$BACKUP_FILE" ]; then
    error_exit "Backup-fil hittades inte: $BACKUP_FILE"
fi

log "🔄 Återställer databas från backup..."
log "   Backup: $BACKUP_FILE"
log "   Mål: $DB_PATH"

# Bekräftelse
read -p "Är du säker på att du vill återställa databasen? Detta kommer att ersätta nuvarande data. (ja/nej): " -r
echo
if [[ ! $REPLY =~ ^[Jj][Aa]$ ]]; then
    log "Avbruten av användare."
    exit 0
fi

# Stoppa tjänsten först
log "⏸️  Stoppar tjänsten $SERVICE_NAME..."
if ! sudo /usr/bin/systemctl stop "$SERVICE_NAME"; then
    error_exit "Kunde inte stoppa tjänsten"
fi

# Skapa en backup av nuvarande databas innan restore
if [ -f "$DB_PATH" ]; then
    EMERGENCY_BACKUP="${DB_PATH}.before_restore_$(date +'%Y%m%d_%H%M%S')"
    log "💾 Skapar nöd-backup av nuvarande databas..."
    if cp "$DB_PATH" "$EMERGENCY_BACKUP"; then
        log "✅ Nöd-backup skapad: $EMERGENCY_BACKUP"
    else
        warn "Kunde inte skapa nöd-backup"
    fi
fi

# Återställ från backup
log "📥 Kopierar backup till $DB_PATH..."
if cp "$BACKUP_FILE" "$DB_PATH"; then
    log "✅ Databas återställd"
else
    error_exit "Kunde inte kopiera backup-fil"
fi

# Starta tjänsten igen
log "▶️  Startar tjänsten $SERVICE_NAME..."
if ! sudo /usr/bin/systemctl start "$SERVICE_NAME"; then
    error_exit "Kunde inte starta tjänsten"
fi

# Vänta på uppstart
log "⏳ Väntar 10 sekunder på att tjänsten ska starta..."
sleep 10

# Health Check
log "🏥 Kör health check..."
HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:8000/health")

if [ "$HTTP_STATUS" -eq 200 ]; then
    log "✅ Återställning slutförd! Tjänsten körs normalt."
    exit 0
else
    error_exit "Health check misslyckades med status: $HTTP_STATUS"
fi
