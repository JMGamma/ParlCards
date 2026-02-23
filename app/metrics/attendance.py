from datetime import date


def compute_attendance(
    ballots: list[dict],
    session_votes: list[dict],
    start_date: date | None = None,
) -> float:
    """
    Compute voting attendance percentage.

    If start_date is given, only count session votes that occurred on or after
    that date (for MPs who joined mid-session).
    """
    if not session_votes:
        return 0.0

    if start_date:
        eligible_votes = [
            v for v in session_votes
            if _parse_date(v.get("date", "")) >= start_date
        ]
    else:
        eligible_votes = session_votes

    total = len(eligible_votes)
    if total == 0:
        return 0.0

    # Only Yes/No ballots count as attendance — "Didn't vote" (present but abstained)
    # and "Paired" (procedural mutual abstention) are both non-attendance.
    votes_cast = sum(1 for b in ballots if b.get("ballot") in ("Yes", "No"))

    return round(votes_cast / total * 100, 1)


def _parse_date(date_str: str) -> date:
    try:
        return date.fromisoformat(date_str)
    except (ValueError, TypeError):
        return date.min
