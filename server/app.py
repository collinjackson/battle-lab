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
        # When the player to move last changed, so viewers can count down their turn clock
        # (Battleground's turnClock is their time left as of their turn's start).
        self.turn_key, self.turn_at = None, None
        try:
            c = json.loads(store.read("clock.json") or "null")
            if c:
                self.turn_key, self.turn_at = tuple(c["key"]), c["at"]
        except Exception as e:
            log("clock: saved one unreadable:", e)
        # The last board survives a restart (a deploy): viewers keep it while the relay catches up.
        try:
            saved = json.loads(store.read("board.json") or "null")
            if saved and saved.get("board"):
                self.board, self.board_at = saved["board"], float(saved.get("at") or 0)
        except Exception as e:
            log("board: saved one unreadable:", e)

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
            fresh = self.turn_key is not None        # just started: we don't know when this turn began
            self.turn_key, self.turn_at = key, (time.time() if fresh else None)
            try:
                self.store.write("clock.json", json.dumps({"key": list(key), "at": self.turn_at}))
            except Exception as e:
                log("clock: save failed:", e)
            await self.send(json.dumps({"type": "clock", "clock": self.clock()}))
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

    def clock(self):
        """{pid, elapsed}: who is to move and for how many seconds (elapsed None if unknown)."""
        if not self.turn_key:
            return None
        return {"game": self.turn_key[0], "pid": self.turn_key[1],
                "elapsed": round(time.time() - self.turn_at, 1) if self.turn_at else None}

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
            gap = relay.MIN_GAP - (time.time() - self.walker.last_walk)
            if gap > 0:
                await asyncio.sleep(gap)
            try:
                if await self.walker.walk(fetch, asyncio.sleep):
                    n, data = self.walker.finished
                    sb = await asyncio.get_running_loop().run_in_executor(None, storyboard, data)
                    sb.update({"game": gid, "turn": n})
                    if self.walker.over:
                        o = self.walker.over
                        sb["over"] = {"winners": o.get("winners") or [], "losers": o.get("losers") or [], "message": o.get("message")}
                    await self.set_board(sb)
                    log("awbw: board for game", gid, "turn", n, "day", sb.get("day"))
            except Exception as e:
                log("awbw: walk failed:", e)


async def ws_handler(req):
    hub = req.app["hub"]
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(req)
    await ws.send_str(json.dumps({"type": "board", "board": hub.board, "age": hub.board_age()}))
    await ws.send_str(json.dumps({"type": "season", "season": hub.season.doc()}))
    await ws.send_str(json.dumps({"type": "clock", "clock": hub.clock()}))
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
    app["tasks"] = [asyncio.create_task(supervise("battleground", lambda: hub.bg_loop(http))),
                    asyncio.create_task(supervise("awbw", lambda: hub.awbw_loop(http))),
                    asyncio.create_task(supervise("season", reload_season))]


async def stop_background(app):
    for t in app["tasks"]:
        t.cancel()
    await app["http"].close()


async def board_json(r):
    hub = r.app["hub"]
    return json_resp({**hub.board, "age": hub.board_age(), "clock": hub.clock()})


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


async def config_js(r):
    """Same-origin runs (local): the relay is this server."""
    return web.Response(text="window.BATTLE_LAB_RELAY = '';\n", content_type="application/javascript",
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
    if LIBAW_WEB.exists():
        app.router.add_static("/libaw/", LIBAW_WEB)
    app.router.add_static("/static/", WEB)
    if background:
        app.on_startup.append(start_background)
        app.on_cleanup.append(stop_background)
    return app


if __name__ == "__main__":
    web.run_app(make_app(), port=int(os.environ.get("PORT", "8080")))
