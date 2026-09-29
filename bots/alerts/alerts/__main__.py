"""Run the alerts bot for one client.

    python -m alerts --client demo-imobiliare          # Telegram + periodic checks
    python -m alerts --client demo-imobiliare --once   # one pass over all searches, then exit (cron)

Environment: TELEGRAM_BOT_TOKEN (required), OWNER_TELEGRAM_CHAT_ID, ALERTS_DATA_DIR,
HTTPS_PROXY (optional, if the sites block the server's IP).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import time

from .bot import AlertsBot
from .config import list_clients, load_client
from .fetcher import Fetcher
from .store import Store
from .telegram import TelegramApi
from .watcher import Watcher


def watch_forever(watcher: Watcher, interval: int) -> None:
    log = logging.getLogger("alerts.watch")
    while True:
        try:
            stats = watcher.run_pass()
            if stats["checked"]:
                log.info("Verificate %(checked)s, trimise %(sent)s, erori %(errors)s", stats)
        except Exception:
            log.exception("Eroare în verificarea căutărilor")
        time.sleep(interval)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="alerts")
    parser.add_argument("--client", default=os.environ.get("ALERTS_CLIENT", "demo-imobiliare"))
    parser.add_argument("--once", action="store_true", help="o singură trecere prin căutări, apoi ieșire")
    parser.add_argument("--interval", type=int, default=60, help="secunde între treceri (fiecare căutare respectă poll_minutes)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("alerts")

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

    api = TelegramApi(token)
    store = Store(cfg.slug)
    watcher = Watcher(cfg, store, Fetcher(), api)
    bot = AlertsBot(cfg, store, api, watcher)

    if args.once:
        stats = watcher.run_pass()
        log.info("Verificate %(checked)s, trimise %(sent)s, erori %(errors)s, expirați %(expired)s", stats)
        return 0

    me = api.get_me()
    log.info("Pornit ca @%s pentru %s (acces: %s, surse: %s, la %s min)", me.get("username"), cfg.business_name, cfg.access, ", ".join(cfg.sources), cfg.poll_minutes)
    threading.Thread(target=watch_forever, args=(watcher, args.interval), name="watch", daemon=True).start()
    api.poll(bot.handle_update)
    return 0


if __name__ == "__main__":
    sys.exit(main())
