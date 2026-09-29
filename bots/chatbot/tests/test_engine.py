from conftest import FakeAnthropic, response, text_block, tool_use_block

from chatbot.engine import ClaudeEngine, DemoEngine, build_system_prompt
from chatbot.store import Session

WEB = Session("web", "abc123")


def test_system_prompt_contains_business_and_knowledge(cfg):
    prompt = build_system_prompt(cfg)
    assert "Salon Lumière" in prompt
    assert "Tuns: 90" in prompt
    assert "save_request" in prompt


def test_plain_reply_is_stored_and_history_is_resent(cfg, store, notifier):
    client = FakeAnthropic(response(text_block("Tunsul costă 90 lei.")), response(text_block("Da, și sâmbăta.")))
    engine = ClaudeEngine(cfg, store, notifier, client=client)

    first = engine.reply(WEB, "Cât costă tunsul?")
    assert first.text == "Tunsul costă 90 lei."
    assert first.events == []

    engine.reply(WEB, "Și sâmbăta lucrați?")
    second_call = client.calls[1]
    assert [m["role"] for m in second_call["messages"]] == ["user", "assistant", "user"]
    assert second_call["messages"][0]["content"] == "Cât costă tunsul?"

    kwargs = client.calls[0]
    assert kwargs["model"] == "claude-opus-5-5"
    assert kwargs["output_config"] == {"effort": "low"}
    assert kwargs["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in kwargs["betas"]
    assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert {t["name"] for t in kwargs["tools"]} == {"save_request", "escalate_to_human"}


def test_save_request_tool_stores_lead_and_notifies(cfg, store, notifier):
    client = FakeAnthropic(
        response(
            text_block("Notez imediat."),
            tool_use_block("save_request", {"name": "Ana", "contact": "0740123456", "request": "tuns marți 13:00"}),
        ),
        response(text_block("Gata, cererea a ajuns la echipă.")),
    )
    engine = ClaudeEngine(cfg, store, notifier, client=client)

    reply = engine.reply(WEB, "Vreau o programare marți la 13, Ana, 0740123456")

    assert reply.text == "Gata, cererea a ajuns la echipă."
    assert reply.events == ["lead"]
    leads = store.leads()
    assert len(leads) == 1 and leads[0]["name"] == "Ana" and leads[0]["contact"] == "0740123456"
    assert len(notifier.sent) == 1 and "Ana" in notifier.sent[0]

    # The tool result went back to the model in the second request.
    follow_up = client.calls[1]["messages"]
    assert follow_up[-1]["role"] == "user"
    assert follow_up[-1]["content"][0]["type"] == "tool_result"
    assert follow_up[-1]["content"][0]["tool_use_id"] == "toolu_1"
    assert follow_up[-1]["content"][0]["is_error"] is False


def test_escalation_tool_notifies_owner(cfg, store, notifier):
    client = FakeAnthropic(
        response(tool_use_block("escalate_to_human", {"reason": "cere un om", "summary": "vrea o reclamație"})),
        response(text_block(cfg.handoff_message)),
    )
    engine = ClaudeEngine(cfg, store, notifier, client=client)
    reply = engine.reply(WEB, "Vreau să vorbesc cu cineva")
    assert reply.events == ["handoff"]
    assert "Preluare" in notifier.sent[0]


def test_refusal_returns_fallback_message(cfg, store, notifier):
    client = FakeAnthropic(response(stop_reason="refusal", stop_details={"category": "cyber"}))
    engine = ClaudeEngine(cfg, store, notifier, client=client)
    reply = engine.reply(WEB, "...")
    assert reply.text == cfg.fallback_message
    assert len(client.calls) == 1


def test_demo_engine_answers_from_knowledge(cfg, store, notifier):
    engine = DemoEngine(cfg, store, notifier)
    reply = engine.reply(WEB, "Cât costă un tuns la bărbați?")
    assert "60" in reply.text
    assert "mod demo" in reply.text

    greeting = engine.reply(WEB, "Bună!")
    assert cfg.greeting in greeting.text

    unknown = engine.reply(WEB, "Vindeți biciclete electrice?")
    assert cfg.fallback_message in unknown.text


def test_demo_engine_escalates_when_customer_asks_for_a_person(cfg, store, notifier):
    engine = DemoEngine(cfg, store, notifier)
    reply = engine.reply(WEB, "Vreau să vorbesc cu un om, am o reclamație")
    assert reply.events == ["handoff"]
    assert len(notifier.sent) == 1
