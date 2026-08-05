#!/bin/bash
# Configure production SECRET_KEY for the Periodical systemd service.
# Run on the production host: sudo bash scripts/configure_prod_secret.sh

set -euo pipefail

GREEN="\033[0;32m"
RED="\033[0;31m"
YELLOW="\033[1;33m"
BLUE="\033[0;34m"
NC="\033[0m"

SERVICE_NAME="${SERVICE_NAME:-ica-schedule.service}"
ENV_FILE="${ENV_FILE:-/etc/periodical.env}"
OVERRIDE_DIR="/etc/systemd/system/${SERVICE_NAME}.d"
OVERRIDE_FILE="${OVERRIDE_DIR}/override.conf"
RESTART_SERVICE="ask"
ROTATE_SECRET="auto"

log() { echo -e "${GREEN}[OK]${NC} $1"; }
info() { echo -e "${BLUE}[INFO]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1" >&2; }

usage() {
    cat <<EOF
Usage: sudo bash scripts/configure_prod_secret.sh [options]

Options:
  --restart       Restart ${SERVICE_NAME} without prompting
  --no-restart    Configure only; do not restart the service
  --rotate        Always generate a new SECRET_KEY
  --keep          Keep an existing strong SECRET_KEY if present (default)
  --env-file PATH Use a different env file (default: ${ENV_FILE})
  --service NAME  Use a different service name (default: ${SERVICE_NAME})
  -h, --help      Show this help

Notes:
  - Rotating SECRET_KEY logs out existing users.
  - The generated key is not printed to stdout.
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --restart) RESTART_SERVICE="yes" ;;
        --no-restart) RESTART_SERVICE="no" ;;
        --rotate) ROTATE_SECRET="yes" ;;
        --keep) ROTATE_SECRET="auto" ;;
        --env-file)
            shift
            ENV_FILE="${1:?Missing value for --env-file}"
            ;;
        --service)
            shift
            SERVICE_NAME="${1:?Missing value for --service}"
            OVERRIDE_DIR="/etc/systemd/system/${SERVICE_NAME}.d"
            OVERRIDE_FILE="${OVERRIDE_DIR}/override.conf"
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            error "Unknown option: $1"
            usage
            exit 1
            ;;
    esac
    shift
done

if [ "${EUID}" -ne 0 ]; then
    error "Run as root: sudo bash scripts/configure_prod_secret.sh"
    exit 1
fi

command -v python3 >/dev/null 2>&1 || { error "python3 is required"; exit 1; }
command -v systemctl >/dev/null 2>&1 || { error "systemctl is required"; exit 1; }

if ! systemctl list-unit-files "${SERVICE_NAME}" >/dev/null 2>&1; then
    error "Service not found: ${SERVICE_NAME}"
    exit 1
fi

is_strong_secret() {
    local value="$1"
    [ "${#value}" -ge 32 ] || return 1
    case "$value" in
        "your-secret-key-change-this-in-production"|"change-me-to-random-secret"|"CHANGE_ME_TO_RANDOM_SECRET")
            return 1
            ;;
    esac
    return 0
}

existing_secret=""
if [ -f "${ENV_FILE}" ]; then
    existing_secret=$(awk -F= '/^SECRET_KEY=/{print substr($0, index($0, "=") + 1)}' "${ENV_FILE}" | tail -n 1)
fi

if [ "${ROTATE_SECRET}" = "yes" ] || ! is_strong_secret "${existing_secret}"; then
    if [ -n "${existing_secret}" ] && [ "${ROTATE_SECRET}" != "yes" ]; then
        warn "Existing SECRET_KEY in ${ENV_FILE} is missing, weak, or a known placeholder. Generating a new one."
    fi
    new_secret=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
else
    new_secret="${existing_secret}"
    info "Keeping existing strong SECRET_KEY from ${ENV_FILE}."
fi

if [ -f "${ENV_FILE}" ]; then
    backup="${ENV_FILE}.bak.$(date +%Y%m%d%H%M%S)"
    cp "${ENV_FILE}" "${backup}"
    chmod 600 "${backup}"
    log "Backed up existing env file to ${backup}"
fi

tmp_env=$(mktemp)
if [ -f "${ENV_FILE}" ]; then
    grep -v -E '^(SECRET_KEY|PRODUCTION|PYTHONUNBUFFERED)=' "${ENV_FILE}" > "${tmp_env}" || true
fi
{
    echo "SECRET_KEY=${new_secret}"
    echo "PRODUCTION=true"
    echo "PYTHONUNBUFFERED=1"
} >> "${tmp_env}"

install -m 600 -o root -g root "${tmp_env}" "${ENV_FILE}"
rm -f "${tmp_env}"
log "Wrote ${ENV_FILE} with 0600 permissions"

mkdir -p "${OVERRIDE_DIR}"
cat > "${OVERRIDE_FILE}" <<EOF
[Service]
Environment=
EnvironmentFile=${ENV_FILE}
EOF
chmod 644 "${OVERRIDE_FILE}"
log "Wrote systemd override ${OVERRIDE_FILE}"

systemctl daemon-reload
log "Reloaded systemd"

if [ "${RESTART_SERVICE}" = "ask" ]; then
    warn "Restarting ${SERVICE_NAME} will log out existing users if SECRET_KEY changed."
    read -r -p "Restart ${SERVICE_NAME} now? [y/N] " reply
    case "${reply}" in
        [Yy]*) RESTART_SERVICE="yes" ;;
        *) RESTART_SERVICE="no" ;;
    esac
fi

if [ "${RESTART_SERVICE}" = "yes" ]; then
    systemctl restart "${SERVICE_NAME}"
    log "Restarted ${SERVICE_NAME}"
    systemctl --no-pager --lines=8 status "${SERVICE_NAME}"
else
    info "Configuration complete. Restart later with: sudo systemctl restart ${SERVICE_NAME}"
fi

info "Current effective env sources:"
systemctl show "${SERVICE_NAME}" -p FragmentPath -p DropInPaths --no-pager
