#!/usr/bin/env bash
# Copy libaw's replay viewer (web/replay.html and what it imports) and its data tables into
# vendor/libaw, served at /libaw/. Rerun after a libaw change.   LIBAW=~/repos/libaw by default.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
LIBAW="${LIBAW:-$([[ -d "$HERE/../libaw" ]] && echo "$HERE/../libaw" || echo "$HOME/repos/libaw")}"
OUT="$HERE/vendor/libaw.new"; FINAL="$HERE/vendor/libaw"
rm -rf "$OUT"; mkdir -p "$OUT/web/js" "$OUT/data"
cp "$LIBAW"/web/replay.html "$LIBAW"/web/style.css "$OUT/web/"
cp "$LIBAW"/web/js/{replay,board,story,logic,art,look}.js "$OUT/web/js/"
cp -R "$LIBAW/web/assets" "$OUT/web/assets"; rm -f "$OUT"/web/assets/*.py
# The engine as WebAssembly, for unit ranges on click (build it first: libaw web/README.md)
# (optional: without it the board still plays, just without ranges)
if [[ -f "$LIBAW/web/pkg/libaw_wasm.js" ]]; then
  mkdir -p "$OUT/web/pkg"; cp "$LIBAW"/web/pkg/libaw_wasm.js "$LIBAW"/web/pkg/libaw_wasm_bg.wasm "$OUT/web/pkg/"
else
  echo "note: no $LIBAW/web/pkg (wasm-pack build crates/wasm --release --target web --out-dir ../../web/pkg); ranges are off" >&2
fi
cp "$LIBAW"/data/{units,terrain,cos}.json "$OUT/data/"
for f in $(grep -ho "from '\./[a-z]*\.js'" "$OUT"/web/js/*.js | sed "s/from '\.\///; s/'//" | sort -u); do
  test -f "$OUT/web/js/$f" || { echo "missing js/$f" >&2; exit 1; }
done
echo "libaw $(git -C "$LIBAW" rev-parse --short HEAD)$(git -C "$LIBAW" diff --quiet || echo -dirty)" > "$OUT/VERSION"
rm -rf "$FINAL"; mv "$OUT" "$FINAL"
echo "vendored $(cat "$FINAL/VERSION") into $FINAL ($(du -sh "$FINAL" | cut -f1))"
