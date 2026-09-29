"""Listing sources: detect the site from a URL, force newest-first sorting, parse a results page.

Parsing prefers the JSON the sites embed in their pages (Next.js ``__NEXT_DATA__`` on
Storia and Autovit, ``window.__PRERENDERED_STATE__`` on OLX) and falls back to the
listing anchors in the HTML. Field names are looked up loosely so small changes on the
sites do not break everything at once.
"""

from __future__ import annotations

import html as htmllib
import json
import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

SOURCES: dict[str, dict] = {
    "olx": {"hosts": ("olx.ro",), "name": "OLX", "base": "https://www.olx.ro", "sort": {"search[order]": "created_at:desc"}},
    "storia": {"hosts": ("storia.ro",), "name": "Storia", "base": "https://www.storia.ro", "sort": {"by": "LATEST", "direction": "DESC"}},
    "autovit": {"hosts": ("autovit.ro",), "name": "Autovit", "base": "https://www.autovit.ro", "sort": {"search[order]": "created_at_first:desc"}},
}


class ParseError(Exception):
    pass


@dataclass
class Listing:
    key: str
    title: str
    url: str
    price: str | None = None
    location: str | None = None
    image: str | None = None
    posted: str | None = None

    def line(self) -> str:
        bits = [b for b in (self.price, self.location) if b]
        return self.title + (f"\n{' · '.join(bits)}" if bits else "") + f"\n{self.url}"


def detect_source(url: str) -> str | None:
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return None
    for code, meta in SOURCES.items():
        if any(host == h or host.endswith("." + h) for h in meta["hosts"]):
            return code
    return None


def source_name(code: str) -> str:
    return SOURCES[code]["name"]


def newest_first(url: str, source: str) -> str:
    """The same search, sorted newest first and on page 1."""
    parts = urlparse(url)
    sort = SOURCES[source]["sort"]
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in sort and k != "page"]
    query += list(sort.items())
    return urlunparse(parts._replace(query=urlencode(query), fragment=""))


def default_name(url: str, source: str) -> str:
    skip = {"ro", "rezultate", "d", "oferte", "autoturisme"}
    segments = [s for s in urlparse(url).path.split("/") if s and s not in skip]
    words = [re.sub(r"^q-", "", s).replace("--", " ").replace("-", " ") for s in segments[-2:]]
    return f"{source_name(source)}: {' / '.join(words)}" if words else source_name(source)


# JSON extraction ---------------------------------------------------------------------

_NEXT_DATA = re.compile(r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
_PRERENDERED = re.compile(r'window\.__PRERENDERED_STATE__\s*=\s*"((?:[^"\\]|\\.)*)"', re.S)


def extract_json(html: str) -> list:
    blobs = []
    for m in _NEXT_DATA.finditer(html):
        try:
            blobs.append(json.loads(m.group(1)))
        except ValueError:
            pass
    for m in _PRERENDERED.finditer(html):
        try:
            blobs.append(json.loads(json.loads(f'"{m.group(1)}"')))
        except ValueError:
            pass
    return blobs


def walk(obj, depth: int = 0):
    """Every dict inside ``obj``; JSON kept as strings (Autovit's urqlState) is decoded too."""
    if depth > 60:
        return
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from walk(v, depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk(v, depth + 1)
    elif isinstance(obj, str) and len(obj) > 40 and obj[:1] in "{[":
        try:
            decoded = json.loads(obj)
        except ValueError:
            return
        yield from walk(decoded, depth + 1)


def _is_listing(d: dict) -> bool:
    """A dict with id, title and a link, plus at least one thing only a listing has (price, place or photo)."""
    title = d.get("title")
    if not (isinstance(title, str) and title.strip() and d.get("id") not in (None, "")):
        return False
    if not (isinstance(d.get("url"), str) or isinstance(d.get("slug"), str)):
        return False
    return bool(_price(d) or _location(d) or _image(d))


def _num(v) -> str:
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, (int, float)):
        v = int(v) if float(v).is_integer() else v
        return f"{v:,}".replace(",", ".")
    return str(v)


def _money(value, currency) -> str:
    cur = {"RON": "lei", "EUR": "€", "USD": "$"}.get(str(currency or "").upper(), currency or "")
    return f"{_num(value)} {cur}".strip()


def _price(d: dict) -> str | None:
    p = d.get("price") or d.get("totalPrice")
    if isinstance(p, dict):
        if p.get("displayValue"):
            return str(p["displayValue"])
        rp = p.get("regularPrice")
        if isinstance(rp, dict) and rp.get("value") is not None:
            return _money(rp["value"], rp.get("currencyCode") or rp.get("currency"))
        amt = p.get("amount")
        if isinstance(amt, dict) and amt.get("units") is not None:
            return _money(amt["units"], amt.get("currencyCode"))
        if p.get("value") is not None:
            return _money(p["value"], p.get("currency") or p.get("currencyCode"))
    elif isinstance(p, (int, float, str)) and p:
        return _num(p)
    for param in d.get("params") or []:
        if isinstance(param, dict) and param.get("key") == "price":
            v = param.get("value")
            if isinstance(v, dict):
                return v.get("label") or _money(v.get("value"), v.get("currency"))
    return None


def _location(d: dict) -> str | None:
    loc = d.get("location")
    if isinstance(loc, str):
        return loc
    if not isinstance(loc, dict):
        return None
    if isinstance(loc.get("address"), dict):
        loc = loc["address"]
    parts: list[str] = []
    for key in ("districtName", "district", "cityName", "city", "regionName", "region"):
        v = loc.get(key)
        if isinstance(v, dict):
            v = v.get("name")
        if isinstance(v, str) and v and v not in parts:
            parts.append(v)
    return ", ".join(parts) or None


def _image(d: dict) -> str | None:
    for key in ("photos", "images"):
        arr = d.get(key)
        if isinstance(arr, list) and arr:
            first = arr[0]
            url = first if isinstance(first, str) else (first.get("large") or first.get("medium") or first.get("link") or first.get("url")) if isinstance(first, dict) else None
            if url:
                return str(url).replace("{width}", "800").replace("{height}", "600")
    th = d.get("thumbnail")
    if isinstance(th, dict):
        return th.get("x1") or th.get("x2")
    return None


def _posted(d: dict) -> str | None:
    for key in ("createdTime", "created_time", "dateCreated", "createdAt", "lastRefreshTime"):
        if isinstance(d.get(key), str):
            return d[key]
    return None


def _url(d: dict, source: str) -> str:
    base = SOURCES[source]["base"]
    u = d.get("url")
    if isinstance(u, str) and u:
        return u if u.startswith("http") else base + ("" if u.startswith("/") else "/") + u
    slug = str(d["slug"]).lstrip("/")
    if source == "storia":
        return f"{base}/ro/oferta/{slug}"
    return f"{base}/{slug}"


# HTML fallback -----------------------------------------------------------------------

_ANCHORS = {
    "olx": re.compile(r'<a[^>]+href="((?:https?://(?:www\.)?olx\.ro)?/d/oferta/[^"?#]+)"[^>]*>(.*?)</a>', re.S),
    "storia": re.compile(r'<a[^>]+href="((?:https?://(?:www\.)?storia\.ro)?/ro/oferta/[^"?#]+)"[^>]*>(.*?)</a>', re.S),
    "autovit": re.compile(r'<a[^>]+href="((?:https?://(?:www\.)?autovit\.ro)?/autoturisme/anunt/[^"?#]+)"[^>]*>(.*?)</a>', re.S),
}
_TAGS = re.compile(r"<[^>]+>")
_ID = re.compile(r"-ID([A-Za-z0-9]+)(?:\.html)?/?$")


def parse_html(source: str, html: str) -> list[Listing]:
    out: dict[str, Listing] = {}
    base = SOURCES[source]["base"]
    for m in _ANCHORS[source].finditer(html):
        href, inner = m.group(1), m.group(2)
        title = " ".join(htmllib.unescape(_TAGS.sub(" ", inner)).split())
        if not title:
            continue
        url = href if href.startswith("http") else base + href
        idm = _ID.search(href)
        key = f"{source}:{idm.group(1) if idm else url}"
        out.setdefault(key, Listing(key, title, url))
    return list(out.values())


def parse(source: str, html: str) -> list[Listing]:
    """Listings on a results page, in page order (newest first when ``newest_first`` was used)."""
    found: dict[str, Listing] = {}
    for blob in extract_json(html):
        for d in walk(blob):
            if _is_listing(d):
                key = f"{source}:{d['id']}"
                if key not in found:
                    found[key] = Listing(key, htmllib.unescape(d["title"].strip()), _url(d, source), _price(d), _location(d), _image(d), _posted(d))
    if not found:
        for listing in parse_html(source, html):
            found.setdefault(listing.key, listing)
    return list(found.values())
