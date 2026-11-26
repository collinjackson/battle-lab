import asyncio, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
import relay  # noqa: E402
import app as appmod  # noqa: E402


def turn(n, nxt=None):
    acts = [{"action": "Move"}]
    if nxt:
        acts.append({"action": "NextTurn", "day": nxt[0], "nextPId": nxt[1]})
    return {"n": n, "actions": acts}


def test_stream_game():
    st = {"game": {"game_id": "1769874"}, "stats": {"historyTurns": 21, "fog": False}}
    assert relay.stream_game(st) == (1769874, 21, False)
    assert relay.stream_game({"stats": {"gameId": "5", "fog": True}}) == (5, 0, True)
    assert relay.stream_game({}) is None and relay.stream_game(None) is None


def test_walker_keeps_last_finished_and_resumes_from_in_progress():
    # AWBW: turns 0..2 finished, turn 3 in progress.
    game = {0: turn(0, (1, "b")), 1: turn(1, (2, "a")), 2: turn(2, (2, "b")), 3: turn(3)}
    asked = []
    async def fetch(p):
        asked.append((p["turn"], p["turnDay"], p["turnPId"], p["initial"])); return game[p["turn"]]
    async def sleep(_): pass
    w = relay.Walker(7)
    assert asyncio.run(w.walk(fetch, sleep)) is True
    assert w.finished[0] == 2 and w.cursor == (3, 2, "b")
    assert asked == [(0, 1, None, True), (1, 1, "b", False), (2, 2, "a", False), (3, 2, "b", False)]
    # Nothing new: one request (the turn still in progress), no new board.
    asked.clear()
    assert asyncio.run(w.walk(fetch, sleep)) is False and asked == [(3, 2, "b", False)]
    # Turn 3 ends: two requests (3, now finished, and 4, in progress).
    game[3] = turn(3, (3, "a")); game[4] = turn(4); asked.clear()
    assert asyncio.run(w.walk(fetch, sleep)) is True and w.finished[0] == 3 and len(asked) == 2


def test_walker_stops_on_a_repeated_next_turn():
    async def fetch(p): return turn(p["turn"], (1, "a"))
    async def sleep(_): pass
    w = relay.Walker(7)
    assert asyncio.run(w.walk(fetch, sleep)) is True and w.finished[0] == 0 and w.cursor[0] == 1


R1 = {"market_id": 14508, "status": "resolved", "resolved_at": "2026-11-26T02:35:49Z", "results": [
    {"username": "quantbot", "pnl": 997, "via_api": True, "is_bot": False},
    {"username": "soccer152", "pnl": 86, "via_api": True, "is_bot": False},
    {"username": "chatter", "pnl": 50, "via_api": False, "is_bot": False},
    {"username": "The House", "pnl": -345, "via_api": False, "is_bot": True}]}
R2 = {"market_id": 14509, "status": "resolved", "resolved_at": "2026-11-26T03:30:00Z", "results": [
    {"username": "soccer152", "pnl": 1200, "via_api": True, "is_bot": False},
    {"username": "quantbot", "pnl": -100, "via_api": True, "is_bot": False},
    {"username": "The House", "pnl": 20, "via_api": False, "is_bot": True}]}
CANCELLED = {"market_id": 14510, "status": "cancelled", "results": []}


def test_season_rows_bots_only_house_apart():
    s = relay.season_rows([R1, R2, CANCELLED])
    assert [b["name"] for b in s["bots"]] == ["soccer152", "quantbot"]
    assert s["bots"][0] == {"name": "soccer152", "games": 2, "pnl": 1286.0, "won": 2, "best": 1200, "last": R2["resolved_at"]}
    assert s["bots"][1]["won"] == 1 and s["bots"][1]["pnl"] == 897.0
    assert s["house"] == {"name": "The House", "games": 2, "pnl": -325.0, "won": 1}
    assert s["markets"] == 2
    assert relay.season_rows([R1, R2], start=14509)["markets"] == 1


def test_season_once_per_market_and_persists(tmp_path):
    store = appmod.LocalStore(tmp_path)
    s = relay.Season(store, "season-1", 14508)
    assert s.add(R1) and not s.add(R1) and not s.add(None) and not s.add({"market_id": 14000, "status": "resolved"})
    s2 = relay.Season(store, "season-1", 14508)
    assert not s2.add(R1) and s2.add(R2)
    assert sorted(f.name for f in (tmp_path / "season-1").iterdir()) == ["14508.json", "14509.json"]
    # Another instance's row shows up on reload.
    other = relay.Season(store, "season-1", 14508)
    other.add({**R2, "market_id": 14511})
    s2.reload()
    assert 14511 in s2.ids and [r["market_id"] for r in s2.results] == [14508, 14509, 14511]
    assert s2.doc()["bots"][0]["name"] == "soccer152"


def test_app_serves_board_season_and_ws(tmp_path):
    from aiohttp.test_utils import TestClient, TestServer

    async def run():
        a = appmod.make_app(store=appmod.LocalStore(tmp_path), background=False)
        async with TestClient(TestServer(a)) as c:
            assert (await (await c.get("/board.json")).json()) == {"none": True, "age": None}
            hub = a["hub"]
            await hub.on_state({"last_result": R1, "game": {"game_id": "9"}, "stats": {"historyTurns": 3, "fog": True}})
            assert hub.target == (9, 3, True) and hub.want.is_set()
            season = await (await c.get("/season.json")).json()
            assert season["markets"] == 1 and season["bots"][0]["name"] == "quantbot"
            ws = await c.ws_connect("/ws")
            first, second = json.loads((await ws.receive()).data), json.loads((await ws.receive()).data)
            assert first["type"] == "board" and second["type"] == "season"
            await hub.set_board({"game": 9, "fog": True})
            assert json.loads((await ws.receive()).data) == {"type": "board", "board": {"game": 9, "fog": True}, "age": 0}
            await ws.close()
            assert (await c.get("/")).status == 200
            r = await c.get("/season.json")
            assert r.headers["Access-Control-Allow-Origin"] == "*"
            h = await (await c.get("/health")).json()
            assert h["ok"] and h["season_markets"] == 1 and h["heard_s"] is not None
            assert "BATTLE_LAB_RELAY" in await (await c.get("/config.js")).text()
    asyncio.run(run())


def test_supervise_restarts_a_crashed_loop():
    runs = []
    async def boom():
        runs.append(1)
        if len(runs) < 2:
            raise RuntimeError("x")
        raise asyncio.CancelledError
    async def run():
        orig = asyncio.sleep
        async def fast(_): await orig(0)
        appmod.asyncio.sleep = fast
        try:
            await appmod.supervise("t", boom)
        except asyncio.CancelledError:
            pass
        finally:
            appmod.asyncio.sleep = orig
    asyncio.run(run())
    assert len(runs) == 2
