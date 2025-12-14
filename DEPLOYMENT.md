# Deployment Guide - Periodical v0.0.20

Guide för att deploya Periodical i produktionsmiljö med HTTPS.

## Innehåll

1. [Översikt](#översikt)
2. [HTTPS-konfiguration](#https-konfiguration)
3. [Reverse Proxy Setup](#reverse-proxy-setup)
4. [Process Manager Setup](#process-manager-setup)
5. [Databas-backup](#databas-backup)
6. [Monitoring & Logging](#monitoring--logging)

---

## Översikt

**Rekommenderad produktionsarkitektur:**

```
Internet
   ↓
[Reverse Proxy: nginx/traefik]  ← HTTPS (port 443)
   ↓                               SSL/TLS hanteras här
[FastAPI/Uvicorn]                ← HTTP (localhost:8000)
   ↓
[SQLite Database]
```

**Varför denna arkitektur?**
- ✅ Reverse proxy hanterar SSL/TLS (enklare certifikathantering)
- ✅ Kan köra flera applikationer på samma server
- ✅ Statiska filer serveras effektivt
- ✅ Load balancing möjligt
- ✅ DDoS-skydd och rate limiting

---

## HTTPS-konfiguration

### Alternativ 1: Nginx som Reverse Proxy (REKOMMENDERAT)

#### Steg 1: Installera Nginx

**Ubuntu/Debian:**
```bash
sudo apt update
sudo apt install nginx
```

**Windows:**
Ladda ner från https://nginx.org/en/download.html

#### Steg 2: Skaffa SSL-certifikat

**Med Let's Encrypt (gratis):**
```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.com
```

Certbot konfigurerar automatiskt nginx och sätter upp auto-förnyelse.

**Med eget certifikat:**
Placera certifikat-filerna i `/etc/ssl/certs/` och `/etc/ssl/private/`

#### Steg 3: Nginx-konfiguration

Se `deployment/nginx-example.conf` för fullständig konfiguration.

**Viktiga inställningar:**
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

    # SSL-certifikat
    ssl_certificate /etc/letsencrypt/live/your-domain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your-domain.com/privkey.pem;

    # Moderna SSL-inställningar
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;

    # Proxy till FastAPI
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Statiska filer (optimering)
    location /static {
        alias /path/to/ICA/v0.0.20/app/static;
        expires 30d;
        add_header Cache-Control "public, immutable";
    }
}
```

#### Steg 4: Testa och starta

```bash
# Testa konfiguration
sudo nginx -t

# Starta/ladda om nginx
sudo systemctl restart nginx
sudo systemctl enable nginx  # Auto-start vid boot
```

---

### Alternativ 2: Traefik som Reverse Proxy

Traefik är enklare för Docker-miljöer och har automatisk Let's Encrypt-integration.

Se `deployment/traefik-example.yml` för Docker Compose-konfiguration.

**Fördelar med Traefik:**
- ✅ Automatisk SSL-certifikat från Let's Encrypt
- ✅ Automatisk service discovery
- ✅ Inbyggd dashboard
- ✅ Perfekt för Docker/Kubernetes

---

### Alternativ 3: HTTPS direkt i Uvicorn (EJ REKOMMENDERAT)

Endast för testmiljö eller om du inte kan använda reverse proxy.

```bash
# Generera självsignerat certifikat (endast för test)
openssl req -x509 -newkey rsa:4096 -keyout key.pem -out cert.pem -days 365 -nodes

# Starta med SSL
uvicorn app.main:app --host 0.0.0.0 --port 8443 --ssl-keyfile key.pem --ssl-certfile cert.pem
```

**OBS:** Självsignerade certifikat ger säkerhetsvarningar i webbläsare!

---

## Reverse Proxy Setup

### Environment Variables för produktion

Skapa `.env`-fil:
```bash
# .env
SECRET_KEY=your-long-random-secret-key-here-change-me
PRODUCTION=true
DATABASE_URL=sqlite:///./app/database/schedule.db
```

**Generera säker SECRET_KEY:**
```bash
# Python
python -c "import secrets; print(secrets.token_urlsafe(32))"

# OpenSSL
openssl rand -base64 32
```

**Ladda environment variables:**
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

### Alternativ 1: Systemd (Linux)

Skapa service-fil: `/etc/systemd/system/ica-schedule.service`

```ini
[Unit]
Description=Periodical FastAPI Application
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=/path/to/ICA v0.0.20
Environment="SECRET_KEY=your-secret-key"
Environment="PRODUCTION=true"
ExecStart=/usr/bin/python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 4
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

**Hantera service:**
```bash
sudo systemctl daemon-reload
sudo systemctl start ica-schedule
sudo systemctl enable ica-schedule  # Auto-start
sudo systemctl status ica-schedule  # Status
sudo journalctl -u ica-schedule -f  # Logs
```

---

### Alternativ 2: Docker (ALLA PLATTFORMAR)

Se `deployment/Dockerfile` och `deployment/docker-compose.yml`

**Fördelar:**
- ✅ Konsistent miljö
- ✅ Enkel deployment
- ✅ Isolering
- ✅ Skalbart

**Starta:**
```bash
docker-compose up -d
```

---

### Alternativ 3: Supervisor (Linux/Mac)

Installera:
```bash
sudo apt install supervisor
```

Konfig: `/etc/supervisor/conf.d/ica-schedule.conf`
```ini
[program:ica-schedule]
command=/usr/bin/python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000
directory=/path/to/ICA v0.0.20
user=www-data
autostart=true
autorestart=true
stderr_logfile=/var/log/ica-schedule/err.log
stdout_logfile=/var/log/ica-schedule/out.log
environment=SECRET_KEY="your-key",PRODUCTION="true"
```

**Hantera:**
```bash
sudo supervisorctl reread
sudo supervisorctl update
sudo supervisorctl start ica-schedule
```

---

## Databas-backup

### Automatisk backup-script

Se `scripts/backup_database.sh`

```bash
#!/bin/bash
# Automatisk SQLite backup

BACKUP_DIR="/path/to/backups"
DB_PATH="/path/to/ICA v0.0.20/app/database/schedule.db"
DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/schedule_backup_$DATE.db"

# Skapa backup
sqlite3 "$DB_PATH" ".backup '$BACKUP_FILE'"

# Komprimera
gzip "$BACKUP_FILE"

# Ta bort backups äldre än 30 dagar
find "$BACKUP_DIR" -name "schedule_backup_*.db.gz" -mtime +30 -delete

echo "Backup klar: ${BACKUP_FILE}.gz"
```

**Schemalägg med cron:**
```bash
# Backup varje dag kl 03:00
0 3 * * * /path/to/backup_database.sh
```

---

## Monitoring & Logging

### Structured Logging

Se `app/core/logging_config.py` för konfiguration.

**Filbaserad logging:**
```python
# I app/main.py
import logging
from logging.handlers import RotatingFileHandler

# Roterande loggfiler (max 10MB, behåll 5 filer)
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

**Sentry-integration (rekommenderat):**
```bash
pip install sentry-sdk[fastapi]
```

```python
# I app/main.py
import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration

sentry_sdk.init(
    dsn="your-sentry-dsn",
    integrations=[FastApiIntegration()],
    traces_sample_rate=0.1,
)
```

### Health Check Endpoint

Redan implementerat: `GET /health`

**Monitoring med Uptime Robot/Better Uptime:**
- Sätt upp extern monitoring på `https://your-domain.com/health`
- Notifieringar vid downtime

---

## Säkerhetschecklista

- [ ] SECRET_KEY satt via environment variable (ej default)
- [ ] PRODUCTION=true i environment variables
- [ ] HTTPS aktiverat (SSL-certifikat installerat)
- [ ] HTTP-till-HTTPS redirect konfigurerad
- [ ] Firewall konfigurerad (endast port 80, 443 öppna)
- [ ] Databas-backup schemalagd
- [ ] Process manager konfigurerad (auto-restart)
- [ ] Logging konfigurerad (filbaserad)
- [ ] Error tracking aktiverat (Sentry)
- [ ] Alla användare har bytt från standardlösenord
- [ ] File permissions korrekta (databas läs/skriv endast för app-user)
- [ ] Rate limiting konfigurerad (i nginx/traefik)
- [ ] CORS-inställningar restriktiva (se docs/CORS.md)
- [ ] Security headers konfigurerade (X-Frame-Options, CSP, etc.)

### CORS Configuration

Periodical har automatisk CORS-konfiguration baserat på miljö:

**Development (PRODUCTION=false):**
- Tillåter alla origins för enkel testning
- Alla metoder och headers tillåtna

**Production (PRODUCTION=true):**
- Endast specificerade origins tillåtna
- Endast GET och POST metoder
- Säker konfiguration

**Konfigurera CORS för produktion:**

```bash
# Om du använder separerad frontend
CORS_ORIGINS=https://your-frontend-domain.com,https://www.your-frontend-domain.com

# För server-rendered app (default - mest säkert)
# Ingen CORS_ORIGINS behövs - all trafik är same-origin
```

Se `docs/CORS.md` för fullständig guide.

---

## Snabbstart - Produktionsdeploy

**1. Förbered servern:**
```bash
# Uppdatera system
sudo apt update && sudo apt upgrade -y

# Installera dependencies
sudo apt install python3 python3-pip nginx certbot python3-certbot-nginx git sqlite3
```

**2. Klona/kopiera applikationen:**
```bash
cd /opt
sudo git clone /path/to/repo
cd "ICA v0.0.20"
sudo pip3 install -r requirements.txt
```

**3. Sätt environment variables:**
```bash
# Generera secret key
python3 -c "import secrets; print(secrets.token_urlsafe(32))" > /tmp/secret.txt

# Skapa .env
sudo tee .env > /dev/null <<EOF
SECRET_KEY=$(cat /tmp/secret.txt)
PRODUCTION=true
EOF

rm /tmp/secret.txt
```

**4. Kör migrations:**
```bash
python3 migrate_to_db.py
python3 migrate_add_password_change.py
```

**5. Konfigurera nginx:**
```bash
sudo cp deployment/nginx-example.conf /etc/nginx/sites-available/ica-schedule
sudo ln -s /etc/nginx/sites-available/ica-schedule /etc/nginx/sites-enabled/
sudo nginx -t
```

**6. Skaffa SSL-certifikat:**
```bash
sudo certbot --nginx -d your-domain.com
```

**7. Starta applikation:**
```bash
sudo cp deployment/ica-schedule.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start ica-schedule
sudo systemctl enable ica-schedule
```

**8. Starta nginx:**
```bash
sudo systemctl restart nginx
```

**9. Verifiera:**
```bash
# Check app status
sudo systemctl status ica-schedule

# Check nginx
sudo systemctl status nginx

# Test HTTPS
curl -I https://your-domain.com
```

**10. Logga in och byt lösenord:**
- Gå till `https://your-domain.com`
- Logga in med admin / Banan1
- Byt lösenord när du blir tillfrågad
- Upprepa för alla användare

**Klart!** 🎉

---

## Felsökning

### Problem: 502 Bad Gateway

**Orsak:** Uvicorn/FastAPI körs inte.

**Lösning:**
```bash
sudo systemctl status ica-schedule
sudo journalctl -u ica-schedule -n 50
```

### Problem: Certifikat-fel

**Orsak:** Certifikat inte installerat korrekt.

**Lösning:**
```bash
sudo certbot renew --dry-run
sudo nginx -t
```

### Problem: Database locked

**Orsak:** SQLite kan ha låsningsproblem vid många samtidiga skrivningar.

**Lösning:**
- Använd `--workers 1` för uvicorn (endast en worker)
- Eller migrera till PostgreSQL för bättre concurrency

### Problem: Static files fungerar inte

**Orsak:** Nginx hittar inte filerna.

**Lösning:**
```bash
# Verifiera path i nginx config
ls -la /path/to/ICA/v0.0.20/app/static/
```

---

## Support och Uppdateringar

**Loggar:**
- Application: `sudo journalctl -u ica-schedule -f`
- Nginx: `sudo tail -f /var/log/nginx/error.log`
- Access: `sudo tail -f /var/log/nginx/access.log`

**Uppdatera applikation:**
```bash
cd /opt/ICA\ v0.0.20
git pull
sudo systemctl restart ica-schedule
```

**Backup innan uppdatering:**
```bash
sqlite3 app/database/schedule.db ".backup backup_$(date +%Y%m%d).db"
```
