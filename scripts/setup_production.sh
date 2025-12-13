#!/bin/bash
# Production setup script for Periodical
# Run this script on a fresh server to set up the application

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log() {
    echo -e "${GREEN}[SETUP]${NC} $1"
}

error() {
    echo -e "${RED}[ERROR]${NC} $1" >&2
}

warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

echo "============================================================"
echo "Periodical - Production Setup"
echo "============================================================"
echo ""

# Check if running as root
if [ "$EUID" -eq 0 ]; then
    warning "Running as root. Consider using a non-root user for the application."
fi

# Get application directory
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
log "Application directory: $APP_DIR"

# Step 1: Check dependencies
log "Step 1: Checking dependencies..."

command -v python3 >/dev/null 2>&1 || { error "python3 is required but not installed. Aborting."; exit 1; }
command -v pip3 >/dev/null 2>&1 || { error "pip3 is required but not installed. Aborting."; exit 1; }
command -v sqlite3 >/dev/null 2>&1 || { warning "sqlite3 not found. Install with: sudo apt install sqlite3"; }

info "Python version: $(python3 --version)"
info "Pip version: $(pip3 --version)"

# Step 2: Create .env file
log "Step 2: Creating environment configuration..."

if [ -f "$APP_DIR/.env" ]; then
    warning ".env file already exists. Skipping creation."
    read -p "Do you want to regenerate SECRET_KEY? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        REGENERATE_KEY=true
    else
        REGENERATE_KEY=false
    fi
else
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    log "Created .env file from .env.example"
    REGENERATE_KEY=true
fi

if [ "$REGENERATE_KEY" = true ]; then
    # Generate secure SECRET_KEY
    log "Generating secure SECRET_KEY..."
    SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")

    # Replace SECRET_KEY in .env
    if [[ "$OSTYPE" == "darwin"* ]]; then
        # macOS
        sed -i '' "s|SECRET_KEY=.*|SECRET_KEY=$SECRET_KEY|" "$APP_DIR/.env"
    else
        # Linux
        sed -i "s|SECRET_KEY=.*|SECRET_KEY=$SECRET_KEY|" "$APP_DIR/.env"
    fi

    log "SECRET_KEY generated and saved to .env"
fi

# Step 3: Install Python dependencies
log "Step 3: Installing Python dependencies..."

cd "$APP_DIR"
pip3 install -r requirements.txt --quiet

log "Dependencies installed"

# Step 4: Create necessary directories
log "Step 4: Creating directories..."

mkdir -p "$APP_DIR/logs"
mkdir -p "$APP_DIR/backups"
mkdir -p "$APP_DIR/app/database"

log "Directories created"

# Step 5: Database setup
log "Step 5: Setting up database..."

if [ -f "$APP_DIR/app/database/schedule.db" ]; then
    warning "Database already exists. Skipping initial migration."

    read -p "Do you want to run password change migration? (Y/n): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Nn]$ ]]; then
        python3 "$APP_DIR/migrate_add_password_change.py"
    fi
else
    log "Running initial database migration..."
    python3 "$APP_DIR/migrate_to_db.py"

    log "Running password change migration..."
    python3 "$APP_DIR/migrate_add_password_change.py"
fi

log "Database setup complete"

# Step 6: Set file permissions
log "Step 6: Setting file permissions..."

chmod 600 "$APP_DIR/.env"
chmod 700 "$APP_DIR/scripts"/*.sh
chmod 755 "$APP_DIR/app/database"
chmod 644 "$APP_DIR/app/database"/*.db 2>/dev/null || true

log "Permissions set"

# Step 7: Test application
log "Step 7: Testing application..."

if python3 -c "from app.main import app; print('OK')" >/dev/null 2>&1; then
    log "Application imports successfully"
else
    error "Application failed to import. Check for errors above."
    exit 1
fi

# Summary
echo ""
echo "============================================================"
log "Setup completed successfully!"
echo "============================================================"
echo ""
info "Next steps:"
echo "  1. Review and update .env file if needed"
echo "  2. Set up reverse proxy (nginx/traefik)"
echo "  3. Configure SSL certificates"
echo "  4. Set up systemd service or Docker"
echo "  5. Configure database backups (cron)"
echo ""
info "Start application manually:"
echo "  cd \"$APP_DIR\""
echo "  source .env"
echo "  uvicorn app.main:app --host 127.0.0.1 --port 8000"
echo ""
info "Set up systemd service:"
echo "  sudo cp deployment/ica-schedule.service /etc/systemd/system/"
echo "  sudo systemctl daemon-reload"
echo "  sudo systemctl enable --now ica-schedule"
echo ""
info "Default credentials:"
echo "  Admin: admin / Banan1 (MUST CHANGE ON FIRST LOGIN)"
echo "  Users: see persons.json (password: London1)"
echo ""
warning "IMPORTANT: Change all default passwords on first login!"
echo ""

exit 0
