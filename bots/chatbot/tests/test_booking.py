from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from conftest import FakeAnthropic, response, text_block, tool_use_block

from chatbot.booking import BookingCalendar, BookingError
from chatbot.engine import ClaudeEngine, build_system_prompt
from chatbot.reminders import ReminderLoop
from chatbot.store import Session
from chatbot.telegram import TelegramBot

TZ = ZoneInfo("Europe/Bucharest")
# Tuesday 2026-10-06, 10:00 local time; the salon is open 09:00-20:00 on weekdays.
NOW = datetime(2026, 10, 6, 10, 0, tzinfo=TZ)
TG = Session("telegram", "555", "Ana")
WEB = Session("web", "w1")


@pytest.fixture
def cal(cfg, store):
    return BookingCalendar(store, cfg.booking)


def test_config_parses_booking_section(cfg):
    b = cfg.booking
    assert b.enabled and b.capacity == 2 and b.slot_minutes == 30
    assert b.hours["sat"] == ["09:00-16:00"] and b.hours["sun"] == []
    assert [s.name for s in b.services][:2] == ["Tuns femei", "Tuns și coafat"]


def test_free_slots_respect_notice_hours_and_duration(cal):
    tuns = cal.service("tuns femei")  # 45 min
    slots = cal.free_slots(NOW.date(), tuns, NOW)
    assert slots[0].strftime("%H:%M") == "11:00"  # 60 min notice from 10:00
    assert slots[-1].strftime("%H:%M") == "19:00"  # 19:00 + 45 min <= 20:00; 19:30 would not fit

    lung = cal.service("Balayage")  # 180 min
    long_slots = cal.free_slots(NOW.date(), lung, NOW)
    assert long_slots[-1].strftime("%H:%M") == "17:00"

    sunday = NOW.date() + timedelta(days=5)
    assert cal.free_slots(sunday, tuns, NOW) == []

    with pytest.raises(BookingError):
        cal.free_slots(NOW.date() - timedelta(days=1), tuns, NOW)
    with pytest.raises(BookingError):
        cal.free_slots(NOW.date() + timedelta(days=90), tuns, NOW)


def test_capacity_and_overlap(cal):
    tuns = cal.service("Tuns femei")
    day = NOW.date().isoformat()
    cal.book(TG, "Ana", "0740000001", "Tuns femei", day, "11:00", now=NOW)
    assert any(s.strftime("%H:%M") == "11:00" for s in cal.free_slots(NOW.date(), tuns, NOW))  # capacity 2
    cal.book(WEB, "Bianca", "0740000002", "Tuns femei", day, "11:00", now=NOW)
    times = [s.strftime("%H:%M") for s in cal.free_slots(NOW.date(), tuns, NOW)]
    assert "11:00" not in times and "11:30" not in times  # both chairs busy until 11:45
    assert "12:00" in times

    with pytest.raises(BookingError) as e:
        cal.book(TG, "Carmen", "0740000003", "Tuns femei", day, "11:00", now=NOW)
    assert "12:00" in str(e.value)  # alternatives are offered


def test_unknown_service_bad_date_and_cancel(cal):
    with pytest.raises(BookingError):
        cal.service("epilare")
    with pytest.raises(BookingError):
        cal.parse_date("6 octombrie")

    appt = cal.book(TG, "Ana", "0740 000 001", "tuns barbati", NOW.date().isoformat(), "12:00", now=NOW)
    assert appt.status == "confirmed" and appt.service == "Tuns bărbați"

    with pytest.raises(BookingError):
        cal.cancel(appt.id, "0799999999", WEB)  # wrong contact, other chat
    cancelled = cal.cancel(appt.id, "0740000001", WEB)  # digits match
    assert cancelled.status == "cancelled"
    assert cal.day_schedule(NOW.date()) == []


def test_pending_when_auto_confirm_is_off(cfg, store):
    cfg.booking.auto_confirm = False
    cal = BookingCalendar(store, cfg.booking)
    appt = cal.book(TG, "Ana", "0740000001", "Coafat", NOW.date().isoformat(), "13:00", now=NOW)
    assert appt.status == "pending"
    assert cal.confirm(appt.id).status == "confirmed"


def test_reminders_go_to_telegram_customers_once(cal, cfg):
    class Api:
        sent = []

        def send_message(self, chat_id, text):
            self.sent.append((chat_id, text))

    api = Api()
    loop = ReminderLoop(cal, cfg.business_name, api)
    cal.book(TG, "Ana", "0740000001", "Tuns femei", NOW.date().isoformat(), "11:30", now=NOW)
    cal.book(WEB, "Web", "0740000009", "Tuns femei", NOW.date().isoformat(), "11:30", now=NOW)
    cal.book(TG, "Târziu", "0740000004", "Tuns femei", NOW.date().isoformat(), "15:00", now=NOW)

    sent = loop.tick(NOW)  # window: 2 hours -> only the 11:30 ones
    assert [a.name for a in sent] == ["Ana"]
    assert api.sent[0][0] == "555" and "11:30" in api.sent[0][1]
    assert loop.tick(NOW) == []  # not sent twice; the web one was marked handled too


def _open_day_next_week(cal):
    """A weekday at least 7 days from the real clock, so 11:00 is free whatever time it is now."""
    day = cal.now().date() + timedelta(days=7)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def test_claude_engine_books_through_tools(cfg, store, notifier, cal):
    booking_day = _open_day_next_week(cal)
    day = booking_day.isoformat()
    client = FakeAnthropic(
        response(tool_use_block("get_free_slots", {"date": day, "service": "Tuns femei"}, id="t1")),
        response(
            text_block("Te programez."),
            tool_use_block(
                "book_appointment",
                {"name": "Ana", "contact": "0740000001", "service": "Tuns femei", "date": day, "time": "11:00", "notes": ""},
                id="t2",
            ),
        ),
        response(text_block("Gata: marți 6 oct, 11:00, Tuns femei. Numărul programării e 1.")),
    )
    engine = ClaudeEngine(cfg, store, notifier, calendar=cal, client=client)
    reply = engine.reply(TG, "Vreau tuns marți la 11, Ana 0740000001")

    assert reply.events == ["booking"]
    assert "Numărul programării" in reply.text
    assert cal.day_schedule(booking_day)[0].name == "Ana"
    assert any("Programare nouă" in n for n in notifier.sent)

    first_call = client.calls[0]
    assert {t["name"] for t in first_call["tools"]} >= {"get_free_slots", "book_appointment", "cancel_appointment"}
    assert "get_free_slots" in first_call["system"][0]["text"]
    assert "Tuns femei: 45 min" in first_call["system"][0]["text"]
    # The current date/time decorates the last user turn only.
    last_user = first_call["messages"][-1]
    assert last_user["content"][0]["text"].startswith("(Acum este ")
    assert last_user["content"][1]["text"].startswith("Vreau tuns")
    slots_result = client.calls[1]["messages"][-1]["content"][0]
    assert slots_result["is_error"] is False and "11:00" in slots_result["content"]


def test_tool_errors_are_returned_to_the_model(cfg, store, notifier, cal):
    client = FakeAnthropic(
        response(tool_use_block("get_free_slots", {"date": "mâine", "service": "Tuns femei"})),
        response(text_block("Îmi dai te rog data exactă?")),
    )
    engine = ClaudeEngine(cfg, store, notifier, calendar=cal, client=client)
    reply = engine.reply(TG, "ai loc mâine?")
    result = client.calls[1]["messages"][-1]["content"][0]
    assert result["is_error"] is True and "AAAA-LL-ZZ" in result["content"]
    assert reply.events == []


def test_prompt_without_booking_has_no_booking_rules(cfg):
    assert "get_free_slots" not in build_system_prompt(cfg, None)


def test_owner_telegram_commands(cfg, store, notifier, cal):
    class Api:
        sent = []

        def send_message(self, chat_id, text):
            self.sent.append((chat_id, text))

        def typing(self, chat_id):
            pass

    from chatbot.engine import DemoEngine

    engine = DemoEngine(cfg, store, notifier, calendar=cal)
    bot = TelegramBot(Api(), engine, owner_chat_id="999")
    appt = cal.book(TG, "Ana", "0740000001", "Coafat", NOW.date().isoformat(), "14:00", now=NOW)

    out = bot.owner_command(f"/programari {NOW.date().isoformat()}")
    assert "Ana" in out and "14:00" in out
    assert "Anulată" in bot.owner_command(f"/anuleaza {appt.id}")
    assert "Nicio programare" in bot.owner_command(f"/programari {NOW.date().isoformat()}")
    assert bot.owner_command("/tuns") is None  # not an owner command, falls through to the engine
    assert "Folosește" in bot.owner_command("/confirma")
