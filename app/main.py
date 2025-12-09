# app/main.py
"""
FastAPI application entry point.
"""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.routes.public import router as public_router
from app.routes.auth_routes import router as auth_router
from app.routes.admin import router as admin_router
from app.database.database import create_tables

# Create database tables on startup
create_tables()

app = FastAPI(title="Periodical")

# Mount static files
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Include routers
app.include_router(public_router)
app.include_router(auth_router)
app.include_router(admin_router)
