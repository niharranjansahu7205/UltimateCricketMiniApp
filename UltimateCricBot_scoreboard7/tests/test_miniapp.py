"""Mini App: Telegram login verification, read-only data layer and HTTP API."""
import asyncio
import hashlib
import hmac
import json
import os
import sys
import time
from urllib.parse import urlencode

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402
from miniapp import data, server  # noqa: E402
from miniapp.auth import validate_init_data  # noqa: E402

TOKEN = "123456:TEST-TOKEN"


def sign(user, token=TOKEN, auth_date=None):
    f = {"auth_date": str(auth_date or int(time.time())), "query_id": "AAA", "user": json.dumps(user)}
    check = "\n".join(f"{k}={v}" for k, v in sorted(f.items()))
    f["hash"] = hmac.new(hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest(), check.encode(), hashlib.sha256).hexdigest()
    return urlencode(f)


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "m.db"))
    database.init_db()
    data._CACHE.update(t=0.0, players=None)
    yield


def seed():
    for uid, name, w, r in [(1, "Ann", 9, 500), (2, "Bob", 5, 900), (3, "Cy", 2, 100)]:
        database.record_known_user(uid, f"u{uid}", name)
        for i in range(w):
            database.record_match_result(-100, uid, f"u{uid}", r // max(w, 1), True)
        database.record_match_result(-100, uid, f"u{uid}", 0, False)


def test_auth_accepts_valid_and_rejects_tampered_or_stale():
    u = {"id": 7, "first_name": "Raj"}
    assert validate_init_data(sign(u), TOKEN)["id"] == 7
    assert validate_init_data(sign(u), "999:OTHER") is None            # wrong bot token
    assert validate_init_data(sign(u).replace("Raj", "Eve"), TOKEN) is None
    assert validate_init_data(sign(u, auth_date=int(time.time()) - 90000), TOKEN) is None
    assert validate_init_data("", TOKEN) is None and validate_init_data("garbage", TOKEN) is None


def test_ranks_profile_and_tiers():
    seed()
    top = data.ranked("overall")
    assert [p["uid"] for p in top][0] in (1, 2) and len(top) == 3
    assert [p["uid"] for p in data.ranked("runs")][0] == 2             # Bob has the most runs
    me = data.profile({"id": 3, "first_name": "Cy"})
    assert me["played"] and me["ranks"]["overall"] == 3 and me["tier"] in ("Rookie", "Regular", "Pro", "Elite", "Legend")
    ghost = data.profile({"id": 999, "first_name": "New"})
    assert not ghost["played"] and ghost["ranks"]["overall"] is None


def test_banned_users_are_hidden():
    seed(); database.ban_user(1, "u1", 0)
    assert 1 not in data.load_players(force=True)


def test_trophies_unlock_by_stats():
    seed()
    t = data.trophies(2)
    ids = {x["id"] for x in t["trophies"] if x["unlocked"]}
    assert {"runs100", "w10"} - ids == {"w10"} or "runs100" in ids
    assert t["total"] == len(data.TROPHIES) and t["unlocked"] == len(ids)


def test_api_requires_signed_login_and_serves_json():
    seed()

    async def run():
        async with TestClient(TestServer(server.make_app(TOKEN, "CricketLeagueBot"))) as c:
            assert (await c.get("/api/me")).status == 401
            assert (await c.get("/api/me", headers={"Authorization": "tma bad"})).status == 401
            h = {"Authorization": "tma " + sign({"id": 1, "first_name": "Ann"})}
            me = await (await c.get("/api/me", headers=h)).json()
            assert me["name"] == "Ann" and me["ranks"]["overall"] in (1, 2)
            r = await (await c.get("/api/ranks?metric=wins", headers=h)).json()
            assert r["top"][0]["name"] == "Ann" and r["me"]["me"] is True
            assert (await c.get("/api/ranks?metric=evil", headers=h)).status == 400
            assert (await c.get("/api/trophies", headers=h)).status == 200
            assert (await (await c.get("/api/tours?group=live", headers=h)).json())["items"] == []
            assert (await c.get("/api/tour?id=abc", headers=h)).status == 400
            assert (await c.get("/api/tour?id=99", headers=h)).status == 404
            page = await c.get("/")
            assert page.status == 200 and "Cricket League" in await page.text()
            assert (await c.get("/healthz")).status == 200
            database.ban_user(1, "u1", 0)
            assert (await c.get("/api/me", headers=h)).status == 403
    asyncio.run(run())


def test_tours_tab_reports_unavailable_without_tournament_module():
    async def run():
        async with TestClient(TestServer(server.make_app(TOKEN, "Bot"))) as c:
            h = {"Authorization": "tma " + sign({"id": 1, "first_name": "A"})}
            d = await (await c.get("/api/tours?group=live", headers=h)).json()
            assert d == {"available": False, "items": []}
            assert (await c.get("/api/tour?id=1", headers=h)).status == 404
            t = await (await c.get("/api/trophies", headers=h)).json()
            assert t["leagues"] == [] and t["total"] == len(data.TROPHIES)
    asyncio.run(run())
