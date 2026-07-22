#!/bin/bash
# Automatic SQLite database backup script for Periodical
# Schedule with cron: 0 3 * * * /path/to/backup_database.sh

set -e  # Exit on error

# Configuration
APP_DIR="/opt/ICA v0.0.20"
DB_PATH="$APP_DIR/app/database/schedule.db"
BACKUP_DIR="$APP_DIR/backups"
DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/schedule_backup_$DATE.db"
RETENTION_DAYS=30

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Logging function
log() {
    echo -e "${GREEN}[$(date +'%Y-%m-%d %H:%M:%S')]${NC} $1"
}

error() {
    echo -e "${RED}[$(date +'%Y-%m-%d %H:%M:%S')] ERROR:${NC} $1" >&2
}

warning() {
    echo -e "${YELLOW}[$(date +'%Y-%m-%d %H:%M:%S')] WARNING:${NC} $1"
}

# Check if database exists
if [ ! -f "$DB_PATH" ]; then
    error "Database not found at: $DB_PATH"
    exit 1
fi

# Create backup directory if it doesn't exist
mkdir -p "$BACKUP_DIR"

log "Starting database backup..."
log "Source: $DB_PATH"
log "Destination: $BACKUP_FILE"

# Perform backup using SQLite's .backup command (online backup)
if sqlite3 "$DB_PATH" ".backup '$BACKUP_FILE'"; then
    log "Backup created successfully"

    # Get file sizes
    ORIGINAL_SIZE=$(du -h "$DB_PATH" | cut -f1)
    BACKUP_SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
    log "Original size: $ORIGINAL_SIZE, Backup size: $BACKUP_SIZE"

    # Compress backup
    log "Compressing backup..."
    if gzip "$BACKUP_FILE"; then
        COMPRESSED_SIZE=$(du -h "${BACKUP_FILE}.gz" | cut -f1)
        log "Compression complete. Compressed size: $COMPRESSED_SIZE"
    else
        warning "Compression failed, keeping uncompressed backup"
    fi
else
    error "Backup failed!"
    exit 1
fi

# Clean up old backups
log "Cleaning up backups older than $RETENTION_DAYS days..."
DELETED_COUNT=$(find "$BACKUP_DIR" -name "schedule_backup_*.db.gz" -mtime +$RETENTION_DAYS -delete -print | wc -l)

if [ "$DELETED_COUNT" -gt 0 ]; then
    log "Deleted $DELETED_COUNT old backup(s)"
else
    log "No old backups to delete"
fi

# List current backups
BACKUP_COUNT=$(find "$BACKUP_DIR" -name "schedule_backup_*.db.gz" | wc -l)
log "Total backups: $BACKUP_COUNT"

# Calculate total backup size
TOTAL_SIZE=$(du -sh "$BACKUP_DIR" | cut -f1)
log "Total backup directory size: $TOTAL_SIZE"

log "Backup completed successfully!"

# Optional: Test backup integrity
log "Verifying backup integrity..."
if gzip -t "${BACKUP_FILE}.gz" 2>/dev/null; then
    log "Backup integrity verified"
else
    error "Backup integrity check failed!"
    exit 1
fi

exit 0
