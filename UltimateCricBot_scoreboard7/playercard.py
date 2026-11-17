"""
/mycard — premium player card (PNG image) with profile picture, name and stats.

Bot owners get a gold "Ultimate" version of the card (render_player_card_gold);
everyone else gets the standard card. Stats are the player's career totals across ALL groups, combining Solo
(/cricket, table player_stats) and Team Cricket (table team_player_stats), plus
the number of tournament achievements. Every number comes straight from the
database — nothing is invented. The tier badge is only a label derived from
total wins.

render_player_card(data, avatar_bytes, bot_username) -> PNG bytes is pure
drawing (Pillow only), reusing the look & helpers of resultcard.py, so it is
easy to test. The Telegram handler never raises: if the profile picture is
missing/hidden a clean initial-letter avatar is drawn instead.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import time

from PIL import Image, ImageDraw, ImageFilter, ImageOps
from telegram import Update
from telegram.ext import ContextTypes

from database import db_cursor, get_achievements
from resultcard import (
    GOLD, GREY, WHITE, W, H,
    _ball, _clean, _crown, _fit, _font, _gradient_bg, _panel, _spaced,
)

logger = logging.getLogger(__name__)

AVATAR_SIZE = 300
COOLDOWN_SECONDS = 15
_last_used: dict[int, float] = {}


# ------------------------------------------------------------------ data ----
def get_card_stats(user_id: int) -> dict:
    """Career totals for one user across every group (Solo + Team Cricket)."""
    with db_cursor() as cur:
        cur.execute(
            "SELECT COALESCE(SUM(matches_played),0) m, COALESCE(SUM(matches_won),0) w, "
            "COALESCE(SUM(matches_lost),0) l, COALESCE(SUM(total_runs),0) r, "
            "COALESCE(MAX(highest_score),0) hs FROM player_stats WHERE user_id = ?",
            (user_id,),
        )
        solo = cur.fetchone()
        cur.execute(
            "SELECT COALESCE(SUM(matches),0) m, COALESCE(SUM(wins),0) w, COALESCE(SUM(losses),0) l, "
            "COALESCE(SUM(runs),0) r, COALESCE(SUM(wickets),0) wk, COALESCE(MAX(highest_score),0) hs, "
            "COALESCE(SUM(potm_awards),0) p FROM team_player_stats WHERE user_id = ?",
            (user_id,),
        )
        team = cur.fetchone()
    matches = solo["m"] + team["m"]
    wins = solo["w"] + team["w"]
    return {
        "matches": matches,
        "wins": wins,
        "losses": solo["l"] + team["l"],
        "win_rate": (wins / matches * 100) if matches else 0.0,
        "runs": solo["r"] + team["r"],
        "highest": max(solo["hs"], team["hs"]),
        "wickets": team["wk"],
        "potm": team["p"],
        "trophies": len(get_achievements(user_id)),
    }


def tier_for(matches: int, wins: int) -> str:
    if matches == 0:
        return "NEW PLAYER"
    if wins < 5:
        return "ROOKIE"
    if wins < 15:
        return "PRO"
    if wins < 40:
        return "ELITE"
    return "LEGEND"


# --------------------------------------------------------------- drawing ----
def _circle_avatar(avatar_bytes: bytes | None, name: str, s: int = AVATAR_SIZE) -> Image.Image:
    """s x s RGBA circle (default AVATAR_SIZE): the real photo, or an initial."""
    img = None
    if avatar_bytes:
        try:
            img = ImageOps.fit(Image.open(io.BytesIO(avatar_bytes)).convert("RGB"), (s, s), Image.LANCZOS)
        except Exception:
            logger.debug("Profile photo could not be decoded; using initial avatar.", exc_info=True)
            img = None
    if img is None:
        img = Image.new("RGB", (s, s))
        d = ImageDraw.Draw(img)
        for y in range(s):
            t = y / (s - 1)
            d.line([(0, y), (s, y)], fill=(int(30 + 20 * t), int(80 - 30 * t), int(70 - 10 * t)))
        letter = (name[:1] or "?").upper()
        d.text((s // 2, s // 2 - s // 50), letter, font=_font(True, s // 2), fill=GOLD, anchor="mm")
    big = Image.new("L", (s * 4, s * 4), 0)
    ImageDraw.Draw(big).ellipse([0, 0, s * 4 - 1, s * 4 - 1], fill=255)
    mask = big.resize((s, s), Image.LANCZOS)
    out = img.convert("RGBA")
    out.putalpha(mask)
    return out


def _stat_tile(base, x, y, w, h, value, label, highlight=False):
    base = _panel(base, (x, y, x + w, y + h), (255, 255, 255, 24),
                  outline=GOLD if highlight else (255, 255, 255, 70), width=3 if highlight else 2,
                  radius=28, shadow=False)
    d = ImageDraw.Draw(base)
    f, v = _fit(d, str(value), True, 56, w - 30, 28)
    d.text((x + w // 2, y + h // 2 - 12), v, font=f, fill=GOLD if highlight else WHITE, anchor="mm")
    d.text((x + w // 2, y + h - 28), label, font=_font(True, 20), fill=GREY, anchor="mm")
    return base


def render_player_card(data: dict, avatar_bytes: bytes | None = None, bot_username: str | None = None) -> bytes:
    """data = {"name", "username" | None, "stats": get_card_stats(...)}"""
    st = data["stats"]
    name = _clean(data.get("name"), True, "Player")
    base = _gradient_bg()
    d = ImageDraw.Draw(base)

    # header
    _spaced(d, (W // 2, 78), "ULTIMATECRIC", _font(True, 40), GOLD, 10)
    d.text((W // 2, 140), "PLAYER CARD", font=_font(True, 30), fill=WHITE, anchor="mm")
    d.line([(W // 2 - 120, 184), (W // 2 + 120, 184)], fill=GOLD, width=4)
    _ball(d, W // 2 - 330, 140, 14)
    _ball(d, W // 2 + 330, 140, 14)

    # main card
    base = _panel(base, (50, 215, W - 50, 1290), (255, 255, 255, 20), outline=GOLD, width=5, radius=50)
    d = ImageDraw.Draw(base)

    # avatar with glow + gold ring
    cx, cy = W // 2, 405
    glow = Image.new("RGBA", base.size, (0, 0, 0, 0))
    r = AVATAR_SIZE // 2 + 26
    ImageDraw.Draw(glow).ellipse([cx - r, cy - r, cx + r, cy + r], fill=(245, 197, 66, 120))
    base = Image.alpha_composite(base, glow.filter(ImageFilter.GaussianBlur(26)))
    d = ImageDraw.Draw(base)
    ring = AVATAR_SIZE // 2 + 10
    d.ellipse([cx - ring, cy - ring, cx + ring, cy + ring], fill=GOLD)
    d.ellipse([cx - ring + 8, cy - ring + 8, cx + ring - 8, cy + ring - 8], fill=(8, 18, 44))
    base.alpha_composite(_circle_avatar(avatar_bytes, name), (cx - AVATAR_SIZE // 2, cy - AVATAR_SIZE // 2))
    d = ImageDraw.Draw(base)

    # name, @username, tier
    f, nm = _fit(d, name, True, 70, W - 200, 32)
    d.text((W // 2, 650), nm, font=f, fill=WHITE, anchor="mm")
    uname = _clean(data.get("username"), False)
    if uname:
        d.text((W // 2, 704), f"@{uname}", font=_font(False, 30), fill=GREY, anchor="mm")
    tier = tier_for(st["matches"], st["wins"])
    tf = _font(True, 28)
    tw = d.textlength(tier, font=tf) + 90
    pill = (W // 2 - tw / 2, 735, W // 2 + tw / 2, 791)
    base = _panel(base, pill, GOLD, radius=28, shadow=False)
    d = ImageDraw.Draw(base)
    _crown(d, int(pill[0]) + 36, 763, 14, (60, 40, 0))
    d.text((W // 2 + 18, 763), tier, font=tf, fill=(60, 40, 0), anchor="mm")

    # stats grid (3 x 3)
    tiles = [
        (st["matches"], "MATCHES", False), (st["wins"], "WINS", True), (st["losses"], "LOSSES", False),
        (f"{st['win_rate']:.1f}%", "WIN RATE", True), (st["runs"], "TOTAL RUNS", False), (st["highest"], "HIGHEST SCORE", True),
        (st["wickets"], "WICKETS", False), (st["potm"], "PLAYER OF MATCH", False), (st["trophies"], "TROPHIES", False),
    ]
    tw_, th_, gap = 296, 130, 20
    x0 = (W - (3 * tw_ + 2 * gap)) // 2
    y0 = 840
    for i, (val, label, hl) in enumerate(tiles):
        base = _stat_tile(base, x0 + (i % 3) * (tw_ + gap), y0 + (i // 3) * (th_ + gap), tw_, th_, val, label, hl)
    d = ImageDraw.Draw(base)

    foot = "Career stats  •  all groups"
    if bot_username:
        foot += f"  •  @{_clean(bot_username, False)}"
    d.text((W // 2, H - 44), foot, font=_font(False, 24), fill=(120, 140, 170), anchor="mm")

    out = io.BytesIO()
    base.convert("RGB").save(out, format="PNG", optimize=True)
    return out.getvalue()


# ---------------------------------------------------------- gold (owners) ----
def _vgrad(size, stops):
    """Vertical gradient; stops = [(0..1, (r, g, b)), ...]."""
    w, h = size
    im = Image.new("RGB", size)
    d = ImageDraw.Draw(im)
    for y in range(h):
        t = y / max(1, h - 1)
        for i in range(len(stops) - 1):
            t0, c0 = stops[i]
            t1, c1 = stops[i + 1]
            if t0 <= t <= t1:
                k = (t - t0) / (t1 - t0 or 1)
                d.line([(0, y), (w, y)], fill=tuple(int(c0[j] + (c1[j] - c0[j]) * k) for j in range(3)))
                break
    return im


def _rr_mask(size, radius):
    m = Image.new("L", (size[0] * 2, size[1] * 2), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size[0] * 2 - 1, size[1] * 2 - 1], radius=radius * 2, fill=255)
    return m.resize(size, Image.LANCZOS)


def render_player_card_gold(data: dict, avatar_bytes: bytes | None = None, bot_username: str | None = None) -> bytes:
    """Owner-only 'Ultimate Gold' version of the card. Same data and signature as
    render_player_card; big number = win rate, rest are the same career stats."""
    st = data["stats"]
    name = _clean(data.get("name"), True, "Player")
    DARK, MID, LIGHT = (52, 34, 4), (92, 62, 8), (255, 244, 200)

    base = _vgrad((W, H), [(0, (10, 10, 16)), (1, (34, 26, 8))]).convert("RGBA")
    x0, y0, x1, y1 = 110, 100, 970, 1340
    cw, ch = x1 - x0, y1 - y0
    glow = Image.new("RGBA", base.size, (0, 0, 0, 0))
    ImageDraw.Draw(glow).rounded_rectangle([x0, y0, x1, y1], radius=90, fill=(245, 190, 60, 255))
    base = Image.alpha_composite(base, glow.filter(ImageFilter.GaussianBlur(40)))

    card = _vgrad((cw, ch), [(0, (252, 234, 160)), (0.45, (222, 171, 56)), (1, (146, 99, 18))]).convert("RGBA")
    ov = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    od.polygon([(cw * .55, 0), (cw * .85, 0), (cw * .35, ch), (cw * .05, ch)], fill=(255, 255, 255, 38))   # sheen
    for i in range(0, ch, 10):
        od.line([(0, i), (cw, i)], fill=(255, 255, 255, 10))                                                # pinstripes
    od.rounded_rectangle([30, 30, cw - 31, ch - 31], radius=64, outline=LIGHT + (150,), width=2)
    card = Image.alpha_composite(card, ov)
    ImageDraw.Draw(card).rounded_rectangle([16, 16, cw - 17, ch - 17], radius=76, outline=MID, width=4)
    base.paste(card, (x0, y0), _rr_mask((cw, ch), 90))
    d = ImageDraw.Draw(base)

    # win-rate "rating" block, tier, matches
    f, rate = _fit(d, f"{round(st['win_rate'])}", True, 190, 225, 90)
    d.text((215, 262), rate, font=f, fill=DARK, anchor="lm")
    d.text((215, 385), "WIN %", font=_font(True, 34), fill=DARK, anchor="lm")
    d.line([(215, 422), (330, 422)], fill=DARK, width=4)
    tier = tier_for(st["matches"], st["wins"])
    tf = _font(True, 38)
    d.text((215, 462), tier, font=tf, fill=DARK, anchor="lm")
    _crown(d, 215 + int(d.textlength(tier, font=tf)) + 38, 462, 17, DARK)
    d.text((215, 520), f"{st['matches']} MATCHES", font=_font(True, 24), fill=MID, anchor="lm")

    # profile picture
    cx, cy, s = 655, 410, 380
    d.ellipse([cx - s // 2 - 14, cy - s // 2 - 14, cx + s // 2 + 14, cy + s // 2 + 14], fill=DARK)
    d.ellipse([cx - s // 2 - 8, cy - s // 2 - 8, cx + s // 2 + 8, cy + s // 2 + 8], fill=(255, 238, 170))
    base.alpha_composite(_circle_avatar(avatar_bytes, name, s), (cx - s // 2, cy - s // 2))
    d = ImageDraw.Draw(base)

    # name + username
    f, nm = _fit(d, name.upper(), True, 100, 700, 40)
    d.text((540, 730), nm, font=f, fill=DARK, anchor="mm")
    uname = _clean(data.get("username"), False)
    if uname:
        d.text((540, 800), f"@{uname}", font=_font(False, 30), fill=MID, anchor="mm")
    d.line([(230, 850), (850, 850)], fill=DARK, width=4)
    d.line([(230, 860), (850, 860)], fill=LIGHT, width=2)

    # stats 3 x 2
    cols = [300, 540, 780]
    tiles = [(st["matches"], "MATCHES"), (st["wins"], "WINS"), (st["losses"], "LOSSES"),
             (st["runs"], "RUNS"), (st["highest"], "HIGHEST"), (st["wickets"], "WICKETS")]
    for i, (v, lab) in enumerate(tiles):
        x, y = cols[i % 3], 935 + (i // 3) * 140
        vf, vt = _fit(d, str(v), True, 70, 220, 30)
        d.text((x, y), vt, font=vf, fill=DARK, anchor="mm")
        d.text((x, y + 50), lab, font=_font(True, 24), fill=MID, anchor="mm")
    for x in (420, 660):
        d.line([(x, 895), (x, 1175)], fill=MID, width=2)
    d.line([(230, 1005), (850, 1005)], fill=MID, width=2)
    d.text((540, 1235), f"PLAYER OF MATCH  {st['potm']}     •     TROPHIES  {st['trophies']}",
           font=_font(True, 27), fill=DARK, anchor="mm")
    _spaced(d, (540, 1290), "ULTIMATECRIC", _font(True, 24), MID, 8)

    out = io.BytesIO()
    base.convert("RGB").save(out, format="PNG", optimize=True)
    return out.getvalue()


# --------------------------------------------------------------- handler ----
async def _fetch_avatar(bot, user_id: int) -> bytes | None:
    """The user's current profile photo, or None (no photo / hidden / error)."""
    try:
        photos = await bot.get_user_profile_photos(user_id, limit=1)
        if not photos.total_count or not photos.photos:
            return None
        tg_file = await bot.get_file(photos.photos[0][-1].file_id)
        return bytes(await tg_file.download_as_bytearray())
    except Exception:
        logger.debug("Could not fetch profile photo for %s", user_id, exc_info=True)
        return None


async def mycard_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    now = time.monotonic()
    if now - _last_used.get(user.id, -COOLDOWN_SECONDS) < COOLDOWN_SECONDS:
        await update.message.reply_text("⏳ Your card is cooling down — try again in a few seconds.")
        return
    _last_used[user.id] = now

    try:
        stats = get_card_stats(user.id)
        avatar = await _fetch_avatar(context.bot, user.id)
        name = user.first_name or user.username or "Player"
        from admin import is_owner  # lazy import, same pattern as elsewhere
        renderer = render_player_card_gold if is_owner(user.id) else render_player_card
        png = await asyncio.to_thread(
            renderer,
            {"name": name, "username": user.username, "stats": stats},
            avatar,
            os.environ.get("BOT_USERNAME"),
        )
    except Exception:
        logger.exception("Could not build /mycard for %s", user.id)
        await update.message.reply_text("⚠️ Couldn't create your card right now. Please try again later.")
        return

    caption = "🏏 <b>Your UltimateCric Player Card</b>"
    if stats["matches"] == 0:
        caption += "\n\nNo matches yet — type /cricket to start your career!"
    await update.message.reply_photo(photo=png, caption=caption, parse_mode="HTML")
