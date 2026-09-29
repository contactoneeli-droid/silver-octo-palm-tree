"""Polite page fetching: browser-like headers, one request at a time per host, clear errors."""

from __future__ import annotations

import time
from urllib.parse import urlparse

import httpx

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ro-RO,ro;q=0.9,en;q=0.7",
}


class FetchError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class Fetcher:
    def __init__(self, client: httpx.Client | None = None, min_interval: float = 2.0):
        # httpx honours HTTP_PROXY / HTTPS_PROXY, so a residential proxy can be set from the environment.
        self.client = client or httpx.Client(timeout=30, follow_redirects=True, headers=DEFAULT_HEADERS)
        self.min_interval = min_interval
        self._last: dict[str, float] = {}

    def get(self, url: str) -> str:
        host = urlparse(url).hostname or ""
        wait = self._last.get(host, 0) + self.min_interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        try:
            r = self.client.get(url)
        except httpx.HTTPError as e:
            raise FetchError(f"conexiune eșuată ({e.__class__.__name__})") from e
        finally:
            self._last[host] = time.monotonic()
        if r.status_code in (403, 429):
            raise FetchError(f"site-ul a refuzat cererea (HTTP {r.status_code}); probabil blochează IP-ul serverului", r.status_code)
        if r.status_code >= 400:
            raise FetchError(f"HTTP {r.status_code}", r.status_code)
        return r.text
