from pathlib import Path
from datetime import datetime, timezone
import json

from app.config import settings


CACHE_ROOT = Path(settings.cache_dir)


class CacheEntry:
    def __init__(self, path: Path):
        self.path = path

    def read(self) -> dict | list | None:
        """Return data if cache is valid (not expired), else None."""
        if not self.path.exists():
            return None
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        expires_at = datetime.fromisoformat(raw["expires_at"])
        if datetime.now(timezone.utc) > expires_at:
            return None
        return raw["data"]

    def read_stale(self) -> dict | list | None:
        """Return data even if expired — used as fallback."""
        if not self.path.exists():
            return None
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return raw.get("data")
        except (json.JSONDecodeError, OSError):
            return None

    def write(self, data: dict | list, ttl_seconds: int, source_url: str = "") -> None:
        now = datetime.now(timezone.utc)
        expires_ts = now.timestamp() + ttl_seconds
        expires_at = datetime.fromtimestamp(expires_ts, tz=timezone.utc)
        payload = {
            "cached_at": now.isoformat(),
            "ttl_seconds": ttl_seconds,
            "expires_at": expires_at.isoformat(),
            "source_url": source_url,
            "data": data,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def is_expired(self) -> bool:
        if not self.path.exists():
            return True
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            expires_at = datetime.fromisoformat(raw["expires_at"])
            return datetime.now(timezone.utc) > expires_at
        except Exception:
            return True

    def cached_at(self) -> str:
        """Return the ISO timestamp when this entry was last cached, or empty string."""
        if not self.path.exists():
            return ""
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return raw.get("cached_at", "")
        except Exception:
            return ""


def cache_entry(relative_path: str) -> CacheEntry:
    return CacheEntry(CACHE_ROOT / relative_path)


def effective_ttl(base_ttl: int, is_in_session: bool) -> int:
    """Apply recess multiplier when Parliament is not sitting."""
    if not is_in_session:
        return int(base_ttl * settings.recess_multiplier)
    return base_ttl
