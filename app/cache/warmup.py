"""
Background warmup task — pre-fetches API data for all current MPs
so percentile rankings are available without blocking user requests.

Progress is persisted to cache/meta/warmup_status.json so the task
resumes after server restarts rather than starting over.
"""
import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from app.api.client import ThrottledAPIClient
from app.api.politicians import fetch_politician_list
from app.api.votes import fetch_session_votes, fetch_politician_ballots
from app.api.speeches import fetch_politician_speeches
from app.api.bills import fetch_sponsored_bills
from app.cache.manager import CACHE_ROOT
from app.config import settings

log = logging.getLogger("parlcards.warmup")

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
    log.info("Warmup started — session %s, cache_dir=%s", session, CACHE_ROOT)

    # Resume raw data fetch progress from previous run (avoids redundant API calls)
    status = load_warmup_status()
    completed: set[str] = set(status.get("completed_slugs", []))
    failed: set[str] = set(status.get("failed_slugs", []))
    log.info("Resuming warmup: %d already completed, %d previously failed", len(completed), len(failed))

    try:
        # Fetch shared data (reads from disk cache if fresh)
        politicians = await fetch_politician_list(client)
        log.info("Politician list loaded: %d MPs", len(politicians))

        session_votes = await fetch_session_votes(client, session)
        log.info("Session votes loaded: %d votes", len(session_votes))

        # Always rebuild card cache for current session — delete existing files first
        session_cards = list(
            (CACHE_ROOT / "computed" / "politicians").glob(f"*/sessions/{session}.json")
        )
        for f in session_cards:
            try:
                f.unlink()
            except OSError:
                pass
        log.info("Cleared %d stale card cache files for session %s", len(session_cards), session)

        # Fetch raw per-MP data (skips MPs already completed in a previous run)
        remaining = [
            p for p in politicians
            if p["slug"] not in completed and p["slug"] not in failed
        ]
        log.info("%d MPs remaining to fetch (of %d total)", len(remaining), len(politicians))

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
            except Exception as e:
                log.warning("Failed to fetch data for %s: %s", slug, e)
                failed.add(slug)

            # Log progress and save every 10 MPs
            if i % 10 == 0:
                log.info("Warmup progress: %d/%d fetched (%d failed)", i + 1, len(remaining), len(failed))
                save_warmup_status(completed, failed)

        log.info("Raw data fetch complete: %d succeeded, %d failed", len(completed), len(failed))

        # Build rankings table (all disk reads at this point)
        from app.metrics.percentiles import (
            build_rankings_table,
            compute_card_metrics_for_mp,
            cache_card_for_mp,
        )
        log.info("Building rankings table...")
        rankings_table = await build_rankings_table(client, session)
        log.info("Rankings table built: %d MPs", len(rankings_table.get("metrics", [])) if rankings_table else 0)

        # Build per-MP card cache (all disk reads — zero API calls)
        log.info("Caching per-MP cards...")
        cards_built = 0
        for politician in politicians:
            slug = politician["slug"]
            try:
                card_data = await compute_card_metrics_for_mp(
                    client, slug, session, session_votes, politician, rankings_table
                )
                if card_data:
                    cache_card_for_mp(slug, session, card_data)
                    cards_built += 1
            except Exception as e:
                log.warning("Failed to cache card for %s: %s", slug, e)

        log.info("Warmup complete: %d/%d cards cached", cards_built, len(politicians))
        save_warmup_status(completed, failed, rankings_complete=True)

    except Exception as e:
        log.exception("Warmup failed with unexpected error: %s", e)
        save_warmup_status(completed, failed)
