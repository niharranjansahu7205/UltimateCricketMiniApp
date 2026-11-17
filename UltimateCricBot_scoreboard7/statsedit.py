"""
Owner-only solo-stats editing — /setstats and /resetstats.

  /setstats @rahul wins 50        set wins to exactly 50
  /setstats @rahul runs +200      add 200 runs
  /setstats @rahul losses -3      take 3 away
  /setstats wins 50               (as a reply to the player's message)
  /setstats @rahul                show that player's current stats in this group
  /resetstats @rahul              preview;  /resetstats @rahul yes  -> zero everything

Editable fields: wins, losses, runs, highest (score). "Matches" is never typed
in: it is always wins + losses (exactly how the game itself counts), so the
numbers can't contradict each other. Highest score can never exceed total runs.

Solo (/cricket) stats only, and only for the GROUP the command is used in — solo
stats are stored per group. /mycard and /globaltop add the groups together, so
edits show up there automatically. Every change is saved in stats_edit_log.
"""

import logging
import re

from telegram import Update
from telegram.ext import ContextTypes

from achievements import _resolve_target
from database import db_cursor
from style import header

logger = logging.getLogger(__name__)

MAX_VALUE = 1_000_000
FIELDS = {  # accepted spellings -> column
    "wins": "matches_won", "win": "matches_won", "won": "matches_won", "matches_won": "matches_won",
    "losses": "matches_lost", "loss": "matches_lost", "lost": "matches_lost", "matches_lost": "matches_lost",
    "runs": "total_runs", "run": "total_runs", "total_runs": "total_runs",
    "highest": "highest_score", "hs": "highest_score", "best": "highest_score", "highest_score": "highest_score",
}
LABELS = {"matches_won": "Wins", "matches_lost": "Losses", "total_runs": "Runs",
          "highest_score": "Highest score", "matches_played": "Matches"}
_COLS = ("matches_played", "matches_won", "matches_lost", "total_runs", "highest_score")


# ------------------------------------------------------------------ data ----
def _ensure_log(cur) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS stats_edit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER, target_id INTEGER, owner_id INTEGER,
            field TEXT, old_value INTEGER, new_value INTEGER,
            at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def _read(cur, chat_id, user_id) -> dict:
    cur.execute(f"SELECT {', '.join(_COLS)} FROM player_stats WHERE chat_id = ? AND user_id = ?", (chat_id, user_id))
    row = cur.fetchone()
    return {c: row[c] for c in _COLS} if row else {c: 0 for c in _COLS}


def get_solo_stats(chat_id: int, user_id: int) -> dict:
    with db_cursor() as cur:
        return _read(cur, chat_id, user_id)


def parse_value(raw: str, current: int) -> int:
    """'50' -> 50 (exact), '+5' -> current+5, '-3' -> current-3."""
    if not re.fullmatch(r"[+-]?\d{1,9}", raw or ""):
        raise ValueError("Value must be a whole number, e.g. 50, +5 or -3.")
    n = int(raw)
    return current + n if raw[0] in "+-" else n


def apply_edit(chat_id: int, user_id: int, name: str, field_word: str, raw_value: str, owner_id: int):
    """Edits one field. Returns (old_stats, new_stats). Raises ValueError with a
    user-friendly message (nothing is written in that case)."""
    word = (field_word or "").lower()
    if word in ("matches", "match", "played", "matches_played"):
        raise ValueError("Matches can't be edited directly — it is always Wins + Losses. Edit wins or losses instead.")
    col = FIELDS.get(word)
    if col is None:
        raise ValueError("Unknown field. Use: wins, losses, runs or highest.")

    with db_cursor() as cur:
        # created inside the same transaction: a rejected edit leaves nothing behind
        cur.execute(
            "INSERT INTO player_stats (chat_id, user_id, username) VALUES (?, ?, ?) "
            "ON CONFLICT(chat_id, user_id) DO UPDATE SET username=excluded.username",
            (chat_id, user_id, name),
        )
        old = _read(cur, chat_id, user_id)
        new = dict(old)
        new[col] = parse_value(raw_value, old[col])
        if not 0 <= new[col] <= MAX_VALUE:
            raise ValueError(f"{LABELS[col]} must stay between 0 and {MAX_VALUE:,}.")
        new["matches_played"] = new["matches_won"] + new["matches_lost"]
        if new["highest_score"] > new["total_runs"]:
            raise ValueError("Highest score can't be more than total runs.")
        cur.execute(
            "UPDATE player_stats SET matches_played=?, matches_won=?, matches_lost=?, total_runs=?, highest_score=? "
            "WHERE chat_id=? AND user_id=?",
            (new["matches_played"], new["matches_won"], new["matches_lost"], new["total_runs"],
             new["highest_score"], chat_id, user_id),
        )
        _ensure_log(cur)
        cur.execute(
            "INSERT INTO stats_edit_log (chat_id, target_id, owner_id, field, old_value, new_value) VALUES (?,?,?,?,?,?)",
            (chat_id, user_id, owner_id, col, old[col], new[col]),
        )
    return old, new


def reset_stats(chat_id: int, user_id: int, owner_id: int) -> dict:
    with db_cursor() as cur:
        old = _read(cur, chat_id, user_id)
        cur.execute(
            "UPDATE player_stats SET matches_played=0, matches_won=0, matches_lost=0, total_runs=0, highest_score=0 "
            "WHERE chat_id=? AND user_id=?", (chat_id, user_id),
        )
        _ensure_log(cur)
        cur.execute(
            "INSERT INTO stats_edit_log (chat_id, target_id, owner_id, field, old_value, new_value) VALUES (?,?,?,?,?,?)",
            (chat_id, user_id, owner_id, "ALL (reset)", old["matches_played"], 0),
        )
    return old


# -------------------------------------------------------------- handlers ----
def _fmt(stats: dict) -> str:
    return (f"Matches: <b>{stats['matches_played']}</b>  •  Wins: <b>{stats['matches_won']}</b>  •  "
            f"Losses: <b>{stats['matches_lost']}</b>\nRuns: <b>{stats['total_runs']}</b>  •  "
            f"Highest: <b>{stats['highest_score']}</b>")


async def _guard(update: Update) -> bool:
    from admin import is_owner  # lazy import, same pattern as game.peek_callback
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("⚠️ Only the bot owners can edit stats.")
        return False
    if update.effective_chat.type not in ("group", "supergroup"):
        await update.message.reply_text(
            "Use this in the group whose stats you want to change — solo stats are stored per group."
        )
        return False
    return True


async def setstats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _guard(update):
        return
    chat_id = update.effective_chat.id
    target_id, target_name, rest = await _resolve_target(update, context)
    usage = (
        "Usage:\n<code>/setstats @user wins 50</code>  (exact)\n<code>/setstats @user runs +200</code>  (add)\n"
        "<code>/setstats @user losses -3</code>  (subtract)\nOr reply to the player's message: "
        "<code>/setstats wins 50</code>\n\nFields: wins, losses, runs, highest. "
        "Matches = wins + losses automatically."
    )
    if target_id is None:
        await update.message.reply_text(
            usage + "\n\n(@username works only if the bot has seen that user; otherwise reply to their message.)",
            parse_mode="HTML",
        )
        return
    if len(rest) != 2:
        await update.message.reply_text(
            header("📊 Current Solo Stats") + f"<b>{target_name}</b> (this group)\n"
            + _fmt(get_solo_stats(chat_id, target_id)) + "\n\n" + usage, parse_mode="HTML")
        return
    try:
        old, new = apply_edit(chat_id, target_id, target_name, rest[0], rest[1], update.effective_user.id)
    except ValueError as e:
        await update.message.reply_text(f"❌ {e}")
        return
    changed = [f"{LABELS[c]}: <b>{old[c]}</b> → <b>{new[c]}</b>"
               for c in _COLS if old[c] != new[c]] or ["No change."]
    await update.message.reply_text(
        header("✏️ Stats Updated") + f"<b>{target_name}</b> (this group)\n" + "\n".join(changed),
        parse_mode="HTML",
    )


async def resetstats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _guard(update):
        return
    chat_id = update.effective_chat.id
    target_id, target_name, rest = await _resolve_target(update, context)
    if target_id is None:
        await update.message.reply_text(
            "Usage: <code>/resetstats @user</code> (or reply to their message), then add <code>yes</code> to confirm.",
            parse_mode="HTML")
        return
    current = get_solo_stats(chat_id, target_id)
    if [a.lower() for a in rest] != ["yes"]:
        await update.message.reply_text(
            header("⚠️ Reset Solo Stats?") + f"<b>{target_name}</b> (this group) currently has:\n" + _fmt(current)
            + "\n\nThis sets everything to 0. To confirm, repeat the command with <code>yes</code> at the end.",
            parse_mode="HTML")
        return
    reset_stats(chat_id, target_id, update.effective_user.id)
    await update.message.reply_text(
        header("🧹 Stats Reset") + f"<b>{target_name}</b>'s solo stats in this group are now 0.", parse_mode="HTML")
