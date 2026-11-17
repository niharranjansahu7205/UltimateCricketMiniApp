"""Owner-only /setstats and /resetstats (solo stats, per group).

Run:  python -m pytest -q tests/test_statsedit.py
"""
import asyncio
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import admin             # noqa: E402
import database as db    # noqa: E402
import playercard        # noqa: E402
import statsedit as se   # noqa: E402

OWNER, G1, G2, RAHUL = 111, -100, -200, 42


def _env(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(admin, "OWNER_IDS", {OWNER})
    db.init_db()
    db.record_known_user(RAHUL, "rahul", "Rahul")


def _seed():
    for _ in range(3): db.record_match_result(G1, RAHUL, "Rahul", 20, True)    # 3 wins
    db.record_match_result(G1, RAHUL, "Rahul", 35, False)                     # 1 loss -> 4 matches, 95 runs, hs 35
    db.record_match_result(G2, RAHUL, "Rahul", 50, True)                      # other group


def _call(fn, text_args, uid=OWNER, chat=G1, ctype="supergroup", reply_to=None):
    out = []

    async def reply_text(t, **kw): out.append(t)
    reply = types.SimpleNamespace(from_user=types.SimpleNamespace(id=reply_to, first_name="Replied", username=None)) \
        if reply_to else None
    upd = types.SimpleNamespace(
        message=types.SimpleNamespace(reply_text=reply_text, reply_to_message=reply),
        effective_user=types.SimpleNamespace(id=uid),
        effective_chat=types.SimpleNamespace(id=chat, type=ctype))
    asyncio.run(fn(upd, types.SimpleNamespace(args=text_args.split())))
    return out[-1]


def _row(chat=G1, uid=RAHUL):
    r = db.get_player_stats(chat, uid)
    return None if r is None else (r["matches_played"], r["matches_won"], r["matches_lost"], r["total_runs"], r["highest_score"])


def test_exact_set_relative_add_and_subtract(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path); _seed()
    assert _row() == (4, 3, 1, 95, 35)
    msg = _call(se.setstats_command, "@rahul wins 50")
    assert "Stats Updated" in msg and "<b>3</b> → <b>50</b>" in msg
    assert _row() == (51, 50, 1, 95, 35)                       # matches auto = wins + losses
    _call(se.setstats_command, "@rahul runs +200");  assert _row()[3] == 295
    _call(se.setstats_command, "@rahul losses -1");  assert _row()[:3] == (50, 50, 0)
    _call(se.setstats_command, "@rahul highest 120"); assert _row()[4] == 120
    _call(se.setstats_command, "@rahul hs 0");        assert _row()[4] == 0                # alias


def test_only_this_group_changes_and_cards_see_it(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path); _seed()
    _call(se.setstats_command, "@rahul wins 10")
    assert _row(G2) == (1, 1, 0, 50, 50)                       # other group untouched
    s = playercard.get_card_stats(RAHUL)                        # /mycard totals both groups
    assert s["wins"] == 10 + 1 and s["matches"] == (10 + 1) + 1 + 0


def test_reply_and_numeric_id_targets(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    _call(se.setstats_command, "runs 77", reply_to=99)
    assert _row(uid=99)[3] == 77
    _call(se.setstats_command, "55 wins 3")
    assert _row(uid=55)[1] == 3


def test_validation_rejects_bad_edits_and_writes_nothing(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path); _seed()
    before = _row()
    for args, needle in [("@rahul matches 99", "Wins + Losses"), ("@rahul wins -10", "between 0"),
                         ("@rahul wins abc", "whole number"), ("@rahul wins 99999999", "between 0"),
                         ("@rahul highest 500", "more than total runs"), ("@rahul runs 10", "more than total runs"),
                         ("@rahul speed 5", "Unknown field")]:
        assert needle in _call(se.setstats_command, args), args
    assert _row() == before
    assert "❌" in _call(se.setstats_command, "77 wins -4")
    assert _row(uid=77) is None                                 # rejected edit left no empty row behind


def test_view_current_stats_and_usage(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path); _seed()
    msg = _call(se.setstats_command, "@rahul")
    assert "Current Solo Stats" in msg and "<b>95</b>" in msg
    assert "Usage" in _call(se.setstats_command, "")
    assert "Usage" in _call(se.setstats_command, "@nobody wins 5")


def test_owner_only_and_group_only(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path); _seed()
    before = _row()
    for fn in (se.setstats_command, se.resetstats_command):
        assert "Only the bot owners" in _call(fn, "@rahul wins 1 yes", uid=5)
        assert "per group" in _call(fn, "@rahul wins 1 yes", ctype="private")
    assert _row() == before


def test_reset_needs_confirmation_and_only_hits_this_player_and_group(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path); _seed()
    db.record_match_result(G1, 7, "Other", 10, True)
    assert "Reset Solo Stats?" in _call(se.resetstats_command, "@rahul")
    assert _row() == (4, 3, 1, 95, 35)
    assert "Stats Reset" in _call(se.resetstats_command, "@rahul yes")
    assert _row() == (0, 0, 0, 0, 0)
    assert _row(G2) == (1, 1, 0, 50, 50) and _row(uid=7) == (1, 1, 0, 10, 10)
    db.record_match_result(G1, RAHUL, "Rahul", 6, True)         # game keeps counting on top afterwards
    assert _row() == (1, 1, 0, 6, 6)


def test_changes_are_logged(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path); _seed()
    _call(se.setstats_command, "@rahul wins 9")
    _call(se.resetstats_command, "@rahul yes")
    with db.db_cursor() as cur:
        rows = [dict(r) for r in cur.execute("SELECT * FROM stats_edit_log ORDER BY id")]
    assert [(r["field"], r["old_value"], r["new_value"]) for r in rows] == [("matches_won", 3, 9), ("ALL (reset)", 10, 0)]
    assert all(r["owner_id"] == OWNER and r["target_id"] == RAHUL and r["chat_id"] == G1 for r in rows)
