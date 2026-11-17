"""aiohttp web server for the Mini App. Runs inside the bot's own event loop (same
process, same Railway service) and only ever READS the database."""

import logging
import os
from pathlib import Path

from aiohttp import web

import database
from . import data
from .auth import validate_init_data

logger = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"


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
    return _json(data.leaderboard(metric, request["user"]["id"]))


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
    return _json({"bot_username": request.app.get("bot_username"), "community_url": os.environ.get("MINIAPP_COMMUNITY_URL", ""),
                  "community_name": os.environ.get("MINIAPP_COMMUNITY_NAME", "Community")})


def make_app(bot_token: str, bot_username: str | None = None) -> web.Application:
    app = web.Application(middlewares=[auth_middleware])
    app["bot_token"] = bot_token
    app["bot_username"] = bot_username
    app.router.add_get("/", index)
    app.router.add_get("/healthz", health)
    app.router.add_get("/api/me", api_me)
    app.router.add_get("/api/ranks", api_ranks)
    app.router.add_get("/api/trophies", api_trophies)
    app.router.add_get("/api/tours", api_tours)
    app.router.add_get("/api/tour", api_tour)
    app.router.add_get("/api/zone", api_zone)
    return app


async def start(bot_token: str, bot_username: str | None, port: int):
    runner = web.AppRunner(make_app(bot_token, bot_username))
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    logger.info("Mini App server listening on 0.0.0.0:%d", port)
    return runner
