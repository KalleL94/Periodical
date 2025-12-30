# app/main.py
"""
FastAPI application entry point.
"""

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.logging_config import get_logger, setup_logging
from app.core.request_logging import RequestLoggingMiddleware
from app.core.sentry_config import init_sentry
from app.database.database import create_tables, get_db
from app.routes.admin import router as admin_router
from app.routes.auth_routes import router as auth_router
from app.routes.public import router as public_router

# Setup logging FIRST (before any other imports that might log)
setup_logging()
logger = get_logger(__name__)

# Initialize Sentry for error tracking (production only)

sentry_enabled = init_sentry()


def validate_required_data_files():
    """
    Validate that all required JSON configuration files exist and are valid.

    Raises:
        RuntimeError: If any required file is missing or contains invalid JSON
    """
    required_files = [
        "data/shift_types.json",
        "data/rotation.json",
        "data/settings.json",
        "data/persons.json",
        "data/ob_rules.json",
        "data/oncall_rules.json",
    ]

    for file_path in required_files:
        path = Path(file_path)

        # Check if file exists
        if not path.exists():
            raise RuntimeError(
                f"Required data file missing: {file_path}\n"
                f"Ensure you are running from the correct directory and all data files are present."
            )

        # Validate JSON syntax
        try:
            with open(path, encoding="utf-8") as f:
                json.load(f)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Invalid JSON in {file_path}: {e}") from e

    logger.info(f"All {len(required_files)} required data files validated successfully")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events."""
    # Startup
    logger.info(
        "Application starting up",
        extra={
            "extra_fields": {
                "production": os.getenv("PRODUCTION", "false").lower() == "true",
                "python_version": os.sys.version,
            }
        },
    )

    # Validate required data files
    try:
        validate_required_data_files()
    except Exception as e:
        logger.error(f"Data file validation failed: {e}", exc_info=True)
        raise

    # Create database tables
    try:
        create_tables()
        logger.info("Database tables created/verified")
    except Exception as e:
        logger.error(f"Failed to create database tables: {e}", exc_info=True)
        raise

    yield

    # Shutdown
    logger.info("Application shutting down")


app = FastAPI(
    title="Periodical",
    description="Employee shift scheduling and OB pay calculation system",
    version="0.0.20",
    lifespan=lifespan,
)

# CORS Configuration
IS_PRODUCTION = os.getenv("PRODUCTION", "false").lower() == "true"
CORS_ORIGINS = (
    [origin.strip() for origin in os.getenv("CORS_ORIGINS", "").split(",") if origin.strip()]
    if os.getenv("CORS_ORIGINS")
    else []
)

if IS_PRODUCTION:
    # Production: Strict CORS - only allow specified origins
    if not CORS_ORIGINS:
        logger.warning(
            "Production mode but no CORS_ORIGINS set. CORS will block all cross-origin requests. "
            "Set CORS_ORIGINS environment variable if you need to allow specific origins."
        )

    allowed_origins = CORS_ORIGINS
    allow_credentials = True
    allowed_methods = ["GET", "POST"]  # Only allow methods we use
    allowed_headers = ["*"]

    logger.info(f"CORS configured for production with origins: {allowed_origins}")
else:
    # Development: Permissive CORS for easier testing
    allowed_origins = ["*"]
    allow_credentials = True
    allowed_methods = ["*"]
    allowed_headers = ["*"]

    logger.info("CORS configured for development (permissive)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=allow_credentials,
    allow_methods=allowed_methods,
    allow_headers=allowed_headers,
    expose_headers=["X-Request-ID"],  # Expose our request ID header
)

# Add request logging middleware
app.add_middleware(RequestLoggingMiddleware)

# Mount static files
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Include routers
app.include_router(public_router)
app.include_router(auth_router)
app.include_router(admin_router)


@app.get("/health", tags=["health"])
async def health_check(db: Session = Depends(get_db)):
    """
    Health check endpoint for monitoring.

    Checks both application status and database connectivity.
    Returns 200 OK if both application and database are healthy.
    Returns 503 Service Unavailable if database connection fails.
    """
    try:
        # Verify database connection with a simple query
        db.execute(text("SELECT 1"))
        return JSONResponse(
            status_code=200,
            content={
                "status": "healthy",
                "service": "periodical",
                "version": "0.0.20",
                "database": "connected",
            },
        )
    except Exception as e:
        logger.error(f"Health check failed - database connection error: {e}", exc_info=True)
        raise HTTPException(
            status_code=503,
            detail={
                "status": "unhealthy",
                "service": "periodical",
                "database": "disconnected",
                "error": "Database connection failed",
            },
        ) from e
