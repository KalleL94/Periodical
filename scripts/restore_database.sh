#!/bin/bash
# Database restore script for Periodical
# Usage: ./restore_database.sh <backup-file.gz>

set -e  # Exit on error

# Configuration
APP_DIR="/opt/ICA v0.0.20"
DB_PATH="$APP_DIR/app/database/schedule.db"
BACKUP_DIR="$APP_DIR/backups"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log() {
    echo -e "${GREEN}[$(date +'%Y-%m-%d %H:%M:%S')]${NC} $1"
}

error() {
    echo -e "${RED}[$(date +'%Y-%m-%d %H:%M:%S')] ERROR:${NC} $1" >&2
}

warning() {
    echo -e "${YELLOW}[$(date +'%Y-%m-%d %H:%M:%S')] WARNING:${NC} $1"
}

# Check if backup file is provided
if [ -z "$1" ]; then
    error "Usage: $0 <backup-file.gz>"
    echo ""
    echo "Available backups:"
    ls -lh "$BACKUP_DIR"/schedule_backup_*.db.gz 2>/dev/null || echo "  No backups found"
    exit 1
fi

BACKUP_FILE="$1"

# Check if backup file exists
if [ ! -f "$BACKUP_FILE" ]; then
    error "Backup file not found: $BACKUP_FILE"
    exit 1
fi

log "Starting database restore from: $BACKUP_FILE"

# Create safety backup of current database
if [ -f "$DB_PATH" ]; then
    SAFETY_BACKUP="${DB_PATH}.before_restore_$(date +%Y%m%d_%H%M%S)"
    log "Creating safety backup of current database..."
    cp "$DB_PATH" "$SAFETY_BACKUP"
    log "Safety backup created: $SAFETY_BACKUP"
fi

# Decompress backup
TEMP_DIR=$(mktemp -d)
TEMP_BACKUP="$TEMP_DIR/schedule.db"

log "Decompressing backup..."
if gunzip -c "$BACKUP_FILE" > "$TEMP_BACKUP"; then
    log "Decompression successful"
else
    error "Decompression failed"
    rm -rf "$TEMP_DIR"
    exit 1
fi

# Verify backup integrity
log "Verifying backup integrity..."
if sqlite3 "$TEMP_BACKUP" "PRAGMA integrity_check;" | grep -q "ok"; then
    log "Backup integrity verified"
else
    error "Backup integrity check failed!"
    rm -rf "$TEMP_DIR"
    exit 1
fi

# Stop application (if using systemd)
if systemctl is-active --quiet ica-schedule; then
    warning "Stopping application..."
    sudo systemctl stop ica-schedule
    NEED_RESTART=true
else
    NEED_RESTART=false
fi

# Restore database
log "Restoring database..."
cp "$TEMP_BACKUP" "$DB_PATH"
log "Database restored successfully"

# Clean up temp files
rm -rf "$TEMP_DIR"

# Restart application if it was running
if [ "$NEED_RESTART" = true ]; then
    log "Restarting application..."
    sudo systemctl start ica-schedule
    log "Application restarted"
fi

log "Restore completed successfully!"
log ""
log "Safety backup available at: $SAFETY_BACKUP"
log "If something went wrong, you can restore it with:"
log "  cp $SAFETY_BACKUP $DB_PATH"

exit 0
