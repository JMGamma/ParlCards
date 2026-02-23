from app.api.client import ThrottledAPIClient
from app.api.votes import fetch_vote_details_batch
from app.config import settings


def _extract_vote_number(vote_url: str) -> str | None:
    """Extract vote number from URL like '/votes/45-1/69/' -> '69'"""
    parts = vote_url.strip("/").split("/")
    if len(parts) >= 3:
        return parts[-1]
    return None


def _extract_session(vote_url: str) -> str | None:
    """Extract session from URL like '/votes/45-1/69/' -> '45-1'"""
    parts = vote_url.strip("/").split("/")
    if len(parts) >= 3:
        return parts[-2]
    return None


async def compute_party_loyalty(
    client: ThrottledAPIClient,
    ballots: list[dict],
    politician_party: str,
    session: str,
) -> float | None:
    """
    Compute the percentage of votes where the politician voted with their party majority.

    Returns None if the politician is independent or has no applicable votes.
    """
    if not politician_party or politician_party.lower() in ("independent", "ind."):
        return None

    if not ballots:
        return 0.0

    # Collect vote numbers we need details for
    vote_numbers: list[str] = []
    ballot_map: dict[str, str] = {}  # vote_number -> politician's ballot ("Yes"/"No")

    for ballot in ballots:
        vote_url = ballot.get("vote_url") or (ballot.get("vote") or {}).get("url", "")
        if not vote_url:
            continue
        ballot_value = ballot.get("ballot", "")
        # Paired votes are a procedural agreement to mutually abstain — exclude entirely.
        # "Didn't vote" while present is a deliberate choice and counts against loyalty.
        if ballot_value == "Paired":
            continue
        vote_num = _extract_vote_number(vote_url)
        if vote_num:
            vote_numbers.append(vote_num)
            ballot_map[vote_num] = ballot_value

    if not vote_numbers:
        return 0.0

    # Fetch all needed vote details (uses cache; concurrent with semaphore)
    vote_details = await fetch_vote_details_batch(client, session, vote_numbers, concurrency=3)

    loyal = 0
    total = 0

    for vote_num, detail in vote_details.items():
        politician_ballot = ballot_map.get(vote_num, "")
        if not politician_ballot:
            continue

        party_votes = detail.get("party_votes", [])
        party_position = _find_party_position(party_votes, politician_party)

        if party_position is None:
            continue

        # Skip free votes (high disagreement within party)
        disagreement = _get_party_disagreement(party_votes, politician_party)
        if disagreement is not None and disagreement > settings.free_vote_threshold:
            continue

        total += 1
        if politician_ballot == party_position:
            loyal += 1

    if total == 0:
        return None  # No applicable votes (e.g. all were free votes)

    return round(loyal / total * 100, 1)


def _find_party_position(party_votes: list[dict], politician_party: str) -> str | None:
    """Find what position the politician's party took on this vote."""
    party_lower = politician_party.lower()
    for pv in party_votes:
        party_name = (pv.get("party") or {}).get("short_name", {})
        if isinstance(party_name, dict):
            name_en = party_name.get("en", "")
        else:
            name_en = str(party_name)
        if name_en.lower() in party_lower or party_lower in name_en.lower():
            return pv.get("vote")
    return None


def _get_party_disagreement(party_votes: list[dict], politician_party: str) -> float | None:
    """Return the disagreement float for the politician's party, if available."""
    party_lower = politician_party.lower()
    for pv in party_votes:
        party_name = (pv.get("party") or {}).get("short_name", {})
        if isinstance(party_name, dict):
            name_en = party_name.get("en", "")
        else:
            name_en = str(party_name)
        if name_en.lower() in party_lower or party_lower in name_en.lower():
            return pv.get("disagreement")
    return None
