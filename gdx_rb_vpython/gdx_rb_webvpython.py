Web VPython 3.2

"""
Go Direct Respiration Belt (GDX-RB) を Web VPython + Bluetooth で読み取る
- チャンネル1: Force (N)  … ベルトの張力 = 呼吸波形
- チャンネル2: Respiration Rate (bpm) … 呼吸数（計測開始から約30秒後に有効）

実行方法（Go Direct Web VPython Getting Started Guide より）
  1. webvpython.org に貼り付け → 「Share or export this program」→「Download as HTML」
     ※「Run this program」では Go Direct に接続できません
  2. ダウンロードした HTML を Chrome で開く
  3. 「Select a Go Direct Device」を押す → 一覧から「GDX-RB xxxxxxx」を選んで「ペア設定」
  4. 「Collect」で計測開始、「Stop」で停止、「Close」で切断
"""

# 動作を確認したバージョンに固定（バージョンなしの URL は更新で挙動が変わることがある）
get_library('https://unpkg.com/@vernier/godirect@1.8.3/dist/webVPython.js')

gdx.open(connection='ble')     # USB 接続なら 'usb'
gdx.select_sensors([1, 2])     # 1=Force, 2=呼吸数
gdx.vp_vernier_canvas()        # Collect/Stop・Close ボタン、周期スライダー、メーター
gdx.start(period=100)          # 100 ms = 10 サンプル/秒。「Select a Go Direct Device」ボタンもここで作られる

# 張力に合わせて膨らむ球（簡易フィードバック）。視点を固定して大きさの変化が見えるようにする
scene.autoscale = False
scene.range = 8
ball = sphere(radius=1, color=color.cyan)

# 直近30秒をスクロール表示するグラフ
g = graph(title='GDX-RB 呼吸波形', xtitle='時間 (s)', ytitle='Force (N)',
          width=700, height=300, scroll=True, xmin=0, xmax=30, fast=True)
force_curve = gcurve(color=color.blue)
info = wtext(text='\n呼吸数: -- bpm')

t = 0
while not gdx.vp_close_is_pressed():
    rate(gdx.vp_rate())                  # 必須: これがないとブラウザが固まる
    if gdx.vp_collect_is_pressed():
        data = gdx.read()
        if not data:                     # 未接続(False) / まだ値がない(None)
            continue
        force = data[0]
        resp_rate = data[1]              # 変数名を rate にすると VPython の rate() が使えなくなる
        t += gdx.vp_get_slider_period() / 1000   # ms → s（スライダーで周期を変えても追従）
        force_curve.plot(t, force)
        ball.radius = max(0.2, force / 5)
        if resp_rate is not None and resp_rate == resp_rate and resp_rate > 0:   # NaN は自分自身と等しくない
            info.text = '\n呼吸数: {:.1f} bpm'.format(resp_rate)
        else:
            info.text = '\n呼吸数: 計算中（約30秒かかります）'

gdx.close()
