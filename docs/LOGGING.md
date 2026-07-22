# Logging Guide - Periodical

Complete guide to logging in Periodical.

## Overview

Periodical uses structured logging with different configurations for development and production environments.

**Features:**
- ✅ Structured JSON logging in production
- ✅ Colored console output in development
- ✅ Automatic log rotation (prevents disk fill-up)
- ✅ Request logging with timing and status codes
- ✅ Authentication event logging
- ✅ Unique request IDs for tracing
- ✅ Separate error log file
- ✅ User and request context in logs

## Log Files

All logs are stored in the `logs/` directory:

```
logs/
├── app.log         # Main application log (INFO and above)
├── error.log       # Error log (ERROR and above)
└── access.log      # HTTP access log (optional)
```

**Log Rotation:**
- Maximum file size: 10MB
- Backup count: 5 files (app.log), 10 files (error.log)
- Old files are automatically deleted

## Log Formats

### Production (JSON)

```json
{
  "timestamp": "2025-12-13T14:30:45.123456Z",
  "level": "INFO",
  "logger": "app.routes.auth_routes",
  "message": "Auth event: login - admin - SUCCESS",
  "module": "auth_routes",
  "function": "login",
  "line": 108,
  "event_type": "login",
  "username": "admin",
  "user_id": 0,
  "success": true,
  "ip": "192.168.1.100"
}
```

### Development (Colored Console)

```
INFO     2025-12-13 14:30:45 [app.routes.auth_routes] Auth event: login - admin - SUCCESS
```

## Environment Configuration

Set `PRODUCTION` environment variable to control logging mode:

```bash
# Development mode (colored console, DEBUG level)
export PRODUCTION=false

# Production mode (JSON files, INFO level)
export PRODUCTION=true
```

## Request Logging

All HTTP requests are automatically logged with:
- Request ID (unique UUID)
- Method and path
- Status code
- Duration in milliseconds
- User ID and username (if authenticated)
- Client IP address

**Example log entry:**

```json
{
  "timestamp": "2025-12-13T14:30:45.123Z",
  "level": "INFO",
  "message": "GET /week - 200 (45.23ms)",
  "request_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "method": "GET",
  "path": "/week",
  "status_code": 200,
  "duration": 45.23,
  "user_id": 1,
  "username": "john"
}
```

**Response Headers:**

Every response includes an `X-Request-ID` header for tracing:
```
X-Request-ID: a1b2c3d4-e5f6-7890-abcd-ef1234567890
```

## Authentication Events

All authentication-related events are logged:

### Login Events

**Successful login:**
```json
{
  "event_type": "login",
  "username": "admin",
  "user_id": 0,
  "success": true,
  "ip": "192.168.1.100",
  "must_change_password": false
}
```

**Failed login:**
```json
{
  "event_type": "login",
  "username": "admin",
  "success": false,
  "ip": "192.168.1.100"
}
```

### Logout Events

```json
{
  "event_type": "logout",
  "username": "admin",
  "user_id": 0,
  "success": true
}
```

### Password Change Events

```json
{
  "event_type": "password_change",
  "username": "admin",
  "user_id": 0,
  "success": true,
  "forced": true
}
```

## Using Logging in Your Code

### Basic Logging

```python
from app.core.logging_config import get_logger

logger = get_logger(__name__)

# Log levels
logger.debug("Detailed debugging information")
logger.info("General information")
logger.warning("Warning message")
logger.error("Error occurred")
logger.critical("Critical error")
```

### Logging with Extra Fields

```python
logger.info(
    "User created",
    extra={
        "extra_fields": {
            "user_id": 123,
            "username": "newuser",
            "role": "user"
        }
    }
)
```

### Logging Authentication Events

```python
from app.core.request_logging import log_auth_event

log_auth_event(
    event_type="login",
    username="admin",
    user_id=0,
    success=True,
    details={"ip": "192.168.1.100"}
)
```

### Logging Security Events

```python
from app.core.request_logging import log_security_event

log_security_event(
    event_type="unauthorized_access_attempt",
    details={
        "path": "/admin/users",
        "user_id": 5,
        "ip": "192.168.1.200"
    },
    level="warning"
)
```

### Using Log Context

```python
from app.core.logging_config import LogContext, get_logger

logger = get_logger(__name__)

with LogContext(user_id=123, action="export"):
    logger.info("Starting data export")
    # ... do work ...
    logger.info("Export completed")
```

## Monitoring Logs

### Real-time Log Monitoring (Development)

```bash
# Watch main log
tail -f logs/app.log

# Watch error log
tail -f logs/error.log

# Watch with grep filtering
tail -f logs/app.log | grep ERROR
```

### Real-time Log Monitoring (Production with systemd)

```bash
# Follow all logs
sudo journalctl -u ica-schedule -f

# Follow with JSON formatting
sudo journalctl -u ica-schedule -f -o json-pretty

# Last 100 lines
sudo journalctl -u ica-schedule -n 100

# Since specific time
sudo journalctl -u ica-schedule --since "1 hour ago"

# Filter by level
sudo journalctl -u ica-schedule -p err
```

### Searching Logs

**Using grep (JSON logs):**

```bash
# Find all login events
grep '"event_type": "login"' logs/app.log

# Find failed logins
grep '"success": false' logs/app.log

# Find logs for specific user
grep '"username": "admin"' logs/app.log

# Find errors
grep '"level": "ERROR"' logs/error.log
```

**Using jq (JSON parsing):**

```bash
# Parse and pretty-print
cat logs/app.log | jq '.'

# Find all 500 errors
cat logs/app.log | jq 'select(.status_code >= 500)'

# Find slow requests (>1 second)
cat logs/app.log | jq 'select(.duration > 1000)'

# Get unique usernames
cat logs/app.log | jq -r '.username' | sort | uniq
```

## Log Aggregation

For production deployments, consider using a log aggregation service:

### ELK Stack (Elasticsearch, Logstash, Kibana)

1. Ship JSON logs to Logstash
2. Index in Elasticsearch
3. Visualize with Kibana

### Loki + Grafana

1. Configure Promtail to ship logs to Loki
2. Query and visualize in Grafana

**Promtail config example:**

```yaml
server:
  http_listen_port: 9080

positions:
  filename: /tmp/positions.yaml

clients:
  - url: http://loki:3100/loki/api/v1/push

scrape_configs:
  - job_name: periodical
    static_configs:
      - targets:
          - localhost
        labels:
          job: periodical
          __path__: /opt/Periodical/logs/*.log
```

### CloudWatch Logs (AWS)

1. Install CloudWatch agent
2. Configure to ship logs/app.log
3. View in AWS CloudWatch console

## Troubleshooting

### No logs appearing

**Check log directory permissions:**
```bash
ls -la logs/
chmod 755 logs/
```

**Check if logging is initialized:**
```python
from app.core.logging_config import setup_logging
setup_logging()
```

### Log files too large

**Check current sizes:**
```bash
du -h logs/
```

**Manually rotate:**
```bash
cd logs/
mv app.log app.log.1
mv error.log error.log.1
# Restart application to create new files
sudo systemctl restart ica-schedule
```

**Reduce retention:**

Edit `app/core/logging_config.py`:
```python
app_handler = logging.handlers.RotatingFileHandler(
    APP_LOG_FILE,
    maxBytes=5_000_000,  # 5MB instead of 10MB
    backupCount=3,       # 3 files instead of 5
)
```

### Too much noise in logs

**Suppress noisy loggers:**

Edit `app/core/logging_config.py`:
```python
# Suppress specific loggers
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
```

**Don't log health checks:**

Already implemented - health check requests are logged at DEBUG level.

## Best Practices

### Do Log:
✅ Authentication events (login, logout, password changes)
✅ Authorization failures
✅ Data modifications (create, update, delete)
✅ Errors and exceptions
✅ Security events
✅ Performance issues (slow requests)

### Don't Log:
❌ Passwords or sensitive data
❌ Full credit card numbers
❌ Authentication tokens
❌ Personal identification numbers
❌ Health check spam (log at DEBUG level)

### Sensitive Data Handling

```python
# BAD - logs password
logger.info(f"User login: {username} with password {password}")

# GOOD - doesn't log password
logger.info(f"User login attempt: {username}")

# BAD - logs full token
logger.info(f"Token created: {token}")

# GOOD - logs only token prefix
logger.info(f"Token created: {token[:8]}...")
```

## Performance Considerations

- **JSON formatting** adds ~5-10% overhead
- **Rotating file handlers** are thread-safe and efficient
- **Health checks** are logged at DEBUG level to reduce noise
- **Request IDs** add minimal overhead (~1ms per request)

## Security Considerations

- **Log files** should be readable only by application user and admin
- **Set permissions:**
  ```bash
  chmod 640 logs/*.log
  chown periodical:periodical logs/*.log
  ```
- **Rotate logs** regularly to prevent sensitive data accumulation
- **Consider encryption** for logs in highly sensitive environments

## Integration with Monitoring

### Prometheus Metrics (Future)

```python
from prometheus_client import Counter, Histogram

login_attempts = Counter('login_attempts_total', 'Total login attempts', ['status'])
request_duration = Histogram('http_request_duration_seconds', 'HTTP request duration')
```

### Sentry Integration

See `DEPLOYMENT.md` for Sentry error tracking setup.

## Conclusion

Proper logging is essential for:
- **Debugging** production issues
- **Security** monitoring and audit trails
- **Performance** analysis and optimization
- **Compliance** requirements (audit logs)

For questions or issues, refer to the main documentation or create an issue on GitHub.
