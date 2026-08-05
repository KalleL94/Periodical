#!/bin/bash
# Set secure file permissions for Periodical
# Run this script after deployment to ensure proper security

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log() {
    echo -e "${GREEN}[PERMISSIONS]${NC} $1"
}

warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

error() {
    echo -e "${RED}[ERROR]${NC} $1" >&2
}

# Get application directory
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
log "Application directory: $APP_DIR"

# Determine app user (who runs the application)
if [ -n "$1" ]; then
    APP_USER="$1"
    log "Using provided app user: $APP_USER"
else
    # Try to detect from systemd service
    if [ -f "/etc/systemd/system/ica-schedule.service" ]; then
        APP_USER=$(grep "^User=" /etc/systemd/system/ica-schedule.service | cut -d'=' -f2)
        log "Detected app user from systemd: $APP_USER"
    else
        APP_USER="$USER"
        warning "No systemd service found, using current user: $APP_USER"
    fi
fi

# Verify user exists
if ! id "$APP_USER" &>/dev/null; then
    error "User '$APP_USER' does not exist"
    exit 1
fi

log "Setting permissions for user: $APP_USER"

# 1. Application directory
log "Setting application directory permissions..."
sudo chown -R "$APP_USER:$APP_USER" "$APP_DIR"
sudo chmod 755 "$APP_DIR"

# 2. .env file (CRITICAL - contains secrets)
if [ -f "$APP_DIR/.env" ]; then
    log "Securing .env file..."
    sudo chown "$APP_USER:$APP_USER" "$APP_DIR/.env"
    sudo chmod 600 "$APP_DIR/.env"  # Only app user can read/write
    log ".env permissions: 600 (read/write for $APP_USER only)"
else
    warning ".env file not found - create it before running in production"
fi

# 3. Database directory and files
if [ -d "$APP_DIR/app/database" ]; then
    log "Securing database directory..."
    sudo chown -R "$APP_USER:$APP_USER" "$APP_DIR/app/database"
    sudo chmod 750 "$APP_DIR/app/database"  # Only app user and group

    # Database files
    for db in "$APP_DIR"/app/database/*.db; do
        if [ -f "$db" ]; then
            sudo chmod 640 "$db"  # Read/write for user, read for group
            log "Database file: $(basename "$db") - 640"
        fi
    done
else
    warning "Database directory not found"
fi

# 4. Logs directory and files
if [ -d "$APP_DIR/logs" ]; then
    log "Securing logs directory..."
    sudo chown -R "$APP_USER:$APP_USER" "$APP_DIR/logs"
    sudo chmod 750 "$APP_DIR/logs"

    # Log files
    for logfile in "$APP_DIR"/logs/*.log*; do
        if [ -f "$logfile" ]; then
            sudo chmod 640 "$logfile"  # Read/write for user, read for group
        fi
    done
    log "Logs directory: 750, files: 640"
else
    warning "Logs directory not found - will be created on first run"
fi

# 5. Backups directory
if [ -d "$APP_DIR/backups" ]; then
    log "Securing backups directory..."
    sudo chown -R "$APP_USER:$APP_USER" "$APP_DIR/backups"
    sudo chmod 700 "$APP_DIR/backups"  # Only app user

    # Backup files
    for backup in "$APP_DIR"/backups/*; do
        if [ -f "$backup" ]; then
            sudo chmod 600 "$backup"  # Only app user can read/write
        fi
    done
    log "Backups directory: 700 (owner only)"
else
    warning "Backups directory not found - will be created by backup script"
fi

# 6. Scripts (make executable)
if [ -d "$APP_DIR/scripts" ]; then
    log "Setting script permissions..."
    sudo chown -R "$APP_USER:$APP_USER" "$APP_DIR/scripts"
    sudo chmod 750 "$APP_DIR/scripts"

    for script in "$APP_DIR"/scripts/*.sh; do
        if [ -f "$script" ]; then
            sudo chmod 750 "$script"  # Executable by owner and group
            log "Script: $(basename "$script") - 750 (executable)"
        fi
    done
fi

# 7. Python files (not executable, readable)
log "Setting Python file permissions..."
find "$APP_DIR/app" -type f -name "*.py" -exec sudo chmod 644 {} \;

# 8. Templates and static files (readable)
log "Setting template and static file permissions..."
if [ -d "$APP_DIR/app/templates" ]; then
    sudo chmod 755 "$APP_DIR/app/templates"
    find "$APP_DIR/app/templates" -type f -exec sudo chmod 644 {} \;
fi

if [ -d "$APP_DIR/app/static" ]; then
    sudo chmod 755 "$APP_DIR/app/static"
    find "$APP_DIR/app/static" -type f -exec sudo chmod 644 {} \;
fi

# 9. Virtual environment (if exists)
if [ -d "$APP_DIR/venv" ]; then
    log "Setting virtual environment permissions..."
    sudo chown -R "$APP_USER:$APP_USER" "$APP_DIR/venv"
    sudo chmod 755 "$APP_DIR/venv"

    # Make venv binaries executable
    if [ -d "$APP_DIR/venv/bin" ]; then
        sudo chmod 755 "$APP_DIR/venv/bin"
        find "$APP_DIR/venv/bin" -type f -exec sudo chmod 755 {} \;
    fi
fi

# 10. Deployment files (readable but not executable)
if [ -d "$APP_DIR/deployment" ]; then
    log "Setting deployment file permissions..."
    sudo chown -R "$APP_USER:$APP_USER" "$APP_DIR/deployment"
    sudo chmod 755 "$APP_DIR/deployment"
    find "$APP_DIR/deployment" -type f -exec sudo chmod 644 {} \;
fi

# 11. Documentation (readable)
if [ -d "$APP_DIR/docs" ]; then
    log "Setting documentation permissions..."
    sudo chmod 755 "$APP_DIR/docs"
    find "$APP_DIR/docs" -type f -exec sudo chmod 644 {} \;
fi

# Summary
echo ""
echo "============================================================"
log "Permission setup completed successfully!"
echo "============================================================"
echo ""
log "Summary of permissions:"
echo "  .env file:           600 (owner read/write only) - CRITICAL"
echo "  Database files:      640 (owner rw, group r)"
echo "  Database directory:  750 (owner rwx, group rx)"
echo "  Log files:           640 (owner rw, group r)"
echo "  Log directory:       750 (owner rwx, group rx)"
echo "  Backup files:        600 (owner read/write only)"
echo "  Backup directory:    700 (owner only)"
echo "  Scripts:             750 (owner/group executable)"
echo "  Python files:        644 (owner rw, others r)"
echo "  Static files:        644 (owner rw, others r)"
echo "  Directories:         755 (owner rwx, others rx)"
echo ""
log "All files owned by: $APP_USER:$APP_USER"
echo ""
warning "Remember to:"
echo "  - Keep .env file secure (never commit to git)"
echo "  - Run backups as $APP_USER"
echo "  - Check permissions after updates"
echo ""

# Verification
log "Verifying critical files..."
if [ -f "$APP_DIR/.env" ]; then
    ENV_PERMS=$(stat -c "%a" "$APP_DIR/.env" 2>/dev/null || stat -f "%OLp" "$APP_DIR/.env" 2>/dev/null)
    if [ "$ENV_PERMS" = "600" ]; then
        log ".env permissions verified: 600 ✓"
    else
        warning ".env permissions: $ENV_PERMS (should be 600)"
    fi
fi

if [ -d "$APP_DIR/backups" ]; then
    BACKUP_PERMS=$(stat -c "%a" "$APP_DIR/backups" 2>/dev/null || stat -f "%OLp" "$APP_DIR/backups" 2>/dev/null)
    if [ "$BACKUP_PERMS" = "700" ]; then
        log "Backups directory verified: 700 ✓"
    else
        warning "Backups directory: $BACKUP_PERMS (should be 700)"
    fi
fi

echo ""
log "Done!"
exit 0
