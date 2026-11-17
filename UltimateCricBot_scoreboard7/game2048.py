"""
2048 mini-game for UltimateCricBot — single player.

/2048 (or /game2048) starts a game in the chat. The board is a 4x4 grid of buttons; tap ⬆️ ⬇️ ⬅️ ➡️ to slide every tile. Equal tiles that bump merge into
one of double value; a new 2 (90%) or 4 (10%) appears after every move that
changed the board. Reach 2048 (you can keep going), and the game ends when the
board is full with no merges left.

Only the player who started a game can press its buttons, so in a group nobody
can mess with someone else's board. Like Tic-Tac-Toe and Connect Four, this
game keeps NO statistics — nothing is written to the database; a game only
lives in memory while it is being played (idle games are cleared after 3 hours).
"""

import logging
import random
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from database import is_banned
from style import header, player_tag

logger = logging.getLogger(__name__)

# message_id -> game state
G2048_GAMES: dict[int, dict] = {}

SIZE = 4
TARGET = 2048
IDLE_SECONDS = 3 * 60 * 60
_rng = random.Random()  # only used to place the new 2/4 tile after a move
DIRECTIONS = ("up", "down", "left", "right")


def _name(user) -> str:
    return user.first_name or user.username or "Player"


# ------------------------------------------------------------ game logic ----
def _slide_left(row):
    """Slides one row left, merging each pair at most once. Returns (new_row, gained)."""
    tiles = [v for v in row if v]
    out, gained, i = [], 0, 0
    while i < len(tiles):
        if i + 1 < len(tiles) and tiles[i] == tiles[i + 1]:
            out.append(tiles[i] * 2)
            gained += tiles[i] * 2
            i += 2
        else:
            out.append(tiles[i])
            i += 1
    return out + [0] * (len(row) - len(out)), gained


def _transpose(board):
    return [list(r) for r in zip(*board)]


def _move(board, direction):
    """Returns (new_board, points_gained, changed)."""
    if direction in ("up", "down"):
        work = _transpose(board)
    else:
        work = [list(r) for r in board]
    reverse = direction in ("right", "down")
    gained, rows = 0, []
    for row in work:
        r = row[::-1] if reverse else row
        slid, g = _slide_left(r)
        rows.append(slid[::-1] if reverse else slid)
        gained += g
    new = _transpose(rows) if direction in ("up", "down") else rows
    return new, gained, new != [list(r) for r in board]


def _empty_cells(board):
    return [(r, c) for r in range(SIZE) for c in range(SIZE) if board[r][c] == 0]


def _spawn(board):
    cells = _empty_cells(board)
    if cells:
        r, c = _rng.choice(cells)
        board[r][c] = 4 if _rng.random() < 0.1 else 2


def _has_moves(board) -> bool:
    if _empty_cells(board):
        return True
    return any(_move(board, d)[2] for d in DIRECTIONS)


def _new_game(owner_id: int, owner_name: str, chat_id: int) -> dict:
    board = [[0] * SIZE for _ in range(SIZE)]
    _spawn(board)
    _spawn(board)
    return {
        "owner_id": owner_id, "owner_name": owner_name, "chat_id": chat_id,
        "board": board, "score": 0, "reached": False,
        "message_id": None, "last": time.monotonic(),
    }


# -------------------------------------------------------------- rendering ----
def _cell(v: int) -> str:
    if v == 0:
        return "·"
    return f"{v // 1000}k" if v >= 100_000 else str(v)   # 131072 -> "131k" keeps cells within 6 wide


def _text(game, final_line=None) -> str:
    best = max(max(r) for r in game["board"])
    lines = [header("🔢 2048"), f"Player: {player_tag(game['owner_name'])}",
             f"Score: <b>{game['score']}</b>  •  Best tile: <b>{best}</b>"]
    if game["reached"]:
        lines += ["", "🏆 <b>You made 2048!</b> Keep going for a higher tile."]
    if final_line:
        lines += ["", final_line]
    return "\n".join(lines)


def _keyboard(board, controls: bool = True) -> InlineKeyboardMarkup:
    """The board itself is a 4x4 grid of buttons (always perfectly aligned, no code
    block). Tile buttons do nothing when tapped. Controls are dropped once the game
    is over, so the final board stays visible."""
    rows = [[InlineKeyboardButton(_cell(v), callback_data="g2048:noop") for v in row] for row in board]
    if controls:
        b = lambda label, d: InlineKeyboardButton(label, callback_data=f"g2048:{d}")  # noqa: E731
        rows += [[b("⬆️", "up")], [b("⬅️", "left"), b("⬇️", "down"), b("➡️", "right")], [b("🛑 End game", "end")]]
    return InlineKeyboardMarkup(rows)


async def _edit(context, game, text, keyboard):
    try:
        await context.bot.edit_message_text(
            chat_id=game["chat_id"], message_id=game["message_id"], text=text,
            parse_mode="HTML", reply_markup=keyboard,
        )
    except TelegramError:
        # e.g. Telegram rate limit when buttons are mashed; the next valid move redraws the board
        logger.debug("2048 board edit failed", exc_info=True)


def _purge_idle() -> None:
    now = time.monotonic()
    for mid in [m for m, g in G2048_GAMES.items() if now - g["last"] > IDLE_SECONDS]:
        G2048_GAMES.pop(mid, None)


# --------------------------------------------------------------- handlers ----
async def game2048_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if is_banned(user.id):
        await update.message.reply_text("🚫 You are banned from playing games with this bot.")
        return
    _purge_idle()
    game = _new_game(user.id, _name(user), update.effective_chat.id)
    sent = await update.message.reply_text(_text(game), parse_mode="HTML", reply_markup=_keyboard(game["board"]))
    game["message_id"] = sent.message_id
    G2048_GAMES[sent.message_id] = game


async def game2048_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.data.endswith(":noop"):
        await query.answer()  # tile buttons are just the board
        return
    game = G2048_GAMES.get(query.message.message_id)
    if not game:
        await query.answer("This game is over. Start a new one with /2048", show_alert=True)
        return
    if query.from_user.id != game["owner_id"]:
        await query.answer(f"This is {game['owner_name']}'s game — start your own with /2048", show_alert=True)
        return

    action = query.data.split(":", 1)[1]
    game["last"] = time.monotonic()

    if action == "end":
        G2048_GAMES.pop(game["message_id"], None)
        await query.answer("Game ended.")
        await _edit(context, game, _text(game, f"🛑 <b>Game ended.</b> Final score: <b>{game['score']}</b>"), _keyboard(game["board"], controls=False))
        return
    if action not in DIRECTIONS:
        await query.answer()
        return

    new_board, gained, changed = _move(game["board"], action)
    if not changed:
        await query.answer("Nothing moves that way — try another direction.")
        return

    game["board"] = new_board
    game["score"] += gained
    _spawn(game["board"])
    if not game["reached"] and any(v >= TARGET for row in game["board"] for v in row):
        game["reached"] = True
    await query.answer()

    if not _has_moves(game["board"]):
        G2048_GAMES.pop(game["message_id"], None)
        await _edit(context, game, _text(game, f"💀 <b>Game over!</b> Final score: <b>{game['score']}</b>"), _keyboard(game["board"], controls=False))
        return
    await _edit(context, game, _text(game), _keyboard(game["board"]))
