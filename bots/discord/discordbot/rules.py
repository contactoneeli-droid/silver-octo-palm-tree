"""The bot's decisions, free of Discord objects so they can be tested: word filter, anti-spam,
warning escalation, XP and levels, ticket names, paid-role activation and expiry."""

from __future__ import annotations

import math
import random
import re
import unicodedata
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .config import ClientConfig, ModerationConfig, PaidRole
from .store import Store, parse, utc_now

INVITE_RE = re.compile(r"(?:discord\.gg|discord(?:app)?\.com/invite)/[A-Za-z0-9-]+", re.I)


def fold(text: str) -> str:
    """Lowercase, no diacritics: 'Prostîe' -> 'prostie'."""
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))


class WordFilter:
    def __init__(self, words: list[str]):
        folded = sorted({fold(w).strip() for w in words if w.strip()}, key=len, reverse=True)
        self.pattern = re.compile(r"(?<![a-z0-9])(?:" + "|".join(re.escape(w) for w in folded) + r")(?![a-z0-9])") if folded else None

    def match(self, text: str) -> str | None:
        if not self.pattern:
            return None
        m = self.pattern.search(fold(text))
        return m.group(0) if m else None


class SpamTracker:
    """True when a user sends more than ``max_messages`` within ``seconds``; warns once per burst."""

    def __init__(self, max_messages: int, seconds: int):
        self.max_messages, self.window = max_messages, timedelta(seconds=seconds)
        self._hits: dict[int, deque] = defaultdict(deque)
        self._flagged: set[int] = set()

    def hit(self, user_id: int, now: datetime) -> tuple[bool, bool]:
        """(is_spam, first_time_in_this_burst)"""
        q = self._hits[user_id]
        q.append(now)
        while q and now - q[0] > self.window:
            q.popleft()
        if len(q) > self.max_messages:
            first = user_id not in self._flagged
            self._flagged.add(user_id)
            return True, first
        self._flagged.discard(user_id)
        return False, False


@dataclass
class Verdict:
    delete: bool = False
    warn_reason: str | None = None
    timeout_minutes: int = 0
    warnings: int = 0
    notes: list[str] = field(default_factory=list)


class Moderation:
    def __init__(self, cfg: ModerationConfig, store: Store):
        self.cfg = cfg
        self.store = store
        self.words = WordFilter(cfg.banned_words)
        self.spam = SpamTracker(cfg.spam_messages, cfg.spam_seconds)

    def judge(self, user_id: int, content: str, now: datetime | None = None, exempt: bool = False) -> Verdict:
        v = Verdict()
        if not self.cfg.enabled or exempt:
            return v
        now = now or utc_now()
        word = self.words.match(content)
        if word:
            v.delete, v.warn_reason = True, f"limbaj nepermis („{word}”)"
        elif self.cfg.delete_invites and INVITE_RE.search(content):
            v.delete, v.warn_reason = True, "link de invitație pe alt server"
        is_spam, first = self.spam.hit(user_id, now)
        if is_spam:
            v.delete = True
            if first and not v.warn_reason:
                v.warn_reason = f"spam ({self.cfg.spam_messages}+ mesaje în {self.cfg.spam_seconds} secunde)"
        if v.warn_reason:
            v.warnings = self.store.add_warning(user_id, None, v.warn_reason)
            if v.warnings >= self.cfg.warn_limit:
                v.timeout_minutes = self.cfg.timeout_minutes
                self.store.clear_warnings(user_id)
        return v

    def warn(self, user_id: int, moderator_id: int, reason: str) -> Verdict:
        v = Verdict(warn_reason=reason)
        v.warnings = self.store.add_warning(user_id, moderator_id, reason)
        if v.warnings >= self.cfg.warn_limit:
            v.timeout_minutes = self.cfg.timeout_minutes
            self.store.clear_warnings(user_id)
        return v


# Levels ----------------------------------------------------------------------------------


def level_for_xp(xp: int) -> int:
    return int(math.sqrt(max(xp, 0) / 100))


def xp_for_level(level: int) -> int:
    return level * level * 100


class Levels:
    def __init__(self, cfg: ClientConfig, store: Store, rng: random.Random | None = None):
        self.cfg = cfg.games
        self.store = store
        self.rng = rng or random.Random()

    def on_message(self, user_id: int, now: datetime | None = None) -> tuple[int, int | None]:
        """Awards XP if the cooldown passed. Returns (total_xp, new_level_if_leveled_up)."""
        now = now or utc_now()
        row = self.store.xp(user_id)
        last = parse(row.get("last_award"))
        if last and now - last < timedelta(seconds=self.cfg.xp_cooldown_seconds):
            return row["xp"], None
        before = level_for_xp(row["xp"])
        total = self.store.add_xp(user_id, self.rng.randint(self.cfg.xp_min, self.cfg.xp_max), now)
        after = level_for_xp(total)
        return total, (after if after > before else None)

    def bonus(self, user_id: int, amount: int, now: datetime | None = None) -> tuple[int, int | None]:
        row = self.store.xp(user_id)
        before = level_for_xp(row["xp"])
        total = self.store.add_xp(user_id, amount, now or utc_now(), count_message=False)
        after = level_for_xp(total)
        return total, (after if after > before else None)

    def role_for_level(self, level: int) -> str | None:
        """The highest configured level role at or below ``level``."""
        eligible = [lvl for lvl in self.cfg.level_roles if lvl <= level]
        return self.cfg.level_roles[max(eligible)] if eligible else None

    def card(self, user_id: int, display: str) -> str:
        row = self.store.xp(user_id)
        level = level_for_xp(row["xp"])
        nxt = xp_for_level(level + 1)
        return f"**{display}**: nivel {level}, {row['xp']} XP ({nxt - row['xp']} până la nivelul {level + 1}), locul {self.store.rank(user_id)}, {row['messages']} mesaje"


# Tickets ---------------------------------------------------------------------------------


def ticket_channel_name(ticket_id: int, display: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", fold(display)).strip("-")[:20] or "membru"
    return f"tichet-{ticket_id:04d}-{slug}"


# Paid roles ------------------------------------------------------------------------------


@dataclass
class Activation:
    user_id: int
    plan: PaidRole
    expires_at: datetime
    already_processed: bool = False


class PaidRoles:
    def __init__(self, cfg: ClientConfig, store: Store):
        self.cfg = cfg.paid_roles
        self.store = store

    def activate(self, user_id: int, plan: PaidRole, *, reference: str, amount_cents: int, provider: str = "stripe", now: datetime | None = None) -> Activation:
        if self.store.payment_exists(reference):
            current = self.store.paid_role(user_id, plan.code)
            return Activation(user_id, plan, parse(current["expires_at"]) if current else utc_now(), already_processed=True)
        now = now or utc_now()
        current = self.store.paid_role(user_id, plan.code)
        base = parse(current["expires_at"]) if current else None
        base = base if base and base > now else now
        expires = base + timedelta(days=plan.days)
        self.store.add_payment(user_id, plan.code, amount_cents, self.cfg.currency, provider, reference)
        self.store.set_paid_role(user_id, plan.code, expires)
        return Activation(user_id, plan, expires)

    def sweep(self, now: datetime | None = None) -> tuple[list[tuple[int, PaidRole]], list[tuple[int, PaidRole, datetime]]]:
        """(expired roles to remove, roles to remind about)"""
        now = now or utc_now()
        expired, remind = [], []
        for row in self.store.paid_roles():
            plan = self.cfg.plan(row["code"])
            if not plan:
                continue
            expires = parse(row["expires_at"])
            if expires <= now:
                self.store.remove_paid_role(row["user_id"], row["code"])
                expired.append((row["user_id"], plan))
            elif expires - now <= timedelta(days=self.cfg.remind_days_before) and row.get("reminded_for") != row["expires_at"]:
                self.store.mark_reminded(row["user_id"], row["code"], row["expires_at"])
                remind.append((row["user_id"], plan, expires))
        return expired, remind

    def describe(self, plan: PaidRole) -> str:
        price = f"{plan.price:.2f}".rstrip("0").rstrip(".")
        return f"{plan.name}: {price} {self.cfg.currency} / {plan.days} zile (rolul @{plan.role})"


# Welcome ---------------------------------------------------------------------------------


def render(template: str, **values) -> str:
    class Safe(dict):
        def __missing__(self, key):
            return "{" + key + "}"

    return template.format_map(Safe(values))
