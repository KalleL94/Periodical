# Deployment Guide - Periodical

Guide for deploying Periodical to a production environment with HTTPS.

## Contents

1. [Overview](#overview)
2. [HTTPS configuration](#https-configuration)
3. [Reverse Proxy Setup](#reverse-proxy-setup)
4. [Process Manager Setup](#process-manager-setup)
5. [Database backup](#database-backup)
6. [Monitoring & Logging](#monitoring--logging)
7. [CI/CD with GitHub Actions](#cicd-with-github-actions)

---

## Overview

**Recommended production architecture:**

```
Internet
   ↓
[Reverse Proxy: nginx/traefik]  ← HTTPS (port 443)
   ↓                               SSL/TLS is terminated here
[FastAPI/Uvicorn]                ← HTTP (localhost:8000)
   ↓
[SQLite Database]
```

**Why this architecture?**
- ✅ The reverse proxy handles SSL/TLS (simpler certificate management)
- ✅ Several applications can run on the same server
- ✅ Static files are served efficiently
- ✅ Load balancing is possible
- ✅ DDoS protection and rate limiting

---

## HTTPS configuration

### Option 1: Nginx as reverse proxy (RECOMMENDED)

#### Step 1: Install Nginx

**Ubuntu/Debian:**
```bash
sudo apt update
sudo apt install nginx
```

**Windows:**
Download from https://nginx.org/en/download.html

#### Step 2: Obtain an SSL certificate

**With Let's Encrypt (free):**
```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.com
```

Certbot configures nginx automatically and sets up auto-renewal.

**With your own certificate:**
Place the certificate files in `/etc/ssl/certs/` and `/etc/ssl/private/`

#### Step 3: Nginx configuration

See `deployment/nginx-example.conf` for the full configuration.

**Key settings:**
```nginx
# Force HTTPS
server {
    listen 80;
    server_name your-domain.com;
    return 301 https://$server_name$request_uri;
}

# HTTPS server
server {
    listen 443 ssl http2;
    server_name your-domain.com;

    # SSL certificate
    ssl_certificate /etc/letsencrypt/live/your-domain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your-domain.com/privkey.pem;

    # Modern SSL settings
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;

    # Proxy to FastAPI
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Static files (optimisation)
    location /static {
        alias /opt/Periodical/app/static;
        expires 30d;
        add_header Cache-Control "public, immutable";
    }
}
```

#### Step 4: Test and start

```bash
# Test the configuration
sudo nginx -t

# Start or reload nginx
sudo systemctl restart nginx
sudo systemctl enable nginx  # Auto-start on boot
```

---

### Option 2: Traefik as reverse proxy

Traefik is simpler for Docker environments and has built-in Let's Encrypt integration.

See `deployment/traefik.yml` for the Docker Compose configuration.

**Advantages of Traefik:**
- ✅ Automatic SSL certificates from Let's Encrypt
- ✅ Automatic service discovery
- ✅ Built-in dashboard
- ✅ Well suited to Docker/Kubernetes

---

### Option 3: HTTPS directly in Uvicorn (NOT RECOMMENDED)

For test environments only, or if you cannot use a reverse proxy.

```bash
# Generate a self-signed certificate (test only)
openssl req -x509 -newkey rsa:4096 -keyout key.pem -out cert.pem -days 365 -nodes

# Start with SSL
uvicorn app.main:app --host 0.0.0.0 --port 8443 --ssl-keyfile key.pem --ssl-certfile cert.pem
```

**Note:** Self-signed certificates produce security warnings in browsers.

---

## Reverse Proxy Setup

### Environment variables for production

Create a `.env` file:
```bash
# .env
SECRET_KEY=your-long-random-secret-key-here-change-me
PRODUCTION=true
DATABASE_URL=sqlite:///./app/database/schedule.db
```

**Generate a secure SECRET_KEY:**
```bash
# Python
python -c "import secrets; print(secrets.token_urlsafe(32))"

# OpenSSL
openssl rand -base64 32
```

**Load the environment variables:**
```bash
# Linux/Mac
export $(cat .env | xargs)

# Windows (PowerShell)
Get-Content .env | ForEach-Object {
    $name, $value = $_.split('=')
    Set-Item -Path env:$name -Value $value
}
```

---

## Process Manager Setup

### Option 1: Systemd (Linux)

Create the service file: `/etc/systemd/system/ica-schedule.service`

```ini
[Unit]
Description=Periodical FastAPI Application
Documentation=https://github.com/your-repo/ica-schedule
After=network.target

[Service]
Type=simple
User=www-data
Group=www-data
WorkingDirectory=/opt/Periodical

# Environment variables
Environment="SECRET_KEY=CHANGE_ME_TO_RANDOM_SECRET"
Environment="PRODUCTION=true"
Environment="PYTHONUNBUFFERED=1"

# Load additional env vars from file (optional)
# EnvironmentFile=/opt/Periodical/.env

# Start command - use the venv for an isolated environment
ExecStart=/opt/Periodical/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1

# Restart policy
Restart=always
RestartSec=10
StartLimitInterval=0

# Security hardening
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/Periodical/app/database
ReadWritePaths=/opt/Periodical/logs

# Resource limits
LimitNOFILE=4096
MemoryMax=512M

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=ica-schedule

[Install]
WantedBy=multi-user.target
```

**What the security settings do:**
- **NoNewPrivileges**: prevents the process from gaining new privileges
- **PrivateTmp**: isolated /tmp directory for the service
- **ProtectSystem=strict**: the whole filesystem is read-only except the paths listed
- **ProtectHome**: blocks access to user home directories
- **ReadWritePaths**: only the database and logs are writable
- **MemoryMax**: caps memory usage at 512MB
- **LimitNOFILE**: at most 4096 open files

**Managing the service:**
```bash
sudo systemctl daemon-reload
sudo systemctl start ica-schedule
sudo systemctl enable ica-schedule  # Auto-start
sudo systemctl status ica-schedule  # Status
sudo journalctl -u ica-schedule -f  # Logs
```

---

### Option 2: Docker (ALL PLATFORMS)

See `deployment/Dockerfile` and `deployment/docker-compose.yml`

**Note:** use the files in the `deployment/` directory, not the root Dockerfile.

**Advantages:**
- ✅ Consistent environment
- ✅ Simple deployment
- ✅ Isolation
- ✅ Scalable

**Start:**
```bash
cd deployment
docker-compose up -d
```

---

### Option 3: Supervisor (Linux/Mac)

Install:
```bash
sudo apt install supervisor
```

Config: `/etc/supervisor/conf.d/ica-schedule.conf`
```ini
[program:ica-schedule]
command=/opt/Periodical/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
directory=/opt/Periodical
user=www-data
autostart=true
autorestart=true
stderr_logfile=/var/log/ica-schedule/err.log
stdout_logfile=/var/log/ica-schedule/out.log
environment=SECRET_KEY="your-key",PRODUCTION="true"
```

**Manage:**
```bash
sudo supervisorctl reread
sudo supervisorctl update
sudo supervisorctl start ica-schedule
```

---

## Database backup

### Automatic backup script

See `scripts/backup_database.sh`

```bash
#!/bin/bash
# Automatic SQLite backup

BACKUP_DIR="/path/to/backups"
DB_PATH="/opt/Periodical/app/database/schedule.db"
DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/schedule_backup_$DATE.db"

# Create the backup
sqlite3 "$DB_PATH" ".backup '$BACKUP_FILE'"

# Compress
gzip "$BACKUP_FILE"

# Remove backups older than 30 days
find "$BACKUP_DIR" -name "schedule_backup_*.db.gz" -mtime +30 -delete

echo "Backup complete: ${BACKUP_FILE}.gz"
```

**Schedule with cron:**
```bash
# Back up every day at 03:00
0 3 * * * /path/to/backup_database.sh
```

---

## Monitoring & Logging

### Structured Logging

See `app/core/logging_config.py` for the configuration.

**File-based logging:**
```python
# In app/main.py
import logging
from logging.handlers import RotatingFileHandler

# Rotating log files (max 10MB, keep 5 files)
handler = RotatingFileHandler(
    'logs/ica-schedule.log',
    maxBytes=10_000_000,
    backupCount=5
)
handler.setFormatter(logging.Formatter(
    '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
))
logging.getLogger().addHandler(handler)
```

### Error Tracking

**Sentry integration (recommended):**
```bash
pip install sentry-sdk[fastapi]
```

```python
# In app/main.py
import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration

sentry_sdk.init(
    dsn="your-sentry-dsn",
    integrations=[FastApiIntegration()],
    traces_sample_rate=0.1,
)
```

### Health Check Endpoint

Already implemented: `GET /health`

**Monitoring with Uptime Robot/Better Uptime:**
- Set up external monitoring against `https://your-domain.com/health`
- Notifications on downtime

---

## CI/CD with GitHub Actions

Periodical has a built-in CI/CD pipeline that automates testing and deployment.

### Overview of the CI/CD flow

```
Developer → PR to main
              ↓
         [CI Pipeline]
         - Syntax check
         - Pytest
         - Dependency check
              ↓
         PR Approved → Merge
              ↓
         [Deploy Pipeline]
         - SSH to prod server
         - Git pull
         - Pip install
         - Restart systemd service
         - Health check
              ↓
         Live in production ✅
```

**Two workflows:**

1. **CI Pipeline** (`.github/workflows/ci.yml`)
   - Triggered: on every Pull Request to `main`
   - Runs: syntax check + pytest
   - Prevents: buggy code from being merged

2. **Deploy Pipeline** (`.github/workflows/deploy.yml`)
   - Triggered: on push/merge to `main`
   - Runs: the deployment script over SSH
   - Result: automatic deployment to production

### GitHub Secrets Configuration

The following secrets must be configured in GitHub for CI/CD to work:

| Secret Name | Description | Example |
|-------------|-------------|---------|
| `PROD_HOST` | IP address or domain name of the production server | `192.168.1.100` or `prod.example.com` |
| `PROD_USER` | SSH user on the server | `deploy` or `www-data` |
| `PROD_SSH_KEY` | Private SSH key for authentication | Contents of `~/.ssh/id_ed25519` |
| `PROD_APP_PATH` | Absolute path to the application on the server | `/opt/Periodical` |

### Configuring GitHub Secrets

**Step 1: Open the repository settings**

1. Navigate to your GitHub repository
2. Click **Settings** (top of the page)
3. In the left menu: **Secrets and variables** → **Actions**
4. Click **New repository secret**

**Step 2: Add the secrets one at a time**

For each secret above:
- **Name**: the exact name from the table (for example `PROD_HOST`)
- **Value**: the corresponding value for your server
- Click **Add secret**

**Example:**
```
Name: PROD_HOST
Value: 192.168.1.100

Name: PROD_USER
Value: deploy

Name: PROD_APP_PATH
Value: /opt/Periodical
```

**PROD_SSH_KEY - special case:**

The SSH key must be in the right format:

```bash
# On your local machine - copy the whole key including header/footer
cat ~/.ssh/id_ed25519

# Output (copy ALL of this):
-----BEGIN OPENSSH PRIVATE KEY-----
b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZW
...many lines...
-----END OPENSSH PRIVATE KEY-----
```

Copy the **entire** output (including `-----BEGIN` and `-----END`) and paste it as the value for `PROD_SSH_KEY`.

### Preparing the server for GitHub Actions SSH

GitHub Actions needs to be able to SSH into the server without a password.

**Step 1: Create a deploy user (recommended)**

```bash
# Create a dedicated deploy user
sudo adduser deploy
sudo usermod -aG sudo deploy

# Allow the deploy user to restart the service WITHOUT a password
echo "deploy ALL=(ALL) NOPASSWD: /bin/systemctl restart ica-schedule" | sudo tee /etc/sudoers.d/deploy
echo "deploy ALL=(ALL) NOPASSWD: /bin/journalctl -u ica-schedule -n 20 --no-pager" | sudo tee -a /etc/sudoers.d/deploy
sudo chmod 0440 /etc/sudoers.d/deploy
```

**Step 2: Generate an SSH key pair (on your local machine)**

```bash
# Generate an ED25519 key (more modern and secure than RSA)
ssh-keygen -t ed25519 -C "github-actions-deploy" -f ~/.ssh/github_deploy_key

# This creates:
# - ~/.ssh/github_deploy_key      (private key - add to GitHub Secrets)
# - ~/.ssh/github_deploy_key.pub  (public key - install on the server)
```

**Step 3: Install the public key on the server**

```bash
# Copy the public key to the server
ssh-copy-id -i ~/.ssh/github_deploy_key.pub deploy@your-server-ip

# OR manually:
cat ~/.ssh/github_deploy_key.pub | ssh deploy@your-server-ip "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys"
```

**Step 4: Test the SSH connection**

```bash
# Test from your local machine
ssh -i ~/.ssh/github_deploy_key deploy@your-server-ip

# If you get in without a password: ✅ it works
```

**Step 5: Verify file permissions**

On the server:
```bash
# The deploy user must own the app directory
sudo chown -R deploy:deploy /opt/Periodical

# Verify
ls -la /opt/Periodical
# Output should show: drwxr-xr-x deploy deploy
```

**Step 6: Configure the systemd service (if not already done)**

Make sure the systemd service exists and is enabled:

```bash
# Verify that the service file exists
ls -la /etc/systemd/system/ica-schedule.service

# If it is missing, copy it from deployment/
sudo cp /opt/Periodical/deployment/ica-schedule.service /etc/systemd/system/

# Enable and start
sudo systemctl daemon-reload
sudo systemctl enable ica-schedule
sudo systemctl start ica-schedule
sudo systemctl status ica-schedule
```

### Verifying the CI/CD setup

**Test 1: create a test PR**

```bash
# Create a feature branch
git checkout -b test-ci-pipeline

# Make a small change
echo "# Test" >> README.md
git add README.md
git commit -m "Test CI pipeline"
git push origin test-ci-pipeline

# Create the PR via the GitHub UI or the gh CLI:
gh pr create --title "Test CI" --body "Testing CI pipeline"
```

**Expected result:**
- The CI workflow starts automatically
- Checkmarks ✅ appear on the PR when the tests pass
- If something is wrong: ❌ and error messages in the Actions log

**Test 2: test deployment (merge the PR)**

Once CI has passed:
```bash
# Merge the PR (via the GitHub UI or CLI)
gh pr merge --squash

# Watch the deployment
gh run watch
```

**Expected result:**
- The deploy workflow is triggered automatically
- The SSH connection to the server succeeds
- `deploy.sh` runs
- The health check returns 200 OK
- A comment on the commit: "**Deployment Successful**"

### Manual deployment (if the automatic one fails)

**Option 1: via the GitHub Actions UI (re-run)**

1. Go to **Actions** in GitHub
2. Select the failed workflow run
3. Click **Re-run failed jobs** or **Re-run all jobs**

**Option 2: manual SSH deploy**

```bash
# SSH to the server
ssh deploy@your-server-ip

# Run the deploy script manually
cd /opt/Periodical
bash scripts/deploy.sh /opt/Periodical

# Follow the output to spot any errors
```

**Option 3: manual deployment step by step**

If the deploy script does not work:

```bash
# 1. SSH to the server
ssh deploy@your-server-ip

# 2. Navigate to the app directory
cd /opt/Periodical

# 3. Back up the database (precaution)
sqlite3 app/database/schedule.db ".backup backup_$(date +%Y%m%d_%H%M%S).db"

# 4. Fetch the latest code
git pull

# 5. Activate the virtual environment
source venv/bin/activate

# 6. Install/update dependencies
pip install .

# 7. Run any migrations (if there are new ones)
# python migrations/migrate_*.py

# 8. Restart the service
sudo systemctl restart ica-schedule

# 9. Verify the status
sudo systemctl status ica-schedule

# 10. Test the health endpoint
curl http://127.0.0.1:8000/health
# Expected: {"status":"healthy"}
```

### CI/CD troubleshooting

**Problem: the CI tests fail**

```bash
# Run the tests locally first
pytest

# If they pass locally but not in CI:
# - Check the Python version (CI uses 3.14)
# - Check that all dependencies are listed in pyproject.toml
```

**Problem: the SSH connection fails**

**Symptom:** `Permission denied (publickey)` or a timeout

**Fix:**
```bash
# Verify that PROD_SSH_KEY was copied correctly (no extra spaces/newlines)
# Test SSH manually with the same key:
ssh -i ~/.ssh/github_deploy_key deploy@your-server-ip

# Check the SSH configuration on the server:
sudo cat /home/deploy/.ssh/authorized_keys
# Should contain your public key

# Verify permissions:
ls -la /home/deploy/.ssh
# authorized_keys should be 600, .ssh should be 700
```

**Problem: the health check fails**

**Symptom:** `❌ Health check failed with status: 000`

**Fix:**
```bash
# 1. Check that the service is running
sudo systemctl status ica-schedule

# 2. Read the most recent logs
sudo journalctl -u ica-schedule -n 50

# 3. Common causes:
# - Port 8000 already in use (change the port in the systemd file)
# - Virtual environment not found (check the path in systemd)
# - SECRET_KEY missing (add it to systemd Environment=)
# - Database migrations needed (run them manually)
```

**Problem: the deploy script hangs**

**Symptom:** the workflow times out after 10+ minutes

**Fix:**
```bash
# Check that deploy.sh is executable:
ssh deploy@your-server-ip "ls -la /opt/Periodical/scripts/deploy.sh"
# Should show: -rwxr-xr-x

# If not, make it executable:
ssh deploy@your-server-ip "chmod +x /opt/Periodical/scripts/deploy.sh"
```

### Best Practices

**1. Always test locally first:**
```bash
# Run the tests before pushing
pytest

# Syntax check
python -m py_compile app/main.py
```

**2. Use feature branches:**
```bash
git checkout -b feature/my-feature
# ... make changes ...
git push origin feature/my-feature
# Create a PR → CI runs automatically
```

**3. Back up before deploying:**

The deploy script does not back up automatically. Consider adding:
```bash
# In scripts/deploy.sh, before git pull:
sqlite3 app/database/schedule.db ".backup backup_$(date +%Y%m%d_%H%M%S).db"
```

**4. Monitor deployments:**
```bash
# Set up notifications
# - GitHub: Settings → Notifications → Actions
# - Email alerts on failed deployments
```

**5. Staging environment (recommended for larger projects):**

For critical production environments, add a staging step:
```yaml
# .github/workflows/deploy-staging.yml
on:
  push:
    branches:
      - develop
```

---

## Security checklist

- [ ] SECRET_KEY set via an environment variable (not the default)
- [ ] PRODUCTION=true in the environment variables
- [ ] HTTPS enabled (SSL certificate installed)
- [ ] HTTP-to-HTTPS redirect configured
- [ ] Firewall configured (only ports 80 and 443 open)
- [ ] Database backup scheduled
- [ ] Process manager configured (auto-restart)
- [ ] Logging configured (file-based)
- [ ] Error tracking enabled (Sentry)
- [ ] All users have changed from the default password
- [ ] File permissions correct (database read/write for the app user only)
- [ ] Rate limiting configured (in nginx/traefik)
- [ ] CORS settings restrictive (see docs/CORS.md)
- [ ] Security headers configured (X-Frame-Options, CSP, etc.)

### CORS Configuration

Periodical configures CORS automatically based on the environment:

**Development (PRODUCTION=false):**
- Loopback origins allowed for local testing
- All methods and headers allowed

**Production (PRODUCTION=true):**
- Only the specified origins are allowed
- Only the GET and POST methods
- Restrictive configuration

**Configuring CORS for production:**

```bash
# If you use a separate frontend
CORS_ORIGINS=https://your-frontend-domain.com,https://www.your-frontend-domain.com

# For the server-rendered app (default - most secure)
# No CORS_ORIGINS needed - all traffic is same-origin
```

See `docs/CORS.md` for the full guide.

---

## Quick start - production deploy

**1. Prepare the server:**
```bash
# Update the system
sudo apt update && sudo apt upgrade -y

# Verify the Python version (CI and the production image use Python 3.14)
python3 --version

# Install dependencies
sudo apt install python3 python3-pip python3-venv nginx certbot python3-certbot-nginx git sqlite3
```

**2. Clone/copy the application:**
```bash
cd /opt
sudo git clone /path/to/repo Periodical
cd Periodical

# Create a virtual environment (recommended)
python3 -m venv venv
source venv/bin/activate

# Install from pyproject.toml
pip install .
```

**3. Set the environment variables:**
```bash
# Generate a secret key
python3 -c "import secrets; print(secrets.token_urlsafe(32))" > /tmp/secret.txt

# Create .env
sudo tee .env > /dev/null <<EOF
SECRET_KEY=$(cat /tmp/secret.txt)
PRODUCTION=true
EOF

rm /tmp/secret.txt
```

**4. Run the migrations:**
```bash
python3 migrations/migrate_to_db.py
python3 migrations/migrate_add_password_change.py
```

**5. Configure nginx:**
```bash
sudo cp deployment/nginx-example.conf /etc/nginx/sites-available/ica-schedule
sudo ln -s /etc/nginx/sites-available/ica-schedule /etc/nginx/sites-enabled/
sudo nginx -t
```

**6. Obtain an SSL certificate:**
```bash
sudo certbot --nginx -d your-domain.com
```

**7. Start the application:**
```bash
sudo cp deployment/ica-schedule.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start ica-schedule
sudo systemctl enable ica-schedule
```

**8. Start nginx:**
```bash
sudo systemctl restart nginx
```

**9. Verify:**
```bash
# Check the app status
sudo systemctl status ica-schedule

# Check nginx
sudo systemctl status nginx

# Test HTTPS
curl -I https://your-domain.com
```

**10. Log in and change the password:**
- Go to `https://your-domain.com`
- Log in as `admin` with the password set in `migrations/migrate_to_db.py`
- Change the password when prompted
- Repeat for all users

**Done.** 🎉

---

## Troubleshooting

### Problem: 502 Bad Gateway

**Cause:** Uvicorn/FastAPI is not running.

**Fix:**
```bash
sudo systemctl status ica-schedule
sudo journalctl -u ica-schedule -n 50
```

### Problem: certificate errors

**Cause:** the certificate is not installed correctly.

**Fix:**
```bash
sudo certbot renew --dry-run
sudo nginx -t
```

### Problem: database locked

**Cause:** SQLite can hit locking problems with many concurrent writes.

**Fix:**
- Use `--workers 1` for uvicorn (a single worker)
- Or migrate to PostgreSQL for better concurrency

### Problem: static files do not work

**Cause:** nginx cannot find the files.

**Fix:**
```bash
# Verify the path in the nginx config
ls -la /opt/Periodical/app/static/
```

---

## Support and updates

**Logs:**
- Application: `sudo journalctl -u ica-schedule -f`
- Nginx: `sudo tail -f /var/log/nginx/error.log`
- Access: `sudo tail -f /var/log/nginx/access.log`

**Update the application:**
```bash
cd /opt/Periodical
git pull
sudo systemctl restart ica-schedule
```

**Back up before updating:**
```bash
sqlite3 app/database/schedule.db ".backup backup_$(date +%Y%m%d).db"
```
