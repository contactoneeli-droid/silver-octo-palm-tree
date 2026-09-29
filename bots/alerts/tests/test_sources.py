from conftest import AUTOVIT_URL, OLX_URL, STORIA_URL, autovit_page, olx_page, storia_page

from alerts.sources import default_name, detect_source, newest_first, parse


def test_detect_source():
    assert detect_source(OLX_URL) == "olx"
    assert detect_source("https://m.olx.ro/oferte/q-bicicleta/") == "olx"
    assert detect_source(STORIA_URL) == "storia"
    assert detect_source(AUTOVIT_URL) == "autovit"
    assert detect_source("https://www.imobiliare.ro/inchirieri-apartamente/cluj-napoca") is None
    assert detect_source("https://evil.com/?u=olx.ro") is None
    assert detect_source("not a url") is None


def test_newest_first_keeps_filters_and_forces_sort():
    url = newest_first(OLX_URL + "&search%5Border%5D=filter_float_price%3Aasc&page=3", "olx")
    assert "currency=EUR" in url and "filter_float_price%3Ato%5D=500" in url
    assert url.endswith("search%5Border%5D=created_at%3Adesc") and "page=" not in url and "asc" not in url
    assert newest_first(STORIA_URL, "storia").endswith("priceMax=500&by=LATEST&direction=DESC")
    assert "created_at_first%3Adesc" in newest_first(AUTOVIT_URL, "autovit")


def test_default_name():
    assert default_name(OLX_URL, "olx") == "OLX: apartamente garsoniere de inchiriat / cluj napoca"
    assert default_name(STORIA_URL, "storia") == "Storia: cluj / cluj napoca"
    assert default_name(AUTOVIT_URL, "autovit") == "Autovit: bmw / seria 3"
    assert default_name("https://www.olx.ro/oferte/q-bicicleta/", "olx") == "OLX: bicicleta"


def test_parse_olx_prerendered_state():
    listings = parse("olx", olx_page([{"id": 11, "title": "Garsonier&#259; Zorilor", "district": "Zorilor"}, {"id": 12, "title": "Ap. 2 camere", "price": "350 €"}]))
    assert [l.key for l in listings] == ["olx:11", "olx:12"]
    first = listings[0]
    assert first.title == "Garsonieră Zorilor"
    assert first.url == "https://www.olx.ro/d/oferta/11-ID11.html"
    assert first.price == "1 200 lei" and first.location == "Zorilor, Cluj-Napoca, Cluj"
    assert first.image == "https://ireland.apollo.olxcdn.com/v1/files/x/image;s=800x600"
    assert first.posted == "2026-09-29T10:00:00+03:00"
    assert listings[1].price == "350 €"
    assert "350 €" in listings[1].line() and listings[1].url in listings[1].line()


def test_parse_storia_next_data():
    listings = parse("storia", storia_page([{"id": 501, "title": "Apartament 2 camere, Plopilor", "price": 480}]))
    assert len(listings) == 1
    l = listings[0]
    assert l.key == "storia:501" and l.url == "https://www.storia.ro/ro/oferta/apartament-501-ID501"
    assert l.price == "480 €" and l.location == "Cluj-Napoca" and l.image == "https://img.storia.test/1l.jpg"


def test_parse_autovit_urql_state():
    listings = parse("autovit", autovit_page([{"id": "7GxQ", "title": "BMW Seria 3 320d", "price": 14500}]))
    assert len(listings) == 1
    l = listings[0]
    assert l.key == "autovit:7GxQ" and l.url.endswith("-ID7GxQ.html")
    assert l.price == "14.500 €" and l.location == "Cluj-Napoca, Cluj" and l.image == "https://img.autovit.test/x1.jpg"


def test_parse_html_fallback_and_dedupe():
    html = """
    <div><a href="/d/oferta/garsoniera-zorilor-IDabc12.html"><h6>Garsonier&#259; Zorilor</h6></a>
    <a href="https://www.olx.ro/d/oferta/garsoniera-zorilor-IDabc12.html">Garsonieră Zorilor</a>
    <a href="/d/oferta/apartament-3-camere-IDxyz9.html"><div><span>Apartament</span> <span>3 camere</span></div></a>
    <a href="/d/oferta/fara-titlu-IDq1.html"><img src="x.jpg"></a></div>
    """
    listings = parse("olx", html)
    assert [(l.key, l.title, l.url) for l in listings] == [
        ("olx:abc12", "Garsonieră Zorilor", "https://www.olx.ro/d/oferta/garsoniera-zorilor-IDabc12.html"),
        ("olx:xyz9", "Apartament 3 camere", "https://www.olx.ro/d/oferta/apartament-3-camere-IDxyz9.html"),
    ]
    assert parse("storia", "<html><body>nimic</body></html>") == []
