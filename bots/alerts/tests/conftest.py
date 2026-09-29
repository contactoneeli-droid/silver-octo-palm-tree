import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from alerts.bot import AlertsBot  # noqa: E402
from alerts.config import load_client  # noqa: E402
from alerts.fetcher import FetchError  # noqa: E402
from alerts.store import Store  # noqa: E402
from alerts.watcher import Watcher  # noqa: E402

OLX_URL = "https://www.olx.ro/imobiliare/apartamente-garsoniere-de-inchiriat/cluj-napoca/?currency=EUR&search%5Bfilter_float_price%3Ato%5D=500"
STORIA_URL = "https://www.storia.ro/ro/rezultate/inchiriere/apartament/cluj/cluj--napoca?priceMax=500"
AUTOVIT_URL = "https://www.autovit.ro/autoturisme/bmw/seria-3?search%5Bfilter_float_price%3Ato%5D=15000"


# Page builders: the JSON shapes the sites embed, wrapped the way each site does it.

def olx_page(ads):
    items = [
        {
            "id": a["id"], "title": a["title"], "url": a.get("url", f"https://www.olx.ro/d/oferta/{a['id']}-ID{a['id']}.html"),
            "location": {"cityName": a.get("city", "Cluj-Napoca"), "regionName": "Cluj", "districtName": a.get("district")},
            "price": {"displayValue": a.get("price", "1 200 lei"), "regularPrice": {"value": 1200, "currencyCode": "RON"}},
            "photos": ["https://ireland.apollo.olxcdn.com/v1/files/x/image;s={width}x{height}"],
            "createdTime": "2026-09-29T10:00:00+03:00",
            "category": {"id": 1, "type": "goods"},
        }
        for a in ads
    ]
    state = {"listing": {"listing": {"ads": items, "totalElements": len(items)}}, "config": {"title": "OLX"}}
    return f"<html><head><title>OLX</title></head><body><div id='root'></div><script>window.__PRERENDERED_STATE__= {json.dumps(json.dumps(state))};</script></body></html>"


def storia_page(items):
    ads = [
        {
            "id": it["id"], "title": it["title"], "slug": it.get("slug", f"apartament-{it['id']}-ID{it['id']}"),
            "totalPrice": {"value": it.get("price", 450), "currency": "EUR"},
            "location": {"address": {"city": {"name": "Cluj-Napoca"}, "street": None}},
            "images": [{"medium": "https://img.storia.test/1.jpg", "large": "https://img.storia.test/1l.jpg"}],
            "dateCreated": "2026-09-29 09:00:00",
        }
        for it in items
    ]
    data = {"props": {"pageProps": {"data": {"searchAds": {"items": ads}}, "seo": {"title": "Storia", "slug": "x", "id": 1}}}, "page": "/ro/rezultate"}
    return f'<!doctype html><html><body><script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script></body></html>'


def autovit_page(nodes):
    edges = [
        {"node": {
            "id": n["id"], "title": n["title"], "url": n.get("url", f"https://www.autovit.ro/autoturisme/anunt/bmw-{n['id']}-ID{n['id']}.html"),
            "price": {"amount": {"units": n.get("price", 15900), "currencyCode": "EUR"}},
            "location": {"city": {"name": "Cluj-Napoca"}, "region": {"name": "Cluj"}},
            "thumbnail": {"x1": "https://img.autovit.test/x1.jpg"},
            "createdAt": "2026-09-29T08:00:00Z",
        }}
        for n in nodes
    ]
    result = {"advertSearch": {"totalCount": len(edges), "edges": edges}}
    data = {"props": {"pageProps": {"urqlState": {"abc": {"data": json.dumps(result)}}}}}
    return f'<html><body><script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script></body></html>'


class FakeFetcher:
    def __init__(self, html=""):
        self.html = html
        self.by_url = {}
        self.calls = []

    def get(self, url):
        self.calls.append(url)
        result = self.by_url.get(url, self.html)
        if isinstance(result, Exception):
            raise result
        return result


class FakeApi:
    def __init__(self):
        self.sent = []  # (chat_id, text, preview)

    def send_message(self, chat_id, text, preview=False):
        self.sent.append((str(chat_id), text, preview))

    def texts_for(self, chat_id):
        return [t for c, t, _ in self.sent if c == str(chat_id)]


@pytest.fixture
def cfg():
    c = load_client("demo-imobiliare", ROOT / "clients")
    c.owner_telegram_chat_id = "1"
    return c


@pytest.fixture
def store(tmp_path, cfg):
    s = Store(cfg.slug, tmp_path)
    yield s
    s.close()


@pytest.fixture
def api():
    return FakeApi()


@pytest.fixture
def fetcher():
    return FakeFetcher(olx_page([{"id": 3, "title": "Garsonieră Zorilor"}, {"id": 2, "title": "Apartament 2 camere Mărăști"}, {"id": 1, "title": "Studio Gheorgheni"}]))


@pytest.fixture
def watcher(cfg, store, fetcher, api):
    return Watcher(cfg, store, fetcher, api)


@pytest.fixture
def bot(cfg, store, api, watcher):
    return AlertsBot(cfg, store, api, watcher)


def message(user_id, text, username="ana"):
    return {"update_id": 1, "message": {"chat": {"id": user_id, "type": "private"}, "from": {"id": user_id, "username": username, "first_name": "Ana"}, "text": text}}
