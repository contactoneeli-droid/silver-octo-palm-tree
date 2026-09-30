"""Run the Discord bot for one client.

    python -m discordbot --client demo-comunitate

Environment: DISCORD_BOT_TOKEN (required), DISCORD_GUILD_ID / OWNER_DISCORD_ID (override
config), STRIPE_SECRET_KEY + STRIPE_WEBHOOK_SECRET + PUBLIC_URL (paid roles), PORT
(webhook server, default 8000), DISCORD_DATA_DIR.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading

from .bot import BotziBot
from .config import list_clients, load_client
from .store import Store
from .stripe_pay import Stripe


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="discordbot")
    parser.add_argument("--client", default=os.environ.get("DISCORD_CLIENT", "demo-comunitate"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("discordbot")

    try:
        cfg = load_client(args.client)
    except FileNotFoundError as e:
        print(e, file=sys.stderr)
        print(f"Clienți disponibili: {', '.join(list_clients()) or '(niciunul)'}", file=sys.stderr)
        return 2

    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        print("Lipsește DISCORD_BOT_TOKEN.", file=sys.stderr)
        return 2

    stripe = None
    if cfg.paid_roles.enabled:
        secret, whsec = os.environ.get("STRIPE_SECRET_KEY"), os.environ.get("STRIPE_WEBHOOK_SECRET")
        if secret and whsec:
            stripe = Stripe(secret, whsec)
        else:
            log.warning("paid_roles e activ dar lipsesc STRIPE_SECRET_KEY / STRIPE_WEBHOOK_SECRET; /abonamente va arăta planurile fără buton de plată.")

    store = Store(cfg.slug)
    bot = BotziBot(cfg, store, stripe, os.environ.get("PUBLIC_URL", f"http://localhost:{args.port}"))

    if stripe is not None:
        import uvicorn

        from .web import create_app

        server = uvicorn.Server(uvicorn.Config(create_app(bot), host="0.0.0.0", port=args.port, log_level="info"))
        threading.Thread(target=server.run, name="web", daemon=True).start()

    bot.run(token, log_handler=None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
