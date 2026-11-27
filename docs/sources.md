# Sources

Other people's work Battle Lab reads or uses, with its licence, checked before reading.

| What | Where | Licence | How we use it |
|---|---|---|---|
| Improved AWBW Music Player (DeveloperJose, _twiggy), v5.29.1 | https://greasyfork.org/en/scripts/518170-improved-awbw-music-player, source https://github.com/DeveloperJose/JS-AWBW-User-Scripts | MIT (Greasy Fork listing and `@license` header, checked 2026-11-26) | Its theme and sound-effect choices (which CO plays which theme, which unit makes which sound) inform `web/sound.js`, credited on the page. The audio files are not in this repo: like the script, the page loads them from the script author's server (josegperez.com/music/). The audio itself is game audio, not covered by the script's MIT licence; if Battle Lab grows past a handful of viewers, confirm with the author (Collin, 2026-11-26). |
| AWBW Battleground API | https://api.awbwbg.otw.gg/ | Public API (Brent) | Market feed, read in the browser. |
| libaw | https://github.com/collinjackson/libaw | MIT OR Apache-2.0 | Board, replay viewer, CO table (vendored at deploy). |
| Kenney's sound packs: Impact Sounds, Sci-Fi Sounds, Interface Sounds, Digital Audio | https://kenney.nl/assets?category=Audio | CC0 1.0 (each pack's License.txt, checked 2026-11-27) | The "CC0" choice of sound effects: trimmed and re-encoded copies in `web/sfx/cc0/` (its README lists each file's source). Two (`rotor`, `sub`) are made here with ffmpeg, also CC0. |
