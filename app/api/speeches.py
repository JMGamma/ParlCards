from app.api.client import ThrottledAPIClient
from app.cache.manager import cache_entry, effective_ttl
from app.cache.session import is_likely_recess
from app.config import settings


async def fetch_politician_speeches(
    client: ThrottledAPIClient, slug: str, session: str
) -> list[dict]:
    """Return all speeches by a politician in a session. Cached per slug+session.

    Also writes a lightweight summary file ({slug}_{session}_summary.json)
    with just the speech count, so build_rankings_table() can avoid parsing
    the full (potentially large) speech cache file.
    """
    in_session = not is_likely_recess()

    # Check summary cache first — we never store full speech text
    summary_entry = cache_entry(f"raw/speeches/{slug}_{session}_summary.json")
    cached_summary = summary_entry.read()
    if cached_summary is not None:
        return []  # Count is available via fetch_speech_count(); full text not stored

    speeches = await client.paginate(
        "/speeches/",
        params={"politician": f"/politicians/{slug}/", "session": session},
    )
    count = len(speeches)

    # Only persist the count — full speech text is never used and averages 5 MB per MP.
    # The summary file IS the cache; the full entry is never written.
    _ensure_summary(slug, session, count, in_session)

    return []  # Callers that need the count use fetch_speech_count()


async def fetch_speech_count(
    client: ThrottledAPIClient, slug: str, session: str
) -> int:
    """Return speech count using a lightweight summary cache (tiny JSON read).

    Falls back (in order) to:
      1. Stale full speech file on disk (instant read, no API call)
      2. Fresh full speech fetch from the API (slow, rate-limited)

    The stale-file fallback is critical for build_rankings_table(): speech files
    have a 4h TTL but the count barely changes session-to-session. Reading a
    stale 5 MB file is ~1ms vs waiting through rate limits for 207 MPs.
    """
    summary_entry = cache_entry(f"raw/speeches/{slug}_{session}_summary.json")

    cached = summary_entry.read()
    if cached is not None:
        return cached["speech_count"]

    # Summary missing or expired — try stale full cache before hitting the API
    in_session = not is_likely_recess()
    full_entry = cache_entry(f"raw/speeches/{slug}_{session}.json")
    stale = full_entry.read_stale()
    if stale is not None:
        count = len(stale)
        # Write a fresh summary so we skip this path next time
        _ensure_summary(slug, session, count, in_session)
        return count

    # No cache at all — do full fetch (which writes the summary as a side effect),
    # then read the summary back rather than using the return value (which is always [])
    await fetch_politician_speeches(client, slug, session)
    cached = summary_entry.read()
    return cached["speech_count"] if cached is not None else 0


def _ensure_summary(slug: str, session: str, count: int, in_session: bool) -> None:
    """Write the summary file if it is missing or expired."""
    summary_entry = cache_entry(f"raw/speeches/{slug}_{session}_summary.json")
    if summary_entry.is_expired():
        ttl = effective_ttl(settings.ttl_speeches, in_session)
        summary_entry.write({"speech_count": count}, ttl_seconds=ttl)
