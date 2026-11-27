"""battle-lab's web service: the page, the board socket, and the season.

  GET  /              the page (web/index.html)
  GET  /libaw/...     libaw's replay viewer and data tables (vendored at build time)
  GET  /board.json    the stream game's last finished turn as a libaw storyboard ({"fog": true}
                      for fog games, {"none": true} before the first board), plus "age": seconds since it was made
  GET  /season.json   the bots' season leaderboard
  GET  /ws            websocket: {"type": "board"|"season", ...} on connect and on every change
  GET  /health

Env: PORT (8080), AWBW_REPLAY_BIN (awbw-replay), SEASON (season-1), SEASON_START (14508),
STORE (a local dir, default ./var; or gs://bucket/prefix), LIBAW_WEB (vendored libaw dir).
Run one instance only: it is the one poller AWBW sees.
"""
import asyncio, json, os, subprocess, sys, tempfile, time
from pathlib import Path

from aiohttp import ClientSession, ClientTimeout, WSMsgType, web

sys.path.insert(0, str(Path(__file__).resolve().parent))
import relay  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
LIBAW_WEB = Path(os.environ.get("LIBAW_WEB", ROOT / "vendor/libaw"))
BIN = os.environ.get("AWBW_REPLAY_BIN", "awbw-replay")
# GAME=<AWBW game id>: follow that game instead of Battleground's stream game (running your own
# copy). Battleground's feed, the market and the season are then left out.
OWN_GAME = int(os.environ["GAME"]) if os.environ.get("GAME", "").strip().isdigit() else None


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


class LocalStore:
    def __init__(self, d):
        self.d = Path(d); self.d.mkdir(parents=True, exist_ok=True)

    def read(self, name):
        p = self.d / name
        return p.read_text() if p.exists() else None

    def write(self, name, text):
        p = self.d / name; p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp"); tmp.write_text(text); tmp.replace(p)

    def list(self, prefix):
        base = self.d / prefix
        return sorted(f"{prefix}{f.name}" for f in base.glob("*.json")) if base.is_dir() else []


class GcsStore:
    def __init__(self, url):
        from google.cloud import storage
        bucket, _, prefix = url[5:].partition("/")
        self.b, self.prefix = storage.Client().bucket(bucket), prefix.strip("/")

    def _blob(self, name):
        return self.b.blob(f"{self.prefix}/{name}" if self.prefix else name)

    def read(self, name):
        bl = self._blob(name)
        return bl.download_as_text() if bl.exists() else None

    def write(self, name, text):
        self._blob(name).upload_from_string(text, content_type="application/json")

    def list(self, prefix):
        full = f"{self.prefix}/{prefix}" if self.prefix else prefix
        cut = len(self.prefix) + 1 if self.prefix else 0
        return sorted(b.name[cut:] for b in self.b.list_blobs(prefix=full))


def make_store(url):
    return GcsStore(url) if url.startswith("gs://") else LocalStore(url)


def storyboard(turn):
    """libaw's storyboard of one AWBW replay turn (awbw-replay --storyboard)."""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(turn, f); path = f.name
    try:
        r = subprocess.run([BIN, "--storyboard", path], capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip()[-300:])
        return json.loads(r.stdout)
    finally:
        os.unlink(path)


class Hub:
    def __init__(self, store):
        self.season = relay.Season(store, os.environ.get("SEASON", "season-1"), int(os.environ.get("SEASON_START", "14508")))
        self.store = store
        self.board = {"none": True}
        self.clients = set()
        self.walker = None
        self.want = asyncio.Event()
        self.target = None            # (gid, turns, fog) from the latest Battleground state
        self.heard = 0.0              # when Battleground's feed last spoke
        self.board_at = 0.0
        self.walk_now = False         # the live feed saw a turn end: walk without the usual wait
        self.live_up = False
        self.awbw_down_at = None   # when AWBW's live feed stopped answering (maintenance, outage), or None
        # When the player to move last changed, so viewers can count down their turn clock
        # (Battleground's turnClock is their time left as of their turn's start).
        self.turn_key, self.turn_at, self.turn_left = None, None, None
        try:
            c = json.loads(store.read("clock.json") or "null")
            if c:
                self.turn_key, self.turn_at, self.turn_left = tuple(c["key"]), c["at"], c.get("left")
        except Exception as e:
            log("clock: saved one unreadable:", e)
        # The last board survives a restart (a deploy): viewers keep it while the relay catches up.
        try:
            saved = json.loads(store.read("board.json") or "null")
            if saved and saved.get("board"):
                self.board, self.board_at = saved["board"], float(saved.get("at") or 0)
        except Exception as e:
            log("board: saved one unreadable:", e)
        if OWN_GAME:
            if str(self.board.get("game")) != str(OWN_GAME):
                self.board, self.board_at = {"none": True}, 0.0
            self.target = (OWN_GAME, 0, False)

    async def send(self, msg):
        dead = []
        for ws in self.clients:
            try:
                await ws.send_str(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)

    async def on_state(self, state):
        self.heard = time.time()
        if self.season.add(state.get("last_result")):
            log("season: recorded market", state["last_result"]["market_id"])
            await self.send(json.dumps({"type": "season", "season": self.season.doc()}))
        st = state.get("stats") or {}
        key = (str((state.get("game") or {}).get("game_id") or st.get("gameId")), str(st.get("currentTurnPid")))
        if st.get("currentTurnPid") and key != self.turn_key:
            # Usually AWBW's own times (set_turn) got here first; this is the fallback.
            fresh = self.turn_key is not None        # just started: we don't know when this turn began
            await self.set_turn(key, time.time() if fresh else None, None)
        g = relay.stream_game(state)
        if g and g != self.target:
            self.target = g
            self.want.set()

    async def set_board(self, doc):
        self.board = doc; self.board_at = time.time()
        try:
            await asyncio.get_running_loop().run_in_executor(None, self.store.write, "board.json", json.dumps({"board": doc, "at": self.board_at}))
        except Exception as e:
            log("board: save failed:", e)
        await self.send(json.dumps({"type": "board", "board": doc, "age": 0}))

    async def set_turn(self, key, at, left):
        """Who is to move (game, pid), since when (epoch), and their clock at that moment (s)."""
        key = (str(key[0]), str(key[1]))
        if (key, at, left) == (self.turn_key, self.turn_at, self.turn_left):
            return
        if key == self.turn_key and at is None:
            return   # nothing new
        self.turn_key, self.turn_at, self.turn_left = key, at, left
        try:
            self.store.write("clock.json", json.dumps({"key": list(key), "at": at, "left": left}))
        except Exception as e:
            log("clock: save failed:", e)
        await self.send(json.dumps({"type": "clock", "clock": self.clock()}))

    async def set_awbw(self, up):
        """AWBW's live feed answering or not; pages say so (its nightly maintenance, an outage)."""
        down = None if up else (self.awbw_down_at or time.time())
        if down == self.awbw_down_at:
            return
        self.awbw_down_at = down
        await self.send(json.dumps({"type": "awbw", "awbw": self.awbw()}))

    def awbw(self):
        """{down: seconds AWBW's live feed has been refusing us, or None}."""
        return {"down": None if self.awbw_down_at is None else round(time.time() - self.awbw_down_at)}

    def clock(self):
        """{game, pid, elapsed, left}: who is to move, for how many seconds (None if unknown), and
        their turn clock when the turn began (AWBW's, None if unknown)."""
        if not self.turn_key:
            return None
        return {"game": self.turn_key[0], "pid": self.turn_key[1],
                "elapsed": round(time.time() - self.turn_at, 1) if self.turn_at else None, "left": self.turn_left}

    def board_age(self):
        """Seconds since the board was made, so a viewer who just arrived starts the turn where
        everyone else is (or on its end) instead of from the start."""
        return round(time.time() - self.board_at, 1) if self.board_at else None

    # Battleground's feed -------------------------------------------------------------------
    async def bg_loop(self, http):
        delay = 1.0
        while True:
            try:
                async with http.ws_connect(relay.BG_STREAM, headers={"User-Agent": relay.UA}, heartbeat=30,
                                           receive_timeout=90) as ws:
                    log("battleground: connected"); delay = 1.0
                    async for m in ws:
                        if m.type != WSMsgType.TEXT:
                            break
                        d = json.loads(m.data)
                        if d.get("type") in ("hello", "state") and d.get("state"):
                            await self.on_state(d["state"])
            except Exception as e:
                log(f"battleground: down ({type(e).__name__}: {e}); retry in {delay:.0f} s")
            try:   # one GET while the socket is down, so a market can't settle unseen
                async with http.get(relay.BG_STATE, headers={"User-Agent": relay.UA}) as r:
                    await self.on_state(await r.json())
            except Exception as e:
                log("battleground: fallback GET failed:", e)
            await asyncio.sleep(delay); delay = min(60.0, delay * 2)

    # AWBW ----------------------------------------------------------------------------------
    async def awbw_loop(self, http):
        async def fetch(payload):
            for attempt in range(4):
                try:
                    async with http.post(relay.AWBW_REPLAY, json=payload, headers=relay.AWBW_HEADERS) as r:
                        if r.status in (429, 502, 503, 504):
                            raise RuntimeError(f"AWBW {r.status}")
                        r.raise_for_status()
                        return await r.json(content_type=None)
                except Exception:
                    if attempt == 3:
                        raise
                    await asyncio.sleep(min(8, 2 ** attempt))
        while True:
            await self.want.wait(); self.want.clear()
            gid, turns, fog = self.target
            if fog:
                self.walker = None
                if self.board.get("game") != gid or not self.board.get("fog"):
                    await self.set_board({"game": gid, "fog": True})
                continue
            if not self.walker or self.walker.gid != gid:
                self.walker = relay.Walker(gid)
                log("awbw: following game", gid)
            # With the live feed up, Battleground's turn count only confirms what the feed said:
            # skip the request when the walker is already on that turn.
            if self.live_up and not self.walk_now and self.walker.current and self.walker.current[0] >= turns - 1:
                continue
            gap = (3.0 if self.walk_now else relay.MIN_GAP) - (time.time() - self.walker.last_walk)
            self.walk_now = False
            if gap > 0:
                await asyncio.sleep(gap)
            try:
                await self.walker.walk(fetch, asyncio.sleep)
                if self.walker.current:
                    c = relay.turn_clock(self.walker.current[1])
                    if c:
                        await self.set_turn((gid, c[0]), c[1], c[2])
                await self.show_walker()
            except Exception as e:
                log("awbw: walk failed:", e)

    async def show_walker(self):
        """The board for where the walker is: the turn in progress if there is one (with whatever
        the live feed has added), else the last finished turn. Sent only when it changed."""
        w = self.walker
        pick = w.current or w.finished
        if not pick:
            return
        n, data = pick
        try:
            sb = await asyncio.get_running_loop().run_in_executor(None, storyboard, data)
        except Exception as e:
            if not w.finished or pick is w.finished:
                raise
            log("awbw: storyboard of the turn in progress failed, showing the last finished one:", e)
            n, data = w.finished
            sb = await asyncio.get_running_loop().run_in_executor(None, storyboard, data)
        sb.update({"game": w.gid, "turn": n})
        if OWN_GAME:
            sb["own"] = True
        if pick is w.current and data is w.current[1]:
            sb["live"] = True
        if w.over:
            o = w.over
            sb["over"] = {"winners": o.get("winners") or [], "losers": o.get("losers") or [], "message": o.get("message")}
        b = self.board
        if (b.get("game"), b.get("turn"), len(b.get("steps") or []), bool(b.get("over"))) == (sb["game"], n, len(sb.get("steps") or []), bool(sb.get("over"))):
            return
        await self.set_board(sb)
        log("awbw: board for game", w.gid, "turn", n, "day", sb.get("day"), "steps", len(sb.get("steps") or []), "(live)" if sb.get("live") else "")

    # AWBW's live feed --------------------------------------------------------------------------
    async def live_loop(self, http):
        """Follow the stream game on AWBW's own live feed (the socket its game page uses, as a
        spectator): each action is added to the turn in progress and the board grows with it. A
        turn's end makes the walker fetch the next turn's start. Fog games are left alone."""
        delay = 2.0
        while True:
            t = self.target
            if not t or t[2]:
                await asyncio.sleep(5); continue
            gid = t[0]
            if self.walker and self.walker.gid == gid and self.walker.over:
                await asyncio.sleep(30); continue   # the game is over: nothing more will come
            try:
                async with http.ws_connect(relay.AWBW_LIVE.format(gid=gid), heartbeat=30,
                                           headers={"User-Agent": relay.UA, "Origin": "https://awbw.amarriner.com"}) as ws:
                    log("live: following game", gid); delay = 2.0; self.live_up = True
                    await self.set_awbw(True)
                    pending = None
                    while self.target and self.target[0] == gid and not self.target[2]:
                        try:
                            m = await ws.receive(timeout=30)
                        except asyncio.TimeoutError:
                            continue
                        if m.type != WSMsgType.TEXT:
                            break
                        acts = relay.live_actions(json.loads(m.data))
                        if not acts:
                            continue
                        w = self.walker
                        if w and w.gid == gid and w.current:
                            w.current[1].setdefault("actions", []).extend(acts)
                        kinds = {a["action"] for a in acts}
                        for a in acts:
                            if a["action"] == "NextTurn" and a.get("nextPId"):
                                left = a.get("nextTimer")
                                await self.set_turn((gid, a["nextPId"]), relay.awbw_time(a.get("nextTurnStart")) or time.time(),
                                                    int(left) if str(left).lstrip("-").isdigit() else None)
                        if kinds & {"NextTurn", "GameOver", "Resign"} or any(relay.game_over({"actions": [a]}) for a in acts):
                            self.walk_now = True; self.want.set()
                        elif w and w.current:
                            if pending and not pending.done():
                                pending.cancel()
                            pending = asyncio.create_task(self._show_soon())
            except Exception as e:
                log(f"live: down ({type(e).__name__}: {e}); retry in {delay:.0f} s")
                await self.set_awbw(False)
            self.live_up = False
            await asyncio.sleep(delay); delay = min(60.0, delay * 2)

    async def _show_soon(self):
        await asyncio.sleep(0.3)   # a burst of messages (move + fire) makes one board
        try:
            await self.show_walker()
        except Exception as e:
            log("live: board failed:", e)


async def ws_handler(req):
    hub = req.app["hub"]
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(req)
    await ws.send_str(json.dumps({"type": "board", "board": hub.board, "age": hub.board_age()}))
    await ws.send_str(json.dumps({"type": "season", "season": hub.season.doc()}))
    await ws.send_str(json.dumps({"type": "clock", "clock": hub.clock()}))
    await ws.send_str(json.dumps({"type": "awbw", "awbw": hub.awbw()}))
    hub.clients.add(ws)
    try:
        async for _ in ws:
            pass   # viewers have nothing to say
    finally:
        hub.clients.discard(ws)
    return ws


def json_resp(doc):
    return web.json_response(doc, headers={"Cache-Control": "no-store"})


async def supervise(name, make):
    """Run a background loop forever: if it ever raises, log it and start it again."""
    while True:
        try:
            await make()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log(f"{name}: crashed ({type(e).__name__}: {e}); restarting in 5 s")
        await asyncio.sleep(5)


async def start_background(app):
    app["http"] = ClientSession(timeout=ClientTimeout(total=30))
    hub, http = app["hub"], app["http"]

    async def reload_season():
        while True:
            await asyncio.sleep(600)
            n = len(hub.season.ids)
            await asyncio.get_running_loop().run_in_executor(None, hub.season.reload)
            if len(hub.season.ids) != n:
                await hub.send(json.dumps({"type": "season", "season": hub.season.doc()}))
    if OWN_GAME:
        async def own_poll():
            # The live feed says when a turn ends; without it, look once a minute.
            while True:
                if not hub.live_up:
                    hub.want.set()
                await asyncio.sleep(60)
        log("following AWBW game", OWN_GAME, "(GAME is set: no Battleground)")
        app["tasks"] = [asyncio.create_task(supervise("awbw", lambda: hub.awbw_loop(http))),
                        asyncio.create_task(supervise("live", lambda: hub.live_loop(http))),
                        asyncio.create_task(supervise("poll", own_poll))]
        return
    app["tasks"] = [asyncio.create_task(supervise("battleground", lambda: hub.bg_loop(http))),
                    asyncio.create_task(supervise("awbw", lambda: hub.awbw_loop(http))),
                    asyncio.create_task(supervise("live", lambda: hub.live_loop(http))),
                    asyncio.create_task(supervise("season", reload_season))]


async def stop_background(app):
    for t in app["tasks"]:
        t.cancel()
    await app["http"].close()


async def board_json(r):
    hub = r.app["hub"]
    return json_resp({**hub.board, "age": hub.board_age(), "clock": hub.clock(), "awbw": hub.awbw()})


async def season_json(r):
    return json_resp(r.app["hub"].season.doc())


async def healthz(r):
    hub = r.app["hub"]
    tasks = r.app.get("tasks") or []
    return json_resp({"ok": all(not t.done() for t in tasks), "heard_s": round(time.time() - hub.heard) if hub.heard else None,
                      "board_s": round(time.time() - hub.board_at) if hub.board_at else None, "viewers": len(hub.clients),
                      "season_markets": len(hub.season.ids)})


async def water_json(r):
    return web.FileResponse(WEB / "awbw-water.json", headers={"Cache-Control": "public, max-age=86400"})


async def sound_js(r):
    return web.FileResponse(WEB / "sound.js", headers={"Cache-Control": "no-cache"})


async def favicon(r):
    return web.FileResponse(WEB / "favicon.svg", headers={"Cache-Control": "public, max-age=86400"})


async def config_js(r):
    """Same-origin runs (local): the relay is this server, and which game it follows if not Battleground's."""
    return web.Response(text=f"window.BATTLE_LAB_RELAY = '';\nwindow.BATTLE_LAB_GAME = {json.dumps(OWN_GAME)};\n", content_type="application/javascript",
                        headers={"Cache-Control": "no-cache"})


@web.middleware
async def cors(request, handler):
    """The page is served from elsewhere (Vercel): let it read the board and the season."""
    resp = await handler(request)
    if request.path.endswith(".json") or request.path == "/health":
        resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


async def index(r):
    return web.FileResponse(WEB / "index.html", headers={"Cache-Control": "no-cache"})


def make_app(store=None, background=True):
    app = web.Application(middlewares=[cors])
    app["hub"] = Hub(store or make_store(os.environ.get("STORE", str(ROOT / "var"))))
    app.router.add_get("/ws", ws_handler)
    app.router.add_get("/board.json", board_json)
    app.router.add_get("/season.json", season_json)
    app.router.add_get("/health", healthz)
    app.router.add_get("/", index)
    app.router.add_get("/config.js", config_js)
    app.router.add_get("/awbw-water.json", water_json)
    app.router.add_get("/sound.js", sound_js)
    app.router.add_get("/favicon.svg", favicon)
    if LIBAW_WEB.exists():
        app.router.add_static("/libaw/", LIBAW_WEB)
    app.router.add_static("/static/", WEB)
    if background:
        app.on_startup.append(start_background)
        app.on_cleanup.append(stop_background)
    return app


if __name__ == "__main__":
    web.run_app(make_app(), port=int(os.environ.get("PORT", "8080")))
