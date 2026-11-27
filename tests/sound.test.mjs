// node tests/sound.test.mjs: theme and effect choices (as Improved AWBW Music Player makes them).
import assert from 'assert/strict';
import { themeURL, stepSounds, sfxURL } from '../web/sound.js';

const B = 'https://josegperez.com/music';
assert.equal(themeURL('Jess'), `${B}/aw-ds/t-jess.ogg`);
assert.equal(themeURL('Jess', null, 'AW2'), `${B}/aw2/t-jess.ogg`);
assert.equal(themeURL('Von Bolt', null, 'AW2'), `${B}/aw-ds/t-vonbolt.ogg`);   // DS only
assert.equal(themeURL('Sturm'), `${B}/aw2/t-sturm.ogg`);                      // no DS theme
assert.equal(themeURL('Lash', 'cop'), `${B}/aw-ds/t-bh-co-power.ogg`);
assert.equal(themeURL('Andy', 'scop', 'AW2'), `${B}/aw2/t-ally-super-co-power.ogg`);
assert.equal(themeURL(null), null);
assert.equal(sfxURL('unit-explode'), `${B}/sfx/unit-explode.ogg`);

const move = { type: 'move', unit_id: 1, path: [[0, 0], [1, 0], [2, 0]] };
assert.deepEqual(stepSounds(move, { kind: 'Tank', seconds: 0.3 }),
  [{ name: 'move-tread-light', at: 0, stop: 0.3 }, { name: 'move-tread-light-rolloff', at: 0.3 }]);
assert.deepEqual(stepSounds(move, { kind: 'Infantry', seconds: 0.3 }), [{ name: 'move-inf', at: 0, stop: 0.3 }]);
assert.deepEqual(stepSounds(move, { kind: 'Rockets', seconds: 0.3 })[0].name, 'move-tires-heavy');   // libaw's name
assert.deepEqual(stepSounds({ ...move, path: [[0, 0]] }, { kind: 'Tank' }), []);                     // didn't move
assert.deepEqual(stepSounds({ type: 'fire', attacker: { display_hp: 10 }, defender: { display_hp: 4 } }, { seconds: 1.1 }), []);
assert.equal(stepSounds({ type: 'fire', attacker: { display_hp: 10 }, defender: { display_hp: 0 } }, { seconds: 1.1 })[0].name, 'unit-explode');
assert.deepEqual(stepSounds({ type: 'fire', attacker: { display_hp: null }, defender: { display_hp: 7 } }, { seconds: 1.1 }), [], 'hidden HP (Sonja) is no kill');
assert.equal(stepSounds({ type: 'capture', done: true })[0].name, 'unit-capture-ally');
assert.equal(stepSounds({ type: 'capture', done: false })[0].name, 'unit-capture-progress');
assert.equal(stepSounds({ type: 'build' })[0].name, 'unit-supply');
assert.equal(stepSounds({ type: 'other', action: 'AttackSeam' })[0].name, 'unit-attack-pipe-seam');
assert.deepEqual(stepSounds({ type: 'end' }), []);
console.log('sound ok');

// The CC0 set: every sound stepSounds can ask for has a file in web/sfx/cc0.
{
  const { existsSync } = await import('fs');
  const { fileURLToPath } = await import('url');
  const names = ['move-inf', 'move-mech', 'move-tread-light', 'move-tread-heavy', 'move-tires-light', 'move-tires-heavy',
    'move-plane', 'move-bcopter', 'move-tcopter', 'move-naval', 'move-sub', 'move-piperunner', 'move-tread-light-rolloff',
    'move-plane-rolloff', 'move-bcopter-rolloff', 'unit-explode', 'unit-capture-ally', 'unit-capture-progress', 'unit-supply',
    'unit-attack-pipe-seam'];
  for (const n of names) {
    const u = sfxURL(n, 'cc0');
    assert.ok(u && u.endsWith('.mp3'), n);
    assert.ok(existsSync(fileURLToPath(u)), `${n}: ${u}`);
  }
  assert.equal(sfxURL('no-such', 'cc0'), null);
  assert.equal(sfxURL('unit-explode', 'game'), `${B}/sfx/unit-explode.ogg`);
}
