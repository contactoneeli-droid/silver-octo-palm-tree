"""HTTP API and embeddable widget (FastAPI)."""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field

from .engine import BaseEngine
from .store import Session

WIDGET_JS = Path(__file__).resolve().parent.parent / "widget" / "widget.js"

DEMO_PAGE = """<!doctype html>
<html lang="ro"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{name} · demo chatbot</title>
<style>body{{font-family:system-ui,sans-serif;max-width:640px;margin:60px auto;padding:0 20px;color:#131c26;line-height:1.6}}
code{{background:#eef;padding:2px 6px;border-radius:4px}}</style></head>
<body>
<h1>{name}</h1>
<p>Aceasta e o pagină de test. Butonul din colțul din dreapta jos deschide asistentul virtual, care răspunde
din informațiile configurate pentru această afacere.</p>
<p>Mod curent: <code>{mode}</code>. Ca să pui asistentul pe orice site, adaugă înainte de <code>&lt;/body&gt;</code>:</p>
<pre><code>&lt;script src="{{origin}}/widget.js" async&gt;&lt;/script&gt;</code></pre>
<script src="/widget.js" async></script>
</body></html>"""


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    session_id: str | None = Field(default=None, max_length=64)


class ChatOut(BaseModel):
    reply: str
    session_id: str
    events: list[str]


def create_app(engine: BaseEngine) -> FastAPI:
    cfg = engine.cfg
    app = FastAPI(title=f"Chatbot {cfg.business_name}", docs_url=None, redoc_url=None)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.allowed_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["content-type"],
    )

    @app.get("/health")
    def health():
        return {"ok": True, "client": cfg.slug, "mode": engine.mode}

    @app.get("/widget-config")
    def widget_config():
        return {"business_name": cfg.business_name, "greeting": cfg.greeting}

    @app.post("/chat", response_model=ChatOut)
    def chat(body: ChatIn):
        session_id = body.session_id or uuid.uuid4().hex
        if not session_id.replace("-", "").isalnum():
            raise HTTPException(400, "session_id invalid")
        reply = engine.reply(Session("web", session_id), body.message.strip())
        return ChatOut(reply=reply.text, session_id=session_id, events=reply.events)

    @app.get("/widget.js")
    def widget():
        return Response(WIDGET_JS.read_text(encoding="utf-8"), media_type="application/javascript")

    @app.get("/", response_class=HTMLResponse)
    def demo_page():
        return DEMO_PAGE.format(name=cfg.business_name, mode=engine.mode)

    return app
