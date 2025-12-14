# app/main.py
"""
FastAPI application entry point.
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from app.routes.public import router as public_router
from app.routes.auth_routes import router as auth_router
from app.routes.admin import router as admin_router
from app.database.database import create_tables
from app.core.logging_config import setup_logging, get_logger
from app.core.request_logging import RequestLoggingMiddleware

# Setup logging FIRST (before any other imports that might log)
setup_logging()
logger = get_logger(__name__)

# Initialize Sentry for error tracking (production only)
from app.core.sentry_config import init_sentry
sentry_enabled = init_sentry()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events."""
    # Startup
    logger.info("Application starting up", extra={
        "extra_fields": {
            "production": os.getenv("PRODUCTION", "false").lower() == "true",
            "python_version": os.sys.version
        }
    })

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
    lifespan=lifespan
)

# CORS Configuration
IS_PRODUCTION = os.getenv("PRODUCTION", "false").lower() == "true"
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "").split(",") if os.getenv("CORS_ORIGINS") else []

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
async def health_check():
    """
    Health check endpoint for monitoring.

    Returns 200 OK if application is running.
    """
    return JSONResponse(
        status_code=200,
        content={
            "status": "healthy",
            "service": "periodical",
            "version": "0.0.20"
        }
    )
