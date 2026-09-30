import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from discordbot.config import load_client  # noqa: E402
from discordbot.store import Store  # noqa: E402
from discordbot.stripe_pay import Stripe  # noqa: E402


class FakeStripe(Stripe):
    def __init__(self):
        super().__init__("sk_test_x", "whsec_test")
        self.created = []

    def checkout_url(self, **kwargs):
        self.created.append(kwargs)
        return f"https://checkout.stripe.test/{len(self.created)}"


@pytest.fixture
def cfg():
    return load_client("demo-comunitate", ROOT / "clients")


@pytest.fixture
def store(tmp_path, cfg):
    s = Store(cfg.slug, tmp_path)
    yield s
    s.close()


@pytest.fixture
def stripe():
    return FakeStripe()


def stripe_event(session_id, user_id, code, amount, guild_id="123456789012345678", client="demo-comunitate", paid=True):
    return {
        "type": "checkout.session.completed",
        "data": {"object": {"id": session_id, "payment_status": "paid" if paid else "unpaid", "amount_total": amount,
                            "metadata": {"discord_user_id": str(user_id), "guild_id": guild_id, "code": code, "client": client}}},
    }


def signed(payload: dict, secret="whsec_test"):
    body = json.dumps(payload).encode()
    return body, Stripe.sign(body, secret)
