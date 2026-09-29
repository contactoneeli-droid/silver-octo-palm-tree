"""Per-client configuration for the alerts bot (``clients/<slug>/config.yaml``)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .sources import SOURCES

CLIENTS_DIR = Path(os.environ.get("ALERTS_CLIENTS_DIR", Path(__file__).resolve().parent.parent / "clients"))


@dataclass
class Texts:
    welcome: str = (
        "Bună! Îți trimit anunțurile noi de pe OLX, Storia și Autovit imediat ce apar.\n\n"
        "Fă o căutare pe site cu filtrele tale (zonă, preț, camere...), copiază linkul din browser și trimite-mi-l aici."
    )
    pending: str = "Contul tău așteaptă activarea. Te anunț imediat ce e gata."
    expired: str = "Abonamentul tău a expirat și alertele s-au oprit. Scrie-ne ca să îl prelungești."
    activated: str = "Contul tău e activ. Trimite-mi linkul unei căutări și pornim."


@dataclass
class ClientConfig:
    slug: str
    business_name: str
    owner_telegram_chat_id: str | None = None
    access: str = "approved"  # "open" | "approved"
    trial_days: int = 0
    subscription_days: int = 30
    max_searches_per_user: int = 5
    poll_minutes: int = 5
    initial_results: int = 3
    max_per_check: int = 10
    sources: list[str] = field(default_factory=lambda: list(SOURCES))
    timezone: str = "Europe/Bucharest"
    texts: Texts = field(default_factory=Texts)


def list_clients(clients_dir: Path = CLIENTS_DIR) -> list[str]:
    if not clients_dir.is_dir():
        return []
    return sorted(p.name for p in clients_dir.iterdir() if (p / "config.yaml").is_file())


def load_client(slug: str, clients_dir: Path = CLIENTS_DIR) -> ClientConfig:
    path = clients_dir / slug / "config.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"Nu există clientul '{slug}' (lipsește {path})")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if "business_name" not in raw:
        raise ValueError(f"{path}: lipsește 'business_name'")
    access = raw.get("access", "approved")
    if access not in ("open", "approved"):
        raise ValueError(f"{path}: access trebuie să fie 'open' sau 'approved'")
    sources = [str(s).lower() for s in (raw.get("sources") or list(SOURCES))]
    unknown = [s for s in sources if s not in SOURCES]
    if unknown:
        raise ValueError(f"{path}: surse necunoscute {unknown}; disponibile: {', '.join(SOURCES)}")
    owner = os.environ.get("OWNER_TELEGRAM_CHAT_ID") or raw.get("owner_telegram_chat_id")
    return ClientConfig(
        slug=slug,
        business_name=raw["business_name"],
        owner_telegram_chat_id=str(owner) if owner else None,
        access=access,
        trial_days=int(raw.get("trial_days", 0)),
        subscription_days=int(raw.get("subscription_days", 30)),
        max_searches_per_user=int(raw.get("max_searches_per_user", 5)),
        poll_minutes=max(1, int(raw.get("poll_minutes", 5))),
        initial_results=int(raw.get("initial_results", 3)),
        max_per_check=int(raw.get("max_per_check", 10)),
        sources=sources,
        timezone=raw.get("timezone", "Europe/Bucharest"),
        texts=Texts(**(raw.get("texts") or {})),
    )
