# Battle Lab

A live page for [AWBW Battleground](https://twitch.tv/awbwbattleground), the prediction market
on Advance Wars By Web games, and the start of a school for prediction bots. It shows:

- **The board**: the stream game's last finished turn, replayed with
  [libaw](https://github.com/collinjackson/libaw) and AWBW's art. Fog games stay hidden while
  they're on. A recap only, never advice (AWBW's rule on active league games).
- **The market**: Battleground's odds, each side's funds, units and army value, and the
  game's trades, read in the browser from Battleground's public feed
  ([API](https://api.awbwbg.otw.gg/)).
- **The API Leaderboard**: every account that traded through Battleground's API, ranked by gold
  won since the season started, with The House (Battleground's market maker) as the benchmark.
- **Twitch**: chat beside the board, the stream on its own tab.

## How it works

`server/app.py` is one small service (aiohttp):

- It listens to Battleground's push feed. When the stream game's turn count goes up, it fetches
  the new turns from AWBW's replay API (about two requests per turn, one game only), runs
  `awbw-replay --storyboard` on the last finished turn, and pushes that board to every viewer
  over `/ws`. Nobody can ask it for any other game.
- It records each settled market's `last_result` once into the season log (a local directory,
  or a GCS bucket in production) and serves the leaderboard at `/season.json`.

Run exactly one instance: it is the only poller AWBW sees.

## Deploy

- `deploy/relay.sh`: the relay on Cloud Run (`battle-lab`, one always-on instance; the season
  in `gs://comtower-battle-lab/battle-lab/season-1/<market>.json`). It serves the page too, as a
  backup address.
- `deploy/web.sh`: the page on Vercel (`battle-lab`), static, with `config.js` naming the relay.
  If the relay is down, the market, trades and chat still work (they come straight from
  Battleground and Twitch); the board and season come back when it does.
- `GET /health` on the relay: whether its loops are running, seconds since Battleground's feed
  last spoke, viewers, season size.

## Run it locally

    python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
    tools/vendor_libaw.sh                         # libaw's viewer -> vendor/libaw
    (cd ~/repos/libaw && cargo build --release -p libaw-replay)
    AWBW_REPLAY_BIN=~/repos/libaw/target/release/awbw-replay PORT=8765 .venv/bin/python server/app.py

Tests: `.venv/bin/python -m pytest -q tests`.

## Next

- A starter bot and a "bot brief" for writing one with any AI chat.
- Bot export (Docker / Fly.io; GitHub Actions for slow strategies).
- Verified bots (proof of artifice): a badge for bots that played with no human in the loop.

## Licence

Battle Lab's code is licensed under either of [Apache-2.0](LICENSE-APACHE) or [MIT](LICENSE-MIT),
at your option, like libaw. That covers this repo's code only. The page loads AWBW's art from
AWBW, and music and sound effects from the author of Improved AWBW Music Player; none of those
files are here, and they belong to their owners. `web/awbw-water.json` (where tiles sit on AWBW's
sprite sheet) is made by `tools/awbw_water.py` at deploy time and isn't committed. See
[docs/sources.md](docs/sources.md).
