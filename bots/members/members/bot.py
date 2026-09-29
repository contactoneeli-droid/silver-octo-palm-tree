"""Telegram handlers: the member's conversation (plans, payment, trial, coupon) and owner commands."""

from __future__ import annotations

import logging
import time

from .config import ClientConfig
from .membership import Membership, fmt_date
from .store import Store, parse, utc_now
from .stripe_pay import Stripe, StripeError
from .telegram import TelegramApi, inline_keyboard

log = logging.getLogger(__name__)

OWNER_HELP = (
    "Comenzi pentru proprietar:\n"
    "/stats – membri activi, expirări, încasări\n"
    "/membri – lista membrilor activi\n"
    "/aproba ID PLAN – activează manual (după transfer bancar)\n"
    "/prelungeste ID ZILE – prelungește un membru\n"
    "/scoate ID – scoate un membru din grup\n"
    "/link ID – trimite din nou linkul de acces"
)


class MembersBot:
    def __init__(self, cfg: ClientConfig, store: Store, api: TelegramApi, membership: Membership, stripe: Stripe | None, public_url: str):
        self.cfg = cfg
        self.store = store
        self.api = api
        self.membership = membership
        self.stripe = stripe
        self.public_url = public_url.rstrip("/")

    # Dispatch ------------------------------------------------------------------------

    def handle_update(self, update: dict) -> None:
        if "callback_query" in update:
            self.handle_callback(update["callback_query"])
            return
        message = update.get("message")
        if not message or "text" not in message or message["chat"]["type"] != "private":
            return
        user = message["from"]
        text = message["text"].strip()
        member = self.store.upsert_member(user["id"], user.get("username"), " ".join(p for p in (user.get("first_name"), user.get("last_name")) if p))

        if self.is_owner(user["id"]) and text.startswith("/"):
            answer = self.owner_command(text)
            if answer is not None:
                self.api.send_message(user["id"], answer)
                return

        if text.startswith("/start") or text.startswith("/planuri") or text.startswith("/renew"):
            self.send_plans(user["id"], member)
        elif text.startswith("/status"):
            self.api.send_message(user["id"], self.status_text(member))
        elif text.startswith("/cod"):
            self.ask_coupon(user["id"])
        elif member.get("awaiting") == "coupon":
            self.apply_coupon(user["id"], text)
        else:
            self.api.send_message(user["id"], "Folosește /planuri ca să alegi un abonament, /status ca să vezi până când ai acces sau /cod pentru un cod de reducere.")

    def handle_callback(self, query: dict) -> None:
        user = query["from"]
        data = query.get("data", "")
        member = self.store.upsert_member(user["id"], user.get("username"), user.get("first_name"))
        self.api.answer_callback(query["id"])
        if data == "plans":
            self.send_plans(user["id"], member)
        elif data.startswith("plan:"):
            self.choose_plan(user["id"], member, data[5:])
        elif data == "trial":
            self.start_trial(user["id"], member)
        elif data == "coupon":
            self.ask_coupon(user["id"])
        elif data.startswith("paid:"):
            self.manual_paid(user, member, data[5:])

    # Member flow ---------------------------------------------------------------------

    def is_owner(self, user_id: int) -> bool:
        return self.cfg.owner_telegram_chat_id is not None and str(user_id) == self.cfg.owner_telegram_chat_id

    def send_plans(self, user_id: int, member: dict) -> None:
        coupon = self.cfg.coupon(member["coupon"]) if member.get("coupon") else None
        rows = [[{"text": self.membership.describe_plan(p, coupon), "callback_data": f"plan:{p.code}"}] for p in self.cfg.plans]
        if self.membership.trial_available(member):
            rows.append([{"text": f"Încearcă gratuit {self.cfg.trial_days} zile", "callback_data": "trial"}])
        if self.cfg.coupons and not coupon:
            rows.append([{"text": "Am un cod de reducere", "callback_data": "coupon"}])
        self.api.send_message(user_id, self.cfg.texts.welcome, inline_keyboard(rows))

    def choose_plan(self, user_id: int, member: dict, code: str) -> None:
        plan = self.cfg.plan(code)
        if not plan:
            self.api.send_message(user_id, "Planul nu mai există. Alege altul cu /planuri.")
            return
        coupon = self.cfg.coupon(member["coupon"]) if member.get("coupon") else None
        cents = self.membership.price_cents(plan, coupon)

        if self.cfg.payment_provider == "stripe" and self.stripe is not None:
            try:
                url = self.stripe.checkout_url(
                    amount_cents=cents,
                    currency=self.cfg.currency,
                    product_name=f"{self.cfg.business_name} · {plan.name}",
                    success_url=f"{self.public_url}/success",
                    cancel_url=f"{self.public_url}/cancel",
                    metadata={"telegram_user_id": str(user_id), "plan": plan.code, "coupon": coupon.code if coupon else "", "client": self.cfg.slug},
                )
            except StripeError as e:
                log.error("Stripe: %s", e)
                self.api.send_message(user_id, "Plata nu e disponibilă momentan. Încearcă din nou în câteva minute.")
                return
            self.api.send_message(
                user_id,
                f"{self.membership.describe_plan(plan, coupon)}\nApasă butonul ca să plătești cu cardul. Accesul se activează automat după plată.",
                inline_keyboard([[{"text": "Plătește cu cardul", "url": url}]]),
            )
        else:
            self.api.send_message(
                user_id,
                f"{self.membership.describe_plan(plan, coupon)}\n\n{self.cfg.texts.manual_instructions}",
                inline_keyboard([[{"text": "Am plătit", "callback_data": f"paid:{plan.code}"}]]),
            )

    def manual_paid(self, user: dict, member: dict, code: str) -> None:
        plan = self.cfg.plan(code)
        if not plan:
            return
        self.api.send_message(user["id"], "Mulțumim! Verificăm plata și îți activăm accesul cât de curând.")
        if self.cfg.owner_telegram_chat_id:
            who = f"@{user['username']}" if user.get("username") else user.get("first_name", "")
            self.api.send_message(
                self.cfg.owner_telegram_chat_id,
                f"💳 {who} (id {user['id']}) spune că a plătit {plan.name}.\nDupă ce verifici: /aproba {user['id']} {plan.code}",
            )

    def start_trial(self, user_id: int, member: dict) -> None:
        if not self.membership.trial_available(member):
            self.api.send_message(user_id, "Perioada de probă a fost deja folosită. Alege un abonament cu /planuri.")
            return
        activation = self.membership.start_trial(user_id)
        self.send_access(user_id, self.cfg.texts.trial_started, activation.invite_link, activation.expires_at)

    def ask_coupon(self, user_id: int) -> None:
        self.store.update_member(user_id, awaiting="coupon")
        self.api.send_message(user_id, "Scrie codul de reducere:")

    def apply_coupon(self, user_id: int, text: str) -> None:
        coupon = self.cfg.coupon(text)
        if not coupon:
            self.store.update_member(user_id, awaiting=None)
            self.api.send_message(user_id, "Codul nu e valid. Poți alege un abonament cu /planuri sau încerca alt cod cu /cod.")
            return
        self.store.update_member(user_id, coupon=coupon.code, awaiting=None)
        self.api.send_message(user_id, f"Codul {coupon.code} e activ: -{coupon.percent}% la prima plată.")
        self.send_plans(user_id, self.store.member(user_id))

    def send_access(self, user_id: int, intro: str, link: str | None, expires_at) -> None:
        until = f"Acces valabil până pe {fmt_date(expires_at, self.membership.tz)}."
        if link:
            self.api.send_message(user_id, f"{intro}\n{link}\n\n{until}")
        else:
            self.api.send_message(user_id, f"{until}\nLinkul de acces vine imediat de la administrator.")
            if self.cfg.owner_telegram_chat_id:
                self.api.send_message(self.cfg.owner_telegram_chat_id, f"⚠️ Nu am putut crea linkul de invitație pentru id {user_id}. Trimite-l cu /link {user_id} după ce verifici că botul e admin în grup.")

    def status_text(self, member: dict) -> str:
        expires = parse(member.get("expires_at"))
        if member.get("status") == "active" and expires and expires > utc_now():
            plan = self.cfg.plan(member.get("plan") or "")
            return f"Ai acces până pe {fmt_date(expires, self.membership.tz)}" + (f" ({plan.name})." if plan else ".")
        return "Nu ai un abonament activ. Alege unul cu /planuri."

    # Stripe webhook ------------------------------------------------------------------

    def on_stripe_event(self, event: dict) -> str:
        if event.get("type") != "checkout.session.completed":
            return "ignored"
        session = event["data"]["object"]
        if session.get("payment_status") != "paid":
            return "unpaid"
        meta = session.get("metadata") or {}
        if meta.get("client") and meta["client"] != self.cfg.slug:
            return "other-client"
        user_id, plan = int(meta.get("telegram_user_id", 0)), self.cfg.plan(meta.get("plan", ""))
        if not user_id or not plan:
            log.error("Webhook Stripe fără user/plan: %s", meta)
            return "bad-metadata"
        activation = self.membership.activate(
            user_id, plan, provider="stripe", reference=session["id"], amount_cents=int(session.get("amount_total") or 0), coupon=meta.get("coupon") or None
        )
        if activation.already_processed:
            return "duplicate"
        self.send_access(user_id, self.cfg.texts.after_payment, activation.invite_link, activation.expires_at)
        if self.cfg.owner_telegram_chat_id:
            self.api.send_message(
                self.cfg.owner_telegram_chat_id,
                f"✅ Plată Stripe: id {user_id}, {plan.name}, {int(session.get('amount_total') or 0) / 100:.2f} {self.cfg.currency}. Acces până pe {fmt_date(activation.expires_at, self.membership.tz)}.",
            )
        return "activated"

    # Owner commands ------------------------------------------------------------------

    def owner_command(self, text: str) -> str | None:
        parts = text.split()
        cmd, args = parts[0].lower().lstrip("/"), parts[1:]
        if cmd in ("help", "ajutor"):
            return OWNER_HELP
        if cmd == "stats":
            return self.membership.stats()
        if cmd == "membri":
            active = self.store.members(status="active")
            if not active:
                return "Niciun membru activ."
            lines = [f"{m['user_id']} {('@' + m['username']) if m.get('username') else (m.get('name') or '')} · {m.get('plan')} · până pe {fmt_date(parse(m['expires_at']), self.membership.tz)}" for m in active]
            return "Membri activi:\n" + "\n".join(lines[:100])
        if cmd == "aproba":
            if len(args) < 2 or not args[0].isdigit():
                return "Folosește: /aproba ID PLAN"
            plan = self.cfg.plan(args[1])
            if not plan:
                return f"Plan necunoscut. Planuri: {', '.join(p.code for p in self.cfg.plans)}"
            user_id = int(args[0])
            member = self.store.member(user_id) or {}
            coupon = self.cfg.coupon(member["coupon"]) if member.get("coupon") else None
            reference = f"manual:{user_id}:{utc_now().strftime('%Y%m%d%H%M%S')}"
            activation = self.membership.activate(user_id, plan, provider="manual", reference=reference, amount_cents=self.membership.price_cents(plan, coupon), coupon=coupon.code if coupon else None)
            self.send_access(user_id, self.cfg.texts.after_payment, activation.invite_link, activation.expires_at)
            return f"Activat: id {user_id}, {plan.name}, până pe {fmt_date(activation.expires_at, self.membership.tz)}."
        if cmd == "prelungeste":
            if len(args) < 2 or not args[0].isdigit() or not args[1].isdigit():
                return "Folosește: /prelungeste ID ZILE"
            user_id, days = int(args[0]), int(args[1])
            activation = self.membership.extend(user_id, days, f"owner:{user_id}:{utc_now().strftime('%Y%m%d%H%M%S')}")
            self.api.send_message(user_id, f"Abonamentul tău a fost prelungit cu {days} zile, până pe {fmt_date(activation.expires_at, self.membership.tz)}.")
            return f"Prelungit: id {user_id} până pe {fmt_date(activation.expires_at, self.membership.tz)}."
        if cmd == "scoate":
            if not args or not args[0].isdigit():
                return "Folosește: /scoate ID"
            self.membership.remove(int(args[0]), reason="scos de proprietar")
            return f"Scos din grup: id {args[0]}."
        if cmd == "link":
            if not args or not args[0].isdigit():
                return "Folosește: /link ID"
            user_id = int(args[0])
            member = self.store.member(user_id)
            expires = parse(member.get("expires_at")) if member else None
            if not member or member.get("status") != "active" or not expires:
                return "Membrul nu are abonament activ."
            link = self.membership._invite(user_id)
            if not link:
                return "Nu pot crea linkul: verifică dacă botul e administrator în grup cu drept de invitare."
            self.send_access(user_id, "Iată linkul tău de acces:", link, expires)
            return f"Link trimis lui {user_id}."
        return None


def expiry_loop(membership: Membership, interval_seconds: int = 600) -> None:
    while True:
        try:
            result = membership.run_expiry_pass()
            if result["reminded"] or result["removed"]:
                log.info("Expirări: reamintiți %s, scoși %s", result["reminded"], result["removed"])
        except Exception:
            log.exception("Eroare în verificarea expirărilor")
        time.sleep(interval_seconds)
