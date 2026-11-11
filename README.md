# sotsuken

GDX-RB（Vernier Go Direct Respiration Belt）による腹部張力計測と呼吸コーチング。

- [`gdx_rb_web/`](gdx_rb_web/) — ブラウザ版の計測・コーチングツール（先生の v1.1 をもとにした v1.2）
- [`gdx_rb_coach/`](gdx_rb_coach/) — YouTube 字幕・音声からキーフレーズを抽出する Python スクリプト

## 環境整備
PIP
```
cd gdx_rb_coach
$env:PYTHONUTF8 = "1"
py -m pip install -r .\requirements.txt
py -X utf8 .\cue_extractor.py --url "https://www.youtube.com/watch?v=slspGIMcK2Q" -o .\cues.json
```

## 実行手順
1. ローカルサーバーを起動する（PowerShell、リポジトリのルートで実行。使っている間は閉じない）
   ```
   cd gdx_rb_web
   py -m http.server 8000
   ```
2. Chrome で開く（別の PowerShell から実行するか、アドレスバーに URL を入力）
   ```
   Start-Process chrome "http://localhost:8000/GDX_RB_Coach_Live.html"
   ```
3. GDX-RB の電源を入れ（赤 LED 点滅）、「GDX-RBに接続して計測」→ 一覧から `GDX-RB xxxxxxx` を選ぶ
   （センサーなしで試すときは「デモ計測」）
4. 10 秒以上、安静呼吸をして基準を取る
5. 「キュー」で `gdx_rb_coach/cues.json` を読み込む
6. YouTube の URL を入れて「動画を読込」→ 再生
7. 「計測停止・CSV保存」で結果を保存する
8. 終わったら、サーバーを起動した PowerShell で `Ctrl + C` を押して止める

※ Windows の Bluetooth 設定で GDX-RB をペアリングしないこと（接続できなくなることがあります）。
※ Graphical Analysis など、ほかのアプリでセンサーに接続している場合は先に切断しておく。
