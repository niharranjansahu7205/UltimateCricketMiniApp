"""Connect Four mini-game: rules, turns, win/draw, cancel — and NO stats saved.

Run:  python -m pytest -q tests/test_connectfour.py
"""
import asyncio
import os
import sqlite3
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import connectfour as c4   # noqa: E402
import database as db      # noqa: E402

CHAT, MID, A, B = -100, 900, 1, 2


class FakeBot:
    def __init__(self):
        self.edits = []

    async def edit_message_text(self, chat_id, message_id, text, parse_mode=None, reply_markup=None):
        self.edits.append((text, reply_markup))


def _env(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    db.init_db()
    c4.C4_GAMES.clear()


def _started(bot=None):
    g = c4._new_game(A, "Nihar", CHAT)
    g.update(opponent_id=B, opponent_name="Bob", stage="playing", turn_id=A, message_id=MID)
    c4.C4_GAMES[MID] = g
    return g


def _cb(fn, uid, data="c4:0", mid=MID):
    answers = []

    async def answer(text=None, show_alert=False): answers.append(text)
    q = types.SimpleNamespace(data=data, answer=answer, message=types.SimpleNamespace(message_id=mid),
                              from_user=types.SimpleNamespace(id=uid, first_name=f"U{uid}", username=None))
    return q, answers


def _play(bot, uid, col):
    q, answers = _cb(c4.connect4_move_callback, uid, f"c4:{col}")
    asyncio.run(c4.connect4_move_callback(types.SimpleNamespace(callback_query=q), types.SimpleNamespace(bot=bot)))
    return answers[-1]


def test_disc_drops_to_lowest_free_slot_and_turns_alternate():
    g = _started(); bot = FakeBot()
    _play(bot, A, 3); _play(bot, B, 3); _play(bot, A, 3)
    assert [g["board"][r][3] for r in (5, 4, 3)] == [c4.RED, c4.YELLOW, c4.RED]
    assert g["board"][2][3] is None and g["turn_id"] == B
    text, kb = bot.edits[-1]
    assert "<b>Bob</b>'s turn (🟡)" in text and c4.NUM_ROW in text
    assert [b.text for b in kb.inline_keyboard[0]] == list("1234567")
    assert [b.callback_data for b in kb.inline_keyboard[0]] == [f"c4:{i}" for i in range(7)]


def test_rejects_wrong_turn_stranger_and_full_column():
    g = _started(); bot = FakeBot()
    assert "not your turn" in _play(bot, B, 0)
    assert "not playing" in _play(bot, 99, 0)
    for i in range(6):                                   # fill column 0 alternately (no win possible)
        _play(bot, A if i % 2 == 0 else B, 0)
    assert "full" in _play(bot, A, 0)
    assert g["turn_id"] == A                             # failed move doesn't use the turn
    assert "🚫" == bot.edits[-1][1].inline_keyboard[0][0].text


def test_find_win_all_four_directions_and_no_false_positive():
    def board(cells, disc=c4.RED):
        b = [[None] * 7 for _ in range(6)]
        for r, c in cells: b[r][c] = disc
        return b
    assert c4._find_win(board([(5, 0), (5, 1), (5, 2), (5, 3)]))                 # horizontal
    assert c4._find_win(board([(2, 6), (3, 6), (4, 6), (5, 6)]))                 # vertical
    assert c4._find_win(board([(5, 0), (4, 1), (3, 2), (2, 3)]))                 # diagonal /
    assert c4._find_win(board([(2, 3), (3, 4), (4, 5), (5, 6)]))                 # diagonal \
    assert c4._find_win(board([(5, 0), (5, 1), (5, 2)])) is None                 # only three
    mixed = board([(5, 0), (5, 1), (5, 3)]); mixed[5][2] = c4.YELLOW
    assert c4._find_win(mixed) is None


def test_win_ends_game_highlights_four_and_removes_buttons():
    g = _started(); bot = FakeBot()
    for col in (0, 6, 1, 6, 2, 5, 3):                   # 🔴 plays 0,1,2,3 on the bottom row
        _play(bot, g["turn_id"], col)
    text, kb = bot.edits[-1]
    assert "Nihar" in text and "wins with 🔴" in text and text.count(c4.WIN_MARK) == 4
    assert kb is None and MID not in c4.C4_GAMES and g["stage"] == "done"
    assert "No active game" in _play(bot, A, 4)          # board is dead afterwards


def test_draw_on_full_board_without_four_in_a_row():
    g = _started(); bot = FakeBot()
    # pattern with no 4-in-line anywhere; last empty cell is top row, col 6 (🔴's colour)
    for r in range(6):
        for c in range(7):
            g["board"][r][c] = c4.RED if ((r // 2) + c) % 2 == 0 else c4.YELLOW
    g["board"][0][6] = None
    assert c4._find_win(g["board"]) is None and not c4._is_full(g["board"])
    g["turn_id"] = A
    _play(bot, A, 6)
    text, kb = bot.edits[-1]
    assert "DRAW" in text and kb is None and MID not in c4.C4_GAMES


def test_join_flow_start_message_and_guards(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    sent = {}

    async def go():
        async def reply_text(text, parse_mode=None, reply_markup=None):
            sent["text"], sent["kb"] = text, reply_markup
            return types.SimpleNamespace(message_id=MID)
        upd = types.SimpleNamespace(
            message=types.SimpleNamespace(reply_text=reply_text),
            effective_chat=types.SimpleNamespace(id=CHAT, type="supergroup"),
            effective_user=types.SimpleNamespace(id=A, first_name="Nihar", username=None))
        await c4.connect4_start(upd, types.SimpleNamespace(bot=FakeBot()))
    asyncio.run(go())
    assert "Connect Four" in sent["text"] and sent["kb"].inline_keyboard[0][0].callback_data == "c4join:accept"
    g = c4.C4_GAMES[MID]
    assert g["stage"] == "awaiting_join" and g["creator_id"] == A

    bot = FakeBot()
    q, ans = _cb(c4.connect4_join_callback, A, "c4join:accept")
    asyncio.run(c4.connect4_join_callback(types.SimpleNamespace(callback_query=q), types.SimpleNamespace(bot=bot)))
    assert "can't join your own" in ans[-1] and g["stage"] == "awaiting_join"

    db.ban_user(5, "Bad", 1)
    q, ans = _cb(c4.connect4_join_callback, 5, "c4join:accept")
    asyncio.run(c4.connect4_join_callback(types.SimpleNamespace(callback_query=q), types.SimpleNamespace(bot=bot)))
    assert "banned" in ans[-1] and g["stage"] == "awaiting_join"

    q, ans = _cb(c4.connect4_join_callback, B, "c4join:accept")
    asyncio.run(c4.connect4_join_callback(types.SimpleNamespace(callback_query=q), types.SimpleNamespace(bot=bot)))
    assert g["stage"] == "playing" and g["turn_id"] == A and g["opponent_id"] == B
    assert "has joined the game" in bot.edits[-1][0]


def test_group_only_and_cancel_rules():
    out = []

    async def go(chat_type, fn, reply=None, uid=A):
        async def reply_text(text, **kw): out.append(text); return types.SimpleNamespace(message_id=1)
        upd = types.SimpleNamespace(
            message=types.SimpleNamespace(reply_text=reply_text, reply_to_message=reply),
            effective_chat=types.SimpleNamespace(id=CHAT, type=chat_type),
            effective_user=types.SimpleNamespace(id=uid, first_name="N", username=None))
        await fn(upd, types.SimpleNamespace(bot=FakeBot()))
    c4.C4_GAMES.clear()
    asyncio.run(go("private", c4.connect4_start))
    assert "only works inside" in out[-1]
    _started()
    asyncio.run(go("supergroup", c4.cancel_connect4, uid=B)); assert "don't have any active" in out[-1]
    asyncio.run(go("supergroup", c4.cancel_connect4, reply=types.SimpleNamespace(message_id=MID), uid=B))
    assert "Only the match creator" in out[-1] and MID in c4.C4_GAMES
    asyncio.run(go("supergroup", c4.cancel_connect4))
    assert "cancelled" in out[-1] and MID not in c4.C4_GAMES


def _snapshot(path):
    conn = sqlite3.connect(path)
    try:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchall() for t in tables}, \
               {t: conn.execute(f"SELECT * FROM {t}").fetchall() for t in tables}
    finally:
        conn.close()


def test_no_stats_are_saved_for_win_or_draw(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    before = _snapshot(db.DB_PATH)
    # a full win...
    g = _started(); bot = FakeBot()
    for col in (0, 6, 1, 6, 2, 5, 3):
        _play(bot, g["turn_id"], col)
    assert "wins with" in bot.edits[-1][0]
    # ...and a draw
    g = _started()
    for r in range(6):
        for c in range(7):
            g["board"][r][c] = c4.RED if ((r // 2) + c) % 2 == 0 else c4.YELLOW
    g["board"][0][6] = None
    _play(bot, A, 6)
    assert "DRAW" in bot.edits[-1][0]
    assert _snapshot(db.DB_PATH) == before                       # every table byte-for-byte unchanged
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "connectfour.py"), encoding="utf-8").read()
    assert "record_match_result" not in src and "add_achievement" not in src and "upsert" not in src
