"""Read-only queries for the Mini App. Never writes to the database and never touches
Solo Mode. Combines Solo + Team Cricket stats across every group into one global profile."""

import sqlite3
import time

import database

_CACHE = {"t": 0.0, "players": None}
CACHE_SECONDS = 20
TIERS = [(1, "Legend"), (5, "Elite"), (25, "Pro"), (60, "Regular"), (100, "Rookie")]  # top-% thresholds


def _points(p) -> int:
    return p["wins"] * 10 + p["runs"] // 5 + p["wickets"] * 5 + p["matches"] + p["potm"] * 15


def _rows(conn, sql):
    try:
        return conn.execute(sql).fetchall()
    except sqlite3.OperationalError:  # table not created yet
        return []


def load_players(force: bool = False) -> dict[int, dict]:
    now = time.time()
    if not force and _CACHE["players"] is not None and now - _CACHE["t"] < CACHE_SECONDS:
        return _CACHE["players"]
    conn = database.get_connection()
    try:
        players: dict[int, dict] = {}

        def get(uid):
            return players.setdefault(uid, dict(uid=uid, name=None, username=None, matches=0, wins=0, losses=0,
                                                runs=0, wickets=0, highest=0, potm=0))
        for r in _rows(conn, "SELECT user_id, MAX(username) u, SUM(matches_played) m, SUM(matches_won) w, "
                             "SUM(matches_lost) l, SUM(total_runs) r, MAX(highest_score) hs FROM player_stats GROUP BY user_id"):
            p = get(r["user_id"])
            p["username"] = r["u"] or p["username"]
            p["matches"] += r["m"] or 0; p["wins"] += r["w"] or 0; p["losses"] += r["l"] or 0
            p["runs"] += r["r"] or 0; p["highest"] = max(p["highest"], r["hs"] or 0)
        for r in _rows(conn, "SELECT user_id, MAX(name) n, SUM(matches) m, SUM(wins) w, SUM(losses) l, SUM(runs) r, "
                             "SUM(wickets) wk, MAX(highest_score) hs, SUM(potm_awards) po FROM team_player_stats GROUP BY user_id"):
            p = get(r["user_id"])
            p["name"] = r["n"] or p["name"]
            p["matches"] += r["m"] or 0; p["wins"] += r["w"] or 0; p["losses"] += r["l"] or 0
            p["runs"] += r["r"] or 0; p["wickets"] += r["wk"] or 0
            p["highest"] = max(p["highest"], r["hs"] or 0); p["potm"] += r["po"] or 0
        for r in _rows(conn, "SELECT user_id, username, first_name FROM known_users"):
            if r["user_id"] in players:
                p = players[r["user_id"]]
                p["name"] = r["first_name"] or p["name"]
                p["username"] = r["username"] or p["username"]
        banned = {r["user_id"] for r in _rows(conn, "SELECT user_id FROM banned_users")}
    finally:
        conn.close()
    for uid in list(players):
        p = players[uid]
        if uid in banned or p["matches"] <= 0:
            del players[uid]
            continue
        p["name"] = p["name"] or p["username"] or f"Player {uid}"
        p["points"] = _points(p)
    _CACHE.update(t=now, players=players)
    return players


METRICS = {"overall": "points", "runs": "runs", "wickets": "wickets", "wins": "wins"}


def ranked(metric: str) -> list[dict]:
    key = METRICS[metric]
    rows = sorted(load_players().values(), key=lambda p: (-p[key], -p["points"], p["uid"]))
    return [dict(p, rank=i, value=p[key]) for i, p in enumerate(rows, start=1)]


def tier_for(rank: int | None, total: int) -> tuple[str, float | None]:
    if not rank or not total:
        return "Rookie", None
    pct = rank / total * 100
    for limit, name in TIERS:
        if pct <= limit:
            return name, round(pct)
    return "Rookie", round(pct)


def _public(p, me_uid=None):
    return dict(rank=p["rank"], uid=p["uid"], name=p["name"], username=p["username"], value=p["value"],
                points=p["points"], me=(p["uid"] == me_uid))


def leaderboard(metric: str, me_uid: int, limit: int = 50) -> dict:
    rows = ranked(metric)
    mine = next((r for r in rows if r["uid"] == me_uid), None)
    return {"metric": metric, "total": len(rows), "top": [_public(r, me_uid) for r in rows[:limit]],
            "me": _public(mine, me_uid) if mine else None}


def profile(user: dict) -> dict:
    uid = user["id"]
    total = len(load_players())
    ranks = {m: next((r["rank"] for r in ranked(m) if r["uid"] == uid), None) for m in METRICS}
    p = load_players().get(uid)
    tier, pct = tier_for(ranks["overall"], total)
    name = user.get("first_name") or (p and p["name"]) or f"Player {uid}"
    return {
        "uid": uid, "name": name, "username": user.get("username"), "photo": user.get("photo_url"),
        "played": bool(p), "total_players": total, "tier": tier, "top_percent": pct, "ranks": ranks,
        "stats": {k: (p[k] if p else 0) for k in ("matches", "wins", "losses", "runs", "wickets", "highest", "potm", "points")},
    }


# ---------------------------------------------------------------- trophies
# (id, name, description, category, tier, stat, target)
TROPHIES = [
    ("runs100", "Century Club", "Score 100 total runs", "Batting", "bronze", "runs", 100),
    ("runs1k", "Run Machine", "Score 1,000 total runs", "Batting", "silver", "runs", 1000),
    ("runs5k", "Run King", "Score 5,000 total runs", "Batting", "gold", "runs", 5000),
    ("hs50", "Half Century", "Reach a top score of 50", "Batting", "bronze", "highest", 50),
    ("hs100", "Centurion", "Reach a top score of 100", "Batting", "silver", "highest", 100),
    ("wk10", "Wicket Taker", "Take 10 wickets (Team Cricket)", "Bowling", "bronze", "wickets", 10),
    ("wk50", "Strike Bowler", "Take 50 wickets", "Bowling", "silver", "wickets", 50),
    ("wk200", "Wicket Master", "Take 200 wickets", "Bowling", "gold", "wickets", 200),
    ("m10", "Newcomer", "Play 10 matches", "Matches", "bronze", "matches", 10),
    ("m50", "Regular", "Play 50 matches", "Matches", "bronze", "matches", 50),
    ("m200", "Veteran", "Play 200 matches", "Matches", "silver", "matches", 200),
    ("m500", "Legend", "Play 500 matches", "Matches", "gold", "matches", 500),
    ("w10", "Winner", "Win 10 matches", "Wins", "bronze", "wins", 10),
    ("w25", "Champion", "Win 25 matches", "Wins", "bronze", "wins", 25),
    ("w100", "Dominator", "Win 100 matches", "Wins", "silver", "wins", 100),
    ("w300", "Unstoppable", "Win 300 matches", "Wins", "gold", "wins", 300),
    ("potm1", "Match Winner", "Win a Player of the Match award", "Awards", "bronze", "potm", 1),
    ("potm10", "Man of Matches", "Win 10 Player of the Match awards", "Awards", "silver", "potm", 10),
    ("league1", "League Champion", "Win a Solo League tournament", "Tournaments", "gold", "leagues", 1),
]


def league_titles(uid: int) -> list[dict]:
    conn = database.get_connection()
    try:
        return [dict(r) for r in _rows(conn, f"SELECT id, name FROM tournaments WHERE champion_user_id={int(uid)} "
                                              "AND status='COMPLETED' ORDER BY id DESC")]
    finally:
        conn.close()


def trophies(uid: int) -> dict:
    p = load_players().get(uid) or {}
    leagues = league_titles(uid)
    stats = {**{k: p.get(k, 0) for k in ("runs", "highest", "wickets", "matches", "wins", "potm")}, "leagues": len(leagues)}
    out = []
    for tid, name, desc, cat, tier, stat, target in TROPHIES:
        have = stats[stat]
        out.append(dict(id=tid, name=name, desc=desc, category=cat, tier=tier, progress=min(have, target),
                        target=target, unlocked=have >= target))
    unlocked = [t for t in out if t["unlocked"]]
    almost = sorted((t for t in out if not t["unlocked"] and t["progress"] > 0), key=lambda t: -t["progress"] / t["target"])[:6]
    conn = database.get_connection()
    try:
        awards = [r["tournament_name"] for r in _rows(
            conn, f"SELECT tournament_name FROM achievements WHERE user_id={int(uid)} ORDER BY added_at DESC")]
    finally:
        conn.close()
    count = lambda t: sum(1 for x in unlocked if x["tier"] == t)
    return {"trophies": out, "unlocked": len(unlocked), "total": len(out), "almost": almost, "awards": awards,
            "leagues": leagues, "medals": {"gold": count("gold"), "silver": count("silver"), "bronze": count("bronze")}}


# ---------------------------------------------------------------- tournaments (read-only)
LIVE = ("IN_PROGRESS", "AWAITING_TIEBREAK")
UPCOMING = ("REGISTRATION_OPEN", "REGISTRATION_CLOSED")


def tournaments_available() -> bool:
    """True when this bot has the Solo League tournament tables."""
    conn = database.get_connection()
    try:
        return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='tournaments'").fetchone() is not None
    finally:
        conn.close()


def tournaments(group: str, q: str = "") -> list[dict]:
    states = {"live": LIVE, "upcoming": UPCOMING, "done": ("COMPLETED",)}.get(group)
    if not states:
        return []
    conn = database.get_connection()
    try:
        ph = ",".join("?" * len(states))
        rows = _rows_args(conn, f"SELECT * FROM tournaments WHERE status IN ({ph}) ORDER BY id DESC LIMIT 50", states)
        out = []
        for t in rows:
            if q and q.lower() not in t["name"].lower():
                continue
            n = conn.execute("SELECT COUNT(*) FROM tournament_participants WHERE tournament_id=? AND status='ACTIVE'", (t["id"],)).fetchone()[0]
            fx = conn.execute("SELECT COUNT(*) total, SUM(result_processed) done FROM tournament_fixtures WHERE tournament_id=?", (t["id"],)).fetchone()
            champ = None
            if t["champion_user_id"]:
                r = conn.execute("SELECT display_name FROM tournament_participants WHERE tournament_id=? AND telegram_user_id=?",
                                 (t["id"], t["champion_user_id"])).fetchone()
                champ = r["display_name"] if r else None
            out.append(dict(id=t["id"], name=t["name"], status=t["status"], players=n, max=t["max_players"],
                            fixtures_total=fx["total"] or 0, fixtures_done=fx["done"] or 0, champion=champ,
                            start_at=t["start_at"], registration_deadline=t["registration_deadline"]))
        return out
    finally:
        conn.close()


def _rows_args(conn, sql, args):
    try:
        return conn.execute(sql, args).fetchall()
    except sqlite3.OperationalError:
        return []


def tournament_detail(tid: int) -> dict | None:
    try:
        from tournament import service
    except Exception:
        return None
    t = service.get_tournament(tid)
    if not t or t["status"] in ("DRAFT", "CANCELLED"):
        return None
    names = service.names_map(tid)
    fx = service.list_fixtures(tid)
    show = [f for f in fx if f["status"] in ("OPEN", "IN_PROGRESS", "SCHEDULED")][:6] + \
           [f for f in reversed(fx) if f["result_processed"]][:6]
    return {
        "id": tid, "name": t["name"], "status": t["status"], "max": t["max_players"],
        "table": [dict(pos=r["position"], name=r["name"], played=r["played"], won=r["won"], lost=r["lost"],
                       tied=r["tied"], runs_for=r["runs_for"], runs_against=r["runs_against"], points=r["points"],
                       withdrawn=r["status"] == "WITHDRAWN") for r in service.get_standings(tid)],
        "fixtures": [dict(no=f["fixture_no"], round=f["round_number"], status=f["status"], scheduled_at=f["scheduled_at"],
                          p1=names.get(f["player1_user_id"], "?"), p2=names.get(f["player2_user_id"], "?"),
                          r1=f["p1_runs"], r2=f["p2_runs"], winner=names.get(f["winner_user_id"]) if f["winner_user_id"] else None,
                          result_type=f["result_type"]) for f in show],
    }
