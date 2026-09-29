"""HTTP side: Stripe webhook plus the success/cancel pages the customer lands on."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse

from .bot import MembersBot
from .stripe_pay import StripeError

log = logging.getLogger(__name__)

PAGE = """<!doctype html><html lang="ro"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title><style>body{{font-family:system-ui,sans-serif;max-width:520px;margin:80px auto;padding:0 20px;color:#131c26;line-height:1.6;text-align:center}}</style></head>
<body><h1>{title}</h1><p>{text}</p></body></html>"""


def create_app(bot: MembersBot) -> FastAPI:
    app = FastAPI(title=f"Membri {bot.cfg.business_name}", docs_url=None, redoc_url=None)

    @app.get("/health")
    def health():
        return {"ok": True, "client": bot.cfg.slug, "payment": bot.cfg.payment_provider}

    @app.post("/stripe/webhook")
    async def stripe_webhook(request: Request, stripe_signature: str = Header(default="")):
        if bot.stripe is None:
            raise HTTPException(404, "Stripe nu e configurat")
        payload = await request.body()
        try:
            event = bot.stripe.verify_webhook(payload, stripe_signature)
        except StripeError as e:
            raise HTTPException(400, str(e))
        result = bot.on_stripe_event(event)
        return {"ok": True, "result": result}

    @app.get("/success", response_class=HTMLResponse)
    def success():
        return PAGE.format(title="Plata a reușit", text="Întoarce-te în Telegram: botul ți-a trimis linkul de acces la grup.")

    @app.get("/cancel", response_class=HTMLResponse)
    def cancel():
        return PAGE.format(title="Plata a fost anulată", text="Nu s-a încasat nimic. Poți relua oricând din Telegram cu /planuri.")

    return app
