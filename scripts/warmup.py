"""
Standalone cache warmup script.

Run this once (or overnight) to pre-fetch data for all current MPs
so the web server can serve stat cards instantly.

Usage:
    uv run python scripts/warmup.py

Options:
    --session 45-1       Parliament session to warm (default: from .env)
    --limit 50           Only warm N politicians (for testing)
    --skip-rankings      Skip building the percentile rankings table at the end
    --resume             Resume from last saved progress (default: always resumes)

Progress is saved every 5 MPs so the script can be interrupted and restarted.
"""
import argparse
import asyncio
import sys
import time
from pathlib import Path

# Ensure project root is on sys.path when run as a script
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.api.client import ThrottledAPIClient
from app.api.politicians import fetch_politician_list
from app.api.votes import fetch_session_votes, fetch_politician_ballots, fetch_vote_detail
from app.api.speeches import fetch_politician_speeches
from app.api.bills import fetch_sponsored_bills
from app.cache.manager import cache_entry, CACHE_ROOT
from app.config import settings
from app.metrics.percentiles import build_rankings_table

import json
from datetime import datetime, timezone

PROGRESS_FILE = CACHE_ROOT / "meta" / "warmup_status.json"


def load_progress() -> dict:
    try:
        if PROGRESS_FILE.exists():
            return json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"completed_slugs": [], "failed_slugs": []}


def save_progress(completed: set[str], failed: set[str], total: int) -> None:
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "completed_slugs": sorted(completed),
        "failed_slugs": sorted(failed),
        "total_mps": total,
        "rankings_complete": False,
    }
    PROGRESS_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def mark_rankings_complete(completed: set[str], failed: set[str], total: int) -> None:
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "completed_slugs": sorted(completed),
        "failed_slugs": sorted(failed),
        "total_mps": total,
        "rankings_complete": True,
    }
    PROGRESS_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")


async def warmup_politician(
    client: ThrottledAPIClient,
    slug: str,
    session: str,
) -> bool:
    """
    Pre-fetch all data needed for one MP's card.
    Returns True on success, False on failure.
    """
    try:
        # Ballots (all historical — filtered client-side to target session)
        ballots = await fetch_politician_ballots(client, slug, session)

        # Speeches and bills can run after ballots (serialized by rate limiter anyway)
        await fetch_politician_speeches(client, slug, session)
        await fetch_sponsored_bills(client, slug, session)

        return True
    except Exception as e:
        print(f"  FAILED: {slug} — {e}", flush=True)
        return False


async def main() -> None:
    parser = argparse.ArgumentParser(description="Pre-warm ParlCards cache")
    parser.add_argument("--session", default=settings.session, help="Parliament session (e.g. 45-1)")
    parser.add_argument("--limit", type=int, default=None, help="Only warm N politicians")
    parser.add_argument("--skip-rankings", action="store_true", help="Skip building the percentile rankings table")
    args = parser.parse_args()

    session = args.session
    print(f"ParlCards cache warmup — session {session}")
    print(f"Rate limit: {settings.rate_limit_per_minute} req/min, {settings.min_delay_seconds}s min delay")
    print()

    client = ThrottledAPIClient()
    await client.start()

    try:
        # Step 1: Fetch politician list
        print("Fetching politician list...", flush=True)
        politicians = await fetch_politician_list(client)
        print(f"  Found {len(politicians)} current MPs", flush=True)

        # Step 2: Fetch session vote list (shared by all MPs)
        print(f"Fetching session {session} vote list...", flush=True)
        session_votes = await fetch_session_votes(client, session)
        print(f"  Found {len(session_votes)} votes in session {session}", flush=True)

        # Step 3: Pre-fetch all vote details (for party loyalty — shared across all MPs)
        print(f"Pre-fetching {len(session_votes)} vote details (permanent cache)...", flush=True)
        vote_detail_cached = 0
        for vote in session_votes:
            vote_num = vote.get("number") or vote.get("url", "").strip("/").split("/")[-1]
            entry = cache_entry(f"raw/votes/detail_{session}_{vote_num}.json")
            if entry.is_expired():
                detail = await fetch_vote_detail(client, session, vote_num)
                if detail:
                    vote_detail_cached += 1
        print(f"  Cached {vote_detail_cached} new vote details ({len(session_votes)} total)", flush=True)

        # Step 4: Per-politician data
        progress = load_progress()
        completed: set[str] = set(progress.get("completed_slugs", []))
        failed: set[str] = set(progress.get("failed_slugs", []))

        remaining = [p for p in politicians if p["slug"] not in completed and p["slug"] not in failed]
        if args.limit:
            remaining = remaining[:args.limit]

        total = len(politicians)
        already_done = len(completed)

        print(f"\nWarming {len(remaining)} MPs ({already_done} already cached, {len(failed)} previously failed)...")
        print("Press Ctrl+C to stop — progress is saved every 5 MPs\n", flush=True)

        start_time = time.monotonic()

        for i, politician in enumerate(remaining):
            slug = politician["slug"]
            name = politician["name"]
            party = politician.get("party", "?")

            elapsed = time.monotonic() - start_time
            done_so_far = already_done + i
            remaining_count = total - done_so_far - 1
            eta_str = ""
            if i > 0 and elapsed > 0:
                rate = i / elapsed  # MPs per second
                if rate > 0:
                    eta_s = remaining_count / rate
                    eta_m = int(eta_s / 60)
                    eta_str = f" (ETA ~{eta_m}m)"

            print(
                f"  [{done_so_far + 1}/{total}] {name} ({party}){eta_str}",
                end="",
                flush=True,
            )

            success = await warmup_politician(client, slug, session)
            if success:
                completed.add(slug)
                print(" ✓", flush=True)
            else:
                failed.add(slug)
                print(" ✗", flush=True)

            # Save progress every 5 MPs
            if (i + 1) % 5 == 0:
                save_progress(completed, failed, total)

        save_progress(completed, failed, total)

        print(f"\nData fetch complete: {len(completed)} succeeded, {len(failed)} failed")

        # Step 5: Build rankings table
        if not args.skip_rankings and len(completed) > 10:
            print("\nBuilding percentile rankings table...", flush=True)
            table = await build_rankings_table(client, session)
            computed = table.get("computed_mps", 0)
            print(f"  Rankings built for {computed} MPs")

            # Step 6: Build per-MP card cache
            # Stored at: cache/computed/politicians/{slug}/sessions/{session}.json
            # Multi-session ready: run --session 44-1 to add a second session layer
            from app.metrics.percentiles import compute_card_metrics_for_mp, cache_card_for_mp

            # Always rebuild — delete existing session card files first
            session_cards = list(
                (CACHE_ROOT / "computed" / "politicians").glob(f"*/sessions/{session}.json")
            )
            for f in session_cards:
                try:
                    f.unlink()
                except OSError:
                    pass

            print(f"\nBuilding per-MP card cache ({len(politicians)} MPs)...", flush=True)
            cards_built = 0
            cards_failed = 0
            t_cards = time.monotonic()

            for i, politician in enumerate(politicians):
                slug = politician["slug"]
                try:
                    card_data = await compute_card_metrics_for_mp(
                        client, slug, session, session_votes, politician, table
                    )
                    if card_data:
                        cache_card_for_mp(slug, session, card_data)
                        cards_built += 1
                    else:
                        cards_failed += 1
                except Exception:
                    cards_failed += 1

                if (i + 1) % 50 == 0 or (i + 1) == len(politicians):
                    print(f"  {i + 1}/{len(politicians)} cards built...", flush=True)

            elapsed_cards = time.monotonic() - t_cards
            print(
                f"  Card cache: {cards_built}/{len(politicians)} MPs "
                f"({cards_failed} failed) in {elapsed_cards:.1f}s",
                flush=True,
            )

            mark_rankings_complete(completed, failed, total)
            print("  Done — cards will now serve from pre-computed cache")
        elif args.skip_rankings:
            print("\nSkipping rankings table (--skip-rankings)")
        else:
            print(f"\nOnly {len(completed)} MPs warmed — need >10 for rankings. Run again without --limit.")

        elapsed_total = time.monotonic() - start_time
        print(f"\nDone in {elapsed_total / 60:.1f} minutes.")

    except KeyboardInterrupt:
        print("\n\nInterrupted — progress saved. Run again to resume.")
    finally:
        await client.stop()


if __name__ == "__main__":
    asyncio.run(main())
