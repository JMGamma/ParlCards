from app.api.client import ThrottledAPIClient
from app.cache.manager import cache_entry, effective_ttl
from app.cache.session import is_likely_recess
from app.config import settings


async def fetch_sponsored_bills(
    client: ThrottledAPIClient, slug: str, session: str
) -> list[dict]:
    """Return all bills sponsored by a politician in a session. Cached per slug+session."""
    in_session = not is_likely_recess()
    entry = cache_entry(f"raw/bills/{slug}_{session}.json")

    cached = entry.read()
    if cached is not None:
        return cached

    bills = await client.paginate(
        "/bills/",
        params={
            "sponsor_politician": f"/politicians/{slug}/",
            "session": session,
        },
    )

    ttl = effective_ttl(settings.ttl_bills, in_session)
    entry.write(bills, ttl_seconds=ttl, source_url=f"/bills/?sponsor_politician={slug}&session={session}")
    return bills
