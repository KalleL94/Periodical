# app/core/request_logging.py
"""
Request logging middleware for tracking all HTTP requests.
"""

import time
import uuid
from collections.abc import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from app.core.logging_config import get_logger

logger = get_logger(__name__)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """
    Middleware that logs all HTTP requests with timing and status codes.

    Adds a unique request ID to each request for tracing.
    """

    def __init__(self, app: ASGIApp):
        super().__init__(app)

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

            # Create log record with extra fields
            extra = {"extra_fields": log_data}

            # Log level based on status code
            if error:
                logger.error(
                    f"{request.method} {request.url.path} - {status_code} "
                    f"({duration_ms:.2f}ms) - ERROR: {error}",
                    extra=extra,
                    exc_info=True,
                )
            elif status_code >= 500:
                logger.error(
                    f"{request.method} {request.url.path} - {status_code} ({duration_ms:.2f}ms)",
                    extra=extra,
                )
            elif status_code >= 400:
                logger.warning(
                    f"{request.method} {request.url.path} - {status_code} ({duration_ms:.2f}ms)",
                    extra=extra,
                )
            else:
                # Don't log health checks at INFO level (reduces noise)
                if request.url.path == "/health":
                    logger.debug(
                        f"{request.method} {request.url.path} - {status_code} "
                        f"({duration_ms:.2f}ms)",
                        extra=extra,
                    )
                else:
                    logger.info(
                        f"{request.method} {request.url.path} - {status_code} "
                        f"({duration_ms:.2f}ms)",
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


def log_security_event(event_type: str, details: dict, level: str = "warning") -> None:
    """
    Log security-related events.

    Args:
        event_type: Type of security event
        details: Event details
        level: Log level (info, warning, error)
    """
    extra = {"extra_fields": {"event_type": event_type, **details}}

    message = f"Security event: {event_type}"

    if level == "error":
        logger.error(message, extra=extra)
    elif level == "warning":
        logger.warning(message, extra=extra)
    else:
        logger.info(message, extra=extra)
