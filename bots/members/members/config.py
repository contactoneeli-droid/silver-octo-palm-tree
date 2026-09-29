"""Per-client configuration for the paid-group bot.

Each client lives in ``clients/<slug>/config.yaml``: the group, the plans,
trial and grace periods, coupons and how members pay (Stripe or manual).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

CLIENTS_DIR = Path(os.environ.get("MEMBERS_CLIENTS_DIR", Path(__file__).resolve().parent.parent / "clients"))


@dataclass
class Plan:
    code: str
    name: str
    days: int
    price: float  # in major units of ``currency`` (e.g. 99 = 99.00 RON)

    @property
    def cents(self) -> int:
        return int(round(self.price * 100))


@dataclass
class Coupon:
    code: str
    percent: int


@dataclass
class Texts:
    welcome: str = "Bună! Aici îți iei accesul la grupul privat. Alege un abonament:"
    after_payment: str = "Mulțumim! Plata a fost înregistrată. Iată linkul tău de acces (valabil 24 de ore, o singură folosire):"
    trial_started: str = "Perioada de probă a început. Iată linkul tău de acces:"
    expiring_soon: str = "Abonamentul tău expiră {when} ({date}). Reînnoiește-l ca să rămâi în grup:"
    expired: str = "Abonamentul tău a expirat și accesul la grup s-a închis. Poți reveni oricând:"
    manual_instructions: str = "Plătește prin transfer bancar, apoi apasă „Am plătit”. Îți activăm accesul după verificare."


@dataclass
class ClientConfig:
    slug: str
    business_name: str
    group_chat_id: int
    currency: str = "RON"
    plans: list[Plan] = field(default_factory=list)
    coupons: list[Coupon] = field(default_factory=list)
    trial_days: int = 0
    grace_days: int = 1
    remind_days_before: int = 3
    payment_provider: str = "stripe"  # "stripe" | "manual"
    owner_telegram_chat_id: str | None = None
    texts: Texts = field(default_factory=Texts)

    def plan(self, code: str) -> Plan | None:
        return next((p for p in self.plans if p.code == code), None)

    def coupon(self, code: str) -> Coupon | None:
        code = code.strip().upper()
        return next((c for c in self.coupons if c.code.upper() == code), None)


def list_clients(clients_dir: Path = CLIENTS_DIR) -> list[str]:
    if not clients_dir.is_dir():
        return []
    return sorted(p.name for p in clients_dir.iterdir() if (p / "config.yaml").is_file())


def load_client(slug: str, clients_dir: Path = CLIENTS_DIR) -> ClientConfig:
    path = clients_dir / slug / "config.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"Nu există clientul '{slug}' (lipsește {path})")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    for key in ("business_name", "group_chat_id", "plans"):
        if key not in raw:
            raise ValueError(f"{path}: lipsește '{key}'")

    plans = [Plan(**p) for p in raw["plans"]]
    if len({p.code for p in plans}) != len(plans):
        raise ValueError(f"{path}: codurile planurilor trebuie să fie unice")
    coupons = [Coupon(**c) for c in raw.get("coupons", []) or []]
    texts = Texts(**(raw.get("texts") or {}))
    payment = raw.get("payment") or {}
    provider = payment.get("provider", "stripe")
    if provider not in ("stripe", "manual"):
        raise ValueError(f"{path}: payment.provider trebuie să fie 'stripe' sau 'manual'")
    if payment.get("manual_instructions"):
        texts.manual_instructions = payment["manual_instructions"]

    owner = os.environ.get("OWNER_TELEGRAM_CHAT_ID") or raw.get("owner_telegram_chat_id")
    group_chat_id = int(os.environ.get("GROUP_CHAT_ID") or raw["group_chat_id"])

    return ClientConfig(
        slug=slug,
        business_name=raw["business_name"],
        group_chat_id=group_chat_id,
        currency=str(raw.get("currency", "RON")).upper(),
        plans=plans,
        coupons=coupons,
        trial_days=int(raw.get("trial_days", 0)),
        grace_days=int(raw.get("grace_days", 1)),
        remind_days_before=int(raw.get("remind_days_before", 3)),
        payment_provider=provider,
        owner_telegram_chat_id=str(owner) if owner else None,
        texts=texts,
    )
