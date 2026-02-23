"""
Background warmup task — pre-fetches API data for all current MPs
so percentile rankings are available without blocking user requests.

Progress is persisted to cache/meta/warmup_status.json so the task
resumes after server restarts rather than starting over.
"""
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from app.api.client import ThrottledAPIClient
from app.api.politicians import fetch_politician_list
from app.api.votes import fetch_session_votes, fetch_politician_ballots
from app.api.speeches import fetch_politician_speeches
from app.api.bills import fetch_sponsored_bills
from app.cache.manager import CACHE_ROOT
from app.config import settings

STATUS_FILE = CACHE_ROOT / "meta" / "warmup_status.json"


def load_warmup_status() -> dict:
    try:
        if STATUS_FILE.exists():
            return json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"completed_slugs": [], "failed_slugs": [], "rankings_complete": False}


def save_warmup_status(
    completed: set[str],
    failed: set[str],
    rankings_complete: bool = False,
    total_requests: int = 0,
) -> None:
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "completed_slugs": sorted(completed),
        "failed_slugs": sorted(failed),
        "rankings_complete": rankings_complete,
        "total_api_requests": total_requests,
    }
    STATUS_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")


async def background_warmup(client: ThrottledAPIClient) -> None:
    """
    Pre-fetch data for all current MPs in the background, then compute and
    store pre-built card metrics for each MP.

    Runs as an asyncio task; does not block user requests.

    On every server start: raw data with valid TTLs is read from disk (no API
    calls), but the per-session card files are always deleted and rebuilt so
    the rendered cards always reflect fresh computations.
    """
    session = settings.session

    # Resume raw data fetch progress from previous run (avoids redundant API calls)
    status = load_warmup_status()
    completed: set[str] = set(status.get("completed_slugs", []))
    failed: set[str] = set(status.get("failed_slugs", []))

    try:
        # Fetch shared data (reads from disk cache if fresh)
        politicians = await fetch_politician_list(client)
        session_votes = await fetch_session_votes(client, session)

        # Always rebuild card cache for current session — delete existing files first
        session_cards = list(
            (CACHE_ROOT / "computed" / "politicians").glob(f"*/sessions/{session}.json")
        )
        for f in session_cards:
            try:
                f.unlink()
            except OSError:
                pass

        # Fetch raw per-MP data (skips MPs already completed in a previous run)
        remaining = [
            p for p in politicians
            if p["slug"] not in completed and p["slug"] not in failed
        ]

        for i, politician in enumerate(remaining):
            slug = politician["slug"]
            try:
                # Fetch the three per-politician data types
                await asyncio.gather(
                    fetch_politician_ballots(client, slug, session),
                    fetch_politician_speeches(client, slug, session),
                    fetch_sponsored_bills(client, slug, session),
                )
                completed.add(slug)
            except Exception:
                failed.add(slug)

            # Save progress every 10 MPs
            if i % 10 == 0:
                save_warmup_status(completed, failed)

        # Build rankings table (all disk reads at this point)
        from app.metrics.percentiles import (
            build_rankings_table,
            compute_card_metrics_for_mp,
            cache_card_for_mp,
        )
        rankings_table = await build_rankings_table(client, session)

        # Build per-MP card cache (all disk reads — zero API calls)
        for politician in politicians:
            slug = politician["slug"]
            try:
                card_data = await compute_card_metrics_for_mp(
                    client, slug, session, session_votes, politician, rankings_table
                )
                if card_data:
                    cache_card_for_mp(slug, session, card_data)
            except Exception:
                pass  # This MP falls back to on-demand computation in /api/card/

        save_warmup_status(completed, failed, rankings_complete=True)

    except Exception:
        save_warmup_status(completed, failed)
