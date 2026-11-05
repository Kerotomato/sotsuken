from pathlib import Path
p=Path(__file__).parent
base=Path('/mnt/data/GDX_RB_Web_Force_Logger_v1_0/GDX_RB_Force_Live.html').read_text()
css='''
    .breathing-settings { display:grid;grid-template-columns:repeat(3,minmax(170px,1fr));gap:12px;align-items:end;padding-top:14px;margin-top:14px;border-top:1px solid #e2eaf0; }
    .phase-card {display:flex;gap:18px;align-items:center;justify-content:space-between;flex-wrap:wrap;border:1px solid #cddce6;border-radius:12px;padding:20px 22px;background:#f6f9fb;transition:background .2s,border-color .2s; }
    .phase-card .phase-name {font-size:clamp(25px,3vw,39px);font-weight:750;line-height:1.24;color:#29465a;}
    .phase-card .phase-lead {color:#526a79;font-size:12px;font-weight:700;letter-spacing:.04em;margin-bottom:6px;}
    .phase-card .phase-desc {font-size:13px;color:#496576;line-height:1.6;margin-top:9px;}
    .phase-card .phase-metric {text-align:right;font-size:12px;color:#5d7383;}
    .phase-card .phase-metric strong{display:block;font-size:19px;color:#17394d;margin-top:5px;font-variant-numeric:tabular-nums;}
    .phase-card.inhale{border-color:#7eb5d4;background:#eaf6fc;}
    .phase-card.inhale .phase-name{color:#15618c;}
    .phase-card.exhale{border-color:#90c6a7;background:#eefaf1;}
    .phase-card.exhale .phase-name{color:#206548;}
    .phase-card.hold{border-color:#e9ba82;background:#fff4e6;}
    .phase-card.hold .phase-name{color:#905a14;}
    .checkbox.inline {margin-left:0;padding:6px 0;}
    .phase-hint {font-size:12px;color:#627a8b;line-height:1.75;margin:12px 0 0;}
    @media (max-width:840px) { .breathing-settings{grid-template-columns:repeat(2,minmax(0,1fr));} }
    @media (max-width:510px) { .breathing-settings{grid-template-columns:1fr;} .phase-card .phase-metric{text-align:left;} }
'''
base=base.replace('  </style>',css+'  </style>',1)
base=base.replace('<title>GDX-RB Force Live | 腹部ベルト張力</title>', '<title>GDX-RB Force Live v1.1 | 呼吸フィードバック</title>',1)
base=base.replace('GDX-RB Force Live Logger</h1>', 'GDX-RB Force Live Logger <span style="font-size:.55em;opacity:.8">v1.1</span></h1>',1)
base=base.replace('腹部ベルト張力（Force, N）をブラウザでリアルタイム表示・CSV保存', '腹部ベルト張力（Force, N）のリアルタイム表示・呼吸状態の推定・CSV保存',1)
original='''        <button id="connectBtn" class="primary">GDX-RBに接続して計測</button>
        <button id="demoBtn">デモ計測</button>
        <button id="stopBtn" disabled>停止・CSV保存</button>
        <button id="csvBtn" disabled>CSVを保存</button>
        <button id="clearBtn" disabled>グラフをクリア</button>
        <label class="checkbox"><input type="checkbox" id="autoSave" checked>停止時にCSVを自動ダウンロード</label>'''
updated='''        <button id="connectBtn" class="primary">GDX-RBに接続して計測</button>
        <button id="demoBtn">デモ計測</button>
        <button id="stopBtn" disabled>計測停止・CSV保存</button>
        <button id="resumeBtn" class="primary" disabled>再計測（接続維持）</button>
        <button id="disconnectBtn" disabled>接続解除</button>
        <button id="csvBtn" disabled>CSVを保存</button>
        <button id="clearBtn" disabled>グラフをクリア</button>
        <label class="checkbox"><input type="checkbox" id="autoSave" checked>停止時にCSVを自動ダウンロード</label>'''
assert original in base
base=base.replace(original,updated,1)
start=base.index('      <div class="buttonbar">')
settings='''      <div class="breathing-settings" aria-label="呼吸フィードバック設定">
        <label class="field" for="polarity">吸気時の張力方向
          <select id="polarity"><option value="increase" selected>吸気で増加（Vernier標準）</option><option value="decrease">吸気で減少（反転）</option></select>
        </label>
        <label class="field" for="sensitivity">呼吸判定の感度
          <select id="sensitivity"><option value="sensitive">高（緩やかな呼吸向け）</option><option value="normal" selected>標準</option><option value="conservative">低（体動・ノイズが多い場合）</option></select>
        </label>
        <label class="field" for="holdSeconds">張力静止の判定時間
          <select id="holdSeconds"><option value="3" selected>3秒</option><option value="5">5秒</option><option value="8">8秒</option></select>
        </label>
      </div>
'''
base=base[:start]+settings+base[start:]
base=base.replace('''      <p class="hint">GDX-RBの <b>Force（チャンネル1）</b> を取得します。Graphical Analysis等の接続を終了してから開始してください。ブラウザの接続先選択画面では、必ずGDX-RBを選択します。</p>''','''      <p class="hint">「計測停止」は <b>センサを切断しません</b>。次の「再計測」でデバイス選択なしに新しい測定を開始します。接続を終了する場合だけ「接続解除」を押してください。Force（チャンネル1）を取得します。</p>''',1)
locator='''    <section class="panel" aria-label="リアルタイムグラフ">'''
phase='''    <section class="panel" aria-label="呼吸状態フィードバック">
      <div class="topline"><h2>呼吸状態フィードバック（張力変化からの推定）</h2><label class="checkbox inline"><input type="checkbox" id="voiceFeedback">状態変化を音声で通知（任意）</label></div>
      <div class="phase-card idle" id="phaseCard" role="status" aria-live="polite">
        <div><div class="phase-lead">現在の呼吸状態（推定）</div><div class="phase-name" id="breathState">待機中</div><div class="phase-desc" id="breathExplanation">計測開始を待っています</div></div>
        <div class="phase-metric">張力の増減速度（設定方向）<strong id="phaseSlope">— N/s</strong></div>
      </div>
      <p class="phase-hint">Vernier標準の装着では吸気時に張力が増え、呼気時に減ります。標準と逆に表示される場合は「吸気時の張力方向」を切り替えてください。「呼吸停止の可能性」は <b>張力変化が小さい状態</b> の検出であり、無呼吸・換気停止を証明しません。体動、ベルトの緩み、浅い呼吸の影響を受けます。</p>
    </section>

'''
assert locator in base
base=base.replace(locator,phase+locator,1)
oldnotes='''      <strong>研究データ上の注意：</strong>CSVに保存するのは画像認識値ではなく、公式JavaScriptライブラリから受信したForce値です。PC受信時刻はセンサ内部の真の測定時刻ではありません。要求周波数は実際の取得周波数を保証せず、特にBluetooth・ブラウザ負荷・バックグラウンド表示の影響を受けます。<br>
      <strong>保存形式：</strong>CSVには測定モード、サンプル番号、PC受信UTC日時、PC経過秒、要求周期に基づく名目経過秒、張力（N）を記録します。データはブラウザのメモリに保持され、CSV保存操作でローカルにダウンロードします。停止前のブラウザ終了では未保存データが失われます。<br>'''
newnotes='''      <strong>研究データ上の注意：</strong>Forceは直接受信した未加工の張力（N）です。PC受信時刻はセンサ内部の測定時刻ではありません。要求周波数は実効周波数を保証せず、BLEやブラウザの負荷による欠損があり得ます。<br>
      <strong>呼吸の推定：</strong>張力を平滑化して約1秒間の増減速度を求めます。「吸気・呼気」はベルト張力の方向に基づく推定、「呼吸停止の可能性」は設定秒数だけ張力変化が小さい場合の候補表示です。<b>呼吸停止の確定診断ではありません。</b><br>
      <strong>保存形式：</strong>CSVには原張力、PC受信時間、要求周期の名目時間に加え、平滑化後の張力、増減速度、推定呼吸状態、判定設定が入ります。自動保存は「停止」を押したときだけです。ブラウザを閉じる前に必ず保存してください。<br>'''
assert oldnotes in base
base=base.replace(oldnotes,newnotes,1)
beg=base.index('  <script>\n    "use strict";')
end=base.index('  </script>',beg)+len('  </script>')
core=(p/'BreathClassifier.js').read_text()
app=(p/'app.js').read_text()
base=base[:beg]+'  <script id="classifier-core">\n'+core+'\n  </script>\n  <script id="app-main">\n'+app+'\n  </script>'+base[end:]
(p/'GDX_RB_Force_Live.html').write_text(base,encoding='utf8')
print('built',len(base))
