import asyncio

import pytest
from conftest import signed, stripe_event

from discordbot.bot import BotziBot
from discordbot.stripe_pay import Stripe, StripeError


@pytest.fixture
def bot(cfg, store, stripe):
    b = BotziBot(cfg, store, stripe, "https://arena.example.com")
    b.scheduled = []

    def schedule(coro):
        b.scheduled.append(coro.cr_frame.f_locals.get("activation"))
        coro.close()

    b._schedule = schedule
    return b


def test_bot_registers_all_commands(bot):
    names = {c.name for c in bot.tree.get_commands()}
    assert names == {"avertizeaza", "avertismente", "iarta", "curata", "tichet", "inchide", "panou_tichete", "nivel", "top", "trivia", "abonamente", "setari"}
    assert bot.intents.members and bot.intents.message_content


def test_stripe_event_activates_role_once(bot, store):
    event = stripe_event("cs_1", 42, "vip", 499)
    assert bot.handle_stripe_event(event) == "activated"
    assert store.paid_role(42, "vip") is not None
    assert len(bot.scheduled) == 1 and bot.scheduled[0].plan.code == "vip"
    assert bot.handle_stripe_event(event) == "duplicate"
    assert bot.handle_stripe_event(stripe_event("cs_2", 42, "vip", 499, paid=False)) == "unpaid"
    assert bot.handle_stripe_event(stripe_event("cs_3", 42, "vip", 499, guild_id="1")) == "other-guild"
    assert bot.handle_stripe_event(stripe_event("cs_4", 42, "vip", 499, client="altul")) == "other-client"
    assert bot.handle_stripe_event(stripe_event("cs_5", 42, "gold", 499)) == "bad-metadata"
    assert bot.handle_stripe_event({"type": "invoice.paid", "data": {"object": {}}}) == "ignored"


def test_checkout_metadata(bot, stripe):
    class User:
        id = 7

    url = bot.checkout_url(User(), bot.cfg.paid_roles.plan("vip-an"))
    assert url.startswith("https://checkout.stripe.test/")
    created = stripe.created[0]
    assert created["amount_cents"] == 3900 and created["currency"] == "EUR"
    assert created["metadata"] == {"discord_user_id": "7", "guild_id": "123456789012345678", "code": "vip-an", "client": "demo-comunitate"}
    assert created["success_url"] == "https://arena.example.com/success"


def test_webhook_endpoint(bot):
    from fastapi.testclient import TestClient

    from discordbot.web import create_app

    client = TestClient(create_app(bot))
    body, header = signed(stripe_event("cs_9", 200, "vip", 499))
    r = client.post("/stripe/webhook", content=body, headers={"stripe-signature": header})
    assert r.status_code == 200 and r.json()["result"] == "activated"
    assert client.post("/stripe/webhook", content=body, headers={"stripe-signature": "t=1,v1=bad"}).status_code == 400
    assert client.get("/health").json() == {"ok": True, "client": "demo-comunitate", "stripe": True}
    assert client.get("/success").status_code == 200


def test_webhook_signature():
    s = Stripe("sk", "whsec_test")
    body, header = signed({"type": "ping"})
    assert s.verify_webhook(body, header)["type"] == "ping"
    with pytest.raises(StripeError):
        s.verify_webhook(body + b" ", header)
    with pytest.raises(StripeError):
        s.verify_webhook(body, Stripe.sign(body, "whsec_test", timestamp=1_000_000))


def test_schedule_without_loop_does_not_crash(cfg, store):
    bot = BotziBot(cfg, store)

    async def noop():
        return None

    bot._schedule(noop())  # no running loop yet: logged, coroutine closed
    asyncio.set_event_loop(asyncio.new_event_loop())
