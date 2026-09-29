from datetime import timedelta

import pytest
from conftest import callback, message, signed, stripe_event

from members.store import parse, utc_now
from members.stripe_pay import Stripe, StripeError


def test_config(cfg):
    assert [p.code for p in cfg.plans] == ["lunar", "trimestrial", "anual"]
    assert cfg.plan("lunar").cents == 9900
    assert cfg.coupon("start20").percent == 20 and cfg.coupon("nope") is None
    assert cfg.trial_days == 3 and cfg.payment_provider == "stripe"


def test_start_shows_plans_trial_and_coupon_buttons(bot, api):
    bot.handle_update(message(100, "/start"))
    buttons = api.buttons_for(100)
    labels = [b["text"] for b in buttons]
    assert labels[0].startswith("Abonament lunar: 99 RON / 30 zile")
    assert any("gratuit 3 zile" in l for l in labels)
    assert any("cod de reducere" in l for l in labels)
    assert [b.get("callback_data") for b in buttons][:3] == ["plan:lunar", "plan:trimestrial", "plan:anual"]


def test_choose_plan_creates_stripe_checkout_with_metadata(bot, api, stripe):
    bot.handle_update(message(100, "/start"))
    bot.handle_update(callback(100, "plan:lunar"))
    assert len(stripe.created) == 1
    created = stripe.created[0]
    assert created["amount_cents"] == 9900 and created["currency"] == "RON"
    assert created["metadata"] == {"telegram_user_id": "100", "plan": "lunar", "coupon": "", "client": "demo-creator"}
    assert created["success_url"] == "https://members.example.com/success"
    pay_button = api.buttons_for(100)[0]
    assert pay_button["url"].startswith("https://checkout.stripe.test/")


def test_coupon_applies_to_checkout(bot, api, stripe):
    bot.handle_update(message(100, "/cod"))
    bot.handle_update(message(100, "start20"))
    assert "START20" in api.texts_for(100)[-2]
    assert api.buttons_for(100)[0]["text"].startswith("Abonament lunar: 79.2 RON")
    bot.handle_update(callback(100, "plan:lunar"))
    assert stripe.created[0]["amount_cents"] == 7920 and stripe.created[0]["metadata"]["coupon"] == "START20"

    bot.handle_update(message(101, "/cod"))
    bot.handle_update(message(101, "GRESIT"))
    assert "nu e valid" in api.texts_for(101)[-1]


def test_stripe_webhook_activates_once_and_sends_invite(bot, api, store, cfg):
    event = stripe_event("cs_1", 100, "lunar", 9900)
    assert bot.on_stripe_event(event) == "activated"
    member = store.member(100)
    assert member["status"] == "active" and member["plan"] == "lunar"
    days = (parse(member["expires_at"]) - utc_now()).total_seconds() / 86400
    assert 29.9 < days <= 30
    assert "https://t.me/+invite1" in api.texts_for(100)[-1]
    assert any("Plată Stripe" in t for t in api.texts_for(1))  # owner notified

    assert bot.on_stripe_event(event) == "duplicate"  # Stripe retries webhooks
    assert api.links == 1

    # A renewal extends from the current expiry, not from today.
    assert bot.on_stripe_event(stripe_event("cs_2", 100, "trimestrial", 24900)) == "activated"
    days = (parse(store.member(100)["expires_at"]) - utc_now()).total_seconds() / 86400
    assert 119.9 < days <= 120

    assert bot.on_stripe_event(stripe_event("cs_3", 100, "lunar", 9900, paid=False)) == "unpaid"
    assert bot.on_stripe_event(stripe_event("cs_4", 100, "lunar", 9900, client="alt-client")) == "other-client"
    assert bot.on_stripe_event({"type": "invoice.paid", "data": {"object": {}}}) == "ignored"


def test_webhook_signature_verification():
    s = Stripe("sk", "whsec_test")
    body, header = signed({"type": "ping"})
    assert s.verify_webhook(body, header)["type"] == "ping"
    with pytest.raises(StripeError):
        s.verify_webhook(body, header.replace("v1=", "v1=0"))
    with pytest.raises(StripeError):
        s.verify_webhook(body + b" ", header)
    body2, header2 = signed({"type": "ping"}, secret="other")
    with pytest.raises(StripeError):
        s.verify_webhook(body2, header2)
    old = Stripe.sign(body, "whsec_test", timestamp=1_000_000)
    with pytest.raises(StripeError):
        s.verify_webhook(body, old)


def test_webhook_endpoint(bot):
    from fastapi.testclient import TestClient

    from members.web import create_app

    client = TestClient(create_app(bot))
    body, header = signed(stripe_event("cs_9", 200, "lunar", 9900))
    r = client.post("/stripe/webhook", content=body, headers={"stripe-signature": header, "content-type": "application/json"})
    assert r.status_code == 200 and r.json()["result"] == "activated"
    r = client.post("/stripe/webhook", content=body, headers={"stripe-signature": "t=1,v1=bad"})
    assert r.status_code == 400
    assert client.get("/success").status_code == 200
    assert client.get("/health").json()["payment"] == "stripe"


def test_trial_once(bot, api, store):
    bot.handle_update(callback(100, "trial"))
    assert store.member(100)["trial_used"] == 1 and store.member(100)["status"] == "active"
    assert "invite1" in api.texts_for(100)[-1]
    bot.handle_update(callback(100, "trial"))
    assert "deja folosită" in api.texts_for(100)[-1]
    bot.handle_update(message(100, "/start"))
    assert not any("gratuit" in b["text"] for b in api.buttons_for(100))


def test_manual_payment_flow(cfg, bot, api, store):
    cfg.payment_provider = "manual"
    bot.handle_update(callback(100, "plan:lunar"))
    assert "transfer" in api.texts_for(100)[-1].lower()
    assert api.buttons_for(100)[0]["callback_data"] == "paid:lunar"
    bot.handle_update(callback(100, "paid:lunar"))
    assert "/aproba 100 lunar" in api.texts_for(1)[-1]

    assert "Activat" in bot.owner_command("/aproba 100 lunar")
    assert store.member(100)["status"] == "active"
    assert "invite" in api.texts_for(100)[-1]
    assert "Plan necunoscut" in bot.owner_command("/aproba 100 vip")


def test_expiry_pass_reminds_once_then_removes(membership, store, api, cfg):
    now = utc_now()
    membership.activate(100, cfg.plan("lunar"), provider="stripe", reference="a", amount_cents=9900, now=now - timedelta(days=28))  # expires in 2 days
    membership.activate(200, cfg.plan("lunar"), provider="stripe", reference="b", amount_cents=9900, now=now - timedelta(days=32))  # expired 2 days ago (> grace 1)
    membership.activate(300, cfg.plan("lunar"), provider="stripe", reference="c", amount_cents=9900, now=now - timedelta(days=10))  # fine

    result = membership.run_expiry_pass(now)
    assert result == {"reminded": [100], "removed": [200]}
    assert api.removed == [200]
    assert store.member(200)["status"] == "expired"
    assert "expiră în 2 zile" in api.texts_for(100)[-1]
    assert any("expirat" in t for t in api.texts_for(200))
    assert api.buttons_for(200)[0]["callback_data"] == "plans"

    assert membership.run_expiry_pass(now + timedelta(hours=1)) == {"reminded": [], "removed": []}  # no second reminder
    # Renewal after the reminder: a new reminder is due for the new expiry, later.
    membership.activate(100, cfg.plan("lunar"), provider="stripe", reference="d", amount_cents=9900, now=now)
    assert membership.run_expiry_pass(now) == {"reminded": [], "removed": []}
    assert membership.run_expiry_pass(now + timedelta(days=30))["reminded"] == [100]


def test_owner_commands_and_stats(bot, api, store, cfg):
    bot.on_stripe_event(stripe_event("cs_1", 100, "lunar", 9900))
    assert "Membri activi: 1" in bot.owner_command("/stats")
    assert "99.00 RON" in bot.owner_command("/stats")
    assert "100 @" in bot.owner_command("/membri") or "100 " in bot.owner_command("/membri")
    assert "Prelungit" in bot.owner_command("/prelungeste 100 10")
    days = (parse(store.member(100)["expires_at"]) - utc_now()).total_seconds() / 86400
    assert 39.9 < days <= 40
    assert "Link trimis" in bot.owner_command("/link 100")
    assert "Scos" in bot.owner_command("/scoate 100")
    assert api.removed == [100] and store.member(100)["status"] == "expired"
    assert bot.owner_command("/whatever") is None
    assert "Comenzi" in bot.owner_command("/help")

    # The owner typing /start still gets the member flow.
    bot.handle_update(message(1, "/start", username="owner"))
    assert api.buttons_for(1)


def test_invite_failure_is_reported_to_owner(cfg, store, membership, stripe):
    from conftest import FakeApi

    from members.bot import MembersBot

    api = FakeApi(fail_invites=True)
    membership.api = api
    bot = MembersBot(cfg, store, api, membership, stripe, "https://x")
    assert bot.on_stripe_event(stripe_event("cs_1", 100, "lunar", 9900)) == "activated"
    assert store.member(100)["status"] == "active"
    assert "administrator" in api.texts_for(100)[-1]
    assert any("/link 100" in t for t in api.texts_for(1))
