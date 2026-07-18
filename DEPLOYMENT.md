# Deployment Guide - Periodical

Guide för att deploya Periodical i produktionsmiljö med HTTPS.

## Innehåll

1. [Översikt](#översikt)
2. [HTTPS-konfiguration](#https-konfiguration)
3. [Reverse Proxy Setup](#reverse-proxy-setup)
4. [Process Manager Setup](#process-manager-setup)
5. [Databas-backup](#databas-backup)
6. [Monitoring & Logging](#monitoring--logging)
7. [CI/CD med GitHub Actions](#cicd-med-github-actions)

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
        alias /opt/Periodical/app/static;
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

# Start command - Använd venv för isolerad miljö
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

**Förklaring av säkerhetsinställningar:**
- **NoNewPrivileges**: Förhindrar att processen får nya privilegier
- **PrivateTmp**: Isolerad /tmp-katalog för tjänsten
- **ProtectSystem=strict**: Hela filsystemet read-only utom specificerade paths
- **ProtectHome**: Blockerar åtkomst till användares hemkataloger
- **ReadWritePaths**: Endast databas och logs får skrivrättigheter
- **MemoryMax**: Begränsar minnesanvändning till 512MB
- **LimitNOFILE**: Maximalt 4096 öppna filer

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

**OBS:** Använd filerna i `deployment/`-mappen, inte root Dockerfile.

**Fördelar:**
- ✅ Konsistent miljö
- ✅ Enkel deployment
- ✅ Isolering
- ✅ Skalbart

**Starta:**
```bash
cd deployment
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
command=/opt/Periodical/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
directory=/opt/Periodical
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
DB_PATH="/opt/Periodical/app/database/schedule.db"
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

## CI/CD med GitHub Actions

Periodical har inbyggd CI/CD-pipeline som automatiserar testning och deployment.

### Översikt av CI/CD-flödet

```
Developer → PR till main
              ↓
         [CI Pipeline]
         - Syntax check
         - Pytest
         - Dependency check
              ↓
         PR Approved → Merge
              ↓
         [Deploy Pipeline]
         - SSH till prod-server
         - Git pull
         - Pip install
         - Restart systemd service
         - Health check
              ↓
         Live på produktion ✅
```

**Två workflows:**

1. **CI Pipeline** (`.github/workflows/ci.yml`)
   - Triggas: Vid varje Pull Request till `main`
   - Kör: Syntax check + pytest
   - Förhindrar: Buggy kod från att mergas

2. **Deploy Pipeline** (`.github/workflows/deploy.yml`)
   - Triggas: Vid push/merge till `main`
   - Kör: Deployment-script via SSH
   - Resultat: Automatisk deployment till produktion

### GitHub Secrets Configuration

För att CI/CD ska fungera måste följande secrets konfigureras i GitHub:

| Secret Name | Beskrivning | Exempel |
|-------------|-------------|---------|
| `PROD_HOST` | IP-adress eller domännamn till produktionsservern | `192.168.1.100` eller `prod.example.com` |
| `PROD_USER` | SSH-användare på servern | `deploy` eller `www-data` |
| `PROD_SSH_KEY` | Privat SSH-nyckel för autentisering | Innehåll av `~/.ssh/id_ed25519` |
| `PROD_APP_PATH` | Absolut sökväg till applikationen på servern | `/opt/Periodical` |

### Konfigurera GitHub Secrets

**Steg 1: Gå till repository settings**

1. Navigera till ditt GitHub repository
2. Klicka på **Settings** (högst upp)
3. I vänstermenyn: **Secrets and variables** → **Actions**
4. Klicka **New repository secret**

**Steg 2: Lägg till secrets en i taget**

För varje secret ovan:
- **Name**: Exakt namn från tabellen (t.ex. `PROD_HOST`)
- **Value**: Motsvarande värde för din server
- Klicka **Add secret**

**Exempel:**
```
Name: PROD_HOST
Value: 192.168.1.100

Name: PROD_USER
Value: deploy

Name: PROD_APP_PATH
Value: /opt/Periodical
```

**PROD_SSH_KEY - Specialfall:**

SSH-nyckeln måste vara i rätt format:

```bash
# På din lokala dator - kopiera hela nyckeln inklusive header/footer
cat ~/.ssh/id_ed25519

# Output (kopiera HELA detta):
-----BEGIN OPENSSH PRIVATE KEY-----
b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZW
...många rader...
-----END OPENSSH PRIVATE KEY-----
```

Kopiera **hela** utskriften (inklusive `-----BEGIN` och `-----END`) och klistra in som värde för `PROD_SSH_KEY`.

### Förbered Servern för GitHub Actions SSH

GitHub Actions behöver kunna SSH:a in på servern utan lösenord.

**Steg 1: Skapa deploy-användare (rekommenderat)**

```bash
# Skapa dedikerad deploy-användare
sudo adduser deploy
sudo usermod -aG sudo deploy

# Ge deploy-användaren rätt att starta om tjänsten UTAN lösenord
echo "deploy ALL=(ALL) NOPASSWD: /bin/systemctl restart ica-schedule" | sudo tee /etc/sudoers.d/deploy
echo "deploy ALL=(ALL) NOPASSWD: /bin/journalctl -u ica-schedule -n 20 --no-pager" | sudo tee -a /etc/sudoers.d/deploy
sudo chmod 0440 /etc/sudoers.d/deploy
```

**Steg 2: Generera SSH-nyckelpar (på din lokala dator)**

```bash
# Generera ED25519-nyckel (modernare och säkrare än RSA)
ssh-keygen -t ed25519 -C "github-actions-deploy" -f ~/.ssh/github_deploy_key

# Detta skapar:
# - ~/.ssh/github_deploy_key      (privat nyckel - lägg i GitHub Secrets)
# - ~/.ssh/github_deploy_key.pub  (publik nyckel - lägg på servern)
```

**Steg 3: Installera publik nyckel på servern**

```bash
# Kopiera den publika nyckeln till servern
ssh-copy-id -i ~/.ssh/github_deploy_key.pub deploy@your-server-ip

# ELLER manuellt:
cat ~/.ssh/github_deploy_key.pub | ssh deploy@your-server-ip "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys"
```

**Steg 4: Testa SSH-anslutning**

```bash
# Testa från din lokala dator
ssh -i ~/.ssh/github_deploy_key deploy@your-server-ip

# Om du kommer in utan lösenord: ✅ Fungerar!
```

**Steg 5: Verifiera fil-rättigheter**

På servern:
```bash
# Deploy-användaren måste äga app-katalogen
sudo chown -R deploy:deploy /opt/Periodical

# Verifiera
ls -la /opt/Periodical
# Output ska visa: drwxr-xr-x deploy deploy
```

**Steg 6: Konfigurera systemd service (om ej redan gjort)**

Se till att systemd-servicen finns och är aktiverad:

```bash
# Verifiera att service-filen finns
ls -la /etc/systemd/system/ica-schedule.service

# Om den saknas, kopiera från deployment/
sudo cp /opt/Periodical/deployment/ica-schedule.service /etc/systemd/system/

# Aktivera och starta
sudo systemctl daemon-reload
sudo systemctl enable ica-schedule
sudo systemctl start ica-schedule
sudo systemctl status ica-schedule
```

### Verifiera CI/CD Setup

**Test 1: Skapa en test-PR**

```bash
# Skapa en feature branch
git checkout -b test-ci-pipeline

# Gör en liten ändring
echo "# Test" >> README.md
git add README.md
git commit -m "Test CI pipeline"
git push origin test-ci-pipeline

# Skapa PR via GitHub UI eller gh CLI:
gh pr create --title "Test CI" --body "Testing CI pipeline"
```

**Förväntat resultat:**
- CI workflow startar automatiskt
- Du ser checkmarks ✅ på PR:n när testerna passerar
- Om något är fel: ❌ och felmeddelanden i Actions-loggen

**Test 2: Test deployment (merge PR)**

Efter CI passerat:
```bash
# Merge PR (via GitHub UI eller CLI)
gh pr merge --squash

# Övervaka deployment
gh run watch
```

**Förväntat resultat:**
- Deploy workflow triggas automatiskt
- SSH-anslutning till servern lyckas
- `deploy.sh` körs
- Health check returnerar 200 OK
- Kommentar på commit: "**Deployment Successful**"

### Manuell Deployment (om automatisk misslyckas)

**Alternativ 1: Via GitHub Actions UI (Re-run)**

1. Gå till **Actions** i GitHub
2. Välj den misslyckade workflow-körningen
3. Klicka **Re-run failed jobs** eller **Re-run all jobs**

**Alternativ 2: Manuell SSH-deploy**

```bash
# SSH till servern
ssh deploy@your-server-ip

# Kör deploy-scriptet manuellt
cd /opt/Periodical
bash scripts/deploy.sh /opt/Periodical

# Följ output för att se eventuella fel
```

**Alternativ 3: Manuell deployment steg-för-steg**

Om deploy-scriptet inte fungerar:

```bash
# 1. SSH till servern
ssh deploy@your-server-ip

# 2. Navigera till app-katalogen
cd /opt/Periodical

# 3. Backup databas (säkerhetsåtgärd)
sqlite3 app/database/schedule.db ".backup backup_$(date +%Y%m%d_%H%M%S).db"

# 4. Hämta senaste kod
git pull

# 5. Aktivera virtual environment
source venv/bin/activate

# 6. Installera/uppdatera dependencies
pip install .

# 7. Kör eventuella migrations (om det finns nya)
# python migrate_*.py

# 8. Starta om tjänsten
sudo systemctl restart ica-schedule

# 9. Verifiera status
sudo systemctl status ica-schedule

# 10. Test health endpoint
curl http://127.0.0.1:8000/health
# Förväntat: {"status":"healthy"}
```

### Felsökning CI/CD

**Problem: CI-testerna failar**

```bash
# Kör testerna lokalt först
pytest

# Om de fungerar lokalt men inte i CI:
# - Kontrollera Python-version (CI använder 3.12)
# - Kontrollera att alla dependencies finns i pyproject.toml
```

**Problem: SSH-anslutning misslyckas**

**Symptom:** `Permission denied (publickey)` eller timeout

**Lösning:**
```bash
# Verifiera att PROD_SSH_KEY är korrekt kopierad (inga extra spaces/newlines)
# Testa SSH manuellt med samma nyckel:
ssh -i ~/.ssh/github_deploy_key deploy@your-server-ip

# Kontrollera SSH-konfiguration på servern:
sudo cat /home/deploy/.ssh/authorized_keys
# Ska innehålla din publika nyckel

# Verifiera permissions:
ls -la /home/deploy/.ssh
# authorized_keys ska vara 600, .ssh ska vara 700
```

**Problem: Health check misslyckas**

**Symptom:** `❌ Health check misslyckades med status: 000`

**Lösning:**
```bash
# 1. Kontrollera att tjänsten körs
sudo systemctl status ica-schedule

# 2. Läs senaste loggarna
sudo journalctl -u ica-schedule -n 50

# 3. Vanliga orsaker:
# - Port 8000 redan upptagen (ändra port i systemd-filen)
# - Virtual environment hittas inte (kontrollera sökväg i systemd)
# - SECRET_KEY saknas (lägg till i systemd Environment=)
# - Databas-migrations behövs (kör manuellt)
```

**Problem: Deploy-scriptet fastnar**

**Symptom:** Workflow timeoutar efter 10+ minuter

**Lösning:**
```bash
# Kontrollera att deploy.sh är körbar:
ssh deploy@your-server-ip "ls -la /opt/Periodical/scripts/deploy.sh"
# Ska visa: -rwxr-xr-x

# Om inte, gör den körbar:
ssh deploy@your-server-ip "chmod +x /opt/Periodical/scripts/deploy.sh"
```

### Best Practices

**1. Testa alltid lokalt först:**
```bash
# Kör tester innan du pushar
pytest

# Syntax check
python -m py_compile app/main.py
```

**2. Använd feature branches:**
```bash
git checkout -b feature/my-feature
# ... gör ändringar ...
git push origin feature/my-feature
# Skapa PR → CI testas automatiskt
```

**3. Backup före deployment:**

Deploy-scriptet backar inte upp automatiskt. Överväg att lägga till:
```bash
# I scripts/deploy.sh, före git pull:
sqlite3 app/database/schedule.db ".backup backup_$(date +%Y%m%d_%H%M%S).db"
```

**4. Övervaka deployments:**
```bash
# Sätt upp notifieringar
# - GitHub: Settings → Notifications → Actions
# - Email alerts vid failed deployments
```

**5. Staging environment (rekommenderat för större projekt):**

För kritiska produktionsmiljöer, lägg till staging:
```yaml
# .github/workflows/deploy-staging.yml
on:
  push:
    branches:
      - develop
```

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

# Verifiera Python-version (projektet använder Python 3.12)
python3 --version  # Ska visa Python 3.12.x

# Installera dependencies
sudo apt install python3 python3-pip python3-venv nginx certbot python3-certbot-nginx git sqlite3
```

**2. Klona/kopiera applikationen:**
```bash
cd /opt
sudo git clone /path/to/repo Periodical
cd Periodical

# Skapa virtual environment (rekommenderat)
python3 -m venv venv
source venv/bin/activate

# Installera från pyproject.toml
pip install .
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
- Logga in som `admin` med lösenordet som sattes i `migrations/migrate_to_db.py`
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
ls -la /opt/Periodical/app/static/
```

---

## Support och Uppdateringar

**Loggar:**
- Application: `sudo journalctl -u ica-schedule -f`
- Nginx: `sudo tail -f /var/log/nginx/error.log`
- Access: `sudo tail -f /var/log/nginx/access.log`

**Uppdatera applikation:**
```bash
cd /opt/Periodical
git pull
sudo systemctl restart ica-schedule
```

**Backup innan uppdatering:**
```bash
sqlite3 app/database/schedule.db ".backup backup_$(date +%Y%m%d).db"
```
