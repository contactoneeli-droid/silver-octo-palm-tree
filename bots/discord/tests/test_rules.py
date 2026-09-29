import random
from datetime import datetime, timedelta, timezone

import pytest

from discordbot.config import GamesConfig, _section
from discordbot.rules import Levels, Moderation, PaidRoles, SpamTracker, WordFilter, level_for_xp, render, ticket_channel_name, xp_for_level

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def test_config_loads_all_sections(cfg):
    assert cfg.business_name == "Arena RO" and cfg.guild_id == 123456789012345678
    assert cfg.welcome.auto_role == "Membru" and cfg.moderation.warn_limit == 3
    assert cfg.tickets.support_role == "Suport"
    assert cfg.games.level_roles == {5: "Veteran", 10: "Legendă"}
    assert [r.code for r in cfg.paid_roles.roles] == ["vip", "vip-an"] and cfg.paid_roles.plan("vip").cents == 499
    assert len(cfg.trivia_questions) == 6 and cfg.trivia_questions[0]["answer"] == 1
    with pytest.raises(ValueError):
        _section(GamesConfig, {"xp_mn": 1})


def test_word_filter_folds_diacritics_and_respects_word_boundaries():
    f = WordFilter(["prost", "Idiot", "ma-ta"])
    assert f.match("Ești un PROST mare") == "prost"
    assert f.match("ce idiót") == "idiot"
    assert f.match("prostie") is None
    assert f.match("aprost") is None
    assert f.match("zi de ma-ta") == "ma-ta"
    assert WordFilter([]).match("orice") is None


def test_spam_tracker_flags_once_per_burst():
    t = SpamTracker(3, 5)
    results = [t.hit(1, NOW + timedelta(seconds=i))[0] for i in range(3)]
    assert results == [False, False, False]
    assert t.hit(1, NOW + timedelta(seconds=3)) == (True, True)
    assert t.hit(1, NOW + timedelta(seconds=4)) == (True, False)
    assert t.hit(2, NOW + timedelta(seconds=4)) == (False, False)  # other user unaffected
    assert t.hit(1, NOW + timedelta(seconds=20)) == (False, False)  # window passed, burst over


def test_moderation_judge_escalates_to_timeout(cfg, store):
    mod = Moderation(cfg.moderation, store)
    assert mod.judge(1, "salut tuturor", NOW).delete is False
    v = mod.judge(1, "ești prost", NOW)
    assert v.delete and "limbaj nepermis" in v.warn_reason and v.warnings == 1 and v.timeout_minutes == 0
    v = mod.judge(1, "intrați pe discord.gg/abc123", NOW)
    assert v.delete and "invitație" in v.warn_reason and v.warnings == 2
    v = mod.judge(1, "idiot", NOW)
    assert v.warnings == 3 and v.timeout_minutes == 60
    assert store.warnings(1) == []  # reset after the timeout
    assert mod.judge(1, "prost", NOW, exempt=True).delete is False
    v = mod.warn(2, moderator_id=9, reason="off-topic")
    assert v.warnings == 1 and store.warnings(2)[0]["moderator_id"] == 9

    cfg.moderation.enabled = False
    assert mod.judge(3, "prost", NOW).delete is False


def test_moderation_spam_warns_once(cfg, store):
    cfg.moderation.spam_messages, cfg.moderation.spam_seconds = 2, 5
    mod = Moderation(cfg.moderation, store)
    verdicts = [mod.judge(5, f"msg {i}", NOW + timedelta(seconds=i)) for i in range(5)]
    assert [v.delete for v in verdicts] == [False, False, True, True, True]
    assert [bool(v.warn_reason) for v in verdicts] == [False, False, True, False, False]
    assert "spam" in verdicts[2].warn_reason


def test_levels_math_and_awards(cfg, store):
    assert [level_for_xp(x) for x in (0, 99, 100, 399, 400, 900)] == [0, 0, 1, 1, 2, 3]
    assert xp_for_level(3) == 900
    cfg.games.xp_min = cfg.games.xp_max = 20
    lv = Levels(cfg, store, random.Random(1))
    assert lv.on_message(1, NOW) == (20, None)
    assert lv.on_message(1, NOW + timedelta(seconds=30)) == (20, None)  # cooldown
    total, up = 20, None
    for i in range(1, 5):
        total, up = lv.on_message(1, NOW + timedelta(minutes=i))
    assert total == 100 and up == 1
    total, up = lv.bonus(1, 300, NOW)
    assert total == 400 and up == 2
    assert lv.role_for_level(4) is None and lv.role_for_level(5) == "Veteran" and lv.role_for_level(12) == "Legendă"
    assert lv.card(1, "Ana").startswith("**Ana**: nivel 2, 400 XP (500 până la nivelul 3), locul 1")
    store.add_xp(2, 1000, NOW)
    assert store.rank(1) == 2 and [r["user_id"] for r in store.top()] == [2, 1]


def test_ticket_channel_name():
    assert ticket_channel_name(7, "Ana Maria Pop") == "tichet-0007-ana-maria-pop"
    assert ticket_channel_name(12, "Ștefan_99!") == "tichet-0012-stefan-99"
    assert ticket_channel_name(3, "🔥🔥") == "tichet-0003-membru"


def test_render_leaves_unknown_placeholders():
    assert render("Bun venit, {mention} pe {server}! {x}", mention="@ana", server="Arena") == "Bun venit, @ana pe Arena! {x}"


def test_paid_roles_activate_extend_and_sweep(cfg, store):
    pr = PaidRoles(cfg, store)
    vip = cfg.paid_roles.plan("vip")
    a = pr.activate(1, vip, reference="cs_1", amount_cents=499, now=NOW)
    assert a.expires_at == NOW + timedelta(days=30) and not a.already_processed
    assert pr.activate(1, vip, reference="cs_1", amount_cents=499, now=NOW).already_processed
    a = pr.activate(1, vip, reference="cs_2", amount_cents=499, now=NOW + timedelta(days=10))
    assert a.expires_at == NOW + timedelta(days=60)  # extends from the current expiry
    pr.activate(2, vip, reference="cs_3", amount_cents=499, now=NOW - timedelta(days=31))

    expired, remind = pr.sweep(NOW)
    assert [(u, p.code) for u, p in expired] == [(2, "vip")] and remind == []
    assert store.paid_role(2, "vip") is None
    expired, remind = pr.sweep(NOW + timedelta(days=58))
    assert expired == [] and [(u, p.code) for u, p, _ in remind] == [(1, "vip")]
    assert pr.sweep(NOW + timedelta(days=59)) == ([], [])  # reminded once
    assert pr.describe(vip) == "VIP lunar: 4.99 EUR / 30 zile (rolul @VIP)"
    assert store.revenue_since(NOW - timedelta(days=365)) == (3, 1497)
