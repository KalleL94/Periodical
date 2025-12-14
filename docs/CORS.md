# CORS Configuration Guide

Complete guide to CORS (Cross-Origin Resource Sharing) configuration in Periodical.

## What is CORS?

CORS is a security feature that controls which origins (domains) can access your API from a web browser. It prevents malicious websites from making unauthorized requests to your application.

## Configuration

Periodical has different CORS configurations for development and production environments.

### Development Mode

**Configuration:**
```
PRODUCTION=false
```

**CORS Settings:**
- ✅ All origins allowed (`*`)
- ✅ All methods allowed (GET, POST, PUT, DELETE, etc.)
- ✅ All headers allowed
- ✅ Credentials allowed (cookies)

**Purpose:** Easy testing and development without CORS restrictions.

### Production Mode

**Configuration:**
```
PRODUCTION=true
CORS_ORIGINS=https://your-domain.com,https://www.your-domain.com
```

**CORS Settings:**
- ✅ Only specified origins allowed
- ✅ Only GET and POST methods allowed
- ✅ Credentials allowed (cookies)
- ✅ X-Request-ID header exposed

**Purpose:** Strict security - only allow requests from your own domain(s).

## Setting CORS Origins

### Single Domain

```bash
CORS_ORIGINS=https://schedule.example.com
```

### Multiple Domains

```bash
CORS_ORIGINS=https://schedule.example.com,https://www.schedule.example.com,https://app.example.com
```

**Important:**
- Use full URLs with protocol (https://)
- No trailing slashes
- Comma-separated, no spaces
- Include all subdomains you want to allow

## Security Best Practices

### ✅ DO:

1. **Always set CORS_ORIGINS in production:**
   ```bash
   CORS_ORIGINS=https://your-domain.com
   ```

2. **Use HTTPS in production:**
   ```bash
   CORS_ORIGINS=https://schedule.example.com  # Good
   ```

3. **Include all necessary subdomains:**
   ```bash
   CORS_ORIGINS=https://schedule.example.com,https://www.schedule.example.com
   ```

4. **Be specific:**
   ```bash
   CORS_ORIGINS=https://schedule.example.com  # Good - specific
   ```

### ❌ DON'T:

1. **Don't use wildcard in production:**
   ```bash
   CORS_ORIGINS=*  # BAD - allows any origin
   ```

2. **Don't use HTTP in production:**
   ```bash
   CORS_ORIGINS=http://schedule.example.com  # BAD - insecure
   ```

3. **Don't include trailing slashes:**
   ```bash
   CORS_ORIGINS=https://schedule.example.com/  # BAD - won't match
   ```

4. **Don't use wildcards in domain:**
   ```bash
   CORS_ORIGINS=https://*.example.com  # BAD - not supported
   ```

## Common Scenarios

### Same-Origin Only (Most Secure)

If your frontend is served from the same domain as the API:

```bash
# No CORS configuration needed
# Browsers don't apply CORS restrictions for same-origin requests
```

**Example:** Both frontend and API at `https://schedule.example.com`

### Separate Frontend Domain

If your frontend is on a different domain:

```bash
CORS_ORIGINS=https://app.example.com
```

**Example:** API at `https://api.example.com`, frontend at `https://app.example.com`

### Multiple Environments

```bash
# Production
CORS_ORIGINS=https://schedule.example.com,https://www.schedule.example.com

# Staging
CORS_ORIGINS=https://staging.schedule.example.com

# Local development (testing with real domain)
CORS_ORIGINS=http://localhost:3000,http://localhost:8000
```

## For Periodical Specifically

### Current Architecture

Periodical is a **server-rendered application** using Jinja2 templates. This means:

- ✅ Templates are served from the same origin as the API
- ✅ Most requests are same-origin (no CORS needed)
- ✅ AJAX requests to `/api/*` endpoints are same-origin

### When CORS Matters

CORS only matters for Periodical if:

1. **You add a separate frontend** (e.g., React, Vue) on a different domain
2. **You expose the API** for external consumption
3. **You use a CDN** for static files on a different domain

### Recommended Configuration

**For typical deployment (server-rendered):**

```bash
# No CORS_ORIGINS needed - same origin
PRODUCTION=true
```

This will:
- Block cross-origin requests (secure)
- Allow same-origin requests (your app works normally)

**For separate frontend or API exposure:**

```bash
PRODUCTION=true
CORS_ORIGINS=https://your-frontend-domain.com
```

## Troubleshooting

### Error: "CORS policy: No 'Access-Control-Allow-Origin' header"

**Problem:** Request from origin not in CORS_ORIGINS list.

**Solution:**
```bash
# Add the origin to CORS_ORIGINS
CORS_ORIGINS=https://schedule.example.com,https://your-other-domain.com
```

### Error: "Credentials mode 'include' requires 'Access-Control-Allow-Origin' to be a specific origin"

**Problem:** Trying to use wildcard (*) with credentials.

**Solution:**
```bash
# Use specific origins instead of wildcard
CORS_ORIGINS=https://schedule.example.com
```

### CORS works in dev but not production

**Problem:** Development uses permissive CORS (*), production is strict.

**Solution:**
```bash
# Set CORS_ORIGINS in production
CORS_ORIGINS=https://your-production-domain.com
```

### Preflight requests failing

**Problem:** Browser sends OPTIONS request which fails.

**Solution:** Check that:
1. CORS_ORIGINS is set correctly
2. Origin matches exactly (https vs http, www vs non-www)
3. No trailing slashes in CORS_ORIGINS

## Testing CORS

### Test from Browser Console

```javascript
// Open your site in browser console and run:
fetch('https://your-api.com/health', {
  method: 'GET',
  credentials: 'include'
})
.then(r => r.json())
.then(data => console.log('Success:', data))
.catch(err => console.error('CORS Error:', err));
```

### Test with curl

```bash
# Test simple request
curl -H "Origin: https://example.com" \
     -H "Access-Control-Request-Method: GET" \
     -H "Access-Control-Request-Headers: Content-Type" \
     -X OPTIONS \
     https://your-api.com/health \
     -v

# Look for Access-Control-Allow-Origin header in response
```

### Check CORS Headers

Open browser DevTools → Network → Click on request → Headers:

**Should see:**
```
Access-Control-Allow-Origin: https://your-domain.com
Access-Control-Allow-Credentials: true
Access-Control-Expose-Headers: X-Request-ID
```

## Advanced Configuration

### Custom CORS per Route

If you need different CORS for specific routes:

```python
from fastapi import APIRouter
from fastapi.middleware.cors import CORSMiddleware

# In your route file
api_router = APIRouter()

@api_router.get("/public-data")
async def public_data():
    # This endpoint could have different CORS rules
    return {"data": "public"}
```

### Conditional CORS

```python
# In app/main.py
if IS_PRODUCTION:
    # Strict CORS
    allowed_origins = CORS_ORIGINS
else:
    # Permissive CORS for development
    allowed_origins = ["*"]
```

## Security Implications

### Why CORS Matters

**Without CORS protection:**
- ❌ Malicious sites could make requests to your API
- ❌ User credentials could be stolen
- ❌ CSRF attacks possible

**With CORS protection:**
- ✅ Only your domains can access the API
- ✅ User credentials protected
- ✅ CSRF mitigation

### Additional Security Layers

CORS is **not enough** for complete security. Also implement:

1. **CSRF tokens** for state-changing operations
2. **Authentication** (already implemented - JWT cookies)
3. **Authorization** (already implemented - role-based)
4. **Rate limiting** (nginx/traefik level)
5. **HTTPS** (already configured in deployment)

## Monitoring

### Log CORS Configuration

On startup, check logs:

```
INFO: CORS configured for production with origins: ['https://schedule.example.com']
```

or

```
INFO: CORS configured for development (permissive)
```

### Monitor CORS Errors

Check logs for CORS-related errors:

```bash
grep "CORS" logs/error.log
```

### Browser Console

Watch for CORS errors in browser console:
```
Access to fetch at 'https://api.example.com' from origin 'https://wrong-domain.com'
has been blocked by CORS policy
```

## Summary

**Default Behavior:**
- **Development:** Permissive (all origins allowed)
- **Production:** Restrictive (only specified origins)

**Configuration:**
```bash
# Development
PRODUCTION=false

# Production (server-rendered, same origin)
PRODUCTION=true

# Production (separate frontend)
PRODUCTION=true
CORS_ORIGINS=https://frontend.example.com
```

**Security Level:**
- 🔴 Development: Low (for convenience)
- 🟢 Production: High (for security)

For most Periodical deployments, the default production config (no CORS_ORIGINS) is **most secure** since the app is server-rendered and doesn't need cross-origin access.

## References

- [MDN CORS Guide](https://developer.mozilla.org/en-US/docs/Web/HTTP/CORS)
- [FastAPI CORS Middleware](https://fastapi.tiangolo.com/tutorial/cors/)
- [OWASP CORS Security](https://owasp.org/www-community/attacks/CORS_OriginHeaderScrutiny)
