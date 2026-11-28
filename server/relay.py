"""The relay: one process that watches AWBW Battleground's stream game and fans it out.

- Battleground's push feed (wss://api.awbwbg.otw.gg/v1/stream, public) says which AWBW game is
  on stream and how many turns it has (stats.historyTurns). When that count goes up, the relay
  fetches the new turns from AWBW's replay API, once, and turns the last finished one into a
  libaw storyboard (`awbw-replay --storyboard`). That board goes to every viewer over our
  websocket. Only the stream game is ever fetched: there is no way to ask for another one.
- Fog games: nothing is fetched and the board says {"fog": true}. A spectator can't see the
  units, so neither can we.
- The board is a recap of a finished turn, never advice on what to do next (AWBW Discord rule 6:
  no advice on active league games, which Battleground often streams).
- Every settled market's last_result (each bettor's pnl, via_api) is appended to the season log
  once, by market_id; the season leaderboard is built from it. Battleground's /v1/results (its
  last 20 games) is read once a minute too, so a game that settled while the relay was down or
  restarting still counts.
"""
import json, time

UA = "battle-lab/0.1 (AWBW Battleground viewer)"
BG_STATE = "https://api.awbwbg.otw.gg/v1/state"
BG_STREAM = "wss://api.awbwbg.otw.gg/v1/stream"
BG_RESULTS = "https://api.awbwbg.otw.gg/v1/results"   # the last 20 settled games, bettor by bettor
AWBW_REPLAY = "https://awbw.amarriner.com/api/game/load_replay.php"
AWBW_LIVE = "wss://awbw.amarriner.com/node/game/{gid}"   # the game page's own live feed, as a spectator
AWBW_HEADERS = {"Content-Type": "application/json;charset=UTF-8", "User-Agent": UA,
                "Origin": "https://awbw.amarriner.com", "Referer": "https://awbw.amarriner.com"}
PAUSE = 1.0          # seconds between AWBW requests in a walk
MIN_GAP = 20.0       # seconds between walks of the same game
MAX_TURNS = 400      # safety valve for one walk


def stream_game(state):
    """(game id, finished-turn count, fog) of the game Battleground has on stream, or None."""
    g = (state or {}).get("game") or {}
    s = (state or {}).get("stats") or {}
    gid = g.get("game_id") or s.get("gameId")
    if not gid:
        return None
    return int(gid), int(s.get("historyTurns") or 0), bool(s.get("fog"))


def next_turn(data):
    """The NextTurn action of an AWBW replay turn: (day, pid), or None while the turn is in progress."""
    for a in reversed((data or {}).get("actions") or []):
        if isinstance(a, dict) and a.get("action") == "NextTurn":
            return a.get("day"), a.get("nextPId")
    return None


AWBW_TZ = "America/New_York"   # AWBW's server clock, which its turn-start times are written in


def awbw_time(s):
    """Epoch seconds of an AWBW server time ("2026-11-26 16:10:57", US Eastern), or None."""
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        return datetime.strptime(str(s), "%Y-%m-%d %H:%M:%S").replace(tzinfo=ZoneInfo(AWBW_TZ)).timestamp()
    except Exception:
        return None


def turn_clock(data):
    """(pid, turn start epoch, seconds on the clock at the start) for the player to move in a turn
    the replay API returned, or None."""
    gs = (data or {}).get("gameState") or {}
    pid = gs.get("currentTurnPId")
    players = gs.get("players") or {}
    for p in (players.values() if isinstance(players, dict) else players):
        if str(p.get("players_id")) == str(pid):
            at = awbw_time(p.get("players_turn_start"))
            left = p.get("players_turn_clock")
            if at:
                return int(pid), at, (int(left) if str(left).lstrip("-").isdigit() else None)
    return None


def game_over(data):
    """The GameOver record of a game's final turn ({winners, losers, message, ...}), or None. AWBW
    puts it on the last action (a Resign) or inside an elimination (a capture, the last unit)."""
    def find(x):
        if isinstance(x, dict):
            if isinstance(x.get("GameOver"), dict):
                return x["GameOver"]
            for v in x.values():
                g = find(v)
                if g:
                    return g
        elif isinstance(x, list):
            for v in reversed(x):
                g = find(v)
                if g:
                    return g
        return None
    return find((data or {}).get("actions") or [])


# Live-feed messages that aren't moves in the game.
NOT_ACTIONS = {"Pause", "ActivityUpdate", "JoinRoom", "LeaveRoom", "Ping", "Pong", "Chat", "Message", "Notification"}


def live_actions(msg):
    """The replay-format actions in one live-feed message. The feed sends a unit's move and what it
    did there together ({"Move": {...}, "Fire": {...}}); the replay lists them one after another."""
    if not isinstance(msg, dict):
        return []
    return [{**v, "action": k} for k, v in msg.items() if isinstance(v, dict) and k not in NOT_ACTIONS]


class Walker:
    """Fetches one game's turns from AWBW, turn by turn from the last one that was still in
    progress, and keeps only what the board needs: the last finished turn."""

    def __init__(self, gid):
        self.gid = gid
        self.cursor = (0, 1, None)      # (turn index, day, pid) of the next request
        self.finished = None             # (turn index, data) of the last finished turn
        self.seen = set()
        self.last_walk = 0.0
        self.over = None                 # the GameOver record, once the final turn is in
        self.current = None              # (turn index, data) of the turn in progress, as fetched

    def payload(self):
        n, day, pid = self.cursor
        return {"gameId": self.gid, "turn": n, "turnDay": day, "turnPId": pid, "initial": n == 0}

    async def walk(self, fetch, sleep, limit=MAX_TURNS):
        """Fetch from the cursor until a turn has no NextTurn (the one in progress). Returns True
        if a newer finished turn was found. `fetch(payload)` returns the turn's JSON."""
        self.last_walk = time.time()
        found = False
        if self.over:
            return False                 # the game is over: nothing more to fetch
        for i in range(limit):
            if i:
                await sleep(PAUSE)
            data = await fetch(self.payload())
            nt = next_turn(data)
            if not nt:
                over = game_over(data)
                if over:                 # the final turn: it ends the game instead of passing it on
                    self.over = over
                    self.finished = (self.cursor[0], data)
                    self.current = None
                    return True
                self.current = (self.cursor[0], data)   # in progress: the live feed adds to it
                return found
            if nt in self.seen:
                return found
            self.seen.add(nt)
            self.finished = (self.cursor[0], data)
            self.cursor = (self.cursor[0] + 1, nt[0], nt[1])
            found = True
        return found


def season_rows(results, start=None):
    """The bots' season leaderboard from settled markets. A bettor counts as a bot in a game when
    they traded through the API in it (via_api). The House (Battleground's market maker, is_bot)
    is kept apart as the benchmark. Rows: name, games, pnl, won (games with pnl > 0), best, last."""
    bots, house = {}, {"name": "The House", "games": 0, "pnl": 0.0, "won": 0}
    for r in results:
        if start is not None and (r.get("market_id") or 0) < start:
            continue
        if r.get("status") != "resolved":
            continue
        for b in r.get("results") or []:
            pnl = float(b.get("pnl") or 0)
            if b.get("is_bot"):
                house["games"] += 1; house["pnl"] += pnl; house["won"] += pnl > 0
                continue
            if not b.get("via_api"):
                continue
            row = bots.setdefault(b.get("username"), {"name": b.get("username"), "games": 0, "pnl": 0.0, "won": 0,
                                                       "best": None, "last": None})
            row["games"] += 1; row["pnl"] += pnl; row["won"] += pnl > 0
            row["best"] = pnl if row["best"] is None else max(row["best"], pnl)
            row["last"] = r.get("resolved_at")
    rows = sorted(bots.values(), key=lambda x: -x["pnl"])
    for x in rows + [house]:
        x["pnl"] = round(x["pnl"], 1)
    return {"bots": rows, "house": house, "markets": sum(1 for r in results if r.get("status") == "resolved"
                                                         and (start is None or (r.get("market_id") or 0) >= start))}


class Season:
    """The season's settled markets, one object per market (`<season>/<market_id>.json`) in a
    Store, so two instances overlapping in a deploy can't overwrite each other's rows."""

    def __init__(self, store, name, start):
        self.store, self.name, self.start = store, name, start
        self.results, self.ids = [], set()
        self.reload()

    def reload(self):
        """Pick up rows written by another instance (or seeded by hand)."""
        for key in self.store.list(self.name + "/"):
            try:
                mid = int(key.rsplit("/", 1)[-1].removesuffix(".json"))
            except ValueError:
                continue
            if mid in self.ids:
                continue
            r = json.loads(self.store.read(key) or "null")
            if isinstance(r, dict) and r.get("market_id") == mid:
                self.results.append(r); self.ids.add(mid)
        self.results.sort(key=lambda r: r["market_id"])

    def add(self, lr):
        """Record a last_result; True if it was new."""
        if not isinstance(lr, dict) or not isinstance(lr.get("market_id"), int) or lr["market_id"] in self.ids:
            return False
        if lr["market_id"] < self.start:
            return False
        row = {"t": round(time.time(), 1), **lr}
        self.store.write(f"{self.name}/{lr['market_id']}.json", json.dumps(row))
        self.results.append(row); self.ids.add(lr["market_id"])
        return True

    def add_results(self, doc):
        """Record the games in a /v1/results document that aren't in the log yet (oldest first,
        so the log stays in settlement order). Returns how many were new."""
        games = (doc or {}).get("games") if isinstance(doc, dict) else None
        if not isinstance(games, list):
            return 0
        new = 0
        for g in sorted((g for g in games if isinstance(g, dict) and g.get("status") in ("resolved", "cancelled")),
                        key=lambda g: g.get("market_id") or 0):
            new += self.add(g)
        if new:
            self.results.sort(key=lambda r: r["market_id"])
        return new

    def doc(self):
        return {"season": self.name, "start_market": self.start, **season_rows(self.results, self.start)}
