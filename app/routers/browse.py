import math

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse

from app.api.client import ThrottledAPIClient
from app.api.politicians import fetch_politician_list
from app.config import settings
from app.dependencies import get_client
from app.metrics.percentiles import get_percentile, load_or_build_rankings
from app.templates_config import templates

router = APIRouter()

PAGE_SIZE = 25

METRICS = {
    "attendance":      {"label": "Voting Attendance",    "unit": "%",         "decimals": 1},
    "party_loyalty":   {"label": "Party Loyalty",        "unit": "%",         "decimals": 1},
    "bills_sponsored": {"label": "Bills Sponsored",      "unit": "",          "decimals": 0},
    "debate_speeches": {"label": "Debate Participation", "unit": " speeches", "decimals": 0},
}


def _build_rows(rankings_table: dict, pol_map: dict, metric: str, group: str, government_party: str) -> list[dict]:
    """Sort, filter, merge, and compute percentiles for all MPs for a given metric+group."""
    metrics_list = rankings_table.get("metrics", [])

    # Filter by group
    if group == "government":
        filtered = [m for m in metrics_list if m.get("party") == government_party]
    elif group == "opposition":
        filtered = [m for m in metrics_list if m.get("party") != government_party]
    else:  # "all" or unknown
        filtered = metrics_list

    # Sort descending; None values go to the bottom
    sorted_metrics = sorted(
        filtered,
        key=lambda m: (m.get(metric) is not None, m.get(metric) or 0),
        reverse=True,
    )

    # Compute percentiles within this group
    all_vals = [m[metric] for m in filtered if m.get(metric) is not None]

    rows = []
    for rank_idx, m in enumerate(sorted_metrics):
        pol = pol_map.get(m["slug"], {})
        val = m.get(metric)
        pct = get_percentile(val, all_vals) if val is not None else None
        rows.append({
            "rank":       rank_idx + 1,
            "slug":       m["slug"],
            "name":       pol.get("name", m["slug"]),
            "party":      pol.get("party", m.get("party", "")),
            "party_slug": pol.get("party_slug", "independent"),
            "riding":     pol.get("riding", ""),
            "province":   pol.get("province", ""),
            "image":      pol.get("image", ""),
            "value":      val,
            "percentile": pct,
        })
    return rows


def _paginate(rows: list, page: int) -> tuple[list, int, int, int]:
    total = len(rows)
    total_pages = max(1, math.ceil(total / PAGE_SIZE))
    page = max(1, min(page, total_pages))
    page_rows = rows[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]
    return page_rows, page, total_pages, total


async def _get_context(client: ThrottledAPIClient, metric: str, group: str, page: int) -> dict:
    """Shared context builder for both full-page and HTMX partial endpoints."""
    if metric not in METRICS:
        metric = "attendance"

    rankings_table = await load_or_build_rankings(client, settings.session)
    politicians = await fetch_politician_list(client)
    pol_map = {p["slug"]: p for p in politicians}

    rows = _build_rows(rankings_table, pol_map, metric, group, settings.government_party) if rankings_table else []
    page_rows, page, total_pages, total = _paginate(rows, page)

    return {
        "metric":             metric,
        "metric_meta":        METRICS[metric],
        "metrics_list":       METRICS,
        "group":              group,
        "page":               page,
        "total_pages":        total_pages,
        "total":              total,
        "rows":               page_rows,
        "rankings_available": rankings_table is not None,
        "session":            settings.session,
    }


@router.get("/browse", response_class=HTMLResponse)
async def browse_page(
    request: Request,
    metric: str = Query(default="attendance"),
    group:  str = Query(default="all"),
    page:   int = Query(default=1),
    client: ThrottledAPIClient = Depends(get_client),
):
    ctx = await _get_context(client, metric, group, page)
    return templates.TemplateResponse("browse.html", {"request": request, **ctx})


@router.get("/api/browse", response_class=HTMLResponse)
async def browse_fragment(
    request: Request,
    metric: str = Query(default="attendance"),
    group:  str = Query(default="all"),
    page:   int = Query(default=1),
    client: ThrottledAPIClient = Depends(get_client),
):
    ctx = await _get_context(client, metric, group, page)
    return templates.TemplateResponse("partials/browse_list.html", {"request": request, **ctx})
