import asyncio
import time
from collections import deque
from urllib.parse import parse_qs, urlparse

import httpx

from app.config import settings


class ThrottledAPIClient:
    BASE_URL = "https://api.openparliament.ca"

    def __init__(self):
        self._request_timestamps: deque[float] = deque()
        self._last_request_time: float = 0.0
        self._client: httpx.AsyncClient | None = None

    def _make_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.BASE_URL,
            headers={
                "Accept": "application/json",
                "User-Agent": f"ParlCards/1.0 (research; {settings.contact_email})",
            },
            timeout=30.0,
        )

    async def start(self) -> None:
        self._client = self._make_client()

    async def stop(self) -> None:
        if self._client:
            await self._client.aclose()

    async def _throttle(self) -> None:
        """Enforce rate limit: max N requests per minute with minimum delay between each."""
        now = time.monotonic()

        # Enforce minimum inter-request delay
        since_last = now - self._last_request_time
        if since_last < settings.min_delay_seconds:
            await asyncio.sleep(settings.min_delay_seconds - since_last)
            now = time.monotonic()

        # Sliding window: evict old timestamps
        while self._request_timestamps and now - self._request_timestamps[0] > 60:
            self._request_timestamps.popleft()

        # Block if at rate limit
        if len(self._request_timestamps) >= settings.rate_limit_per_minute:
            wait_until = self._request_timestamps[0] + 60
            sleep_for = wait_until - now
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)
            now = time.monotonic()
            while self._request_timestamps and now - self._request_timestamps[0] > 60:
                self._request_timestamps.popleft()

        self._request_timestamps.append(time.monotonic())
        self._last_request_time = time.monotonic()

    async def get(self, path: str, params: dict | None = None) -> dict:
        """Fetch a single API endpoint with retry/backoff."""
        assert self._client is not None, "Client not started; call start() first"
        max_retries = 4
        for attempt in range(max_retries):
            await self._throttle()
            try:
                response = await self._client.get(path, params=params)

                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", 60))
                    await asyncio.sleep(retry_after)
                    continue

                if response.status_code in (502, 503, 504):
                    backoff = 5 * (2 ** attempt)
                    await asyncio.sleep(backoff)
                    continue

                response.raise_for_status()
                return response.json()

            except httpx.TimeoutException:
                if attempt == max_retries - 1:
                    raise
                await asyncio.sleep(5 * (attempt + 1))

        raise RuntimeError(f"Failed after {max_retries} attempts: {path}")

    async def paginate(self, path: str, params: dict | None = None) -> list:
        """Fetch all pages of a paginated endpoint and return merged objects list."""
        base_params: dict = {**(params or {}), "limit": 100}
        results: list = []
        current_params = {**base_params, "offset": 0}

        while True:
            page = await self.get(path, params=current_params)
            objects = page.get("objects", [])
            results.extend(objects)

            next_url = page.get("pagination", {}).get("next_url") or page.get("next")
            if not next_url or not objects:
                break

            parsed = urlparse(next_url)
            qs = parse_qs(parsed.query)
            new_offset = qs.get("offset", [None])[0]
            if new_offset is None:
                break
            current_params = {**base_params, "offset": int(new_offset)}

        return results
