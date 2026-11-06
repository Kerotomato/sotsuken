"""v1.2 (動画コーチング) を v1.1 から生成する。v1_1/ 以下は先生の配布物のまま変更しない。

  python build_html.py   ->  GDX_RB_Coach_Live.html
"""
import hashlib
from pathlib import Path

p = Path(__file__).parent
v11 = p / 'v1_1'

# v1.1 が配布時のままであることを確認
for line in (v11 / 'MANIFEST_SHA256.txt').read_text().splitlines():
    digest, name = line.split(maxsplit=1)
    f = v11 / name
    if f.exists():
        assert hashlib.sha256(f.read_bytes()).hexdigest() == digest, f'{name} が v1.1 配布物と一致しません'

base = (v11 / 'GDX_RB_Force_Live.html').read_text(encoding='utf8')

css = '''
    .coach-grid {display:grid;grid-template-columns:minmax(220px,1fr) minmax(260px,1.4fr);gap:12px;align-items:end;}
    .coach-grid input[type=url],.coach-grid input[type=file] {width:100%;padding:9px;border-radius:7px;border:1px solid #b9cbd6;color:#14354a;font:inherit;font-size:14px;background:#fff;min-height:42px;}
    .coach-grid .inline {display:flex;gap:8px;}
    .coach-grid .inline button {white-space:nowrap;}
    .yt-wrap {margin-top:14px;aspect-ratio:16/9;max-width:640px;border-radius:8px;overflow:hidden;background:#0b1a24;}
    .yt-wrap iframe {width:100%;height:100%;border:0;display:block;}
    .coach-card {display:flex;gap:18px;align-items:center;justify-content:space-between;flex-wrap:wrap;border:1px solid #cddce6;border-radius:12px;padding:18px 22px;background:#f6f9fb;margin-top:14px;transition:background .2s,border-color .2s;}
    .coach-card.ok {border-color:#90c6a7;background:#eefaf1;}
    .coach-card.ng {border-color:#e2a49c;background:#fff1ef;}
    .coach-cue {font-size:clamp(25px,3vw,39px);font-weight:750;line-height:1.24;color:#29465a;}
    .coach-msg {font-size:15px;color:#496576;line-height:1.6;margin-top:8px;font-weight:600;}
    .coach-card.ok .coach-msg {color:#206548;}
    .coach-card.ng .coach-msg {color:#a1302a;}
    .gauge {width:min(320px,100%);font-size:12px;color:#5d7383;}
    .gauge-track {position:relative;height:26px;margin:8px 0 4px;border-radius:6px;background:#e8eff3;overflow:hidden;}
    .gauge-band {position:absolute;top:0;bottom:0;background:rgba(60,154,106,.28);border-left:2px solid #3c9a6a;border-right:2px solid #3c9a6a;}
    .gauge-marker {position:absolute;top:2px;bottom:2px;width:6px;margin-left:-3px;border-radius:3px;background:#17394d;}
    .gauge-ticks {display:flex;justify-content:space-between;font-size:11px;color:#7b8f9c;}
    .gauge strong {font-size:19px;color:#17394d;font-variant-numeric:tabular-nums;}
    .coach-timeline {width:100%;height:110px;display:block;margin-top:14px;border:1px solid #dae5ec;border-radius:8px;}
    .coach-table {border-collapse:collapse;font-size:12px;margin-top:10px;width:100%;}
    .coach-table th,.coach-table td {border-bottom:1px solid #e5edf2;padding:5px 8px;text-align:left;}
    .coach-table td.num {text-align:right;font-variant-numeric:tabular-nums;}
    details.coach-details {margin-top:12px;font-size:13px;color:#466073;}
    @media (max-width:840px) { .coach-grid{grid-template-columns:1fr;} }
'''
base = base.replace('  </style>', css + '  </style>', 1)

for old, new in [
    ('<title>GDX-RB Force Live v1.1 | 呼吸フィードバック</title>', '<title>GDX-RB Coach Live v1.2 | 呼吸フィードバック・動画コーチング</title>'),
    ('<span style="font-size:.55em;opacity:.8">v1.1</span></h1>', '<span style="font-size:.55em;opacity:.8">v1.2 coaching</span></h1>'),
    ('のリアルタイム表示・呼吸状態の推定・CSV保存', 'のリアルタイム表示・呼吸状態の推定・動画の指示に合わせたコーチング・CSV保存'),
]:
    assert old in base, old
    base = base.replace(old, new, 1)

panel = '''    <section class="panel" aria-label="動画コーチング">
      <div class="topline"><h2>動画コーチング（指導音声のキーフレーズに連動）</h2><div id="coachClock" class="status"><span class="dot"></span><span id="coachClockText">キュー未読込</span></div></div>
      <div class="coach-grid">
        <label class="field" for="cueFile">キュー（cue_extractor.py の JSON ／ 字幕 SRT・VTT）
          <input type="file" id="cueFile" accept=".json,.srt,.vtt,.txt">
        </label>
        <label class="field" for="ytUrl">YouTube URL（任意：再生位置に自動同期）
          <span class="inline"><input type="url" id="ytUrl" placeholder="https://www.youtube.com/watch?v=..."><button id="ytLoadBtn">動画を読込</button></span>
        </label>
      </div>
      <div class="buttonbar">
        <button id="coachStartBtn" class="primary" disabled>コーチング開始</button>
        <button id="coachBackBtn">−0.5 s</button>
        <button id="coachFwdBtn">＋0.5 s</button>
        <button id="coachRewindBtn">先頭に戻す</button>
        <button id="baselineBtn">張力の基準を取得（直近20秒）</button>
        <button id="coachCsvBtn" disabled>コーチング結果CSV</button>
      </div>
      <div id="coachInfo" class="message" role="status" aria-live="polite"></div>
      <div id="ytPlayerWrap" class="yt-wrap" hidden><div id="ytPlayer"></div></div>
      <div class="coach-card idle" id="coachCard" role="status" aria-live="polite">
        <div><div class="phase-lead">動画の指示</div><div class="coach-cue" id="coachCue">—</div><div class="coach-msg" id="coachMsg"></div></div>
        <div class="gauge">腹部張力（安静呼気＝0、安静吸気＝1）<strong id="gaugeText" style="float:right">基準未取得</strong>
          <div class="gauge-track"><div class="gauge-band" id="gaugeBand" style="display:none"></div><div class="gauge-marker" id="gaugeMarker" style="display:none"></div></div>
          <div class="gauge-ticks"><span>−0.4</span><span>0</span><span>0.6</span><span>1.0</span><span>1.6</span></div>
        </div>
      </div>
      <canvas id="coachTimeline" class="coach-timeline" role="img" aria-label="キューのタイムライン（過去5秒〜先15秒）"></canvas>
      <details class="coach-details"><summary>キュー一覧と適合率</summary><table class="coach-table" id="coachTable"></table></details>
      <p class="phase-hint">先に10秒以上 <b>安静呼吸</b> を計測してから開始すると、その区間を張力の基準（0〜1）にします。緑の帯が指示に対する目標範囲です。「息を止めない」は上の呼吸状態が「呼吸停止の可能性」になったときに注意を出します（判定時間の設定に従います）。HTMLを直接開いた場合はYouTubeの埋め込みが再生できないことがあります。そのときは別タブで動画を再生し、再生と同時に「コーチング開始」を押してください（±0.5 sでずれを補正できます）。</p>
    </section>

'''
locator = '''    <section class="panel" aria-label="リアルタイムグラフ">'''
assert locator in base
base = base.replace(locator, panel + locator, 1)

oldnote = '''      <strong>保存形式：</strong>CSVには原張力'''
newnote = '''      <strong>動画コーチング：</strong>指示の判定は上記の呼吸状態の推定と、安静呼吸を基準にした張力の相対値に基づく目安です。CSVには動画時刻、有効な指示、判定（1=適合、0=不適合、空欄=判定なし）を追加します。<br>
      <strong>保存形式：</strong>CSVには原張力'''
assert oldnote in base
base = base.replace(oldnote, newnote, 1)

# app.js（v1.1）にコーチング用のフックを追加
app = (v11 / 'app.js').read_text(encoding='utf8')
beg = base.index('  <script id="app-main">\n') + len('  <script id="app-main">\n')
end = base.index('\n  </script>', beg)
assert base[beg:end] == app, 'v1.1 HTML の app-main が v1_1/app.js と一致しません'
for old, new in [
    ('  app.rows.push(r);',
     '  if(window.GDXCoach)GDXCoach.onSample(r,result); // v1.2 coaching\n  app.rows.push(r);'),
    ("  app.classifier.reset();\n  app.lastPhase='NONE';",
     "  app.classifier.reset();\n  if(window.GDXCoach)GDXCoach.onNewTrial(); // v1.2 coaching\n  app.lastPhase='NONE';"),
    ("  app.classifier.reset();\n  if(app.running){updatePhase('WARMUP')",
     "  app.classifier.reset();\n  if(window.GDXCoach)GDXCoach.onSettingsChange(); // v1.2 coaching\n  if(app.running){updatePhase('WARMUP')"),
    ("'sensitivity','hold_threshold_s'];",
     "'sensitivity','hold_threshold_s','video_time_s','coach_cues','coach_ok'];"),
    ('r.breath_state,r.inhale_when_force,r.sensitivity,r.hold_threshold_s]',
     'r.breath_state,r.inhale_when_force,r.sensitivity,r.hold_threshold_s,r.video_time_s,r.coach_cues,r.coach_ok]'),
]:
    assert app.count(old) == 1, old
    app = app.replace(old, new, 1)

core = (p / 'CoachCore.js').read_text(encoding='utf8')
ui = (p / 'coach_ui.js').read_text(encoding='utf8')
base = (base[:beg] + app + '\n  </script>\n  <script id="coach-core">\n' + core +
        '\n  </script>\n  <script id="coach-ui">\n' + ui + base[end:])
(p / 'GDX_RB_Coach_Live.html').write_text(base, encoding='utf8')
print('built', len(base))
