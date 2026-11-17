"""2048 mini-game: slide/merge rules, spawning, game over, ownership — and NO stats saved.

Run:  python -m pytest -q tests/test_game2048.py
"""
import asyncio
import os
import random
import sqlite3
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db     # noqa: E402
import game2048 as g      # noqa: E402

CHAT, MID, OWNER, OTHER = -100, 700, 1, 2


class FakeBot:
    def __init__(self):
        self.edits = []

    async def edit_message_text(self, chat_id, message_id, text, parse_mode=None, reply_markup=None):
        self.edits.append((text, reply_markup))


def _env(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    db.init_db()
    g.G2048_GAMES.clear()
    monkeypatch.setattr(g, "_rng", random.Random(1))


def _kb_labels(kb):
    return [[b.text for b in row] for row in kb.inline_keyboard]


def _kb_data(kb):
    return [[b.callback_data for b in row] for row in kb.inline_keyboard]


def _game(board, score=0):
    game = g._new_game(OWNER, "Nihar", CHAT)
    game.update(board=[list(r) for r in board], score=score, message_id=MID)
    g.G2048_GAMES[MID] = game
    return game


async def _press(bot, uid, action, mid=MID):
    answers = []

    async def answer(text=None, show_alert=False): answers.append(text)
    q = types.SimpleNamespace(data=f"g2048:{action}", answer=answer, message=types.SimpleNamespace(message_id=mid),
                              from_user=types.SimpleNamespace(id=uid))
    await g.game2048_callback(types.SimpleNamespace(callback_query=q), types.SimpleNamespace(bot=bot))
    return answers[-1]


def press(bot, uid, action):
    return asyncio.run(_press(bot, uid, action))


def test_slide_left_rules():
    assert g._slide_left([2, 2, 2, 2]) == ([4, 4, 0, 0], 8)
    assert g._slide_left([2, 2, 4, 0]) == ([4, 4, 0, 0], 4)
    assert g._slide_left([0, 2, 0, 2]) == ([4, 0, 0, 0], 4)
    assert g._slide_left([2, 2, 2, 0]) == ([4, 2, 0, 0], 4)
    assert g._slide_left([4, 4, 8, 0]) == ([8, 8, 0, 0], 8)       # merged tile doesn't merge again
    assert g._slide_left([2, 4, 2, 4]) == ([2, 4, 2, 4], 0)
    assert g._slide_left([0, 0, 0, 0]) == ([0, 0, 0, 0], 0)


def test_all_four_directions():
    b = [[2, 0, 2, 0], [0, 4, 0, 0], [0, 0, 0, 0], [2, 0, 0, 2]]
    assert g._move(b, "left")[0] == [[4, 0, 0, 0], [4, 0, 0, 0], [0, 0, 0, 0], [4, 0, 0, 0]]
    assert g._move(b, "right")[0] == [[0, 0, 0, 4], [0, 0, 0, 4], [0, 0, 0, 0], [0, 0, 0, 4]]
    up = g._move(b, "up")
    assert up[0] == [[4, 4, 2, 2], [0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0]] and up[1] == 4 and up[2]
    assert g._move(b, "down")[0] == [[0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0], [4, 4, 2, 2]]
    still = [[2, 4, 8, 16]] + [[0] * 4] * 3
    assert g._move(still, "left") == (still, 0, False)             # nothing moves
    assert still == [[2, 4, 8, 16], [0] * 4, [0] * 4, [0] * 4]     # input never mutated


def test_new_game_starts_with_two_tiles_and_board_is_a_button_grid(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    game = g._new_game(OWNER, "Nihar", CHAT)
    tiles = [v for r in game["board"] for v in r if v]
    assert len(tiles) == 2 and all(v in (2, 4) for v in tiles)
    game["board"] = [[2, 0, 4, 0], [128, 2, 0, 0], [0, 0, 1024, 16], [4, 8, 0, 131072]]
    text = g._text(game)
    assert "<pre>" not in text and "┌" not in text                       # no code block / no "COPY CODE"
    assert "<b>Nihar</b>" in text and "Score: <b>0</b>" in text and "Best tile: <b>131072</b>" in text
    kb = g._keyboard(game["board"])
    assert _kb_labels(kb)[:4] == [["2", "·", "4", "·"], ["128", "2", "·", "·"], ["·", "·", "1024", "16"], ["4", "8", "·", "131k"]]
    assert all(len(row) == 4 for row in kb.inline_keyboard[:4])          # always a clean 4x4
    assert _kb_data(kb)[:4] == [["g2048:noop"] * 4] * 4
    assert _kb_data(kb)[4:] == [["g2048:up"], ["g2048:left", "g2048:down", "g2048:right"], ["g2048:end"]]
    assert len(g._keyboard(game["board"], controls=False).inline_keyboard) == 4   # board only


def test_valid_move_scores_adds_exactly_one_new_tile(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    start = [[2, 2, 0, 0], [0, 0, 0, 0], [0, 0, 4, 0], [0, 0, 0, 0]]
    game = _game(start); bot = FakeBot()
    press(bot, OWNER, "left")
    slid = g._move(start, "left")[0]
    diff = [(r, c) for r in range(4) for c in range(4) if game["board"][r][c] != slid[r][c]]
    assert len(diff) == 1 and slid[diff[0][0]][diff[0][1]] == 0 and game["board"][diff[0][0]][diff[0][1]] in (2, 4)
    assert game["score"] == 4
    text, kb = bot.edits[-1]
    assert "Score: <b>4</b>" in text and kb is not None
    assert _kb_data(kb)[4:] == [["g2048:up"], ["g2048:left", "g2048:down", "g2048:right"], ["g2048:end"]]
    assert _kb_labels(kb)[:4] == [[g._cell(v) for v in row] for row in game["board"]]   # buttons show the new board


def test_invalid_move_changes_nothing_and_spawns_nothing(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    start = [[2, 4, 8, 16], [0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0]]
    game = _game(start); bot = FakeBot()
    assert "Nothing moves" in press(bot, OWNER, "left")
    assert game["board"] == start and game["score"] == 0 and bot.edits == []


def test_only_owner_can_press_and_dead_games_are_refused(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    game = _game([[2, 0, 0, 0]] + [[0] * 4] * 3); bot = FakeBot()
    before = [list(r) for r in game["board"]]
    assert "Nihar's game" in press(bot, OTHER, "right")
    assert game["board"] == before and bot.edits == []
    assert "game is over" in asyncio.run(_press(bot, OWNER, "left", mid=12345))


def test_game_over_when_full_with_no_merges(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    assert not g._has_moves([[2, 4, 2, 4], [4, 2, 4, 2], [2, 4, 2, 4], [4, 2, 4, 2]])
    assert g._has_moves([[2, 2, 4, 8], [4, 8, 16, 32], [8, 16, 32, 64], [16, 32, 64, 128]])   # full, but a merge exists
    # one move from a dead board: sliding right opens (3,0); the new tile (forced to 4) completes a checkerboard
    game = _game([[2, 4, 2, 4], [4, 2, 4, 2], [2, 4, 2, 4], [2, 4, 2, 0]], score=100); bot = FakeBot()
    monkeypatch.setattr(g, "_spawn", lambda b: b[3].__setitem__(0, 4))
    press(bot, OWNER, "right")
    text, kb = bot.edits[-1]
    assert game["board"] == [[2, 4, 2, 4], [4, 2, 4, 2], [2, 4, 2, 4], [4, 2, 4, 2]]
    assert "Game over" in text and "<b>100</b>" in text and MID not in g.G2048_GAMES
    assert len(kb.inline_keyboard) == 4 and _kb_labels(kb)[3] == ["4", "2", "4", "2"]     # final board stays, no controls
    assert "game is over" in asyncio.run(_press(bot, OWNER, "left"))      # buttons are dead afterwards


def test_reaching_2048_is_announced_and_play_continues(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    game = _game([[1024, 1024, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0]]); bot = FakeBot()
    press(bot, OWNER, "left")
    text, kb = bot.edits[-1]
    assert "You made 2048" in text and kb is not None and game["reached"] and game["score"] == 2048
    press(bot, OWNER, "down")                           # still playable afterwards
    assert "You made 2048" in bot.edits[-1][0] and MID in g.G2048_GAMES


def test_tapping_a_tile_does_nothing_for_anyone(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    game = _game([[2, 0, 0, 0]] + [[0] * 4] * 3); bot = FakeBot()
    before = [list(r) for r in game["board"]]
    assert press(bot, OWNER, "noop") is None and press(bot, OTHER, "noop") is None       # silent, no popup
    assert asyncio.run(_press(bot, OWNER, "noop", mid=999)) is None                      # even on a finished game
    assert game["board"] == before and game["score"] == 0 and bot.edits == []


def test_end_button_finishes_the_game(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    _game([[2, 0, 0, 0]] + [[0] * 4] * 3, score=40); bot = FakeBot()
    press(bot, OWNER, "end")
    text, kb = bot.edits[-1]
    assert "Game ended" in text and "<b>40</b>" in text and MID not in g.G2048_GAMES
    assert len(kb.inline_keyboard) == 4 and _kb_labels(kb)[0][0] == "2"                    # board stays, controls removed


def test_start_command_banned_and_idle_cleanup(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    sent = []

    def upd(uid):
        async def reply_text(text, parse_mode=None, reply_markup=None):
            sent.append((text, reply_markup)); return types.SimpleNamespace(message_id=MID)
        return types.SimpleNamespace(message=types.SimpleNamespace(reply_text=reply_text),
                                     effective_chat=types.SimpleNamespace(id=CHAT, type="private"),
                                     effective_user=types.SimpleNamespace(id=uid, first_name="Nihar", username=None))
    old = _game([[2, 0, 0, 0]] + [[0] * 4] * 3)
    old["last"] -= g.IDLE_SECONDS + 5
    g.G2048_GAMES[555] = old
    asyncio.run(g.game2048_start(upd(OWNER), types.SimpleNamespace(bot=FakeBot())))
    assert "2048" in sent[-1][0] and sent[-1][1] is not None
    assert 555 not in g.G2048_GAMES and g.G2048_GAMES[MID]["owner_id"] == OWNER      # idle game purged, new one stored
    db.ban_user(9, "Bad", 1)
    asyncio.run(g.game2048_start(upd(9), types.SimpleNamespace(bot=FakeBot())))
    assert "banned" in sent[-1][0]


def _snapshot(path):
    conn = sqlite3.connect(path)
    try:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        return {t: conn.execute(f"SELECT * FROM {t}").fetchall() for t in tables}
    finally:
        conn.close()


def test_whole_game_to_the_end_saves_no_stats(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    before = _snapshot(db.DB_PATH)
    game = _game([[2, 0, 0, 0]] + [[0] * 4] * 3); bot = FakeBot()
    rng = random.Random(3)

    async def play():
        for _ in range(5000):
            if MID not in g.G2048_GAMES:
                return
            await _press(bot, OWNER, rng.choice(g.DIRECTIONS))
    asyncio.run(play())
    assert MID not in g.G2048_GAMES and "Game over" in bot.edits[-1][0]      # a real game really ended
    assert _snapshot(db.DB_PATH) == before
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "game2048.py"), encoding="utf-8").read()
    assert "record_match_result" not in src and "add_achievement" not in src and "upsert" not in src
