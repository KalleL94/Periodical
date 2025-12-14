# File Permissions Guide

Guide for setting secure file permissions in Periodical.

## Why File Permissions Matter

Proper file permissions prevent:
- ❌ Unauthorized access to sensitive data
- ❌ Passwords and secrets being read by other users
- ❌ Database tampering
- ❌ Log file manipulation
- ❌ Backup file theft

## Quick Setup

### Automatic (Recommended)

```bash
cd /opt/Periodical
sudo scripts/set_permissions.sh
```

This script automatically:
- ✅ Detects app user from systemd service
- ✅ Sets all permissions correctly
- ✅ Verifies critical files
- ✅ Provides detailed summary

### Manual

See [Manual Setup](#manual-setup) section below.

## Permission Reference

### Critical Files

| File/Directory | Permissions | Owner | Description |
|---|---|---|---|
| `.env` | `600` | app-user | **CRITICAL** - Contains SECRET_KEY |
| `app/database/*.db` | `640` | app-user | Database files with user data |
| `backups/` | `700` | app-user | Backup files (old databases) |
| `backups/*.db.gz` | `600` | app-user | Compressed backup files |

### Application Files

| File/Directory | Permissions | Owner | Description |
|---|---|---|---|
| `app/` | `755` | app-user | Application code directory |
| `app/**/*.py` | `644` | app-user | Python source files |
| `app/templates/` | `755` | app-user | Jinja2 templates directory |
| `app/templates/**/*.html` | `644` | app-user | Template files |
| `app/static/` | `755` | app-user | Static files directory |
| `app/static/**/*` | `644` | app-user | CSS, JS, images |

### Logs and Data

| File/Directory | Permissions | Owner | Description |
|---|---|---|---|
| `logs/` | `750` | app-user | Log directory |
| `logs/*.log` | `640` | app-user | Log files |
| `app/database/` | `750` | app-user | Database directory |

### Scripts

| File/Directory | Permissions | Owner | Description |
|---|---|---|---|
| `scripts/` | `750` | app-user | Scripts directory |
| `scripts/*.sh` | `750` | app-user | Executable scripts |

### Virtual Environment

| File/Directory | Permissions | Owner | Description |
|---|---|---|---|
| `venv/` | `755` | app-user | Virtual environment |
| `venv/bin/*` | `755` | app-user | Executables |

## Permission Meanings

### Numeric Permissions

```
7 = rwx (read, write, execute)
6 = rw- (read, write)
5 = r-x (read, execute)
4 = r-- (read only)
0 = --- (no access)
```

**Three digits represent:**
1. Owner permissions
2. Group permissions
3. Other permissions

**Examples:**
- `600` = Owner: rw-, Group: ---, Others: ---
- `640` = Owner: rw-, Group: r--, Others: ---
- `644` = Owner: rw-, Group: r--, Others: r--
- `700` = Owner: rwx, Group: ---, Others: ---
- `750` = Owner: rwx, Group: r-x, Others: ---
- `755` = Owner: rwx, Group: r-x, Others: r-x

## Security Levels

### 🔴 Critical (600, 700)

**Files:** `.env`, `backups/`

**Why:** Contains secrets, passwords, sensitive data

**Permissions:** Only app user can read/write

### 🟡 Sensitive (640, 750)

**Files:** Database files, log files

**Why:** Contains user data, may have PII

**Permissions:** App user read/write, group read

### 🟢 Public (644, 755)

**Files:** Python code, templates, static files

**Why:** Application code, not sensitive

**Permissions:** Owner read/write, all read

## Manual Setup

### 1. Set Application Owner

```bash
sudo chown -R app-user:app-user /opt/Periodical
```

Replace `app-user` with the user that runs the application (e.g., `www-data`, `periodical`, `kakan`).

### 2. Secure .env File

```bash
sudo chmod 600 /opt/Periodical/.env
sudo chown app-user:app-user /opt/Periodical/.env
```

**Verify:**
```bash
ls -la /opt/Periodical/.env
# Should show: -rw------- 1 app-user app-user
```

### 3. Secure Database

```bash
sudo chmod 750 /opt/Periodical/app/database
sudo chmod 640 /opt/Periodical/app/database/*.db
```

### 4. Secure Logs

```bash
sudo chmod 750 /opt/Periodical/logs
sudo chmod 640 /opt/Periodical/logs/*.log
```

### 5. Secure Backups

```bash
sudo chmod 700 /opt/Periodical/backups
sudo chmod 600 /opt/Periodical/backups/*
```

### 6. Make Scripts Executable

```bash
sudo chmod 750 /opt/Periodical/scripts/*.sh
```

### 7. Set Python Files

```bash
find /opt/Periodical/app -name "*.py" -exec chmod 644 {} \;
```

### 8. Set Static Files

```bash
chmod 755 /opt/Periodical/app/static
find /opt/Periodical/app/static -type f -exec chmod 644 {} \;
```

## Verification

### Check Critical Files

```bash
# .env (should be 600)
ls -l /opt/Periodical/.env

# Database (should be 640)
ls -l /opt/Periodical/app/database/schedule.db

# Backups directory (should be 700)
ls -ld /opt/Periodical/backups
```

### Verify Ownership

```bash
# All files should be owned by app user
ls -la /opt/Periodical
```

### Test Access

```bash
# As different user, try to read .env (should fail)
sudo -u nobody cat /opt/Periodical/.env
# Expected: Permission denied
```

## Common Issues

### Issue: "Permission denied" when app starts

**Cause:** Application user can't read necessary files.

**Solution:**
```bash
# Make sure app user owns files
sudo chown -R app-user:app-user /opt/Periodical

# Make sure directories are executable
sudo chmod 755 /opt/Periodical
sudo chmod 755 /opt/Periodical/app
```

### Issue: Can't write to database

**Cause:** Database file or directory not writable by app user.

**Solution:**
```bash
sudo chown app-user:app-user /opt/Periodical/app/database
sudo chmod 750 /opt/Periodical/app/database
sudo chmod 640 /opt/Periodical/app/database/schedule.db
```

### Issue: Logs not being created

**Cause:** Logs directory doesn't exist or isn't writable.

**Solution:**
```bash
sudo mkdir -p /opt/Periodical/logs
sudo chown app-user:app-user /opt/Periodical/logs
sudo chmod 750 /opt/Periodical/logs
```

### Issue: Backup script fails

**Cause:** Backup directory permissions or ownership.

**Solution:**
```bash
sudo mkdir -p /opt/Periodical/backups
sudo chown app-user:app-user /opt/Periodical/backups
sudo chmod 700 /opt/Periodical/backups
```

## Best Practices

### ✅ DO:

1. **Run app as non-root user:**
   ```bash
   # Good: specific user
   User=periodical

   # Bad: root user
   User=root
   ```

2. **Restrict .env file:**
   ```bash
   chmod 600 .env  # Only owner can read
   ```

3. **Separate backup permissions:**
   ```bash
   chmod 700 backups/  # Only owner can access
   ```

4. **Use groups wisely:**
   ```bash
   # Allow web server to read static files
   chown app-user:www-data app/static
   chmod 750 app/static
   ```

5. **Run set_permissions.sh after updates:**
   ```bash
   sudo scripts/set_permissions.sh
   ```

### ❌ DON'T:

1. **Don't make everything 777:**
   ```bash
   chmod 777 /opt/Periodical  # BAD - anyone can do anything
   ```

2. **Don't run as root:**
   ```bash
   # Bad in systemd service:
   User=root
   ```

3. **Don't commit .env to git:**
   ```bash
   # Make sure .gitignore includes .env
   echo ".env" >> .gitignore
   ```

4. **Don't make database world-readable:**
   ```bash
   chmod 644 schedule.db  # BAD - others can read
   ```

5. **Don't forget to set ownership:**
   ```bash
   # Not enough:
   chmod 600 .env

   # Need both:
   chown app-user:app-user .env
   chmod 600 .env
   ```

## Automated Checks

### Run Permission Audit

```bash
# Check all critical files
sudo scripts/set_permissions.sh

# Look for world-readable sensitive files
find /opt/Periodical -perm -004 -name "*.env" -o -name "*.db"

# Look for world-writable files (security risk)
find /opt/Periodical -perm -002 -type f
```

### Include in Deployment

Add to deployment script:

```bash
# In deploy.sh
echo "Setting file permissions..."
sudo scripts/set_permissions.sh
echo "Permissions set successfully"
```

## Security Checklist

After deployment, verify:

- [ ] `.env` is 600 and owned by app user
- [ ] Database files are 640
- [ ] Database directory is 750
- [ ] Backup directory is 700
- [ ] Backup files are 600
- [ ] Log files are 640
- [ ] Scripts are executable (750)
- [ ] No world-writable files exist
- [ ] App user is non-root
- [ ] All files owned by app user

## Integration with Systemd

Your systemd service should run as app user:

```ini
[Service]
User=periodical
Group=periodical
```

**NOT:**
```ini
[Service]
User=root  # BAD - security risk
```

## Summary

**Most Important:**
1. `.env` must be 600 (owner only)
2. Backups must be 700/600 (owner only)
3. Database should be 640 (owner rw, group r)
4. Never run as root
5. Use `set_permissions.sh` for automatic setup

**Quick Command:**
```bash
sudo scripts/set_permissions.sh
```

That's it! Your file permissions are now secure. 🔒
