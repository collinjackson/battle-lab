# Battle Lab

**Try it: [battle-lab-pi.vercel.app](https://battle-lab-pi.vercel.app/)**

Watch an Advance Wars By Web game live in your browser: every move animated on the board as it's
made, with AWBW's art, the players' funds and CO power, optional path arrows and cursor (after
[AWBW Ghost Input](https://greasyfork.org/en/scripts/604828-awbw-ghost-input)), and optional music
and sound effects.

The hosted page follows [AWBW Battleground](https://twitch.tv/awbwbattleground)'s stream game, with
its prediction market, Twitch chat and a leaderboard of the bots trading on it. You can also run
your own copy on your computer and point it at any AWBW game.

## Run it on your computer

You need git, Python 3, and a [Rust toolchain](https://rustup.rs/). Battle Lab draws the board
with [libaw](https://github.com/collinjackson/libaw), so check it out next to this repo:

    git clone https://github.com/collinjackson/libaw
    git clone https://github.com/collinjackson/battle-lab
    cd battle-lab
    tools/dev.sh 1770263

Use the number at the end of the game's address (`game.php?games_id=1770263`), then open
**http://localhost:8765/**. The first run takes a few minutes while it builds libaw's replay tool.

With a game number, the page shows just that game: the board and the players' cards, without
Battleground's market, leaderboard and chat. Without one (`tools/dev.sh`), you get the hosted
page: Battleground's stream game and all.

To switch games, stop the server (Ctrl+C) and start it again with the new number.

Options, as environment variables:

| Variable | Meaning |
|---|---|
| `PORT` | Where to serve the page (default `8765`). |
| `LIBAW` | Where libaw is checked out, if not `../libaw`. |
| `STORE` | Where the last board is saved, so a restart picks up where it was (default `var/`). |

Optional: install [wasm-pack](https://rustwasm.github.io/wasm-pack/) before the first run. With it,
pressing and holding a unit shows its ranges, and the ghost shows each move's range. The board
plays fine without it.

### What you can watch

- Games in progress, followed move by move.
- Finished games: the last turn, and who won.
- Fog-of-war games are left out while they're being played. AWBW hides their units from
  spectators, and so does Battle Lab.

## Using the page

- **Sound** (the speaker over the board): CO themes and unit sound effects, in step with the
  board, from the author of the Improved AWBW Music Player. Browsers need one click on the page
  before they allow sound.
- **Settings** (the gear):
  - Board: AWBW's art or libaw's, animated sprites, HP bars, the camera following the action.
  - Moves: Ghost Input-style arrows and cursor.
  - Sound: volumes, and the Dual Strike or Advance Wars 2 soundtrack.
  - Theme: light, dark, or your system's.
- **Hover or tap a tile** to see what's on it. **Press and hold a unit** for its ranges.

## How it works

`server/app.py` is one small Python service (aiohttp), and the only part that talks to AWBW:

- It follows the game on **AWBW's live feed**, the websocket AWBW's own game page uses, as a
  spectator. Each action is added to the turn in progress as it's made.
- At each turn's start it fetches the board once from AWBW's replay API. That's its only request
  per turn.
- libaw's `awbw-replay --storyboard` turns the turn into a storyboard, which the server pushes to
  every open page over `/ws`.

The page (`web/index.html`) embeds libaw's replay viewer and hands it each new storyboard in place,
so the board never reloads between moves or turns. Pages connect to your server, not to AWBW, so
extra viewers cost AWBW nothing. AWBW is run by volunteers, so please run one server per game.

## Hosting it

Any host that runs a Python web server with one instance always up will do; set `GAME` in its
environment to follow one game. The hosted copy runs the server on Google Cloud Run
(`deploy/relay.sh`) and the page on Vercel (`deploy/web.sh`, with `config.js` naming the
server). `GET /health` reports whether the server's loops are running and how many viewers are
connected.

## Development

Tests: `.venv/bin/python -m pytest -q tests`. After changing libaw, rerun `tools/vendor_libaw.sh`,
or restart `tools/dev.sh`, which does it for you.

## Licence

Battle Lab's code is licensed under either of [Apache-2.0](LICENSE-APACHE) or [MIT](LICENSE-MIT),
at your option, like libaw. That covers this repo's code only:

- The page loads AWBW's art from AWBW, and the music and sound effects from the author of the
  Improved AWBW Music Player. None of those files are in this repo, and they belong to their
  owners.
- `web/awbw-water.json` (where tiles sit on AWBW's sprite sheet) is made on your machine by
  `tools/awbw_water.py` and isn't committed.

See [docs/sources.md](docs/sources.md).
