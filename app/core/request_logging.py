# app/core/request_logging.py
"""
Request logging middleware for tracking all HTTP requests.
"""

import logging
import time
import uuid
from collections.abc import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

# API traffic gets its own logger so it lands in logs/api.log as well, where a
# misbehaving client can be traced without digging through the whole app log.
api_logger = logging.getLogger("app.api")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """
    Middleware that logs all HTTP requests with timing and status codes.

    Adds a unique request ID to each request for tracing.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Generate unique request ID
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id

        # Start timer
        start_time = time.time()

        # Process request
        try:
            response = await call_next(request)
            status_code = response.status_code
            error = None
        except Exception as e:
            status_code = 500
            error = str(e)
            raise
        finally:
            # Calculate duration
            duration_ms = (time.time() - start_time) * 1000

            # Get user info if authenticated
            user_id = None
            username = None
            if hasattr(request.state, "user"):
                user_id = request.state.user.id
                username = request.state.user.username

            # Log request
            log_data = {
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": status_code,
                "duration": duration_ms,
            }

            if user_id:
                log_data["user_id"] = user_id
            if username:
                log_data["username"] = username

            # The query string and the client are what make an API call
            # reproducible afterwards: which date and time was asked for, and
            # which integration asked.
            is_api = request.url.path.startswith("/api/")
            if request.url.query:
                log_data["query"] = request.url.query
            if is_api:
                log_data["user_agent"] = request.headers.get("user-agent")

            # Create log record with extra fields
            extra = {"extra_fields": log_data}
            log = api_logger if is_api else logger

            # Log level based on status code
            if error:
                log.error(
                    f"{request.method} {request.url.path} - {status_code} ({duration_ms:.2f}ms) - ERROR: {error}",
                    extra=extra,
                    exc_info=True,
                )
            elif status_code >= 500:
                log.error(
                    f"{request.method} {request.url.path} - {status_code} ({duration_ms:.2f}ms)",
                    extra=extra,
                )
            elif status_code >= 400:
                log.warning(
                    f"{request.method} {request.url.path} - {status_code} ({duration_ms:.2f}ms)",
                    extra=extra,
                )
            else:
                # Don't log health checks at INFO level (reduces noise)
                if request.url.path == "/health":
                    log.debug(
                        f"{request.method} {request.url.path} - {status_code} ({duration_ms:.2f}ms)",
                        extra=extra,
                    )
                else:
                    log.info(
                        f"{request.method} {request.url.path} - {status_code} ({duration_ms:.2f}ms)",
                        extra=extra,
                    )

        # Add request ID to response headers
        response.headers["X-Request-ID"] = request_id

        return response


def log_auth_event(
    event_type: str,
    username: str,
    user_id: int | None = None,
    success: bool = True,
    details: dict | None = None,
) -> None:
    """
    Log authentication-related events.

    Args:
        event_type: Type of event (login, logout, password_change, etc.)
        username: Username involved
        user_id: User ID if known
        success: Whether the event was successful
        details: Additional details to log
    """
    log_data = {
        "event_type": event_type,
        "username": username,
        "success": success,
    }

    if user_id:
        log_data["user_id"] = user_id

    if details:
        log_data.update(details)

    extra = {"extra_fields": log_data}

    if success:
        logger.info(f"Auth event: {event_type} - {username} - SUCCESS", extra=extra)
    else:
        logger.warning(f"Auth event: {event_type} - {username} - FAILED", extra=extra)
