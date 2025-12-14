# app/core/logging_config.py
"""
Logging configuration for Periodical.

Provides structured logging with file rotation and different levels
for development and production environments.
"""

import logging
import logging.handlers
import os
import sys
import json
from datetime import datetime
from pathlib import Path
from typing import Any


# Determine if running in production
IS_PRODUCTION = os.getenv("PRODUCTION", "false").lower() == "true"

# Log directory
LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

# Log files
APP_LOG_FILE = LOG_DIR / "app.log"
ACCESS_LOG_FILE = LOG_DIR / "access.log"
ERROR_LOG_FILE = LOG_DIR / "error.log"


class JSONFormatter(logging.Formatter):
    """
    JSON formatter for structured logging.

    Outputs logs as JSON for easier parsing by log aggregation tools
    (Elasticsearch, Loki, CloudWatch, etc.)
    """

    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        # Add extra fields if present
        if hasattr(record, "extra_fields"):
            log_data.update(record.extra_fields)

        # Add user info if present
        if hasattr(record, "user_id"):
            log_data["user_id"] = record.user_id

        if hasattr(record, "username"):
            log_data["username"] = record.username

        # Add request info if present
        if hasattr(record, "request_id"):
            log_data["request_id"] = record.request_id

        if hasattr(record, "method"):
            log_data["method"] = record.method

        if hasattr(record, "path"):
            log_data["path"] = record.path

        if hasattr(record, "status_code"):
            log_data["status_code"] = record.status_code

        if hasattr(record, "duration"):
            log_data["duration_ms"] = record.duration

        return json.dumps(log_data, ensure_ascii=False)


class ColoredFormatter(logging.Formatter):
    """
    Colored formatter for console output in development.
    """

    COLORS = {
        'DEBUG': '\033[36m',      # Cyan
        'INFO': '\033[32m',       # Green
        'WARNING': '\033[33m',    # Yellow
        'ERROR': '\033[31m',      # Red
        'CRITICAL': '\033[35m',   # Magenta
    }
    RESET = '\033[0m'

    def format(self, record: logging.LogRecord) -> str:
        # Add color to level name
        if record.levelname in self.COLORS:
            record.levelname = (
                f"{self.COLORS[record.levelname]}{record.levelname}{self.RESET}"
            )
        return super().format(record)


def setup_logging() -> None:
    """
    Configure logging for the application.

    In production:
    - JSON format
    - Logs to rotating files
    - INFO level for app logs
    - Separate error log file

    In development:
    - Colored console output
    - DEBUG level
    - Human-readable format
    """

    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG if not IS_PRODUCTION else logging.INFO)

    # Remove existing handlers
    root_logger.handlers.clear()

    if IS_PRODUCTION:
        # Production: JSON logging to rotating files

        # App log (INFO and above)
        app_handler = logging.handlers.RotatingFileHandler(
            APP_LOG_FILE,
            maxBytes=10_000_000,  # 10MB
            backupCount=5,
            encoding='utf-8'
        )
        app_handler.setLevel(logging.INFO)
        app_handler.setFormatter(JSONFormatter())
        root_logger.addHandler(app_handler)

        # Error log (ERROR and above)
        error_handler = logging.handlers.RotatingFileHandler(
            ERROR_LOG_FILE,
            maxBytes=10_000_000,  # 10MB
            backupCount=10,
            encoding='utf-8'
        )
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(JSONFormatter())
        root_logger.addHandler(error_handler)

        # Console output (WARNING and above)
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.WARNING)
        console_handler.setFormatter(JSONFormatter())
        root_logger.addHandler(console_handler)

    else:
        # Development: Colored console output
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.DEBUG)

        formatter = ColoredFormatter(
            fmt='%(levelname)-8s %(asctime)s [%(name)s] %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)

        # Also log to file in development (but simpler format)
        file_handler = logging.handlers.RotatingFileHandler(
            APP_LOG_FILE,
            maxBytes=5_000_000,  # 5MB
            backupCount=2,
            encoding='utf-8'
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(
            '%(levelname)s %(asctime)s [%(name)s:%(lineno)d] %(message)s'
        ))
        root_logger.addHandler(file_handler)

    # Configure uvicorn loggers
    logging.getLogger("uvicorn").setLevel(logging.INFO)
    logging.getLogger("uvicorn.access").setLevel(logging.INFO)
    logging.getLogger("uvicorn.error").setLevel(logging.INFO)

    # Suppress noisy loggers
    logging.getLogger("watchfiles").setLevel(logging.WARNING)

    # Log startup message
    logger = logging.getLogger(__name__)
    logger.info(
        f"Logging configured (production={IS_PRODUCTION})",
        extra={
            "extra_fields": {
                "log_dir": str(LOG_DIR.absolute()),
                "production": IS_PRODUCTION
            }
        }
    )


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger instance.

    Args:
        name: Logger name (typically __name__)

    Returns:
        Logger instance
    """
    return logging.getLogger(name)


# Convenience function for adding extra context to logs
class LogContext:
    """
    Context manager for adding extra fields to log records.

    Usage:
        with LogContext(user_id=123, action="login"):
            logger.info("User logged in")
    """

    def __init__(self, **kwargs):
        self.extra_fields = kwargs
        self.old_factory = None

    def __enter__(self):
        self.old_factory = logging.getLogRecordFactory()

        def record_factory(*args, **kwargs):
            record = self.old_factory(*args, **kwargs)
            for key, value in self.extra_fields.items():
                setattr(record, key, value)
            return record

        logging.setLogRecordFactory(record_factory)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        logging.setLogRecordFactory(self.old_factory)
