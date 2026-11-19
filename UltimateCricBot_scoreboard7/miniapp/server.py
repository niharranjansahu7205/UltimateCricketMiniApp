"""aiohttp web server for the Mini App. Runs inside the bot's own event loop (same
process, same Railway service) and only ever READS the database."""

import asyncio
import hashlib
import hmac
import logging
import os
import time
from pathlib import Path

from aiohttp import web

import database
from . import data
from .auth import validate_init_data

logger = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"


# ---------------------------------------------------------------- avatars
# <img> tags can't send our Authorization header, so every avatar URL carries a short
# signature (made with a key derived from the bot token). Only users that appear in a
# response we generated can be fetched, and nobody can enumerate arbitrary Telegram ids.
AVATAR_TTL, NEG_TTL, CACHE_MAX = 6 * 3600, 600, 600
_AVATARS: dict[int, tuple[float, bytes | None]] = {}
_SEM = asyncio.Semaphore(4)


def avatar_sig(bot_token: str, uid: int) -> str:
    key = hashlib.sha256(b"avatar:" + bot_token.encode()).digest()
    return hmac.new(key, str(int(uid)).encode(), hashlib.sha256).hexdigest()[:16]


async def fetch_avatar(bot, uid: int) -> bytes | None:
    """Profile photo bytes (small size) or None. Cached in memory; failures cached briefly."""
    now = time.time()
    hit = _AVATARS.get(uid)
    if hit and now - hit[0] < (AVATAR_TTL if hit[1] else NEG_TTL):
        return hit[1]
    body = None
    async with _SEM:
        try:
            photos = await bot.get_user_profile_photos(uid, limit=1)
            if photos.total_count and photos.photos:
                sizes = photos.photos[0]
                pick = next((p for p in sizes if p.width >= 160), sizes[-1])
                f = await bot.get_file(pick.file_id)
                body = bytes(await f.download_as_bytearray())
        except Exception:
            logger.debug("avatar fetch failed for %s", uid, exc_info=True)
    if len(_AVATARS) >= CACHE_MAX:
        for k in sorted(_AVATARS, key=lambda k: _AVATARS[k][0])[: CACHE_MAX // 4]:
            _AVATARS.pop(k, None)
    _AVATARS[uid] = (now, body)
    return body


async def avatar(request: web.Request):
    try:
        uid = int(request.match_info["uid"])
    except ValueError:
        return web.Response(status=400)
    sig = request.query.get("s", "")
    if not hmac.compare_digest(sig, avatar_sig(request.app["bot_token"], uid)):
        return web.Response(status=403)
    bot = request.app.get("bot")
    body = await fetch_avatar(bot, uid) if bot else None
    if not body:
        return web.Response(status=404, headers={"Cache-Control": "public, max-age=300"})
    return web.Response(body=body, content_type="image/jpeg", headers={"Cache-Control": "public, max-age=3600"})


def _json(payload, status=200):
    return web.json_response(payload, status=status, headers={"Cache-Control": "no-store"})


@web.middleware
async def auth_middleware(request: web.Request, handler):
    if request.path.startswith("/api/"):
        header = request.headers.get("Authorization", "")
        init_data = header[4:] if header.startswith("tma ") else ""
        user = validate_init_data(init_data, request.app["bot_token"])
        if not user:
            return _json({"error": "unauthorized"}, 401)
        try:
            if database.is_banned(user["id"]):
                return _json({"error": "banned"}, 403)
        except Exception:
            pass
        request["user"] = user
    return await handler(request)


async def index(request):
    return web.FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


async def health(request):
    return web.Response(text="ok")


async def api_me(request):
    return _json(data.profile(request["user"]))


async def api_ranks(request):
    metric = request.query.get("metric", "overall")
    if metric not in data.METRICS:
        return _json({"error": "bad metric"}, 400)
    d = data.leaderboard(metric, request["user"]["id"])
    token = request.app["bot_token"]
    for row in d["top"] + ([d["me"]] if d["me"] else []):
        row["sig"] = avatar_sig(token, row["uid"])
    return _json(d)


async def api_trophies(request):
    return _json(data.trophies(request["user"]["id"]))


async def api_tours(request):
    return _json({"available": data.tournaments_available(),
                  "items": data.tournaments(request.query.get("group", "live"), request.query.get("q", "")[:40])})


async def api_tour(request):
    try:
        tid = int(request.query.get("id", ""))
    except ValueError:
        return _json({"error": "bad id"}, 400)
    d = data.tournament_detail(tid)
    return _json(d) if d else _json({"error": "not found"}, 404)


async def api_zone(request):
    return _json({"app_name": os.environ.get("MINIAPP_NAME", "UltimateCric").strip() or "UltimateCric", "bot_username": request.app.get("bot_username"), "community_url": os.environ.get("MINIAPP_COMMUNITY_URL", ""),
                  "community_name": os.environ.get("MINIAPP_COMMUNITY_NAME", "Community")})


def make_app(bot_token: str, bot_username: str | None = None, bot=None) -> web.Application:
    app = web.Application(middlewares=[auth_middleware])
    app["bot_token"] = bot_token
    app["bot_username"] = bot_username
    app["bot"] = bot
    app.router.add_get("/", index)
    app.router.add_get("/healthz", health)
    app.router.add_get("/avatar/{uid}", avatar)
    app.router.add_get("/api/me", api_me)
    app.router.add_get("/api/ranks", api_ranks)
    app.router.add_get("/api/trophies", api_trophies)
    app.router.add_get("/api/tours", api_tours)
    app.router.add_get("/api/tour", api_tour)
    app.router.add_get("/api/zone", api_zone)
    return app


async def start(bot_token: str, bot_username: str | None, port: int, bot=None):
    runner = web.AppRunner(make_app(bot_token, bot_username, bot))
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    logger.info("Mini App server listening on 0.0.0.0:%d", port)
    return runner
