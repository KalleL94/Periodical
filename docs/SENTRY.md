# Sentry Error Tracking Guide

Complete guide to setting up and using Sentry error tracking in Periodical.

## What is Sentry?

Sentry is an error tracking and monitoring platform that:
- ✅ Captures exceptions and errors in production
- ✅ Provides detailed stack traces and context
- ✅ Tracks performance issues
- ✅ Alerts you when errors occur
- ✅ Shows error trends over time
- ✅ Helps debug production issues

## Quick Setup

### 1. Create Sentry Account

1. Go to https://sentry.io
2. Sign up for free account
3. Create a new project
4. Select "Python" → "FastAPI"
5. Copy your DSN (Data Source Name)

### 2. Install Sentry SDK

```bash
cd /opt/Periodical
source venv/bin/activate
pip install sentry-sdk[fastapi]
```

### 3. Configure Sentry

Add to `.env`:

```bash
SENTRY_DSN=https://abc123@o123456.ingest.sentry.io/7654321
SENTRY_ENVIRONMENT=production
RELEASE_VERSION=periodical@0.0.20
```

### 4. Restart Application

```bash
sudo systemctl restart ica-schedule
```

**That's it!** Sentry is now tracking errors. 🎉

## Configuration

### Environment Variables

| Variable | Required | Description | Example |
|---|---|---|---|
| `SENTRY_DSN` | Yes | Sentry project DSN | `https://abc@sentry.io/123` |
| `SENTRY_ENVIRONMENT` | No | Environment name | `production`, `staging` |
| `RELEASE_VERSION` | No | Release version | `periodical@0.0.20` |
| `PRODUCTION` | Yes | Must be `true` for Sentry | `true` |

### Sentry DSN

**Format:**
```
https://{public_key}@{organization}.ingest.sentry.io/{project_id}
```

**Where to find:**
1. Go to Sentry project
2. Settings → Client Keys (DSN)
3. Copy the DSN

### Environment Names

Use different environments to separate errors:

```bash
# Production
SENTRY_ENVIRONMENT=production

# Staging
SENTRY_ENVIRONMENT=staging

# Test
SENTRY_ENVIRONMENT=test
```

## What Gets Tracked

### Automatic Tracking

Sentry automatically captures:

**Errors:**
- ✅ Unhandled exceptions
- ✅ HTTP 500 errors
- ✅ Database errors
- ✅ Authentication failures
- ✅ Template rendering errors

**Context:**
- ✅ Request URL and method
- ✅ User ID and username (when logged in)
- ✅ Stack traces
- ✅ Local variables
- ✅ Breadcrumbs (user actions before error)

**Performance:**
- ✅ HTTP request duration
- ✅ Database query timing
- ✅ Slow endpoints

### Manual Tracking

You can also manually capture errors and messages:

```python
from app.core.sentry_config import capture_exception, capture_message

# Capture exception
try:
    risky_operation()
except Exception as e:
    capture_exception(e, context={"user_action": "export_data"})

# Send message
capture_message("Important event occurred", level="warning")
```

## Privacy and Security

### What is Filtered

Periodical automatically filters sensitive data:

**Filtered Headers:**
- ❌ Cookie
- ❌ Authorization
- ❌ X-API-Key

**Filtered Query Parameters:**
- ❌ password
- ❌ token

**Other:**
- ❌ Personally Identifiable Information (PII) disabled
- ❌ Request body not sent (may contain passwords)

### What is Sent

**Safe to send:**
- ✅ User ID (numeric)
- ✅ Username (no email)
- ✅ Request path (no query params with secrets)
- ✅ Error messages
- ✅ Stack traces
- ✅ Breadcrumbs

**Example Sentry event:**
```json
{
  "exception": "ValueError: Invalid date format",
  "user": {
    "id": 5,
    "username": "john"
  },
  "request": {
    "url": "/year/2026",
    "method": "GET",
    "headers": {
      "Cookie": "[Filtered]"
    }
  },
  "breadcrumbs": [
    {"message": "User logged in", "category": "auth"},
    {"message": "GET /week", "category": "http"},
    {"message": "GET /year/2026", "category": "http"}
  ]
}
```

## Using Sentry

### View Errors

1. Go to https://sentry.io
2. Select your project
3. View Issues tab
4. Click on any error to see details

### Error Details

Each error shows:
- **Stack trace** - Where the error occurred
- **Breadcrumbs** - User actions before error
- **Context** - Request data, user info
- **Environment** - OS, Python version
- **Frequency** - How often it occurs
- **Affected users** - How many users hit this error

### Set Up Alerts

1. Project Settings → Alerts
2. Create new alert rule
3. Choose conditions (e.g., "When error rate > 10/hour")
4. Add notification (email, Slack, etc.)

### Performance Monitoring

Sentry tracks performance with 10% sampling:

**Metrics:**
- Request duration
- Database query time
- Slow endpoints
- Throughput

**View:**
1. Performance tab
2. See transaction details
3. Identify slow queries

## Integration Features

### User Context

When a user is logged in, Sentry knows who they are:

```python
# Automatically set on login
set_user_context(user_id=5, username="john")

# Cleared on logout
clear_user_context()
```

### Breadcrumbs

Track user actions before an error:

```python
add_breadcrumb(
    message="User exported data",
    category="action",
    level="info",
    data={"format": "csv", "rows": 1000}
)
```

### Custom Context

Add extra context to errors:

```python
from app.core.sentry_config import capture_exception

try:
    process_schedule(year=2026)
except Exception as e:
    capture_exception(e, context={
        "schedule": {
            "year": 2026,
            "person_count": 10
        }
    })
```

## Testing Sentry

### Manual Test

Trigger a test error:

```python
# Add to a route temporarily
@router.get("/sentry-test")
async def sentry_test():
    raise ValueError("Sentry test error - ignore me")
```

Then:
1. Visit `/sentry-test`
2. Check Sentry dashboard
3. You should see the error appear within seconds

### Check Initialization

Look for this in logs on startup:

```
INFO: Sentry initialized successfully (environment: production)
```

If not initialized:
```
WARNING: SENTRY_DSN not set. Error tracking disabled.
```

## Troubleshooting

### Sentry not capturing errors

**Check:**
1. `PRODUCTION=true` in .env
2. `SENTRY_DSN` is set correctly
3. Sentry SDK installed: `pip list | grep sentry`
4. Check startup logs for initialization message

**Test:**
```bash
# Check environment variables
cat .env | grep SENTRY

# Check if SDK is installed
pip show sentry-sdk

# Check logs
sudo journalctl -u ica-schedule | grep -i sentry
```

### SDK not installed error

**Error:**
```
WARNING: Sentry SDK not installed
```

**Fix:**
```bash
pip install sentry-sdk[fastapi]
```

### Invalid DSN error

**Error:**
```
ERROR: Failed to initialize Sentry: Invalid DSN
```

**Fix:**
- Check DSN format is correct
- Ensure no extra spaces
- Verify DSN in Sentry project settings

### No events appearing in Sentry

**Possible causes:**
1. **Sampling rate** - Only 10% of requests tracked for performance
2. **Error level** - Only ERROR and above sent
3. **Firewall** - Sentry.io might be blocked
4. **DSN wrong project** - Check project ID matches

**Test connectivity:**
```bash
curl -I https://sentry.io
# Should return 200 OK
```

## Best Practices

### ✅ DO:

1. **Set release versions:**
   ```bash
   RELEASE_VERSION=periodical@0.0.20
   ```

2. **Use environments:**
   ```bash
   SENTRY_ENVIRONMENT=production  # or staging, test
   ```

3. **Monitor error trends:**
   - Check Sentry daily
   - Fix frequent errors
   - Set up alerts

4. **Add context to errors:**
   ```python
   capture_exception(error, context={"action": "export"})
   ```

5. **Test in staging first:**
   - Use separate Sentry project for staging
   - Verify errors are captured correctly

### ❌ DON'T:

1. **Don't send PII:**
   ```python
   # Already filtered, but be aware
   ```

2. **Don't ignore errors:**
   - Check Sentry regularly
   - Fix issues that affect users

3. **Don't track development:**
   ```bash
   # Development shouldn't use Sentry
   PRODUCTION=false  # Sentry disabled
   ```

4. **Don't commit DSN to git:**
   ```bash
   # .env should be in .gitignore
   ```

5. **Don't set sample rate to 100%:**
   ```python
   # Performance tracking at 10% is fine
   traces_sample_rate=0.1  # Default, don't change
   ```

## Costs and Limits

### Free Tier

Sentry free tier includes:
- ✅ 5,000 errors/month
- ✅ 10,000 performance transactions/month
- ✅ 1 project
- ✅ 30-day retention
- ✅ Email alerts

**Sufficient for:**
- Small to medium deployments
- <100 users
- Early stage

### Paid Plans

If you exceed free tier:
- **Team:** $26/month (50k errors, 100k transactions)
- **Business:** $80/month (more features)

**When to upgrade:**
- High traffic (>100 concurrent users)
- Need longer retention (>30 days)
- Multiple projects
- Advanced features (release tracking, custom alerts)

## Alternative to Sentry

If you don't want to use Sentry:

### Option 1: File-based Logging

Already implemented! Errors are logged to `logs/error.log`:

```bash
tail -f logs/error.log
```

### Option 2: Self-hosted Sentry

Run your own Sentry instance:
- https://develop.sentry.dev/self-hosted/

### Option 3: Other Services

- **Rollbar** - Similar to Sentry
- **Bugsnag** - Error tracking
- **Airbrake** - Error monitoring
- **LogRocket** - Session replay + errors

## Deployment Checklist

Before going to production with Sentry:

- [ ] Sentry account created
- [ ] Project created in Sentry
- [ ] Sentry SDK installed (`pip install sentry-sdk[fastapi]`)
- [ ] `SENTRY_DSN` set in .env
- [ ] `PRODUCTION=true` in .env
- [ ] `SENTRY_ENVIRONMENT` set (production/staging)
- [ ] `RELEASE_VERSION` set (optional but recommended)
- [ ] Test error sent to Sentry
- [ ] Error appears in Sentry dashboard
- [ ] Alert rule created
- [ ] Notification channel configured (email/Slack)

## Summary

**Setup:**
```bash
# 1. Install
pip install sentry-sdk[fastapi]

# 2. Configure
echo "SENTRY_DSN=https://..." >> .env

# 3. Restart
sudo systemctl restart ica-schedule
```

**What you get:**
- 🎯 Automatic error tracking
- 🎯 Real-time error notifications
- 🎯 Detailed debugging info
- 🎯 Performance monitoring
- 🎯 User context (who hit the error)

**Cost:**
- Free tier: 5,000 errors/month
- Perfect for small/medium deployments

For more information, see:
- [Sentry Documentation](https://docs.sentry.io/)
- [FastAPI Integration](https://docs.sentry.io/platforms/python/guides/fastapi/)
- [Sentry Pricing](https://sentry.io/pricing/)
