"""Run the paid-group bot for one client.

    python -m members --client demo-creator

Environment: TELEGRAM_BOT_TOKEN (required), OWNER_TELEGRAM_CHAT_ID, GROUP_CHAT_ID
(overrides config), STRIPE_SECRET_KEY + STRIPE_WEBHOOK_SECRET + PUBLIC_URL (for card
payments), PORT (default 8000), MEMBERS_DATA_DIR, TZ_NAME (default Europe/Bucharest).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
from zoneinfo import ZoneInfo

from .bot import MembersBot, expiry_loop
from .config import list_clients, load_client
from .membership import Membership
from .store import Store
from .stripe_pay import Stripe
from .telegram import TelegramApi


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="members")
    parser.add_argument("--client", default=os.environ.get("MEMBERS_CLIENT", "demo-creator"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    parser.add_argument("--no-web", action="store_true", help="doar Telegram, fără webhook Stripe (plăți manuale)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("members")

    try:
        cfg = load_client(args.client)
    except FileNotFoundError as e:
        print(e, file=sys.stderr)
        print(f"Clienți disponibili: {', '.join(list_clients()) or '(niciunul)'}", file=sys.stderr)
        return 2

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print("Lipsește TELEGRAM_BOT_TOKEN.", file=sys.stderr)
        return 2

    stripe = None
    if cfg.payment_provider == "stripe":
        secret, whsec = os.environ.get("STRIPE_SECRET_KEY"), os.environ.get("STRIPE_WEBHOOK_SECRET")
        if secret and whsec:
            stripe = Stripe(secret, whsec)
        else:
            log.warning("payment.provider e 'stripe' dar lipsesc STRIPE_SECRET_KEY / STRIPE_WEBHOOK_SECRET; trec pe plăți manuale.")
            cfg.payment_provider = "manual"

    api = TelegramApi(token)
    store = Store(cfg.slug)
    membership = Membership(cfg, store, api, ZoneInfo(os.environ.get("TZ_NAME", "Europe/Bucharest")))
    bot = MembersBot(cfg, store, api, membership, stripe, os.environ.get("PUBLIC_URL", f"http://localhost:{args.port}"))

    me = api.get_me()
    log.info("Pornit ca @%s pentru %s (plăți: %s, grup %s)", me.get("username"), cfg.business_name, cfg.payment_provider, cfg.group_chat_id)

    threading.Thread(target=expiry_loop, args=(membership,), name="expiry", daemon=True).start()

    if args.no_web:
        api.poll(bot.handle_update)
        return 0

    threading.Thread(target=api.poll, args=(bot.handle_update,), name="telegram", daemon=True).start()

    import uvicorn

    from .web import create_app

    uvicorn.run(create_app(bot), host="0.0.0.0", port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
