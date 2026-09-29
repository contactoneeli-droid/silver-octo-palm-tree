"""Membership rules: pricing, activation, renewal, trial, reminders and removal."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from .config import ClientConfig, Coupon, Plan
from .store import Store, iso, parse, utc_now
from .telegram import TelegramApi, inline_keyboard

log = logging.getLogger(__name__)

RENEW_BUTTON = [{"text": "Reînnoiește abonamentul", "callback_data": "plans"}]


@dataclass
class Activation:
    user_id: int
    plan: Plan
    expires_at: datetime
    invite_link: str | None
    already_processed: bool = False


def fmt_date(dt: datetime, tz) -> str:
    return dt.astimezone(tz).strftime("%d.%m.%Y")


def when_text(days: int) -> str:
    if days <= 0:
        return "azi"
    if days == 1:
        return "mâine"
    return f"în {days} zile"


class Membership:
    def __init__(self, cfg: ClientConfig, store: Store, api: TelegramApi, tz):
        self.cfg = cfg
        self.store = store
        self.api = api
        self.tz = tz

    # Pricing -----------------------------------------------------------------------

    def price_cents(self, plan: Plan, coupon: Coupon | None) -> int:
        if not coupon:
            return plan.cents
        return max(0, int(round(plan.cents * (100 - coupon.percent) / 100)))

    def describe_plan(self, plan: Plan, coupon: Coupon | None = None) -> str:
        cents = self.price_cents(plan, coupon)
        price = f"{cents / 100:.2f}".rstrip("0").rstrip(".")
        extra = f" (cu codul {coupon.code}, -{coupon.percent}%)" if coupon else ""
        return f"{plan.name}: {price} {self.cfg.currency} / {plan.days} zile{extra}"

    # Access ------------------------------------------------------------------------

    def _invite(self, user_id: int) -> str | None:
        try:
            expire = int((utc_now() + timedelta(hours=24)).timestamp())
            return self.api.create_invite_link(self.cfg.group_chat_id, f"membru {user_id}", expire)
        except Exception as e:  # the payment is still recorded; the owner can resend the link
            log.error("Nu pot crea link de invitație pentru %s: %s", user_id, e)
            return None

    def activate(
        self,
        user_id: int,
        plan: Plan,
        *,
        provider: str,
        reference: str,
        amount_cents: int,
        coupon: str | None = None,
        days: int | None = None,
        now: datetime | None = None,
    ) -> Activation:
        """Grant ``days`` (default: the plan's) from now or from the current expiry, whichever is later."""
        if self.store.payment_by_reference(reference):
            member = self.store.member(user_id) or {}
            return Activation(user_id, plan, parse(member.get("expires_at")) or utc_now(), None, already_processed=True)

        now = now or utc_now()
        member = self.store.member(user_id) or self.store.upsert_member(user_id, None, None)
        current = parse(member.get("expires_at"))
        base = current if current and current > now else now
        expires = base + timedelta(days=days if days is not None else plan.days)

        self.store.add_payment(user_id, plan.code, amount_cents, self.cfg.currency, provider, reference, coupon)
        fields = dict(plan=plan.code, expires_at=iso(expires), status="active", coupon=None, awaiting=None, reminded_for=None)
        if provider == "trial":
            fields["trial_used"] = 1
        self.store.update_member(user_id, **fields)

        link = self._invite(user_id)
        return Activation(user_id, plan, expires, link)

    def trial_available(self, member: dict | None) -> bool:
        return self.cfg.trial_days > 0 and not (member and member.get("trial_used"))

    def start_trial(self, user_id: int, now: datetime | None = None) -> Activation:
        plan = Plan(code="trial", name="Perioadă de probă", days=self.cfg.trial_days, price=0)
        return self.activate(user_id, plan, provider="trial", reference=f"trial:{user_id}", amount_cents=0, days=self.cfg.trial_days, now=now)

    def extend(self, user_id: int, days: int, reference: str) -> Activation:
        member = self.store.member(user_id)
        plan = self.cfg.plan(member["plan"]) if member and member.get("plan") else None
        plan = plan or Plan(code="owner", name="Prelungire", days=days, price=0)
        return self.activate(user_id, plan, provider="owner", reference=reference, amount_cents=0, days=days)

    def remove(self, user_id: int, reason: str = "expirat") -> None:
        try:
            self.api.remove_from_group(self.cfg.group_chat_id, user_id)
        except Exception as e:
            log.error("Nu pot scoate %s din grup: %s", user_id, e)
        self.store.update_member(user_id, status="expired")
        log.info("Membrul %s scos din grup (%s)", user_id, reason)

    # Periodic pass -------------------------------------------------------------------

    def run_expiry_pass(self, now: datetime | None = None) -> dict[str, list[int]]:
        """Reminds members who expire soon and removes those past expiry + grace. Returns who got what."""
        now = now or utc_now()
        reminded: list[int] = []
        removed: list[int] = []
        for m in self.store.members(status="active"):
            expires = parse(m["expires_at"])
            if not expires:
                continue
            if expires + timedelta(days=self.cfg.grace_days) <= now:
                self.remove(m["user_id"])
                removed.append(m["user_id"])
                self._notify(m["user_id"], self.cfg.texts.expired)
                continue
            days_left = math.ceil((expires - now).total_seconds() / 86400)
            if expires > now and days_left <= self.cfg.remind_days_before and m.get("reminded_for") != m["expires_at"]:
                text = self.cfg.texts.expiring_soon.format(when=when_text(days_left), date=fmt_date(expires, self.tz))
                self._notify(m["user_id"], text)
                self.store.update_member(m["user_id"], reminded_for=m["expires_at"])
                reminded.append(m["user_id"])
        return {"reminded": reminded, "removed": removed}

    def _notify(self, user_id: int, text: str) -> None:
        try:
            self.api.send_message(user_id, text, inline_keyboard([RENEW_BUTTON]))
        except Exception as e:  # user may have blocked the bot
            log.warning("Nu pot scrie membrului %s: %s", user_id, e)

    # Stats ---------------------------------------------------------------------------

    def stats(self, now: datetime | None = None) -> str:
        now = now or utc_now()
        active = self.store.members(status="active")
        soon = [m for m in active if parse(m["expires_at"]) and parse(m["expires_at"]) - now <= timedelta(days=self.cfg.remind_days_before)]
        count30, cents30 = self.store.revenue_since(now - timedelta(days=30))
        return (
            f"📊 {self.cfg.business_name}\n"
            f"Membri activi: {len(active)}\n"
            f"Expiră în {self.cfg.remind_days_before} zile: {len(soon)}\n"
            f"Plăți ultimele 30 zile: {count30} ({cents30 / 100:.2f} {self.cfg.currency})"
        )
