# app/core/sentry_config.py
"""
Sentry configuration for error tracking in production.

Sentry captures exceptions, errors, and performance data from production
environments for monitoring and debugging.
"""

import logging
import os

logger = logging.getLogger(__name__)


def init_sentry() -> bool:
    """
    Initialize Sentry error tracking.

    Returns:
        True if Sentry was initialized successfully, False otherwise.
    """
    # Only initialize in production
    is_production = os.getenv("PRODUCTION", "false").lower() == "true"
    sentry_dsn = os.getenv("SENTRY_DSN", "").strip()

    if not is_production:
        logger.info("Sentry disabled in development mode")
        return False

    if not sentry_dsn:
        logger.warning(
            "SENTRY_DSN not set. Error tracking disabled. "
            "Set SENTRY_DSN environment variable to enable Sentry in production."
        )
        return False

    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.logging import LoggingIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration

        # Logging integration - send error logs to Sentry
        logging_integration = LoggingIntegration(
            level=logging.INFO,  # Breadcrumbs from INFO and above
            event_level=logging.ERROR,  # Send errors and above as events
        )

        # Initialize Sentry
        sentry_sdk.init(
            dsn=sentry_dsn,
            # Integrations
            integrations=[
                FastApiIntegration(),
                StarletteIntegration(),
                logging_integration,
            ],
            # Performance monitoring (traces)
            traces_sample_rate=0.1,  # 10% of requests tracked for performance
            # Error sampling
            sample_rate=1.0,  # Send 100% of errors
            # Release tracking
            release=os.getenv("RELEASE_VERSION", "periodical@0.0.20"),
            # Environment
            environment=os.getenv("SENTRY_ENVIRONMENT", "production"),
            # Additional options
            send_default_pii=False,  # Don't send personally identifiable info
            attach_stacktrace=True,  # Include stack traces
            # Before send hook - filter sensitive data
            before_send=before_send_hook,
        )

        env = os.getenv('SENTRY_ENVIRONMENT', 'production')
        logger.info(f"Sentry initialized successfully (environment: {env})")
        return True

    except ImportError:
        logger.warning("Sentry SDK not installed. Install with: pip install sentry-sdk[fastapi]")
        return False
    except Exception as e:
        logger.error(f"Failed to initialize Sentry: {e}", exc_info=True)
        return False


def before_send_hook(event, hint):
    """
    Filter sensitive data before sending to Sentry.

    Args:
        event: Sentry event data
        hint: Additional context

    Returns:
        Modified event or None to drop the event
    """
    # Remove sensitive headers
    if "request" in event:
        if "headers" in event["request"]:
            sensitive_headers = ["cookie", "authorization", "x-api-key"]
            for header in sensitive_headers:
                if header in event["request"]["headers"]:
                    event["request"]["headers"][header] = "[Filtered]"

    # Remove sensitive query parameters
    if "request" in event and "query_string" in event["request"]:
        query = event["request"]["query_string"]
        if query and ("password" in query.lower() or "token" in query.lower()):
            event["request"]["query_string"] = "[Filtered]"

    return event


def capture_exception(error: Exception, context: dict = None):
    """
    Manually capture an exception to Sentry with additional context.

    Args:
        error: Exception to capture
        context: Additional context to send with the error
    """
    try:
        import sentry_sdk

        if context:
            with sentry_sdk.push_scope() as scope:
                for key, value in context.items():
                    scope.set_context(key, value)
                sentry_sdk.capture_exception(error)
        else:
            sentry_sdk.capture_exception(error)

    except ImportError:
        # Sentry not installed, just log
        logger.error(f"Error occurred: {error}", exc_info=True)


def capture_message(message: str, level: str = "info", context: dict = None):
    """
    Send a message to Sentry.

    Args:
        message: Message to send
        level: Severity level (debug, info, warning, error, fatal)
        context: Additional context
    """
    try:
        import sentry_sdk

        if context:
            with sentry_sdk.push_scope() as scope:
                for key, value in context.items():
                    scope.set_context(key, value)
                sentry_sdk.capture_message(message, level=level)
        else:
            sentry_sdk.capture_message(message, level=level)

    except ImportError:
        # Sentry not installed, just log
        log_level = getattr(logging, level.upper(), logging.INFO)
        logger.log(log_level, message)


def set_user_context(user_id: int, username: str = None, email: str = None):
    """
    Set user context for Sentry events.

    Args:
        user_id: User ID
        username: Username (optional)
        email: Email (optional)
    """
    try:
        import sentry_sdk

        sentry_sdk.set_user(
            {
                "id": user_id,
                "username": username,
                "email": email,
            }
        )
    except ImportError:
        pass


def clear_user_context():
    """Clear user context (e.g., after logout)."""
    try:
        import sentry_sdk

        sentry_sdk.set_user(None)
    except ImportError:
        pass


def add_breadcrumb(message: str, category: str = "default", level: str = "info", data: dict = None):
    """
    Add a breadcrumb for debugging.

    Breadcrumbs are trails of events that happened before an error.

    Args:
        message: Breadcrumb message
        category: Category (auth, navigation, http, etc.)
        level: Severity level
        data: Additional data
    """
    try:
        import sentry_sdk

        sentry_sdk.add_breadcrumb(message=message, category=category, level=level, data=data or {})
    except ImportError:
        pass
