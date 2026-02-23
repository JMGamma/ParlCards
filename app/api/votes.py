import asyncio

from app.api.client import ThrottledAPIClient
from app.cache.manager import cache_entry, effective_ttl
from app.cache.session import is_likely_recess
from app.config import settings


async def fetch_session_votes(client: ThrottledAPIClient, session: str) -> list[dict]:
    """Return all votes in a session. Cached."""
    in_session = not is_likely_recess()
    entry = cache_entry(f"raw/votes/session_{session}_all.json")

    cached = entry.read()
    if cached is not None:
        return cached

    votes = await client.paginate("/votes/", params={"session": session})

    ttl = effective_ttl(settings.ttl_session_votes, in_session)
    entry.write(votes, ttl_seconds=ttl, source_url=f"/votes/?session={session}")
    return votes


async def fetch_politician_ballots(
    client: ThrottledAPIClient, slug: str, session: str, stale_ok: bool = False
) -> list[dict]:
    """
    Return all ballots cast by a politician for votes in the given session.
    Cached per slug+session.

    Note: The API's session parameter on /votes/ballots/ filters by the
    politician's membership session, not the vote's session. We fetch all
    ballots and filter client-side by vote URL prefix to get the correct data.

    stale_ok: if True, serve cached data even if expired (used by rankings build —
    ballots for already-cast votes are immutable, so stale data is correct).
    """
    in_session = not is_likely_recess()
    entry = cache_entry(f"raw/ballots/{slug}_{session}.json")

    cached = entry.read()
    if cached is not None:
        return cached

    # For rankings computation, accept stale ballot data (votes don't change retroactively)
    if stale_ok:
        stale = entry.read_stale()
        if stale is not None:
            return stale

    # Fetch all ballots for this politician (no session filter)
    all_ballots = await client.paginate(
        "/votes/ballots/",
        params={"politician": f"/politicians/{slug}/"},
    )

    # Filter to only ballots where the vote URL belongs to the target session
    # Vote URLs look like: /votes/45-1/69/
    session_prefix = f"/votes/{session}/"
    ballots = [
        b for b in all_ballots
        if (b.get("vote_url") or "").startswith(session_prefix)
    ]

    ttl = effective_ttl(settings.ttl_ballots, in_session)
    entry.write(ballots, ttl_seconds=ttl, source_url=f"/votes/ballots/?politician={slug} (filtered to {session})")
    return ballots


async def fetch_vote_detail(
    client: ThrottledAPIClient, session: str, vote_number: int | str
) -> dict | None:
    """Return a single vote's detail including party_votes breakdown. Cached permanently."""
    entry = cache_entry(f"raw/votes/detail_{session}_{vote_number}.json")

    cached = entry.read()
    if cached is not None:
        return cached

    try:
        detail = await client.get(f"/votes/{session}/{vote_number}/")
    except Exception:
        return None

    # Vote details are immutable — cache effectively forever
    entry.write(detail, ttl_seconds=settings.ttl_vote_detail, source_url=f"/votes/{session}/{vote_number}/")
    return detail


async def fetch_vote_details_batch(
    client: ThrottledAPIClient,
    session: str,
    vote_numbers: list[int | str],
    concurrency: int = 3,
) -> dict[str, dict]:
    """Fetch multiple vote details concurrently with a semaphore. Returns {vote_num: detail}."""
    semaphore = asyncio.Semaphore(concurrency)

    async def fetch_one(num: int | str) -> tuple[str, dict | None]:
        async with semaphore:
            detail = await fetch_vote_detail(client, session, num)
            return str(num), detail

    results = await asyncio.gather(*[fetch_one(n) for n in vote_numbers])
    return {k: v for k, v in results if v is not None}
