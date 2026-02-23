from datetime import datetime, timezone


# Parliamentary recess windows: (month_start, day_start, month_end, day_end)
# Wrapping windows (Dec→Jan) handled specially
RECESS_WINDOWS = [
    (6, 23, 9, 15),   # Summer recess
    (12, 17, 1, 26),  # Winter break (wraps year)
    (2, 14, 2, 21),   # Family Day week
    (3, 15, 3, 29),   # Spring break
]


def is_likely_recess(dt: datetime | None = None) -> bool:
    """Heuristic: is Parliament likely in recess based on calendar?"""
    if dt is None:
        dt = datetime.now(timezone.utc)
    m, d = dt.month, dt.day
    for start_m, start_d, end_m, end_d in RECESS_WINDOWS:
        if start_m <= end_m:
            if (m, d) >= (start_m, start_d) and (m, d) <= (end_m, end_d):
                return True
        else:
            # Wraps year boundary (e.g. Dec 17 – Jan 26)
            if (m, d) >= (start_m, start_d) or (m, d) <= (end_m, end_d):
                return True
    return False
