"""Run the chatbot for one client.

    python -m chatbot --client demo-salon web        # HTTP API + widget
    python -m chatbot --client demo-salon telegram   # Telegram bot (needs TELEGRAM_BOT_TOKEN)
    python -m chatbot --client demo-salon all        # both

Environment: ANTHROPIC_API_KEY (otherwise demo mode), TELEGRAM_BOT_TOKEN,
OWNER_TELEGRAM_CHAT_ID, PORT (default 8000), CHATBOT_DATA_DIR.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading

from .config import list_clients, load_client
from .engine import make_engine
from .notify import LogNotifier, TelegramNotifier
from .store import Store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="chatbot")
    parser.add_argument("mode", choices=["web", "telegram", "all"])
    parser.add_argument("--client", default=os.environ.get("CHATBOT_CLIENT", "demo-salon"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("chatbot")

    try:
        cfg = load_client(args.client)
    except FileNotFoundError as e:
        print(e, file=sys.stderr)
        print(f"Clienți disponibili: {', '.join(list_clients()) or '(niciunul)'}", file=sys.stderr)
        return 2

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if token and cfg.owner_telegram_chat_id:
        notifier = TelegramNotifier(token, cfg.owner_telegram_chat_id)
    else:
        notifier = LogNotifier()
        log.warning("Fără OWNER_TELEGRAM_CHAT_ID + TELEGRAM_BOT_TOKEN, notificările merg doar în log.")

    store = Store(cfg.slug)
    engine = make_engine(cfg, store, notifier)
    log.info("Client: %s (%s), motor: %s, model: %s", cfg.slug, cfg.business_name, engine.mode, cfg.model)

    if args.mode in ("telegram", "all"):
        if not token:
            print("Lipsește TELEGRAM_BOT_TOKEN.", file=sys.stderr)
            return 2
        from .telegram import TelegramApi, TelegramBot

        bot = TelegramBot(TelegramApi(token), engine)
        if args.mode == "telegram":
            bot.run()
            return 0
        threading.Thread(target=bot.run, name="telegram", daemon=True).start()

    import uvicorn

    from .web import create_app

    uvicorn.run(create_app(engine), host="0.0.0.0", port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
