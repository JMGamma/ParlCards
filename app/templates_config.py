"""
Single shared Jinja2Templates instance with custom filters registered.
Import this instead of creating a new Jinja2Templates in each router.
"""
from fastapi.templating import Jinja2Templates
from app.metrics.percentiles import bar_color, ordinal

templates = Jinja2Templates(directory="app/templates")
templates.env.globals["bar_color"] = bar_color
templates.env.globals["ordinal"] = ordinal
