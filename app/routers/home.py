import glob
import os
import re
import unicodedata

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse

from app.api.client import ThrottledAPIClient
from app.api.politicians import fetch_politician_list
from app.config import settings
from app.dependencies import get_client
from app.templates_config import templates

router = APIRouter()

# Full province names keyed by 2-letter code, for building searchable strings
_PROVINCE_NAMES = {
    "AB": "alberta",
    "BC": "british columbia",
    "MB": "manitoba",
    "NB": "new brunswick",
    "NL": "newfoundland and labrador",
    "NS": "nova scotia",
    "NT": "northwest territories",
    "NU": "nunavut",
    "ON": "ontario",
    "PE": "prince edward island",
    "QC": "quebec",
    "SK": "saskatchewan",
    "YT": "yukon",
}


def _normalize(s: str) -> str:
    """Lowercase and strip accents so 'québec' == 'quebec', 'montréal' == 'montreal'."""
    return unicodedata.normalize("NFD", s.lower()).encode("ascii", "ignore").decode()


def _available_sessions() -> list[str]:
    """Scan the rankings cache dir and return sorted session identifiers that have data."""
    pattern = os.path.join(settings.cache_dir, "computed", "rankings", "all_metrics_*.json")
    sessions = []
    for path in glob.glob(pattern):
        m = re.search(r"all_metrics_(.+)\.json$", path)
        if m:
            sessions.append(m.group(1))
    return sorted(sessions)


@router.get("/", response_class=HTMLResponse)
async def home(request: Request):
    sessions = _available_sessions()
    return templates.TemplateResponse("home.html", {"request": request, "available_sessions": sessions})


@router.get("/search", response_class=HTMLResponse)
async def search(
    request: Request,
    q: str = Query(default="", min_length=0),
    client: ThrottledAPIClient = Depends(get_client),
):
    if len(q) < 2:
        return HTMLResponse("")

    politicians = await fetch_politician_list(client)
    q_norm = _normalize(q)

    def matches(p: dict) -> bool:
        if q_norm in _normalize(p["name"]):
            return True
        if q_norm in _normalize(p.get("riding") or ""):
            return True
        # Province: match against both the 2-letter code and the full name
        code = (p.get("province") or "").upper()
        province_search = code.lower() + " " + _PROVINCE_NAMES.get(code, "")
        if q_norm in province_search:
            return True
        return False

    results = [p for p in politicians if matches(p)][:8]

    return templates.TemplateResponse(
        "partials/search_results.html",
        {"request": request, "results": results, "query": q},
    )
