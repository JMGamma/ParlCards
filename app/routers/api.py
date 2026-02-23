import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from app.api.client import ThrottledAPIClient
from app.api.politicians import fetch_politician_detail
from app.api.votes import fetch_politician_ballots, fetch_session_votes
from app.api.speeches import fetch_politician_speeches
from app.api.bills import fetch_sponsored_bills
from app.config import settings
from app.dependencies import get_client
from app.templates_config import templates
from app.metrics.attendance import compute_attendance
from app.metrics.party_loyalty import compute_party_loyalty
from app.metrics.bills_count import compute_bills_count
from app.metrics.debate_participation import compute_debate_speeches
from app.metrics.percentiles import (
    load_or_build_rankings,
    compute_percentiles_for_mp,
    compute_percentiles_for_mp_by_group,
    filter_table_by_group,
    compute_distributions_for_mp,
    load_card_for_mp,
)

router = APIRouter()


@router.get("/api/card/{slug}", response_class=HTMLResponse)
async def card_fragment(
    slug: str,
    request: Request,
    group: str = "all",
    client: ThrottledAPIClient = Depends(get_client),
):
    session = settings.session
    mp_party = ""

    # Fetch politician detail (should already be cached from page shell request)
    politician = await fetch_politician_detail(client, slug)
    if politician is None:
        raise HTTPException(status_code=404, detail="Politician not found")

    mp_party = politician.get("party", "")

    # Always load the rankings table — needed for distributions on every path
    rankings_table = await load_or_build_rankings(client, session)

    # Fast path: serve pre-computed card metrics if available (group=all only)
    if group == "all":
        cached_card = load_card_for_mp(slug, session)
        if cached_card is not None and rankings_table:
            filtered = filter_table_by_group(slug, rankings_table, "all", mp_party, settings.government_party)
            distributions = compute_distributions_for_mp(slug, filtered)
            return templates.TemplateResponse(
                "partials/card.html",
                {
                    "request": request,
                    "politician": politician,
                    "metrics": cached_card["metrics"],
                    "percentiles": cached_card["percentiles"],
                    "distributions": distributions,
                    "rankings_available": True,
                    "session": session,
                    "active_group": group,
                },
            )

    # Group-filtered path: compute percentiles and distributions for the requested group.
    # Metrics come from pre-computed card (if available) — no raw data re-fetch needed.
    if group != "all" and rankings_table:
        cached_card = load_card_for_mp(slug, session)
        if cached_card is not None:
            filtered = filter_table_by_group(slug, rankings_table, group, mp_party, settings.government_party)
            percentiles = compute_percentiles_for_mp(slug, filtered)
            distributions = compute_distributions_for_mp(slug, filtered)
            return templates.TemplateResponse(
                "partials/card.html",
                {
                    "request": request,
                    "politician": politician,
                    "metrics": cached_card["metrics"],
                    "percentiles": percentiles,
                    "distributions": distributions,
                    "rankings_available": True,
                    "session": session,
                    "active_group": group,
                },
            )

    # Fallback: on-demand computation (cold start or warmup not yet run for this MP)
    session_votes, ballots, speeches, bills = await asyncio.gather(
        fetch_session_votes(client, session),
        fetch_politician_ballots(client, slug, session),
        fetch_politician_speeches(client, slug, session),
        fetch_sponsored_bills(client, slug, session),
    )

    attendance = compute_attendance(ballots, session_votes)
    bills_count, bills_list = compute_bills_count(bills)
    speeches_count = compute_debate_speeches(speeches)
    loyalty = await compute_party_loyalty(
        client, ballots, mp_party, session
    )

    metrics = {
        "attendance": attendance,
        "party_loyalty": loyalty,
        "bills_sponsored": bills_count,
        "bills_list": bills_list[:5],
        "debate_speeches": speeches_count,
        "total_votes": len(session_votes),
        "votes_cast": sum(1 for b in ballots if b.get("ballot") in ("Yes", "No")),
    }

    percentiles: dict = {}
    distributions: dict = {}
    rankings_available = False

    if rankings_table:
        # Ensure this MP is in the table for percentile computation
        existing = next(
            (m for m in rankings_table.get("metrics", []) if m["slug"] == slug),
            None,
        )
        if existing is None:
            rankings_table["metrics"].append({
                "slug": slug,
                "party": mp_party,
                "attendance": attendance,
                "party_loyalty": loyalty,
                "bills_sponsored": bills_count,
                "debate_speeches": speeches_count,
            })

        filtered = filter_table_by_group(slug, rankings_table, group, mp_party, settings.government_party)
        percentiles = compute_percentiles_for_mp(slug, filtered)
        distributions = compute_distributions_for_mp(slug, filtered)
        rankings_available = True

    return templates.TemplateResponse(
        "partials/card.html",
        {
            "request": request,
            "politician": politician,
            "metrics": metrics,
            "percentiles": percentiles,
            "distributions": distributions,
            "rankings_available": rankings_available,
            "session": session,
            "active_group": group,
        },
    )
