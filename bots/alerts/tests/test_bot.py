from datetime import timedelta

from conftest import AUTOVIT_URL, OLX_URL, STORIA_URL, FetchError, message, olx_page, storia_page

from alerts.store import iso, utc_now


def test_watcher_first_run_then_only_new(watcher, store, fetcher):
    store.upsert_user(100, "ana", "Ana", status="active")
    search = store.add_search(100, "Chirii Cluj", OLX_URL, "olx")

    listings, first = watcher.check_and_record(search)
    assert first and [l.key for l in listings] == ["olx:3", "olx:2", "olx:1"]
    assert fetcher.calls[0].endswith("created_at%3Adesc")
    assert store.seen_keys(search["id"]) == {"olx:3", "olx:2", "olx:1"}

    assert watcher.check_and_record(store.search(search["id"])) == ([], False)

    fetcher.html = olx_page([{"id": 4, "title": "Nou!"}, {"id": 3, "title": "Garsonieră Zorilor"}, {"id": 2, "title": "Apartament 2 camere Mărăști"}])
    listings, first = watcher.check_and_record(store.search(search["id"]))
    assert not first and [l.key for l in listings] == ["olx:4"]
    assert "olx:4" in store.seen_keys(search["id"])
    assert store.search(search["id"])["last_checked_at"] is not None


def test_run_pass_sends_new_listings_and_respects_poll_interval(watcher, store, api, fetcher, cfg):
    store.upsert_user(100, "ana", "Ana", status="active")
    store.upsert_user(200, "dan", "Dan", status="pending")
    store.add_search(100, "Chirii Cluj", OLX_URL, "olx")
    store.add_search(200, "Nu rulează", OLX_URL, "olx")

    now = utc_now()
    stats = watcher.run_pass(now)
    assert stats == {"checked": 1, "sent": 3, "errors": 0, "expired": 0}
    texts = api.texts_for(100)
    assert texts[0].startswith("Căutarea «Chirii Cluj» e activă")
    assert "Garsonieră Zorilor" in texts[1] and api.sent[1][2] is True  # link preview on
    assert api.texts_for(200) == []

    assert watcher.run_pass(now + timedelta(minutes=1))["checked"] == 0  # not due yet
    fetcher.html = olx_page([{"id": i, "title": f"Anunț {i}"} for i in range(20, 0, -1)])
    stats = watcher.run_pass(now + timedelta(minutes=cfg.poll_minutes))
    assert stats["checked"] == 1 and stats["sent"] == cfg.max_per_check
    summary = api.texts_for(100)[-1]
    assert summary.startswith(f"🆕 {cfg.max_per_check} anunțuri noi") and "1. Anunț 20" in summary and "Anunț 4" not in summary


def test_errors_back_off_and_alert_owner_once(watcher, store, api, fetcher, cfg):
    store.upsert_user(100, "ana", "Ana", status="active")
    search = store.add_search(100, "Chirii Cluj", OLX_URL, "olx")
    fetcher.html = FetchError("site-ul a refuzat cererea (HTTP 403)", 403)

    now = utc_now()
    assert watcher.run_pass(now)["errors"] == 1
    assert store.search(search["id"])["error_count"] == 1 and api.texts_for("1") == []
    assert watcher.run_pass(now + timedelta(minutes=cfg.poll_minutes))["checked"] == 0  # backoff: 2x interval after 1 failure
    assert watcher.run_pass(now + timedelta(minutes=cfg.poll_minutes * 2))["errors"] == 1
    assert watcher.run_pass(now + timedelta(minutes=cfg.poll_minutes * 5))["errors"] == 1
    owner = api.texts_for("1")
    assert len(owner) == 1 and "a eșuat de 3 ori" in owner[0] and "HTTP 403" in owner[0]
    assert watcher.run_pass(now + timedelta(minutes=cfg.poll_minutes * 9))["errors"] == 1
    assert len(api.texts_for("1")) == 1  # still one alert

    fetcher.html = olx_page([{"id": 1, "title": "Revenit"}])
    assert watcher.run_pass(now + timedelta(minutes=cfg.poll_minutes * 14))["checked"] == 1
    s = store.search(search["id"])
    assert s["error_count"] == 0 and s["error_notified"] == 0 and s["last_error"] is None


def test_open_access_flow(cfg, bot, api, store, fetcher):
    cfg.access = "open"
    bot.handle_update(message(100, "/start"))
    assert api.texts_for(100)[0].startswith("Bună!") and store.user(100)["status"] == "active"

    bot.handle_update(message(100, OLX_URL))
    searches = store.user_searches(100)
    assert len(searches) == 1 and searches[0]["name"] == "OLX: apartamente garsoniere de inchiriat / cluj napoca"
    texts = api.texts_for(100)
    assert any("e activă" in t for t in texts) and any("Garsonieră Zorilor" in t for t in texts)
    assert texts[-1].startswith("De acum te anunț")

    bot.handle_update(message(100, f"/adauga {STORIA_URL} Chirii Storia"))
    assert store.user_searches(100)[1]["name"] == "Chirii Storia"

    bot.handle_update(message(100, "/lista"))
    listing = api.texts_for(100)[-1]
    assert "1. OLX: apartamente" in listing and "2. Chirii Storia (Storia), activă" in listing

    bot.handle_update(message(100, "/verifica 1"))
    assert api.texts_for(100)[-1].startswith("Nimic nou")
    fetcher.html = olx_page([{"id": 9, "title": "Proaspăt"}, {"id": 3, "title": "Garsonieră Zorilor"}])
    bot.handle_update(message(100, "/verifica 1"))
    assert "Proaspăt" in api.texts_for(100)[-1]

    bot.handle_update(message(100, "/pauza 2"))
    assert store.user_searches(100)[1]["active"] == 0
    bot.handle_update(message(100, "/pornire 2"))
    assert store.user_searches(100)[1]["active"] == 1
    bot.handle_update(message(100, "/sterge 1"))
    assert [s["name"] for s in store.user_searches(100)] == ["Chirii Storia"]
    bot.handle_update(message(100, "/sterge 7"))
    assert "numărul căutării" in api.texts_for(100)[-1]
    bot.handle_update(message(100, "/status"))
    assert "fără limită de timp" in api.texts_for(100)[-1]


def test_limits_and_unsupported_sources(cfg, bot, api, store):
    cfg.access, cfg.max_searches_per_user, cfg.sources = "open", 2, ["olx", "storia"]
    bot.handle_update(message(100, "https://www.imobiliare.ro/inchirieri-apartamente/cluj-napoca"))
    assert "doar OLX, Storia" in api.texts_for(100)[-1]
    bot.handle_update(message(100, AUTOVIT_URL))
    assert "doar OLX, Storia" in api.texts_for(100)[-1]
    bot.handle_update(message(100, OLX_URL))
    bot.handle_update(message(100, OLX_URL))
    assert "Ai deja această căutare" in api.texts_for(100)[-1]
    bot.handle_update(message(100, STORIA_URL))
    bot.handle_update(message(100, "https://www.olx.ro/oferte/q-bicicleta/"))
    assert "cel mult 2 căutări" in api.texts_for(100)[-1]
    assert len(store.user_searches(100)) == 2
    bot.handle_update(message(100, "salut"))
    assert "Nu am înțeles" in api.texts_for(100)[-1]


def test_approved_access_trial_and_owner_commands(cfg, bot, api, store, watcher):
    cfg.trial_days = 0
    bot.handle_update(message(100, "/start"))
    assert store.user(100)["status"] == "pending"
    assert any("așteaptă activarea" in t for t in api.texts_for(100))
    assert "/aproba 100 30" in api.texts_for("1")[-1]

    bot.handle_update(message(100, OLX_URL))
    assert store.user_searches(100) == [] and "așteaptă activarea" in api.texts_for(100)[-1]

    bot.handle_update(message(1, "/aproba 100", username="owner"))
    assert "Activat: 100" in api.texts_for("1")[-1]
    assert store.user(100)["status"] == "active"
    assert any("Contul tău e activ" in t for t in api.texts_for(100))
    bot.handle_update(message(100, OLX_URL))
    assert len(store.user_searches(100)) == 1

    bot.handle_update(message(1, "/aproba 100 10", username="owner"))
    days = (utc_now() - utc_now()).days  # noqa: F841 (readability)
    from alerts.store import parse as parse_dt

    left = (parse_dt(store.user(100)["expires_at"]) - utc_now()).total_seconds() / 86400
    assert 39.9 < left <= 40

    bot.handle_update(message(1, "/utilizatori", username="owner"))
    assert "100 @ana: activ până pe" in api.texts_for("1")[-1] and "1 căutări" in api.texts_for("1")[-1]
    bot.handle_update(message(1, "/stats", username="owner"))
    assert "Utilizatori: 2 activi" in api.texts_for("1")[-1] and "Căutări active: 1" in api.texts_for("1")[-1]
    bot.handle_update(message(1, "/ruleaza", username="owner"))
    assert api.texts_for("1")[-1].startswith("Verificate")
    bot.handle_update(message(1, "/help", username="owner"))
    assert "Comenzi pentru proprietar" in api.texts_for("1")[-1]

    # Expiry stops the alerts and tells both sides.
    store.update_user(100, expires_at=iso(utc_now() - timedelta(minutes=1)))
    assert watcher.run_pass()["expired"] == 1
    assert store.user(100)["status"] == "expired"
    assert api.texts_for(100)[-1].startswith("Abonamentul tău a expirat")
    assert "a expirat" in api.texts_for("1")[-1]
    bot.handle_update(message(100, "/lista"))
    assert api.texts_for(100)[-1].startswith("Abonamentul tău a expirat")

    bot.handle_update(message(1, "/blocheaza 100", username="owner"))
    assert store.user(100)["status"] == "blocked"
    bot.handle_update(message(100, "/status"))
    assert api.texts_for(100)[-1] == "Contul tău e oprit."

    # Trial: a new user gets access immediately for trial_days.
    cfg.trial_days = 3
    bot.handle_update(message(300, "/start", username="ion"))
    u = store.user(300)
    assert u["status"] == "active" and u["expires_at"] is not None
    assert "probă 3 zile" in api.texts_for("1")[-1]


def test_add_search_when_site_unreachable(cfg, bot, api, store, fetcher):
    cfg.access = "open"
    fetcher.html = FetchError("conexiune eșuată (ConnectTimeout)")
    bot.handle_update(message(100, STORIA_URL))
    assert len(store.user_searches(100)) == 1
    assert "nu am putut citi pagina acum" in api.texts_for(100)[-1]
    assert store.user_searches(100)[0]["error_count"] == 1
    fetcher.html = storia_page([{"id": 1, "title": "Ap 2 camere"}])
    bot.handle_update(message(100, "/verifica 1"))
    assert "Ap 2 camere" in api.texts_for(100)[-1]
