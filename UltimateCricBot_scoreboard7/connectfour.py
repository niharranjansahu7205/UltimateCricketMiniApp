"""
Connect Four mini-game for UltimateCricBot.

/connect4 starts a match with a Join button (same pattern as /tictac). The
player who ran it is 🔴 and moves first; whoever presses Join is 🟡. Players tap
a column number (1-7); the disc drops to the lowest free slot. First to get four
in a row (across, down or diagonally) wins; a full board is a draw.

Like Tic-Tac-Toe, this game keeps NO statistics: nothing is written to the
database (no wins, no history, no achievements) — matches only live in memory
while they are being played. Every match is keyed by its own message_id, so
several can run at once, even in the same group.
"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from database import is_banned
from style import header, player_tag

# message_id -> game state
C4_GAMES: dict[int, dict] = {}

ROWS, COLS = 6, 7
EMPTY, RED, YELLOW = "⚫", "🔴", "🟡"
NUM_ROW = "1️⃣2️⃣3️⃣4️⃣5️⃣6️⃣7️⃣"
WIN_MARK = "✅"
TITLE = "🔴 Connect Four"


def _name(user) -> str:
    return user.first_name or user.username or "Player"


def _new_game(creator_id: int, creator_name: str, chat_id: int) -> dict:
    return {
        "creator_id": creator_id,
        "creator_name": creator_name,
        "opponent_id": None,
        "opponent_name": None,
        "board": [[None] * COLS for _ in range(ROWS)],  # row 0 = top; None / 🔴 / 🟡
        "turn_id": None,
        "stage": "awaiting_join",  # awaiting_join -> playing -> done
        "message_id": None,
        "chat_id": chat_id,
    }


def _disc_for(game, user_id) -> str:
    return RED if user_id == game["creator_id"] else YELLOW


def _drop(board, col: int):
    """Lowest free row in `col`, or None if the column is full."""
    for r in range(ROWS - 1, -1, -1):
        if board[r][col] is None:
            return r
    return None


def _find_win(board):
    """The four winning cells [(r, c), ...] or None."""
    for r in range(ROWS):
        for c in range(COLS):
            disc = board[r][c]
            if disc is None:
                continue
            for dr, dc in ((0, 1), (1, 0), (1, 1), (1, -1)):
                cells = [(r + i * dr, c + i * dc) for i in range(4)]
                if all(0 <= rr < ROWS and 0 <= cc < COLS and board[rr][cc] == disc for rr, cc in cells):
                    return cells
    return None


def _is_full(board) -> bool:
    return all(cell is not None for cell in board[0])


def _board_text(board, win_cells=()) -> str:
    win = set(win_cells)
    lines = []
    for r in range(ROWS):
        lines.append("".join(
            WIN_MARK if (r, c) in win else (board[r][c] or EMPTY) for c in range(COLS)
        ))
    lines.append(NUM_ROW)
    return "\n".join(lines)


def _keyboard(board) -> InlineKeyboardMarkup:
    # a full column shows 🚫 instead of its number
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🚫" if board[0][c] is not None else str(c + 1), callback_data=f"c4:{c}")
        for c in range(COLS)
    ]])


def _vs_line(game) -> str:
    return f"{RED} {player_tag(game['creator_name'])}  vs  {YELLOW} {player_tag(game['opponent_name'])}"


async def connect4_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        await update.message.reply_text(
            "🔴 Connect Four only works inside <b>groups</b>! Add me to a group to play.",
            parse_mode="HTML",
        )
        return
    user = update.effective_user
    if is_banned(user.id):
        await update.message.reply_text("🚫 You are banned from playing games with this bot.")
        return

    game = _new_game(user.id, _name(user), chat.id)
    text = (
        header(TITLE)
        + f"A new match has been started by {player_tag(_name(user))}! ({RED})\n\n"
        + f"Press <b>Join</b> below to play as {YELLOW}."
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Join Match", callback_data="c4join:accept")]])
    sent = await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
    game["message_id"] = sent.message_id
    C4_GAMES[sent.message_id] = game


async def connect4_join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    game = C4_GAMES.get(query.message.message_id)
    if not game or game["stage"] != "awaiting_join":
        await query.answer("This match is no longer accepting players.", show_alert=True)
        return
    user = query.from_user
    if user.id == game["creator_id"]:
        await query.answer("You can't join your own match!", show_alert=True)
        return
    if is_banned(user.id):
        await query.answer("🚫 You are banned from playing games with this bot.", show_alert=True)
        return

    game["opponent_id"] = user.id
    game["opponent_name"] = _name(user)
    game["stage"] = "playing"
    game["turn_id"] = game["creator_id"]  # 🔴 always starts
    await query.answer("You joined the match!")
    await _render(context, game, joined_text=True)


async def _render(context, game, joined_text=False):
    turn_name = game["creator_name"] if game["turn_id"] == game["creator_id"] else game["opponent_name"]
    lines = [header(TITLE)]
    if joined_text:
        lines.append(f"- {player_tag(game['opponent_name'])} has joined the game!\n")
    lines += [_vs_line(game), "", _board_text(game["board"]), "",
              f"{player_tag(turn_name)}'s turn ({_disc_for(game, game['turn_id'])})"]
    await context.bot.edit_message_text(
        chat_id=game["chat_id"], message_id=game["message_id"], text="\n".join(lines),
        parse_mode="HTML", reply_markup=_keyboard(game["board"]),
    )


async def _finish(context, game, result_line: str, win_cells=()):
    lines = [header(TITLE), _vs_line(game), "", _board_text(game["board"], win_cells), "", result_line]
    game["stage"] = "done"
    C4_GAMES.pop(game["message_id"], None)   # no stats are saved — the match is simply forgotten
    await context.bot.edit_message_text(
        chat_id=game["chat_id"], message_id=game["message_id"], text="\n".join(lines), parse_mode="HTML",
    )


async def connect4_move_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    game = C4_GAMES.get(query.message.message_id)
    if not game or game["stage"] != "playing":
        await query.answer("No active game right now.", show_alert=True)
        return
    user_id = query.from_user.id
    if user_id not in (game["creator_id"], game["opponent_id"]):
        await query.answer("You're not playing this match!", show_alert=True)
        return
    if user_id != game["turn_id"]:
        await query.answer("It's not your turn!", show_alert=True)
        return

    col = int(query.data.split(":", 1)[1])
    row = _drop(game["board"], col) if 0 <= col < COLS else None
    if row is None:
        await query.answer("That column is full! Pick another one.", show_alert=True)
        return

    disc = _disc_for(game, user_id)
    game["board"][row][col] = disc
    await query.answer(f"You dropped {disc} in column {col + 1}!")

    win_cells = _find_win(game["board"])
    if win_cells:
        winner = game["creator_name"] if disc == RED else game["opponent_name"]
        await _finish(context, game, f"🎉 {player_tag(winner)} wins with {disc}! 🎉", win_cells)
        return
    if _is_full(game["board"]):
        await _finish(context, game, "🤝 <b>It's a DRAW!</b>")
        return

    game["turn_id"] = game["opponent_id"] if user_id == game["creator_id"] else game["creator_id"]
    await _render(context, game)


async def cancel_connect4(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel a pending/ongoing Connect Four match (same pattern as /canceltictac)."""
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if update.message.reply_to_message:
        mid = update.message.reply_to_message.message_id
        game = C4_GAMES.get(mid)
        if not game or game["chat_id"] != chat_id:
            await update.message.reply_text("That message isn't an active Connect Four match.")
            return
        if game["creator_id"] != user_id:
            await update.message.reply_text("Only the match creator can cancel it.")
            return
        C4_GAMES.pop(mid, None)
        await update.message.reply_text("❌ Connect Four match cancelled.")
        return

    own = [mid for mid, g in C4_GAMES.items() if g["chat_id"] == chat_id and g["creator_id"] == user_id]
    if not own:
        await update.message.reply_text(
            "You don't have any active Connect Four match here. "
            "(Reply to a specific match message with /cancelconnect4 to cancel it.)"
        )
        return
    if len(own) > 1:
        await update.message.reply_text(
            "You have multiple active Connect Four matches in this group. "
            "Reply to the specific match's message with /cancelconnect4 to cancel that one."
        )
        return
    C4_GAMES.pop(own[0], None)
    await update.message.reply_text("❌ Connect Four match cancelled.")
