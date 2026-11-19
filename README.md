# sotsuken

GDX-RB（Vernier Go Direct Respiration Belt）による腹部張力計測と呼吸コーチング。

- [`gdx_rb_web/`](gdx_rb_web/) — ブラウザ版の計測・コーチングツール（先生の v1.1 をもとにした v1.2）
- [`gdx_rb_coach/`](gdx_rb_coach/) — YouTube 字幕・音声からキーフレーズを抽出する Python スクリプト
- [`analysis/`](analysis/) — ヨガ手本動画と被験者の比較（呼吸・動きの評価と修正アドバイス）

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

## ヨガ手本比較解析（analysis/）
手本動画（https://www.youtube.com/watch?v=Da2L6PfR8DY）と被験者のデータを比べて、呼吸と動きを評価する。
被験者の動画・データ・解析結果（`data/`、`results/`）は `.gitignore` 済みで、リポジトリには入れない。

※ この節のコマンドはすべて **リポジトリのルート**（`sotsuken\sotsuken`）で実行する。
`gdx_rb_web` などのサブフォルダにいると `.\analysis\...` が見つからずエラーになる。

準備（初回のみ。ffmpeg は音声の変換に使う）
```
cd C:\Users\r04i10\sotsuken\sotsuken
$env:PYTHONUTF8 = "1"
py -m pip install -r .\analysis\requirements.txt
winget install Gyan.FFmpeg
```

### 段階1: 手本動画の解析
```
py -X utf8 .\analysis\s1_reference.py
```
- `data/reference/` に手本動画（mp4）と音声（wav）を保存する
- `results/reference/` に呼吸キュー（`cues.json`）、文字起こし（`transcript.csv`）、関節角度（`reference_angles.csv`）、確認用グラフ（`reference_overview.png`）を出力する
- 字幕のキュー抽出がうまくいかないときは `--whisper small` を付けて、音声から文字起こしする

