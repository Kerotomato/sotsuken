# キュー抽出（cue_extractor.py）

YouTube の字幕・音声や字幕ファイルから「息を止めない」「吸いながら」「吐きながら」などの
キーフレーズを時刻付きで抽出し、`cues.json` に保存します。
作った `cues.json` は、ブラウザ版 [`../gdx_rb_web/GDX_RB_Coach_Live.html`](../gdx_rb_web/) の
「動画コーチング」で読み込みます。

SRT / VTT の字幕ファイルなら、ブラウザ版に直接読み込めます（Python は不要です）。
このスクリプトが必要なのは、YouTube から字幕を取得するときと、字幕がない動画を Whisper で文字起こしするときです。

```bash
pip install -r requirements.txt
python cue_extractor.py --url "https://www.youtube.com/watch?v=XXXX" -o cues.json            # 字幕（手動 > 自動生成）
python cue_extractor.py --url "https://www.youtube.com/watch?v=XXXX" --whisper small -o cues.json  # 字幕なし動画
python cue_extractor.py --audio lesson.mp4 --whisper small -o cues.json
python cue_extractor.py --subs sample_lesson.srt -o cues.json
```

| ファイル | 内容 |
|---|---|
| `cue_extractor.py` | 字幕・音声の取得とキュー抽出 |
| `breath_core.py` | キーフレーズの規則（ブラウザ版の `CoachCore.js` と同じ内容） |
| `sample_lesson.srt` / `sample_cues.json` | 動作確認用のサンプル（`gdx_rb_web` のテストでも使用） |

キーフレーズの規則を変えるときは `breath_core.py` と `../gdx_rb_web/CoachCore.js` の両方を直し、
`sample_cues.json` を作り直してから、`node ../gdx_rb_web/tests/test_coach_core.cjs` で両者が一致することを確認してください。

以前の Python 版リアルタイムモニタ（`gdx_rb_live.py`）は、先生の v1.1 を土台にしたブラウザ版に置き換えました。
