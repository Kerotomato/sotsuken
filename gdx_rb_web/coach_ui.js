'use strict';
/*
 * Video-coaching panel. Loaded after app-main; uses its globals (app, el, settings).
 * app.js calls GDXCoach.onSample / onNewTrial / onSettingsChange (hooks added by build_html.py).
 */
(function () {
  const C = window.CoachCore;
  const CUE_COLOR = {INHALE:'#2f86b5', EXHALE:'#3c9a6a', HOLD:'#b07a2a', NO_HOLD:'#137c80', BRACE:'#b5523b', RELAX:'#7c8f9b'};
  const ROW = {constraint:0, tension:1, phase:2};
  const ROW_LABEL = ['制約', '張力', '呼吸相'];
  const BASELINE_S = 20, AUTO_BASELINE_S = 30, MIN_BASELINE_S = 10;
  const coach = {
    cues:[], source:'', mode:'manual', manualRunning:false, startPerf:0, pausedAt:0, offset:0,
    player:null, playerReady:false, baseline:null, baselinePol:1, stats:new Map(), last:null
  };
  const timeline = el('coachTimeline');
  const tctx = timeline.getContext('2d');

  function info(text, severity='info') {
    const e = el('coachInfo');
    e.textContent = text || '';
    e.className = text ? 'message active ' + severity : 'message';
  }
  function polarity() { return settings().polarity === 'decrease' ? -1 : 1; }

  // ---- clock: YouTube player time when available, otherwise a manual stopwatch ----
  function ytState() { try { return coach.player.getPlayerState(); } catch (_) { return -1; } }
  function clockRunning() {
    if (coach.mode === 'youtube') return coach.playerReady && ytState() === 1;
    return coach.manualRunning;
  }
  function videoTime() {
    if (coach.mode === 'youtube' && coach.playerReady) {
      try { return coach.player.getCurrentTime() + coach.offset; } catch (_) { return coach.offset; }
    }
    return (coach.manualRunning ? (performance.now() - coach.startPerf) / 1000 : coach.pausedAt) + coach.offset;
  }
  function toggleClock() {
    if (!coach.cues.length) return;
    if (coach.mode === 'youtube' && coach.playerReady) {
      if (ytState() === 1) coach.player.pauseVideo(); else { autoBaseline(); coach.player.playVideo(); }
      return;
    }
    if (coach.manualRunning) {
      coach.pausedAt = (performance.now() - coach.startPerf) / 1000;
      coach.manualRunning = false;
    } else {
      autoBaseline();
      coach.startPerf = performance.now() - coach.pausedAt * 1000;
      coach.manualRunning = true;
    }
  }
  function rewind() {
    coach.pausedAt = 0; coach.startPerf = performance.now();
    if (coach.mode === 'youtube' && coach.playerReady) coach.player.seekTo(0, true);
  }

  // ---- tension baseline from quiet breathing (smoothed force, in the inhale direction) ----
  function computeBaseline(spanS) {
    const rows = app.rows;
    if (!rows.length) return null;
    const lastT = rows[rows.length - 1].pc_elapsed_s;
    const win = rows.filter(r => r.pc_elapsed_s >= lastT - spanS);
    if (!win.length || lastT - win[0].pc_elapsed_s < MIN_BASELINE_S) return null;
    const pol = polarity();
    return C.makeBaseline(win.map(r => r.smooth_N * pol));
  }
  function setBaseline(b, how) {
    coach.baseline = b; coach.baselinePol = polarity();
    if (b) info(`基準を${how}しました（安静呼気 ${(b.lo * coach.baselinePol).toFixed(2)} N ／ 安静吸気 ${(b.hi * coach.baselinePol).toFixed(2)} N）。`);
  }
  function autoBaseline() {
    if (coach.baseline) return;
    const b = computeBaseline(AUTO_BASELINE_S);
    if (b) setBaseline(b, '直近の安静呼吸から自動取得');
    else info(`張力の基準が未取得です。「お腹を固める」「力を抜く」は判定しません（${MIN_BASELINE_S}秒以上の安静呼吸を計測してから開始するか「基準を取得」を押してください）。`, 'error');
  }

  // ---- hooks called from app.js ----
  function onSample(r, result) {
    r.video_time_s = ''; r.coach_cues = ''; r.coach_ok = '';
    if (!coach.cues.length || !clockRunning()) return;
    const tv = videoTime();
    const active = C.activeCues(coach.cues, tv);
    const norm = C.normalise(coach.baseline, result.smooth * coach.baselinePol);
    const evals = active.map(c => ({cue:c, ...C.evaluate(c, tv, result, norm)}));
    for (const e of evals) {
      if (e.ok === null) continue;
      const s = coach.stats.get(e.cue) || {ok:0, n:0};
      s.ok += e.ok ? 1 : 0; s.n += 1;
      coach.stats.set(e.cue, s);
    }
    const judged = evals.filter(e => e.ok !== null);
    r.video_time_s = tv.toFixed(3);
    r.coach_cues = active.map(c => c.kind).join('+');
    r.coach_ok = judged.length ? (judged.every(e => e.ok) ? 1 : 0) : '';
    coach.last = {perf:performance.now(), evals, norm};
    el('coachCsvBtn').disabled = false;
  }
  function onNewTrial() { coach.stats = new Map(); coach.last = null; renderTable(); }
  function onSettingsChange() {
    if (coach.baseline && polarity() !== coach.baselinePol) {
      coach.baseline = null;
      info('吸気時の張力方向が変わったため、張力の基準をリセットしました。', 'error');
    }
  }

  // ---- loading cues / YouTube ----
  async function loadCueFile(file) {
    try {
      const cues = C.loadCues(await file.text());
      if (!cues.length) { info('キーフレーズが見つかりませんでした（「吸いながら」「吐きながら」「息を止めない」など）。', 'error'); return; }
      coach.cues = cues; coach.source = file.name; coach.stats = new Map();
      rewind();
      el('coachStartBtn').disabled = false;
      info(`${file.name} から ${cues.length} 件のキューを読み込みました。`);
      renderTable();
    } catch (err) {
      info('キューを読み込めません: ' + String(err && err.message || err), 'error');
    }
  }
  function youtubeId(url) {
    const m = /(?:v=|youtu\.be\/|shorts\/|embed\/)([\w-]{11})/.exec(url);
    return m ? m[1] : (/^[\w-]{11}$/.test(url) ? url : null);
  }
  function loadYouTubeApi() {
    if (window.YT && window.YT.Player) return Promise.resolve();
    return new Promise((resolve, reject) => {
      const prev = window.onYouTubeIframeAPIReady;
      window.onYouTubeIframeAPIReady = () => { if (prev) prev(); resolve(); };
      const s = document.createElement('script');
      s.src = 'https://www.youtube.com/iframe_api';
      s.onerror = () => reject(new Error('YouTube IFrame API を読み込めません'));
      document.head.appendChild(s);
      setTimeout(() => reject(new Error('YouTube IFrame API の読み込みがタイムアウトしました')), 10000);
    });
  }
  async function loadYouTube() {
    const id = youtubeId(el('ytUrl').value.trim());
    if (!id) { info('YouTube の URL を確認してください。', 'error'); return; }
    try {
      await loadYouTubeApi();
      el('ytPlayerWrap').hidden = false;
      if (coach.player) { coach.player.cueVideoById(id); coach.mode = 'youtube'; return; }
      coach.player = new YT.Player('ytPlayer', {
        videoId:id, playerVars:{playsinline:1, rel:0},
        events:{
          onReady:() => { coach.playerReady = true; coach.mode = 'youtube'; coach.manualRunning = false;
                          info('YouTube と同期します。動画を再生するとコーチングが進みます。'); },
          onStateChange:e => { if (e.data === 1) autoBaseline(); },
          onError:e => {
            coach.mode = 'manual'; coach.playerReady = false;
            info(`動画を埋め込み再生できません（エラー ${e.data}）。HTMLを直接開いている場合は localhost から開くか、別タブで動画を再生して「コーチング開始」で手動同期してください。`, 'error');
          }
        }
      });
    } catch (err) {
      coach.mode = 'manual';
      info(String(err.message || err) + '。手動同期（コーチング開始ボタン）を使用してください。', 'error');
    }
  }

  // ---- rendering ----
  function renderCard(tv, running) {
    const active = C.activeCues(coach.cues, tv);
    const fresh = coach.last && performance.now() - coach.last.perf < 1500 ? coach.last : null;
    el('coachCue').textContent = active.length ? active.map(c => C.CUE_KINDS[c.kind][0]).join(' ＋ ') : (coach.cues.length ? '（指示なし）' : '—');
    el('coachCue').style.color = active.length ? CUE_COLOR[active[active.length - 1].kind] : '';
    let msg = '', state = 'idle';
    if (!coach.cues.length) msg = 'キューファイルを読み込んでください';
    else if (!running) msg = coach.mode === 'youtube' ? '動画を再生すると開始します' : '「コーチング開始」を動画の再生と同時に押してください';
    else if (!app.running) msg = 'センサ計測中ではないため判定していません';
    else if (fresh) {
      const bad = fresh.evals.filter(e => e.ok === false);
      const ok = fresh.evals.filter(e => e.ok === true);
      msg = bad.length ? bad[0].msg : fresh.evals.map(e => e.msg).join(' ／ ');
      state = bad.length ? 'ng' : ok.length ? 'ok' : 'idle';
    }
    el('coachMsg').textContent = msg;
    el('coachCard').className = 'coach-card ' + state;

    // gauge: normalised tension, -0.4 .. 1.6
    const pct = v => Math.min(100, Math.max(0, (v + 0.4) / 2 * 100));
    const bandCue = active.slice().reverse().find(c => C.TARGET_BAND[c.kind]);
    if (bandCue) {
      const [lo, hi] = C.TARGET_BAND[bandCue.kind];
      Object.assign(el('gaugeBand').style, {display:'block', left:pct(lo) + '%', width:(pct(hi) - pct(lo)) + '%'});
    } else el('gaugeBand').style.display = 'none';
    const norm = fresh ? fresh.norm : null;
    el('gaugeMarker').style.display = norm === null ? 'none' : 'block';
    if (norm !== null) el('gaugeMarker').style.left = pct(norm) + '%';
    el('gaugeText').textContent = coach.baseline ? (norm === null ? '—' : norm.toFixed(2)) : '基準未取得';
  }
  function renderTimeline(tv) {
    const rect = timeline.getBoundingClientRect();
    const w = Math.max(200, rect.width), h = Math.max(90, rect.height);
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    if (timeline.width !== Math.round(w * dpr) || timeline.height !== Math.round(h * dpr)) {
      timeline.width = Math.round(w * dpr); timeline.height = Math.round(h * dpr);
    }
    tctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    tctx.clearRect(0, 0, w, h); tctx.fillStyle = '#fff'; tctx.fillRect(0, 0, w, h);
    const left = 52, right = 10, top = 6, bottom = 22, rowH = (h - top - bottom) / 3;
    const x0 = tv - 5, x1 = tv + 15, xp = x => left + (x - x0) / (x1 - x0) * (w - left - right);
    tctx.font = '11px -apple-system,BlinkMacSystemFont,"Yu Gothic",sans-serif';
    tctx.textBaseline = 'middle';
    ROW_LABEL.forEach((lab, i) => {
      const y = top + (2 - i) * rowH;
      tctx.fillStyle = '#f5f9fb'; tctx.fillRect(left, y + 2, w - left - right, rowH - 4);
      tctx.fillStyle = '#5d788b'; tctx.textAlign = 'right'; tctx.fillText(lab, left - 8, y + rowH / 2);
    });
    tctx.save(); tctx.beginPath(); tctx.rect(left, 0, w - left - right, h); tctx.clip();
    for (const c of coach.cues) {
      if (c.end < x0 || c.t > x1) continue;
      const y = top + (2 - ROW[C.CUE_KINDS[c.kind][1]]) * rowH;
      const bx = xp(c.t), bw = Math.max(2, xp(c.end) - bx);
      tctx.fillStyle = CUE_COLOR[c.kind];
      tctx.fillRect(bx, y + 3, bw, rowH - 6);
      tctx.save(); tctx.beginPath(); tctx.rect(bx, y, bw - 2, rowH); tctx.clip();   // keep the label inside its bar
      tctx.fillStyle = '#fff'; tctx.textAlign = 'left';
      tctx.fillText(C.CUE_KINDS[c.kind][0], Math.max(bx, left) + 4, y + rowH / 2);
      tctx.restore();
    }
    tctx.restore();
    tctx.strokeStyle = '#17394d'; tctx.lineWidth = 2;
    tctx.beginPath(); tctx.moveTo(xp(tv), top); tctx.lineTo(xp(tv), h - bottom); tctx.stroke();
    tctx.fillStyle = '#617889'; tctx.textAlign = 'center'; tctx.textBaseline = 'top';
    for (let s = Math.ceil(x0 / 5) * 5; s <= x1; s += 5) if (s >= 0) tctx.fillText(s + 's', xp(s), h - bottom + 5);
  }
  function renderTable() {
    const rows = coach.cues.map(c => {
      const s = coach.stats.get(c);
      const pct = s && s.n ? (s.ok / s.n * 100).toFixed(0) + '%' : '—';
      return `<tr><td>${c.t.toFixed(1)}–${c.end.toFixed(1)}</td><td style="color:${CUE_COLOR[c.kind]};font-weight:700">${C.CUE_KINDS[c.kind][0]}</td>` +
             `<td>${escapeHtml(c.text)}</td><td class="num">${pct}</td></tr>`;
    });
    el('coachTable').innerHTML = '<tr><th>動画時刻 (s)</th><th>キュー</th><th>フレーズ</th><th>適合率</th></tr>' + rows.join('');
  }
  function escapeHtml(s) { return String(s).replace(/[&<>"]/g, ch => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;'}[ch])); }
  function render() {
    const running = clockRunning();
    const tv = videoTime();
    el('coachClockText').textContent = !coach.cues.length ? 'キュー未読込' :
      `${running ? '同期中' : '停止中'} · ${tv.toFixed(1)} s${coach.offset ? `（補正 ${coach.offset > 0 ? '+' : ''}${coach.offset.toFixed(1)} s）` : ''} · ${coach.mode === 'youtube' ? 'YouTube' : '手動'}`;
    el('coachClock').className = 'status' + (running ? ' running' : coach.cues.length ? ' warn' : '');
    el('coachStartBtn').textContent = running ? 'コーチング一時停止' : 'コーチング開始';
    renderCard(tv, running);
    renderTimeline(tv);
  }
  function downloadResults() {
    const lines = [['cue_start_s', 'cue_end_s', 'kind', 'label', 'phrase', 'judged_samples', 'ok_samples', 'compliance', 'context'].join(',')];
    for (const c of coach.cues) {
      const s = coach.stats.get(c) || {ok:0, n:0};
      lines.push([c.t, c.end, c.kind, C.CUE_KINDS[c.kind][0], c.text, s.n, s.ok, s.n ? (s.ok / s.n).toFixed(3) : '', c.context || '']
        .map(escapeCsvCell).join(','));
    }
    const blob = new Blob(['﻿', lines.join('\r\n'), '\r\n'], {type:'text/csv;charset=utf-8'});
    const uri = URL.createObjectURL(blob), link = document.createElement('a');
    link.href = uri; link.download = (app.sessionName || 'GDX_RB_Coach') + '_coach.csv';
    document.body.appendChild(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(uri), 60000);
  }

  el('cueFile').addEventListener('change', e => { if (e.target.files[0]) loadCueFile(e.target.files[0]); });
  el('ytLoadBtn').addEventListener('click', loadYouTube);
  el('coachStartBtn').addEventListener('click', toggleClock);
  el('coachBackBtn').addEventListener('click', () => { coach.offset -= 0.5; });
  el('coachFwdBtn').addEventListener('click', () => { coach.offset += 0.5; });
  el('coachRewindBtn').addEventListener('click', rewind);
  el('baselineBtn').addEventListener('click', () => {
    const b = computeBaseline(BASELINE_S);
    if (b) setBaseline(b, `直近${BASELINE_S}秒から取得`);
    else info(`基準を取得できません。${MIN_BASELINE_S}秒以上、安静呼吸の張力を計測してから押してください。`, 'error');
  });
  el('coachCsvBtn').addEventListener('click', downloadResults);
  setInterval(render, 100);
  setInterval(() => { if (clockRunning()) renderTable(); }, 1000);
  window.GDXCoach = {onSample, onNewTrial, onSettingsChange, _state:coach};
  render();
})();
