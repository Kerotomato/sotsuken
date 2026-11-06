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
