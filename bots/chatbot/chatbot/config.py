"""Per-client configuration.

Each client lives in ``clients/<slug>/`` with a ``config.yaml`` and a
``knowledge/`` directory of Markdown files describing the business. The
same code serves every client; only the folder changes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

CLIENTS_DIR = Path(os.environ.get("CHATBOT_CLIENTS_DIR", Path(__file__).resolve().parent.parent / "clients"))

DEFAULT_MODEL = "claude-opus-5-5"

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


@dataclass
class Service:
    name: str
    minutes: int
    price: str | None = None


@dataclass
class BookingConfig:
    """The booking add-on: when enabled the bot can look up free slots and book."""

    enabled: bool = False
    timezone: str = "Europe/Bucharest"
    slot_minutes: int = 30
    capacity: int = 1  # appointments that can run at the same time (e.g. number of chairs)
    min_notice_minutes: int = 60
    max_days_ahead: int = 60
    auto_confirm: bool = True  # False: bookings wait for the owner's /confirma
    remind_hours_before: float = 2
    hours: dict[str, list[str]] = field(default_factory=dict)  # {"mon": ["09:00-20:00"], ...}
    services: list[Service] = field(default_factory=list)


@dataclass
class ClientConfig:
    slug: str
    business_name: str
    language: str = "ro"
    tone: str = "prietenos și concis"
    greeting: str = "Bună! Cu ce te pot ajuta?"
    handoff_message: str = "Am transmis colegilor mei și te vor contacta cât de curând."
    fallback_message: str = "Îmi pare rău, nu pot răspunde la asta. Te pot ajuta cu altceva?"
    owner_telegram_chat_id: str | None = None
    allowed_origins: list[str] = field(default_factory=lambda: ["*"])
    model: str = DEFAULT_MODEL
    effort: str = "low"
    max_history: int = 20
    booking: BookingConfig = field(default_factory=BookingConfig)
    knowledge: dict[str, str] = field(default_factory=dict)
    root: Path | None = None

    @property
    def knowledge_text(self) -> str:
        parts = []
        for name in sorted(self.knowledge):
            parts.append(f"## {name}\n\n{self.knowledge[name].strip()}")
        return "\n\n".join(parts)


def list_clients(clients_dir: Path = CLIENTS_DIR) -> list[str]:
    if not clients_dir.is_dir():
        return []
    return sorted(p.name for p in clients_dir.iterdir() if (p / "config.yaml").is_file())


def _parse_booking(raw: dict | None) -> BookingConfig:
    raw = dict(raw or {})
    services = [Service(**s) if isinstance(s, dict) else Service(name=str(s), minutes=30) for s in raw.pop("services", [])]
    hours = {}
    for day, spans in (raw.pop("hours", {}) or {}).items():
        if day not in WEEKDAYS:
            raise ValueError(f"booking.hours: zi necunoscută '{day}' (folosește {', '.join(WEEKDAYS)})")
        hours[day] = list(spans or [])
    known = set(BookingConfig.__dataclass_fields__) - {"services", "hours"}
    unknown = set(raw) - known
    if unknown:
        raise ValueError(f"booking: chei necunoscute {sorted(unknown)}")
    return BookingConfig(services=services, hours=hours, **raw)


def load_client(slug: str, clients_dir: Path = CLIENTS_DIR) -> ClientConfig:
    root = clients_dir / slug
    config_path = root / "config.yaml"
    if not config_path.is_file():
        raise FileNotFoundError(f"Nu există clientul '{slug}' (lipsește {config_path})")

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if "business_name" not in raw:
        raise ValueError(f"{config_path}: lipsește 'business_name'")

    knowledge: dict[str, str] = {}
    knowledge_dir = root / "knowledge"
    if knowledge_dir.is_dir():
        for path in sorted(knowledge_dir.glob("*.md")):
            knowledge[path.stem] = path.read_text(encoding="utf-8")

    # Environment variables override secrets-ish fields so they never live in git.
    owner_chat = os.environ.get("OWNER_TELEGRAM_CHAT_ID") or raw.get("owner_telegram_chat_id")

    known = {f for f in ClientConfig.__dataclass_fields__ if f not in {"slug", "knowledge", "root", "booking"}}
    extra = {k: v for k, v in raw.items() if k in known and k != "owner_telegram_chat_id"}
    return ClientConfig(
        slug=slug,
        knowledge=knowledge,
        root=root,
        owner_telegram_chat_id=str(owner_chat) if owner_chat else None,
        booking=_parse_booking(raw.get("booking")),
        **extra,
    )
