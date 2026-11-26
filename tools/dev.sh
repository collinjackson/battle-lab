#!/usr/bin/env bash
# Run Battle Lab on this machine: the relay and the page together, at http://localhost:8765/.
#
#   tools/dev.sh 1770263        # follow that AWBW game
#   tools/dev.sh                # follow AWBW Battleground's stream game, with its market and chat
#
# Needs python3, a Rust toolchain (cargo) and libaw checked out next to this repo (../libaw), or
# LIBAW=/path/to/libaw. Optional: wasm-pack, for unit ranges and the ghost's move ranges.
# Env: PORT (8765), STORE (var/: the saved board), GAME (instead of the argument).
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE"
GAME="${1:-${GAME:-}}"
LIBAW="${LIBAW:-$([[ -d "$HERE/../libaw" ]] && echo "$HERE/../libaw" || echo "$HOME/repos/libaw")}"
if [[ ! -d "$LIBAW/web" ]]; then
  echo "libaw not found. Clone it next to this repo:" >&2
  echo "  git clone https://github.com/collinjackson/libaw \"$HERE/../libaw\"" >&2
  exit 1
fi
export LIBAW

[[ -x .venv/bin/python ]] || { python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt; }
(cd "$LIBAW" && cargo build -q --release -p libaw-replay)
if [[ ! -f "$LIBAW/web/pkg/libaw_wasm.js" ]] && command -v wasm-pack >/dev/null; then
  (cd "$LIBAW" && wasm-pack build crates/wasm --release --target web --out-dir ../../web/pkg)
fi
tools/vendor_libaw.sh
[[ -f web/awbw-water.json ]] || python3 -I tools/awbw_water.py   # where AWBW's sea tiles sit on its sprite sheet

export AWBW_REPLAY_BIN="$LIBAW/target/release/awbw-replay" PORT="${PORT:-8765}" STORE="${STORE:-$HERE/var}"
if [[ -n "$GAME" ]]; then export GAME; echo "Following AWBW game $GAME at http://localhost:$PORT/"
else unset GAME; echo "Following AWBW Battleground's stream game at http://localhost:$PORT/"; fi
exec .venv/bin/python server/app.py
