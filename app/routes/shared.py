# app/routes/shared.py
"""
Shared utilities and templates for route modules.
"""

from fastapi.templating import Jinja2Templates

from app.core.helpers import contrast_color
from app.core.utils import get_today

# Shared Jinja2 templates instance
templates = Jinja2Templates(directory="app/templates")

# Register Jinja filter for contrast color on badges
templates.env.filters["contrast"] = contrast_color

# Add now (today's date) as a global for templates
# Note: This is set once at module load. For dynamic "today", use get_today() in routes.
templates.env.globals["now"] = get_today()
