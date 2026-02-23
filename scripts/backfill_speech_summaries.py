"""
One-time backfill: generate speech summary files from existing full speech caches.

Each full speech file averages 5.3 MB. This script reads each one and writes a
50-byte summary file with just the count, so build_rankings_table() can skip
parsing the full files entirely.

Usage:
    uv run python scripts/backfill_speech_summaries.py
"""
import json
import sys
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.cache.manager import CACHE_ROOT, cache_entry
from app.cache.session import is_likely_recess
from app.cache.manager import effective_ttl
from app.config import settings

SESSION = settings.session
SPEECHES_DIR = CACHE_ROOT / "raw" / "speeches"


def main():
    in_session = not is_likely_recess()
    ttl = effective_ttl(settings.ttl_speeches, in_session)

    full_files = sorted(SPEECHES_DIR.glob(f"*_{SESSION}.json"))
    total = len(full_files)
    print(f"Found {total} full speech cache files for session {SESSION}")

    generated = 0
    skipped = 0
    failed = 0
    t0 = time.monotonic()

    for i, full_path in enumerate(full_files):
        slug = full_path.stem.replace(f"_{SESSION}", "")
        summary_entry = cache_entry(f"raw/speeches/{slug}_{SESSION}_summary.json")

        # Skip if summary already fresh
        if not summary_entry.is_expired():
            skipped += 1
            continue

        # Read full file even if expired (stale count is fine for rankings)
        full_entry = cache_entry(f"raw/speeches/{slug}_{SESSION}.json")
        if not full_entry.path.exists():
            skipped += 1
            continue

        try:
            raw = json.loads(full_path.read_text(encoding="utf-8"))
            count = len(raw.get("data", []))
            summary_entry.write({"speech_count": count}, ttl_seconds=ttl)
            generated += 1

            elapsed = time.monotonic() - t0
            rate = generated / elapsed if elapsed > 0 else 0
            eta = (total - i - 1) / rate if rate > 0 else 0
            print(
                f"  [{i+1}/{total}] {slug}: {count} speeches  "
                f"({rate:.1f}/s, ETA {eta:.0f}s)",
                end="\r",
            )
        except Exception as e:
            print(f"\n  [{i+1}/{total}] FAILED {slug}: {e}")
            failed += 1

    elapsed = time.monotonic() - t0
    print(f"\nDone in {elapsed:.1f}s")
    print(f"  Generated: {generated}")
    print(f"  Skipped (already fresh): {skipped}")
    print(f"  Failed: {failed}")
    total_summaries = len(list(SPEECHES_DIR.glob(f"*_{SESSION}_summary.json")))
    print(f"  Total summary files now: {total_summaries} / {total}")


if __name__ == "__main__":
    main()
