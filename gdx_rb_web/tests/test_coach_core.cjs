// node tests/test_coach_core.cjs
'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const C = require('../CoachCore.js');
const repo = path.join(__dirname, '..', '..');
let passed = 0;
const test = (name, fn) => { fn(); passed++; console.log('ok -', name); };

test('JS extraction matches cue_extractor.py output for the sample lesson', () => {
  const srt = fs.readFileSync(path.join(repo, 'gdx_rb_coach', 'sample_lesson.srt'), 'utf8');
  const py = JSON.parse(fs.readFileSync(path.join(repo, 'gdx_rb_coach', 'sample_cues.json'), 'utf8')).cues;
  const js = C.loadCues(srt);
  assert.deepStrictEqual(js.map(c => [c.t, c.end, c.kind, c.text]), py.map(c => [c.t, c.end, c.kind, c.text]));
});
test('cue JSON is loaded as-is', () => {
  const json = fs.readFileSync(path.join(repo, 'gdx_rb_coach', 'sample_cues.json'), 'utf8');
  assert.strictEqual(C.loadCues(json).length, 10);
});
test('negated hold is not read as hold', () => {
  for (const s of ['息を止めないで', '息は止めずに', '呼吸を止めない', "Don't hold your breath"])
    assert.deepStrictEqual(C.findPhrases(s).map(h => h.kind), ['NO_HOLD'], s);
  assert.deepStrictEqual(C.findPhrases('息を止めて、吐きながら').map(h => h.kind), ['HOLD', 'EXHALE']);
  assert.deepStrictEqual(C.findPhrases('吸って、吐いて').map(h => h.kind), ['INHALE', 'EXHALE']);
});
test('HOLD ends an active NO_HOLD constraint', () => {
  const cues = C.finalizeCues([{t:0, kind:'NO_HOLD', text:''}, {t:5, kind:'HOLD', text:''}]);
  assert.strictEqual(cues[0].end, 5);
});
test('activeCues keeps one cue per channel', () => {
  const cues = C.finalizeCues([{t:0, kind:'NO_HOLD', text:''}, {t:1, kind:'INHALE', text:''}, {t:2, kind:'BRACE', text:''}]);
  assert.deepStrictEqual(C.activeCues(cues, 2.5).map(c => c.kind).sort(), ['BRACE', 'INHALE', 'NO_HOLD']);
});
test('evaluate follows BreathClassifier states', () => {
  const cue = kind => ({t:0, end:10, kind});
  const r = code => ({code, smooth:0, slope:0});
  assert.strictEqual(C.evaluate(cue('NO_HOLD'), 0.5, r('HOLD_CANDIDATE'), null).ok, null, 'reaction time');
  assert.strictEqual(C.evaluate(cue('NO_HOLD'), 2, r('HOLD_CANDIDATE'), null).ok, false);
  assert.strictEqual(C.evaluate(cue('NO_HOLD'), 2, r('EXHALE'), null).ok, true);
  assert.strictEqual(C.evaluate(cue('INHALE'), 2, r('INHALE'), null).ok, true);
  assert.strictEqual(C.evaluate(cue('INHALE'), 2, r('EXHALE'), 0.2).ok, false);
  assert.strictEqual(C.evaluate(cue('INHALE'), 2, r('TRANSITION'), 0.8).ok, true, 'already at inhale level');
  assert.strictEqual(C.evaluate(cue('EXHALE'), 2, r('TRANSITION'), 0.7).ok, null);
  assert.strictEqual(C.evaluate(cue('HOLD'), 2, r('TRANSITION'), null).ok, true);
  assert.strictEqual(C.evaluate(cue('BRACE'), 2, r('TRANSITION'), null).ok, null, 'no baseline');
  assert.strictEqual(C.evaluate(cue('BRACE'), 2, r('TRANSITION'), 0.9).ok, true);
  assert.strictEqual(C.evaluate(cue('RELAX'), 2, r('TRANSITION'), 0.9).ok, false);
  assert.strictEqual(C.evaluate(cue('INHALE'), 2, r('WARMUP'), 0.9).ok, null);
});
test('baseline normalises quiet breathing to 0..1', () => {
  const v = Array.from({length:200}, (_, i) => 18 + 4 * Math.sin(i / 10));
  const b = C.makeBaseline(v);
  assert.ok(Math.abs(C.normalise(b, b.lo)) < 1e-9 && Math.abs(C.normalise(b, b.hi) - 1) < 1e-9);
  assert.strictEqual(C.makeBaseline(Array(100).fill(18)), null, 'flat signal has no baseline');
});
console.log(`${passed} tests passed`);
