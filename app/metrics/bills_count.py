def compute_bills_count(bills: list[dict]) -> tuple[int, list[dict]]:
    """
    Return (count, bill_summaries) for display on the card.

    bill_summaries is a list of dicts with keys: number, name, introduced.
    """
    summaries = []
    for bill in bills:
        number = bill.get("number", "")
        name_data = bill.get("name") or {}
        name = name_data.get("en", "") if isinstance(name_data, dict) else str(name_data)
        introduced = bill.get("introduced", "")
        summaries.append({"number": number, "name": name, "introduced": introduced})

    # Sort by introduced date descending
    summaries.sort(key=lambda b: b["introduced"] or "", reverse=True)

    return len(bills), summaries
