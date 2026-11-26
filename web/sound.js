// Optional music and sound effects, in step with the board.
//
// The choices of which theme and which sound follow "Improved AWBW Music Player" by DeveloperJose
// and _twiggy (MIT, https://greasyfork.org/en/scripts/518170-improved-awbw-music-player), and the
// audio files load from the same place that script loads them (the author's server). The files are
// game audio, not part of this repo. See docs/sources.md.

const BASE = 'https://josegperez.com/music';

// From the script: Black Hole COs get Black Hole power themes; some COs only have DS themes, and
// Sturm has no DS theme.
const BLACK_HOLE = new Set(['flak', 'lash', 'adder', 'hawke', 'sturm', 'jugger', 'koal', 'kindle', 'vonbolt']);
const DS_ONLY = new Set(['jake', 'rachel', 'sasha', 'javier', 'grimm', 'kindle', 'jugger', 'koal', 'vonbolt']);

// From the script: the sound a unit makes as it starts to move, and as it stops (if any).
const MOVE = {
  APC: 'move-tread-light', 'Anti-Air': 'move-tread-light', Artillery: 'move-tread-light', 'B-Copter': 'move-bcopter',
  Battleship: 'move-naval', 'Black Boat': 'move-naval', 'Black Bomb': 'move-plane', Bomber: 'move-plane', Carrier: 'move-naval',
  Cruiser: 'move-naval', Fighter: 'move-plane', Infantry: 'move-inf', Lander: 'move-naval', 'Md.Tank': 'move-tread-heavy',
  Mech: 'move-mech', 'Mega Tank': 'move-tread-heavy', Missile: 'move-tires-heavy', Neotank: 'move-tread-heavy',
  Piperunner: 'move-piperunner', Recon: 'move-tires-light', Rocket: 'move-tires-heavy', Stealth: 'move-plane', Sub: 'move-sub',
  'T-Copter': 'move-tcopter', Tank: 'move-tread-light',
};
const ROLLOFF = new Set(['APC', 'Anti-Air', 'Artillery', 'B-Copter', 'Black Bomb', 'Bomber', 'Fighter', 'Md.Tank', 'Mega Tank',
  'Missile', 'Neotank', 'Recon', 'Rocket', 'Stealth', 'T-Copter', 'Tank']);
// libaw's unit names that differ from AWBW's.
const ALIAS = { Missiles: 'Missile', Rockets: 'Rocket' };

const coKey = (co) => String(co ?? '').toLowerCase().replace(/[^a-z]/g, '');

/** The theme's URL for a CO (power: null | 'cop' | 'scop'), on soundtrack 'DS' or 'AW2'. */
export function themeURL(co, power = null, game = 'DS') {
  const c = coKey(co);
  if (!c) return null;
  let dir = game === 'AW2' && !DS_ONLY.has(c) ? 'aw2' : 'aw-ds';
  if (c === 'sturm') dir = 'aw2';
  if (!power) return `${BASE}/${dir}/t-${c}.ogg`;
  return `${BASE}/${dir}/t-${BLACK_HOLE.has(c) ? 'bh' : 'ally'}-${power === 'scop' ? 'super-co-power' : 'co-power'}.ogg`;
}

export const sfxURL = (name) => `${BASE}/sfx/${name}.ogg`;

/** The sounds for one storyboard step: [{name, at (s after the step starts), stop (s, to cut a
 *  loop)}]. kind: the moving unit's kind; seconds: the step's length on the timeline. */
export function stepSounds(st, { kind = null, seconds = 0 } = {}) {
  switch (st.type) {
    case 'move': {
      const k = ALIAS[kind] ?? kind;
      if (!MOVE[k] || (st.path?.length ?? 0) < 2) return [];
      const out = [{ name: MOVE[k], at: 0, stop: seconds }];
      if (ROLLOFF.has(k)) out.push({ name: `${MOVE[k]}-rolloff`, at: seconds });
      return out;
    }
    case 'fire': {
      const dead = [st.attacker, st.defender].some((s) => s && s.display_hp != null && s.display_hp <= 0);   // null: hidden HP (Sonja)
      return dead ? [{ name: 'unit-explode', at: Math.min(0.6, seconds * 0.6) }] : [];
    }
    case 'capture': return [{ name: st.done ? 'unit-capture-ally' : 'unit-capture-progress', at: 0.1 }];
    case 'build': return [{ name: 'unit-supply', at: 0 }];
    case 'join': return [{ name: 'unit-supply', at: 0 }];
    case 'other': return st.action === 'AttackSeam' ? [{ name: 'unit-attack-pipe-seam', at: 0 }] : [];
    default: return [];
  }
}

/** Plays music and effects. Nothing sounds until on() is called from a click (browsers allow
 *  audio only after the viewer interacts).
 *
 *  Effects go through one AudioContext: each file is fetched and decoded once, and each play is a
 *  short-lived buffer source that frees itself when it ends. (A new <audio> per effect piles up
 *  media players until the browser stops starting new ones, which silenced effects after a while
 *  while the music played on.) If decoding fails (a browser without Ogg in Web Audio), effects
 *  fall back to a few reused <audio> elements. */
export class Sound {
  constructor({ music = 0.35, sfx = 0.5, game = 'DS' } = {}) {
    this.enabled = false; this.vol = { music, sfx }; this.game = game;
    this.track = null; this.trackURL = null;
    this.ctx = null; this.gain = null; this.buffers = new Map(); this.live = new Set();
    this.pool = []; this.useElements = false;
    this.stats = { played: 0, failed: 0 };   // for checking from the console / tests
    this.resume = null;   // {url, t}: where a theme was when the page went away (a reload)
    this.onBlocked = () => {};   // (blocked: boolean) the browser is holding sound back until a click
  }

  on() {
    this.enabled = true;
    try {
      this.ctx ??= new (window.AudioContext || window.webkitAudioContext)();
      if (!this.gain) { this.gain = this.ctx.createGain(); this.gain.connect(this.ctx.destination); }
      this.gain.gain.value = this.vol.sfx;
      this.ctx.resume?.();
    } catch { this.useElements = true; }
    if (this.trackURL) this.playTheme(this.trackURL, true);
  }

  off() {
    this.enabled = false;
    this.track?.pause();
    this.stopAll();
  }

  stopAll() {
    for (const n of this.live) { try { n.stop(); } catch {} }
    this.live.clear();
    for (const a of this.pool) a.pause();
  }

  setVolume(kind, v) {
    this.vol[kind] = v;
    if (kind === 'music' && this.track) this.track.volume = v;
    if (kind === 'sfx' && this.gain) this.gain.gain.value = v;
  }

  /** Loop a theme (by URL); the same URL keeps playing. */
  playTheme(url, force = false) {
    if (!url || (url === this.trackURL && !force && this.track && !this.track.paused)) { this.trackURL = url; return; }
    this.trackURL = url;
    if (!this.enabled) return;
    const old = this.track;
    const a = new Audio(url);
    a.loop = true; a.volume = this.vol.music;
    a.onerror = () => {   // no such theme on this soundtrack: try the other one, then stay quiet
      if (url.includes('/aw-ds/')) this.playTheme(url.replace('/aw-ds/', '/aw2/'), true);
    };
    // Pick up where the same theme was before a reload.
    if (this.resume?.url === url && this.resume.t > 0) {
      const t = this.resume.t;
      a.addEventListener('loadedmetadata', () => { try { a.currentTime = t % (a.duration || Infinity); } catch {} }, { once: true });
    }
    this.resume = null;
    // Just after a reload Chrome can refuse the first play() and allow it a moment later (the
    // tab's earlier click carries over once the page is up): try again a few times.
    const tries = [300, 800, 1500, 3000, 6000];
    const go = () => a.play().then(() => this.onBlocked(false), (e) => {
      if (e?.name !== 'NotAllowedError') return;
      if (!tries.length) { this.onBlocked(true); return; }
      setTimeout(() => { if (this.track === a && this.enabled && a.paused) go(); }, tries.shift());
    });
    go();
    if (old) { old.pause(); old.removeAttribute('src'); old.load(); }   // release its player
    this.track = a;
  }

  buffer(name) {
    if (!this.buffers.has(name)) {
      const p = fetch(sfxURL(name)).then((r) => { if (!r.ok) throw new Error(r.status); return r.arrayBuffer(); })
        .then((b) => new Promise((ok, no) => this.ctx.decodeAudioData(b, ok, no)));
      p.catch(() => {});
      this.buffers.set(name, p);
    }
    return this.buffers.get(name);
  }

  /** One effect, now; if stop is given, it loops and is cut after that many seconds (movement). */
  play(name, stop = null) {
    if (!this.enabled || !this.vol.sfx) return;
    if (this.useElements || !this.ctx) return this.playElement(name, stop);
    if (this.ctx.state === 'suspended') this.ctx.resume?.();
    this.buffer(name).then((buf) => {
      if (!this.enabled) return;
      const n = this.ctx.createBufferSource();
      n.buffer = buf; n.loop = !!stop; n.connect(this.gain);
      n.onended = () => { n.disconnect(); this.live.delete(n); };
      this.live.add(n);
      n.start();
      if (stop) n.stop(this.ctx.currentTime + stop);
      this.stats.played++;
    }, () => { this.stats.failed++; this.useElements = true; this.playElement(name, stop); });
  }

  // Fallback: at most 8 <audio> elements, reused.
  playElement(name, stop) {
    let a = this.pool.find((x) => x.paused || x.ended);
    if (!a) { if (this.pool.length >= 8) return; a = new Audio(); this.pool.push(a); }
    clearTimeout(a._stop);
    a.src = sfxURL(name); a.volume = this.vol.sfx; a.loop = !!stop; a.currentTime = 0;
    a.play().then(() => this.stats.played++, () => this.stats.failed++);
    if (stop) a._stop = setTimeout(() => a.pause(), stop * 1000);
  }
}
