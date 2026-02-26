import asyncio

from app.api.client import ThrottledAPIClient
from app.api.politicians import fetch_politician_list
from app.api.votes import fetch_politician_ballots, fetch_session_votes
from app.api.speeches import fetch_speech_count
from app.api.bills import fetch_sponsored_bills
from app.cache.manager import cache_entry
from app.config import settings
from app.metrics.attendance import compute_attendance
from app.metrics.party_loyalty import compute_party_loyalty
from app.metrics.bills_count import compute_bills_count


def get_percentile(value: float, all_values: list[float]) -> int:
    """
    Return the percentile rank (0–100) of value within all_values.

    Anchored at both extremes:
      - Tied at the maximum  → 100  (no one scores higher)
      - Tied at the minimum  → 0    (no one scores lower)
      - Everyone else        → linearly scaled 1–99 based on their rank
                               among the non-maximum MPs

    This handles the common case where many MPs tie at the top (e.g. 210/340
    with 100% attendance) — all of them correctly show 100th percentile rather
    than being compressed to the 38th percentile by a strict "count below" formula.
    The same logic applies symmetrically at the bottom (e.g. 286/340 MPs with
    0 bills sponsored all correctly show 0th percentile).
    """
    if not all_values:
        return 50
    count_below = sum(1 for v in all_values if v < value)
    count_above = sum(1 for v in all_values if v > value)
    if count_above == 0:
        return 100
    if count_below == 0:
        return 0
    # Scale linearly within the non-maximum population (1–99 range)
    non_top = len(all_values) - sum(1 for v in all_values if v == max(all_values))
    return round(count_below / non_top * 99)


def ordinal(n: int) -> str:
    """Convert an integer to its ordinal string: 1 -> '1st', 2 -> '2nd', etc."""
    if 11 <= (n % 100) <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def bar_color(percentile: int) -> str:
    """Return hex color for a percentile value."""
    if percentile >= 80:
        return "#22C55E"
    elif percentile >= 60:
        return "#84CC16"
    elif percentile >= 40:
        return "#EAB308"
    elif percentile >= 20:
        return "#F97316"
    else:
        return "#EF4444"


async def compute_single_mp_metrics(
    client: ThrottledAPIClient,
    slug: str,
    session: str,
    session_votes: list[dict],
    politician: dict,
) -> dict | None:
    """Compute all 4 metrics for a single MP. Returns None on error."""
    try:
        # Use fetch_speech_count() instead of fetch_politician_speeches() here:
        # speeches cache averages 5.3 MB per MP (up to 67 MB for prolific speakers).
        # Parsing 1.8 GB across 340 MPs just to call len() was causing build_rankings_table()
        # to hang. The summary file is ~50 bytes and contains only the count.
        #
        # stale_ok=True for ballots: votes already cast are immutable, so expired
        # ballot cache is still correct data. Avoids re-fetching 176 ballot files
        # at 20 req/min just to compute rankings.
        ballots, speeches_count, bills = await asyncio.gather(
            fetch_politician_ballots(client, slug, session, stale_ok=True),
            fetch_speech_count(client, slug, session),
            fetch_sponsored_bills(client, slug, session),
        )

        attendance = compute_attendance(ballots, session_votes)
        bills_count, _ = compute_bills_count(bills)
        loyalty = await compute_party_loyalty(
            client, ballots, politician.get("party", ""), session
        )

        return {
            "slug": slug,
            "attendance": attendance,
            "party_loyalty": loyalty,
            "bills_sponsored": bills_count,
            "debate_speeches": speeches_count,  # int from fetch_speech_count()
        }
    except Exception:
        return None


async def load_or_build_rankings(
    client: ThrottledAPIClient,
    session: str,
) -> dict | None:
    """
    Load the rankings table from cache, or return None if not yet built.
    Building happens in the background warmup task.
    """
    entry = cache_entry(f"computed/rankings/all_metrics_{session}.json")
    return entry.read()


async def build_rankings_table(
    client: ThrottledAPIClient,
    session: str,
    progress_callback=None,
) -> dict:
    """
    Build the full rankings table for all current MPs.
    This is slow on cold start (~hours). Called from warmup task.
    Returns the table dict.
    """
    politicians = await fetch_politician_list(client)
    session_votes = await fetch_session_votes(client, session)

    all_metrics: list[dict] = []
    failed: list[str] = []

    for i, politician in enumerate(politicians):
        slug = politician["slug"]
        metrics = await compute_single_mp_metrics(
            client, slug, session, session_votes, politician
        )
        if metrics:
            metrics["party"] = politician.get("party", "")
            all_metrics.append(metrics)
        else:
            failed.append(slug)

        if progress_callback:
            progress_callback(i + 1, len(politicians), slug)

    table = {
        "session": session,
        "total_mps": len(politicians),
        "computed_mps": len(all_metrics),
        "failed_slugs": failed,
        "metrics": all_metrics,
    }

    entry = cache_entry(f"computed/rankings/all_metrics_{session}.json")
    entry.write(table, ttl_seconds=settings.ttl_rankings)
    return table


async def compute_card_metrics_for_mp(
    client: ThrottledAPIClient,
    slug: str,
    session: str,
    session_votes: list[dict],
    politician: dict,
    rankings_table: dict,
) -> dict | None:
    """
    Compute the full card data for one MP: raw metrics + bills_list + percentiles.

    Called during warmup after all raw data is cached and the rankings table is built.
    All fetch calls at this point are disk reads — zero API calls.

    Returns a dict ready to store in cache/computed/politicians/{slug}/sessions/{session}.json,
    or None on error.
    """
    from datetime import datetime, timezone

    # Get the 4 core metrics (uses stale_ok for ballots, speech summaries)
    single = await compute_single_mp_metrics(client, slug, session, session_votes, politician)
    if single is None:
        return None

    # Fetch bills again (disk hit) to get the summaries list.
    # compute_single_mp_metrics() discards the summaries with `_` — we need them for bills_list.
    bills = await fetch_sponsored_bills(client, slug, session)
    _, bills_summaries = compute_bills_count(bills)

    # Fetch ballots again (disk hit, stale_ok) for votes_cast count.
    ballots = await fetch_politician_ballots(client, slug, session, stale_ok=True)

    # votes_cast = only Yes/No ballots, matching what compute_attendance() counts
    votes_cast = sum(1 for b in ballots if b.get("ballot") in ("Yes", "No"))

    metrics = {
        "attendance": single["attendance"],
        "party_loyalty": single["party_loyalty"],
        "bills_sponsored": single["bills_sponsored"],
        "bills_list": bills_summaries[:5],
        "debate_speeches": single["debate_speeches"],
        "total_votes": len(session_votes),
        "votes_cast": votes_cast,
    }

    percentiles = compute_percentiles_for_mp(slug, rankings_table)

    return {
        "slug": slug,
        "session": session,
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "metrics": metrics,
        "percentiles": percentiles,
    }


def cache_card_for_mp(slug: str, session: str, card_data: dict) -> None:
    """
    Write pre-computed card data to:
      cache/computed/politicians/{slug}/sessions/{session}.json

    Uses a 10-year TTL (effectively permanent). Warmup owns invalidation by
    deleting and rebuilding all session files on each run.

    The politician/{slug}/sessions/ directory structure is future-proof:
    - Add session 44-1 data by running warmup --session 44-1
    - List available sessions with glob("politicians/{slug}/sessions/*.json")
    - Add career.json as a sibling of sessions/ when career stats are built
    """
    entry = cache_entry(f"computed/politicians/{slug}/sessions/{session}.json")
    entry.write(card_data, ttl_seconds=settings.ttl_vote_detail)


def load_card_for_mp(slug: str, session: str) -> dict | None:
    """
    Load pre-computed card data from:
      cache/computed/politicians/{slug}/sessions/{session}.json

    Returns None if the file does not exist (triggers fallback to on-demand
    computation in /api/card/{slug}).
    """
    entry = cache_entry(f"computed/politicians/{slug}/sessions/{session}.json")
    return entry.read()


def compute_percentiles_for_mp(slug: str, table: dict) -> dict:
    """
    Given the full rankings table, compute percentile ranks for one MP.
    Returns a dict of {metric_name: percentile_int}.
    """
    metrics_list = table.get("metrics", [])

    attendance_vals = [m["attendance"] for m in metrics_list if m["attendance"] is not None]
    loyalty_vals = [m["party_loyalty"] for m in metrics_list if m["party_loyalty"] is not None]
    bills_vals = [m["bills_sponsored"] for m in metrics_list]
    speeches_vals = [m["debate_speeches"] for m in metrics_list]

    mp_metrics = next((m for m in metrics_list if m["slug"] == slug), None)
    if mp_metrics is None:
        return {
            "attendance": 50,
            "party_loyalty": 50,
            "bills_sponsored": 50,
            "debate_speeches": 50,
        }

    result: dict = {}

    result["attendance"] = get_percentile(mp_metrics["attendance"] or 0, attendance_vals)

    if mp_metrics["party_loyalty"] is not None:
        result["party_loyalty"] = get_percentile(mp_metrics["party_loyalty"], loyalty_vals)
    else:
        result["party_loyalty"] = None  # Independent MP

    result["bills_sponsored"] = get_percentile(mp_metrics["bills_sponsored"], bills_vals)
    result["debate_speeches"] = get_percentile(mp_metrics["debate_speeches"], speeches_vals)

    return result


def filter_table_by_group(
    slug: str,
    table: dict,
    group: str,
    mp_party: str,
    government_party: str,
) -> dict:
    """
    Return a copy of table with its metrics list filtered to the active comparison group.

    group: "all" | "party" | "government" | "opposition"

    Always ensures the MP themselves is included (for cross-group comparisons).
    Falls back to the full table if the filtered group has fewer than 5 MPs.
    """
    metrics_list = table.get("metrics", [])

    if group == "party":
        filtered = [m for m in metrics_list if m.get("party", "") == mp_party]
    elif group == "government":
        filtered = [m for m in metrics_list if m.get("party", "") == government_party]
    elif group == "opposition":
        filtered = [m for m in metrics_list if m.get("party", "") != government_party]
    else:  # "all" or unknown
        filtered = metrics_list

    # Always ensure the MP themselves is included so they can be ranked even in
    # cross-group comparisons (e.g. a Liberal MP vs. opposition).
    mp_own = next((m for m in metrics_list if m["slug"] == slug), None)
    if mp_own is not None and not any(m["slug"] == slug for m in filtered):
        filtered = filtered + [mp_own]

    # Fallback to full table if group is too small to be meaningful
    if len(filtered) < 5:
        filtered = metrics_list

    return {**table, "metrics": filtered}


def compute_percentiles_for_mp_by_group(
    slug: str,
    table: dict,
    group: str,
    mp_party: str,
    government_party: str,
) -> dict:
    """
    Compute percentile ranks for one MP relative to a filtered comparison group.
    Delegates filtering to filter_table_by_group().
    """
    filtered_table = filter_table_by_group(slug, table, group, mp_party, government_party)
    return compute_percentiles_for_mp(slug, filtered_table)


_INTEGER_METRICS = {"bills_sponsored", "debate_speeches"}


def compute_distributions_for_mp(
    slug: str,
    table: dict,
    num_buckets: int = 20,
) -> dict:
    """
    Compute spark-histogram bucket data for all 4 metrics.

    The table should already be filtered to the active comparison group
    (use filter_table_by_group() before calling this).

    For integer-valued metrics (bills_sponsored, debate_speeches), bins are
    discrete: one bin per possible integer value (0..max), so bin count =
    max_value + 1 and every bar has the same width. For continuous metrics
    (attendance, party_loyalty) the fixed num_buckets is used.

    Returns a dict of {metric: {"buckets": list[int], "mp_bucket": int} | None}.
    None is returned for a metric when the MP has no value (e.g. party_loyalty for
    independents) or the distribution is degenerate.
    """
    metrics_list = table.get("metrics", [])
    mp_metrics = next((m for m in metrics_list if m["slug"] == slug), None)

    result = {}
    for metric in ("attendance", "party_loyalty", "bills_sponsored", "debate_speeches"):
        all_vals = [m[metric] for m in metrics_list if m.get(metric) is not None]

        if not all_vals or mp_metrics is None or mp_metrics.get(metric) is None:
            result[metric] = None
            continue

        lo, hi = min(all_vals), max(all_vals)

        if metric in _INTEGER_METRICS and int(hi) < num_buckets:
            # Discrete binning: one bin per integer value 0..max (only when range fits)
            hi_int = int(hi)
            n = hi_int + 1  # bins for 0, 1, 2, ..., hi_int
            buckets = [0] * n
            for v in all_vals:
                buckets[int(v)] += 1
            mp_bucket = int(mp_metrics[metric])
        else:
            span = hi - lo or 1  # avoid division by zero when all values are identical
            n = num_buckets
            buckets = [0] * n
            for v in all_vals:
                idx = min(int((v - lo) / span * n), n - 1)
                buckets[idx] += 1
            mp_val = mp_metrics[metric]
            mp_bucket = min(int((mp_val - lo) / span * n), n - 1)

        result[metric] = {"buckets": buckets, "mp_bucket": mp_bucket, "lo": lo, "hi": hi}

    return result
