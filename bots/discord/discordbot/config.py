"""Per-client configuration (``clients/<slug>/config.yaml``): which modules run and how."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

CLIENTS_DIR = Path(os.environ.get("DISCORD_CLIENTS_DIR", Path(__file__).resolve().parent.parent / "clients"))


@dataclass
class WelcomeConfig:
    enabled: bool = True
    channel: str = "bun-venit"
    message: str = "Bun venit, {mention}! Ești membrul cu numărul {count}. Citește regulile în {rules}."
    rules_channel: str | None = "reguli"
    auto_role: str | None = None
    dm_message: str | None = None


@dataclass
class ModerationConfig:
    enabled: bool = True
    banned_words: list[str] = field(default_factory=list)
    delete_invites: bool = True
    spam_messages: int = 5
    spam_seconds: int = 5
    warn_limit: int = 3
    timeout_minutes: int = 60
    log_channel: str | None = "log-moderare"
    exempt_roles: list[str] = field(default_factory=lambda: ["Moderator", "Admin"])


@dataclass
class TicketsConfig:
    enabled: bool = True
    category: str = "Tichete"
    support_role: str | None = "Suport"
    panel_text: str = "Ai nevoie de ajutor? Apasă butonul și deschidem un canal privat doar pentru tine."
    opening_text: str = "Salut, {mention}! Spune-ne ce problemă ai și cineva din echipă îți răspunde aici."
    max_open_per_user: int = 1


@dataclass
class PaidRole:
    code: str
    role: str
    name: str
    days: int
    price: float

    @property
    def cents(self) -> int:
        return int(round(self.price * 100))


@dataclass
class PaidRolesConfig:
    enabled: bool = False
    currency: str = "EUR"
    roles: list[PaidRole] = field(default_factory=list)
    remind_days_before: int = 3

    def plan(self, code: str) -> PaidRole | None:
        return next((r for r in self.roles if r.code == code), None)


@dataclass
class GamesConfig:
    enabled: bool = True
    levels: bool = True
    xp_min: int = 15
    xp_max: int = 25
    xp_cooldown_seconds: int = 60
    level_up_message: str = "🎉 {mention} a ajuns la nivelul {level}!"
    level_roles: dict[int, str] = field(default_factory=dict)
    trivia: bool = True
    trivia_xp: int = 50


@dataclass
class ClientConfig:
    slug: str
    business_name: str
    guild_id: int
    owner_discord_id: int | None = None
    welcome: WelcomeConfig = field(default_factory=WelcomeConfig)
    moderation: ModerationConfig = field(default_factory=ModerationConfig)
    tickets: TicketsConfig = field(default_factory=TicketsConfig)
    paid_roles: PaidRolesConfig = field(default_factory=PaidRolesConfig)
    games: GamesConfig = field(default_factory=GamesConfig)
    trivia_questions: list[dict] = field(default_factory=list)
    root: Path | None = None


def _section(cls, raw: dict | None):
    raw = dict(raw or {})
    known = {f for f in cls.__dataclass_fields__}
    unknown = set(raw) - known
    if unknown:
        raise ValueError(f"{cls.__name__}: chei necunoscute {sorted(unknown)}; permise: {sorted(known)}")
    return cls(**raw)


def list_clients(clients_dir: Path = CLIENTS_DIR) -> list[str]:
    if not clients_dir.is_dir():
        return []
    return sorted(p.name for p in clients_dir.iterdir() if (p / "config.yaml").is_file())


def load_client(slug: str, clients_dir: Path = CLIENTS_DIR) -> ClientConfig:
    root = clients_dir / slug
    path = root / "config.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"Nu există clientul '{slug}' (lipsește {path})")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    for key in ("business_name", "guild_id"):
        if key not in raw:
            raise ValueError(f"{path}: lipsește '{key}'")

    paid_raw = dict(raw.get("paid_roles") or {})
    roles = [PaidRole(**r) for r in paid_raw.pop("roles", []) or []]
    if len({r.code for r in roles}) != len(roles):
        raise ValueError(f"{path}: codurile rolurilor plătite trebuie să fie unice")
    paid = _section(PaidRolesConfig, paid_raw)
    paid.roles = roles
    paid.currency = paid.currency.upper()

    games_raw = dict(raw.get("games") or {})
    games_raw["level_roles"] = {int(k): str(v) for k, v in (games_raw.get("level_roles") or {}).items()}
    games = _section(GamesConfig, games_raw)

    trivia_path = root / "trivia.yaml"
    questions = []
    if trivia_path.is_file():
        for i, q in enumerate(yaml.safe_load(trivia_path.read_text(encoding="utf-8")) or []):
            if not {"q", "options", "answer"} <= set(q) or not 0 <= int(q["answer"]) < len(q["options"]) or not 2 <= len(q["options"]) <= 4:
                raise ValueError(f"{trivia_path}: întrebarea {i + 1} trebuie să aibă q, 2-4 options și answer (index valid)")
            questions.append({"q": str(q["q"]), "options": [str(o) for o in q["options"]], "answer": int(q["answer"])})

    owner = os.environ.get("OWNER_DISCORD_ID") or raw.get("owner_discord_id")
    guild_id = int(os.environ.get("DISCORD_GUILD_ID") or raw["guild_id"])
    return ClientConfig(
        slug=slug,
        business_name=raw["business_name"],
        guild_id=guild_id,
        owner_discord_id=int(owner) if owner else None,
        welcome=_section(WelcomeConfig, raw.get("welcome")),
        moderation=_section(ModerationConfig, raw.get("moderation")),
        tickets=_section(TicketsConfig, raw.get("tickets")),
        paid_roles=paid,
        games=games,
        trivia_questions=questions,
        root=root,
    )
