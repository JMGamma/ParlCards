from app.api.client import ThrottledAPIClient
from app.cache.manager import CacheEntry, cache_entry, effective_ttl
from app.cache.session import is_likely_recess
from app.config import settings


def _slug_from_url(url: str) -> str:
    """Extract slug from OpenParliament URL: '/politicians/pierre-poilievre/' -> 'pierre-poilievre'"""
    return url.strip("/").split("/")[-1]


def _party_slug(party_short: str) -> str:
    """Convert party short name to a CSS-safe slug."""
    mapping = {
        "Liberal": "liberal",
        "Lib.": "liberal",
        "Conservative": "conservative",
        "CPC": "conservative",
        "NDP": "ndp",
        "NDP-New Democratic Party": "ndp",
        "Bloc Québécois": "bloc",
        "BQ": "bloc",
        "Green Party": "green",
        "GP": "green",
    }
    for key, val in mapping.items():
        if key.lower() in party_short.lower():
            return val
    return "independent"


def _normalize_politician(raw: dict) -> dict:
    """Flatten a politician API record into a simpler dict."""
    party_en = ""
    party_slug_val = "independent"

    # List endpoint uses current_party; detail endpoint uses memberships[]
    party_data = raw.get("current_party") or {}
    if party_data:
        party_en = (party_data.get("short_name") or {}).get("en", "")
        party_slug_val = _party_slug(party_en)
    else:
        # Extract from most recent membership (detail endpoint)
        memberships = raw.get("memberships") or []
        if memberships:
            # Memberships are ordered newest-first; take the first one
            latest = memberships[0]
            party_info = latest.get("party") or {}
            party_en = (party_info.get("short_name") or {}).get("en", "")
            if not party_en:
                party_en = (party_info.get("name") or {}).get("en", "")
            party_slug_val = _party_slug(party_en)

    riding_data = raw.get("current_riding") or {}
    riding_name = (riding_data.get("name") or {}).get("en", "")
    province = riding_data.get("province", "")

    # Detail endpoint: extract riding from latest membership if not in current_riding
    if not riding_name:
        memberships = raw.get("memberships") or []
        if memberships:
            latest = memberships[0]
            riding_info = latest.get("riding") or {}
            riding_name = (riding_info.get("name") or {}).get("en", "")
            province = province or riding_info.get("province", "")

    slug = _slug_from_url(raw.get("url", ""))
    image = raw.get("image", "")
    if image and not image.startswith("http"):
        image = f"https://openparliament.ca{image}"

    return {
        "slug": slug,
        "name": raw.get("name", ""),
        "url": raw.get("url", ""),
        "party": party_en,
        "party_slug": party_slug_val,
        "riding": riding_name,
        "province": province,
        "image": image,
    }


async def fetch_politician_list(client: ThrottledAPIClient) -> list[dict]:
    """Return all current MPs as a list of normalized dicts. Cached."""
    in_session = not is_likely_recess()
    entry = cache_entry("raw/politicians/list.json")

    cached = entry.read()
    if cached is not None:
        return cached

    raw_list = await client.paginate("/politicians/", params={"current": "True"})
    normalized = [_normalize_politician(p) for p in raw_list]

    ttl = effective_ttl(settings.ttl_politician_list, in_session)
    entry.write(normalized, ttl_seconds=ttl, source_url="/politicians/?current=True")
    return normalized


async def fetch_politician_detail(client: ThrottledAPIClient, slug: str) -> dict | None:
    """Return a single politician's detail record. Cached per slug."""
    in_session = not is_likely_recess()
    entry = cache_entry(f"raw/politicians/{slug}.json")

    cached = entry.read()
    if cached is not None:
        return cached

    try:
        raw = await client.get(f"/politicians/{slug}/")
    except Exception:
        return None

    normalized = _normalize_politician(raw)
    # Also preserve extra fields that may be useful later
    normalized["memberships"] = raw.get("memberships", [])

    ttl = effective_ttl(settings.ttl_politician_detail, in_session)
    entry.write(normalized, ttl_seconds=ttl, source_url=f"/politicians/{slug}/")
    return normalized
