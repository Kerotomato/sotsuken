/*
 * GDX-RB coaching core: breathing-cue extraction from subtitles and
 * per-sample judgement against BreathClassifier output.
 * Cue rules mirror gdx_rb_coach/breath_core.py (checked by tests/test_coach_core.cjs).
 * Judgements are heuristic feedback on belt tension, NOT clinical assessments.
 * No external dependencies. Embedded verbatim inside GDX_RB_Coach_Live.html.
 */
(function (root) {
  'use strict';
  // kind: [label, channel, max duration s]
  //   phase: breathing-phase instruction / tension: abdominal tension / constraint: e.g. "do not hold"
  const CUE_KINDS = Object.freeze({
    NO_HOLD: ['息を止めない', 'constraint', 20],
    HOLD:    ['息を止める',   'phase',       8],
    INHALE:  ['吸いながら',   'phase',       6],
    EXHALE:  ['吐きながら',   'phase',       6],
    BRACE:   ['お腹を固める', 'tension',    10],
    RELAX:   ['力を抜く',     'tension',     6]
  });
  // Earlier rules win at the same position, so "息を止めない" is never read as "息を止め".
  const CUE_RULES = [
    ['NO_HOLD', "(息|呼吸)[をは]?(止め|とめ|こらえ|詰め)(ない|ず|ないで)|呼吸を?続け|(don'?t|do not|never) hold (your )?breath|keep breathing"],
    ['HOLD',    '(息|呼吸)[をは]?(止め|とめ)(て|る|たまま|ます)|息を?こらえ|hold (your )?breath'],
    ['INHALE',  '吸いながら|(息を?)?吸(って|う|い込|います)|breathe in|inhal'],
    ['EXHALE',  '吐きながら|(息を?)?吐(いて|く|き切|きます)|breathe out|exhal'],
    ['BRACE',   'お腹に力|腹圧|お腹を?(固め|締め|膨らま|張)|腹筋に力|ブレーシング|brac'],
    ['RELAX',   '力を?抜|リラックス|脱力|緩め|ゆるめ|relax']
  ].map(([kind, src]) => [kind, new RegExp(src, 'giu')]);

  const REACTION_S = 0.8;   // no judgement right after a cue appears
  const TARGET_BAND = Object.freeze({INHALE:[0.6, 1.3], EXHALE:[-0.3, 0.4], BRACE:[0.6, 1.6], RELAX:[-0.3, 0.35]});

  const round2 = v => Math.round(v * 100) / 100;

  function findPhrases(text) {
    const hits = [];
    CUE_RULES.forEach(([kind, re], prio) => {
      re.lastIndex = 0;
      for (const m of text.matchAll(re)) hits.push({start:m.index, prio, end:m.index + m[0].length, kind, s:m[0]});
    });
    hits.sort((a, b) => a.start - b.start || a.prio - b.prio || a.end - b.end);
    const out = [];
    let lastEnd = -1;
    for (const h of hits) {
      if (h.start < lastEnd) continue;
      out.push({pos:h.start, kind:h.kind, text:h.s});
      lastEnd = h.end;
    }
    return out;
  }

  function finalizeCues(cues, mergeS = 2.0) {
    const sorted = cues.slice().sort((a, b) => a.t - b.t);
    const merged = [];
    for (const c of sorted) {
      const prev = merged[merged.length - 1];
      if (prev && prev.kind === c.kind && c.t - prev.t < mergeS) continue;
      merged.push({...c});
    }
    merged.forEach((c, i) => {
      let end = c.t + CUE_KINDS[c.kind][2];
      for (const n of merged.slice(i + 1)) {
        // "息を止めて" lifts an active "息を止めない" constraint.
        if (CUE_KINDS[n.kind][1] === CUE_KINDS[c.kind][1] || (c.kind === 'NO_HOLD' && n.kind === 'HOLD')) {
          end = Math.min(end, n.t); break;
        }
      }
      c.end = round2(end);
    });
    return merged;
  }

  // SRT / WebVTT -> [{start, end, text}]
  function parseSubtitles(src) {
    const ts = '(\\d+):(\\d+):(\\d+)[,.](\\d+)';
    const re = new RegExp(ts + '\\s*-->\\s*' + ts);
    const sec = (h, m, s, ms) => Number(h) * 3600 + Number(m) * 60 + Number(s) + Number(ms) / 10 ** ms.length;
    const out = [];
    for (const block of src.replace(/^﻿/, '').replace(/\r\n?/g, '\n').split(/\n\s*\n/)) {
      const m = re.exec(block);
      if (!m) continue;
      const body = block.slice(m.index + m[0].length).trim().replace(/<[^>]+>/g, '').replace(/\n/g, ' ');
      out.push({start:sec(...m.slice(1, 5)), end:sec(...m.slice(5, 9)), text:body});
    }
    return out;
  }

  function extractCues(segments, lead = 0) {
    const cues = [];
    for (const seg of segments) {
      for (const p of findPhrases(seg.text)) {
        // Position inside the line -> time, by linear interpolation.
        const t = seg.start + p.pos / Math.max(seg.text.length, 1) * (seg.end - seg.start);
        cues.push({t:round2(Math.max(0, t - lead)), kind:p.kind, text:p.text, end:0, context:seg.text});
      }
    }
    return finalizeCues(cues);
  }

  // cue_extractor.py JSON, or raw SRT/VTT text
  function loadCues(text) {
    const trimmed = text.replace(/^﻿/, '').trim();
    if (trimmed.startsWith('{')) {
      const cues = JSON.parse(trimmed).cues;
      if (!Array.isArray(cues)) throw new Error('cues 配列がありません');
      return cues.filter(c => c.kind in CUE_KINDS && Number.isFinite(c.t) && Number.isFinite(c.end));
    }
    return extractCues(parseSubtitles(trimmed));
  }

  function activeCues(cues, t) {
    const byChannel = {};
    for (const c of cues) if (c.t <= t && t < c.end) byChannel[CUE_KINDS[c.kind][1]] = c;
    return Object.values(byChannel);
  }

  // Tension normalised to quiet breathing: 0 = end of quiet exhale (p10), 1 = end of quiet inhale (p90).
  function makeBaseline(values) {
    if (values.length < 20) return null;
    const s = values.slice().sort((a, b) => a - b);
    const q = p => s[Math.min(s.length - 1, Math.max(0, Math.round(p * (s.length - 1))))];
    const lo = q(0.10), hi = q(0.90);
    return hi - lo > 0.05 ? {lo, hi} : null;
  }
  const normalise = (baseline, value) => baseline ? (value - baseline.lo) / (baseline.hi - baseline.lo) : null;

  // res: BreathClassifier.process() output. norm: normalised tension or null.
  // Returns {ok: true|false|null, msg}; null means "not judged".
  function evaluate(cue, tVideo, res, norm) {
    const label = CUE_KINDS[cue.kind][0];
    if (tVideo - cue.t < REACTION_S || !res || res.code === 'WARMUP') return {ok:null, msg:label};
    const code = res.code;
    const band = TARGET_BAND[cue.kind];
    switch (cue.kind) {
      case 'NO_HOLD':
        return code === 'HOLD_CANDIDATE'
          ? {ok:false, msg:'張力の変化が小さい状態が続いています（息止めの可能性）→ ゆっくり吐き続けましょう'}
          : {ok:true, msg:'呼吸は続いています（推定）'};
      case 'HOLD':
        if (code === 'TRANSITION' || code === 'HOLD_CANDIDATE') return {ok:true, msg:'張力を保持できています'};
        return {ok:false, msg:'張力をそのまま保ちましょう'};
      case 'INHALE':
        if (code === 'INHALE' || (norm !== null && norm >= band[0])) return {ok:true, msg:'吸えています（推定）'};
        if (code === 'TRANSITION') return {ok:null, msg:'お腹を膨らませるように吸いましょう'};
        return {ok:false, msg:'お腹を膨らませるように吸いましょう'};
      case 'EXHALE':
        if (code === 'EXHALE' || (norm !== null && norm <= band[1])) return {ok:true, msg:'吐けています（推定）'};
        if (code === 'TRANSITION') return {ok:null, msg:'ゆっくり吐きましょう'};
        return {ok:false, msg:'ゆっくり吐きましょう'};
      case 'BRACE':
        if (norm === null) return {ok:null, msg:'基準未取得のため腹圧を判定できません'};
        return norm >= band[0] ? {ok:true, msg:'お腹周りの張力 OK'} : {ok:false, msg:'お腹周りをもう少し張りましょう'};
      case 'RELAX':
        if (norm === null) return {ok:null, msg:'基準未取得のため脱力を判定できません'};
        return norm <= band[1] ? {ok:true, msg:'力が抜けています'} : {ok:false, msg:'お腹の力を抜きましょう'};
    }
    return {ok:null, msg:''};
  }

  const api = {CUE_KINDS, CUE_RULES, TARGET_BAND, REACTION_S, findPhrases, finalizeCues, parseSubtitles,
               extractCues, loadCues, activeCues, makeBaseline, normalise, evaluate};
  root.CoachCore = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
