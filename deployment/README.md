# Deployment Configuration Files

This directory contains configuration files and examples for deploying Periodical in production.

## Files Overview

### Reverse Proxy Configurations

- **`nginx-example.conf`** - Nginx reverse proxy configuration with HTTPS
  - SSL/TLS configuration
  - Security headers
  - Rate limiting
  - Static file serving optimization

- **`traefik.yml`** - Traefik static configuration
  - Automatic Let's Encrypt SSL certificates
  - Docker provider configuration
  - Access logging

### Container Deployments

- **`Dockerfile`** - Docker image build instructions
  - Python 3.12 slim base
  - Non-root user
  - Health checks

- **`docker-compose.yml`** - Docker Compose with Traefik
  - Complete production setup
  - Automatic HTTPS via Traefik
  - Volume persistence
  - Security labels

### Process Management

- **`ica-schedule.service`** - Systemd service unit file
  - Auto-restart on failure
  - Resource limits
  - Security hardening
  - Environment variable support

## Quick Start Guides

### Option 1: Nginx + Systemd (Traditional)

1. **Install dependencies:**
   ```bash
   sudo apt install nginx certbot python3-certbot-nginx
   ```

2. **Configure Nginx:**
   ```bash
   sudo cp nginx-example.conf /etc/nginx/sites-available/ica-schedule
   # Edit the file and replace 'your-domain.com' with your actual domain
   sudo ln -s /etc/nginx/sites-available/ica-schedule /etc/nginx/sites-enabled/
   sudo nginx -t
   ```

3. **Get SSL certificate:**
   ```bash
   sudo certbot --nginx -d your-domain.com
   ```

4. **Set up systemd service:**
   ```bash
   sudo cp ica-schedule.service /etc/systemd/system/
   # Edit the file and update paths and SECRET_KEY
   sudo systemctl daemon-reload
   sudo systemctl enable --now ica-schedule
   ```

### Option 2: Docker + Traefik (Modern)

1. **Prepare environment:**
   ```bash
   # Create traefik directory and config
   mkdir -p traefik
   cp traefik.yml traefik/
   touch traefik/acme.json
   chmod 600 traefik/acme.json
   ```

2. **Configure:**
   ```bash
   # Edit docker-compose.yml
   # - Replace 'your-domain.com' with actual domain
   # - Replace 'your-email@example.com' in traefik.yml
   # - Set SECRET_KEY environment variable
   ```

3. **Start:**
   ```bash
   docker-compose up -d
   ```

4. **View logs:**
   ```bash
   docker-compose logs -f ica-schedule
   ```

## Configuration Checklist

Before deploying, update these values:

### In `nginx-example.conf`:
- [ ] `server_name your-domain.com` → your actual domain
- [ ] SSL certificate paths (if not using Certbot)
- [ ] `/opt/ICA v0.0.20` → actual application path

### In `ica-schedule.service`:
- [ ] `WorkingDirectory` → actual application path
- [ ] `SECRET_KEY` → random secret key (generate with scripts/setup_production.sh)
- [ ] `User` and `Group` → appropriate system user

### In `docker-compose.yml`:
- [ ] `your-domain.com` → actual domain
- [ ] `SECRET_KEY` → random secret key
- [ ] Update admin password for Traefik dashboard
- [ ] Email address in traefik.yml for Let's Encrypt

### In `traefik.yml`:
- [ ] `email` → your email for Let's Encrypt notifications

## Environment Variables

Required environment variables:

```bash
SECRET_KEY=<random-secret-key>  # Generate with: python -c "import secrets; print(secrets.token_urlsafe(32))"
PRODUCTION=true
```

Optional:
```bash
DATABASE_URL=sqlite:///./app/database/schedule.db
LOG_LEVEL=INFO
TZ=Europe/Stockholm
SENTRY_DSN=<your-sentry-dsn>
```

## SSL/TLS Certificates

### Let's Encrypt (Automatic)

**With Nginx:**
```bash
sudo certbot --nginx -d your-domain.com
```

**With Traefik:**
Automatic when configured in `traefik.yml` (already done in example).

### Manual Certificates

Place certificates in:
- Certificate: `/etc/ssl/certs/your-domain.crt`
- Private key: `/etc/ssl/private/your-domain.key`

Update nginx configuration:
```nginx
ssl_certificate /etc/ssl/certs/your-domain.crt;
ssl_certificate_key /etc/ssl/private/your-domain.key;
```

## Systemd Service Commands

```bash
# Start service
sudo systemctl start ica-schedule

# Stop service
sudo systemctl stop ica-schedule

# Restart service
sudo systemctl restart ica-schedule

# Enable auto-start on boot
sudo systemctl enable ica-schedule

# View status
sudo systemctl status ica-schedule

# View logs
sudo journalctl -u ica-schedule -f

# View last 100 lines
sudo journalctl -u ica-schedule -n 100
```

## Docker Commands

```bash
# Start containers
docker-compose up -d

# Stop containers
docker-compose down

# View logs
docker-compose logs -f ica-schedule

# Restart specific service
docker-compose restart ica-schedule

# Rebuild and restart
docker-compose up -d --build

# Execute command in container
docker-compose exec ica-schedule bash
```

## Security Hardening

### Firewall (UFW)

```bash
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

### Fail2ban (Nginx)

Create `/etc/fail2ban/filter.d/nginx-ica-schedule.conf`:
```ini
[Definition]
failregex = ^<HOST> .* "POST /login HTTP.*" 401
ignoreregex =
```

Create `/etc/fail2ban/jail.d/nginx-ica-schedule.conf`:
```ini
[nginx-ica-schedule]
enabled = true
port = http,https
filter = nginx-ica-schedule
logpath = /var/log/nginx/ica-schedule-access.log
maxretry = 5
bantime = 3600
```

## Monitoring

### Health Check

The application provides a health check endpoint:
```bash
curl https://your-domain.com/health
```

### Uptime Monitoring

Set up external monitoring with:
- [UptimeRobot](https://uptimerobot.com/) (free)
- [Better Uptime](https://betteruptime.com/)
- [Pingdom](https://www.pingdom.com/)

Monitor: `https://your-domain.com/health`

### Application Logs

**Systemd:**
```bash
sudo journalctl -u ica-schedule -f --output=json-pretty
```

**Docker:**
```bash
docker-compose logs -f --tail=100 ica-schedule
```

## Backup & Restore

See `../scripts/` directory for backup scripts.

**Manual backup:**
```bash
sqlite3 /path/to/schedule.db ".backup backup_$(date +%Y%m%d).db"
gzip backup_*.db
```

**Restore:**
```bash
# Stop application first
sudo systemctl stop ica-schedule
# Restore
gunzip -c backup_20231201.db.gz > /path/to/schedule.db
# Start application
sudo systemctl start ica-schedule
```

## Troubleshooting

### 502 Bad Gateway

**Cause:** Application not running.

**Solution:**
```bash
sudo systemctl status ica-schedule
sudo journalctl -u ica-schedule -n 50
```

### Permission Denied

**Cause:** Incorrect file permissions.

**Solution:**
```bash
sudo chown -R www-data:www-data /opt/ICA\ v0.0.20
chmod 644 /opt/ICA\ v0.0.20/app/database/*.db
```

### SSL Certificate Errors

**Cause:** Certificate expired or not found.

**Solution:**
```bash
sudo certbot renew --dry-run
sudo systemctl reload nginx
```

## Performance Tuning

### Nginx

```nginx
# Worker processes (= number of CPU cores)
worker_processes auto;

# Worker connections
events {
    worker_connections 1024;
}

# Enable gzip compression
gzip on;
gzip_types text/plain text/css application/json application/javascript;
```

### Uvicorn Workers

For better performance with multiple CPU cores:
```bash
uvicorn app.main:app --workers 4
```

**Note:** SQLite has limited concurrent write support. Use `--workers 1` to avoid database locking issues.

## Support

For issues, refer to:
- Main documentation: `../DEPLOYMENT.md`
- Setup script: `../scripts/setup_production.sh`
- Backup scripts: `../scripts/backup_database.sh`
