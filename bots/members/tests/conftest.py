import json
import sys
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from members.bot import MembersBot  # noqa: E402
from members.config import load_client  # noqa: E402
from members.membership import Membership  # noqa: E402
from members.store import Store  # noqa: E402
from members.stripe_pay import Stripe  # noqa: E402

TZ = ZoneInfo("Europe/Bucharest")


class FakeApi:
    """Records Telegram calls; invite links are numbered."""

    def __init__(self, fail_invites=False):
        self.sent = []  # (chat_id, text, reply_markup)
        self.removed = []
        self.links = 0
        self.fail_invites = fail_invites

    def send_message(self, chat_id, text, reply_markup=None):
        self.sent.append((str(chat_id), text, reply_markup))
        return {"message_id": len(self.sent)}

    def answer_callback(self, callback_query_id, text=None):
        pass

    def create_invite_link(self, chat_id, name, expire_at):
        if self.fail_invites:
            raise RuntimeError("Not enough rights")
        self.links += 1
        return f"https://t.me/+invite{self.links}"

    def remove_from_group(self, chat_id, user_id):
        self.removed.append(user_id)

    def texts_for(self, chat_id):
        return [t for c, t, _ in self.sent if c == str(chat_id)]

    def buttons_for(self, chat_id):
        last = [m for c, _, m in self.sent if c == str(chat_id) and m]
        return [b for row in last[-1]["inline_keyboard"] for b in row] if last else []


class FakeStripe(Stripe):
    def __init__(self):
        super().__init__("sk_test_x", "whsec_test")
        self.created = []

    def checkout_url(self, **kwargs):
        self.created.append(kwargs)
        return f"https://checkout.stripe.test/{len(self.created)}"


@pytest.fixture
def cfg():
    c = load_client("demo-creator", ROOT / "clients")
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
def membership(cfg, store, api):
    return Membership(cfg, store, api, TZ)


@pytest.fixture
def stripe():
    return FakeStripe()


@pytest.fixture
def bot(cfg, store, api, membership, stripe):
    return MembersBot(cfg, store, api, membership, stripe, "https://members.example.com")


def message(user_id, text, username="ana"):
    return {"update_id": 1, "message": {"chat": {"id": user_id, "type": "private"}, "from": {"id": user_id, "username": username, "first_name": "Ana"}, "text": text}}


def callback(user_id, data, username="ana"):
    return {"update_id": 2, "callback_query": {"id": "cb1", "from": {"id": user_id, "username": username, "first_name": "Ana"}, "data": data}}


def stripe_event(session_id, user_id, plan, amount, coupon="", client="demo-creator", paid=True):
    return {
        "type": "checkout.session.completed",
        "data": {"object": {"id": session_id, "payment_status": "paid" if paid else "unpaid", "amount_total": amount,
                            "metadata": {"telegram_user_id": str(user_id), "plan": plan, "coupon": coupon, "client": client}}},
    }


def signed(payload: dict, secret="whsec_test"):
    body = json.dumps(payload).encode()
    return body, Stripe.sign(body, secret)
