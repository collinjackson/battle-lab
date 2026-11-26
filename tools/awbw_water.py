#!/usr/bin/env python3
"""Build web/awbw-water.json: where AWBW's joined sea and custom shoal tiles sit on its terrain
sprite sheet (terrain/terrain_spritesheet.png), per weather, read from AWBW's terrain/tilesets.js
as text (never run). libaw's viewer draws those tiles as clipped regions of the sheet, loaded
straight from AWBW. Two requests to AWBW; rerun only if AWBW changes its sheet. The deploy
scripts run it when the file is missing. The file is AWBW's data, so it is never committed.

  python3 -I tools/awbw_water.py
"""
import json, re, struct, urllib.request
from pathlib import Path

UA = {"User-Agent": "battle-lab/0.1 (AWBW Battleground viewer)"}
SITE = "https://awbw.amarriner.com/terrain/"


def get(path, rng=None):
    h = dict(UA, **({"Range": rng} if rng else {}))
    with urllib.request.urlopen(urllib.request.Request(SITE + path, headers=h), timeout=30) as r:
        return r.read()


def main():
    js = get("tilesets.js").decode()
    head = get("terrain_spritesheet.png", "bytes=0-23")
    assert head[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", head[16:24])
    s = js[js.index('"terrain/aw2/":{'):]
    out = {"sheet": SITE + "terrain_spritesheet.png", "width": width, "height": height, "tiles": {}}
    for weather in ("clear", "rain", "snow"):
        a = s.index(f"{weather}:{{"); b = s.index("}},", a)
        out["tiles"][weather] = {n: [int(x), int(y), int(w), int(h)] for n, x, y, w, h in
                                 re.findall(r"([a-z0-9]+):\{x:(\d+),y:(\d+),w:(\d+),h:(\d+)\}", s[a:b])
                                 if re.fullmatch(r"(sea|shoal)\d+", n)}
    f = Path(__file__).resolve().parent.parent / "web/awbw-water.json"
    f.write_text(json.dumps(out, separators=(",", ":")))
    print(f, width, height, {w: len(t) for w, t in out["tiles"].items()})


if __name__ == "__main__":
    main()
