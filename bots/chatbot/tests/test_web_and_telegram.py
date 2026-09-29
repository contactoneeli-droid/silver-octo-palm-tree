from fastapi.testclient import TestClient

from chatbot.engine import DemoEngine
from chatbot.store import Session
from chatbot.telegram import TelegramBot
from chatbot.web import create_app


def test_web_chat_round_trip(cfg, store, notifier):
    app = create_app(DemoEngine(cfg, store, notifier))
    client = TestClient(app)

    assert client.get("/health").json() == {"ok": True, "client": "demo-salon", "mode": "demo"}
    assert client.get("/widget-config").json()["business_name"] == "Salon Lumière"

    r = client.post("/chat", json={"message": "Care e programul sâmbăta?"})
    assert r.status_code == 200
    data = r.json()
    assert "09:00 - 16:00" in data["reply"]
    assert data["session_id"]

    # Same session keeps its history.
    r2 = client.post("/chat", json={"message": "Mulțumesc", "session_id": data["session_id"]})
    assert r2.status_code == 200
    assert len(store.history(Session("web", data["session_id"]), 20)) == 4


def test_web_rejects_empty_and_huge_messages(cfg, store, notifier):
    client = TestClient(create_app(DemoEngine(cfg, store, notifier)))
    assert client.post("/chat", json={"message": ""}).status_code == 422
    assert client.post("/chat", json={"message": "x" * 2001}).status_code == 422
    assert client.post("/chat", json={"message": "hi", "session_id": "bad id!"}).status_code == 400


def test_widget_and_demo_page_are_served(cfg, store, notifier):
    client = TestClient(create_app(DemoEngine(cfg, store, notifier)))
    js = client.get("/widget.js")
    assert js.status_code == 200 and "bz-panel" in js.text
    page = client.get("/")
    assert page.status_code == 200 and "Salon Lumière" in page.text


class FakeTelegramApi:
    def __init__(self):
        self.sent = []

    def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))

    def typing(self, chat_id):
        pass


def _update(text, chat_id=42):
    return {"update_id": 1, "message": {"chat": {"id": chat_id}, "from": {"first_name": "Ana"}, "text": text}}


def test_telegram_start_and_question(cfg, store, notifier):
    api = FakeTelegramApi()
    bot = TelegramBot(api, DemoEngine(cfg, store, notifier))

    bot.handle_update(_update("/start"))
    assert api.sent[-1] == (42, cfg.greeting)

    bot.handle_update(_update("Aveți parcare?"))
    assert "parcarea publică" in api.sent[-1][1].lower() or "parcare" in api.sent[-1][1].lower()

    bot.handle_update({"update_id": 2, "message": {"chat": {"id": 42}, "sticker": {}}})
    assert len(api.sent) == 2  # non-text updates are ignored
