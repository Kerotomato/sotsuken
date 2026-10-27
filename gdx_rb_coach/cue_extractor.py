"""YouTube / 音声ファイル / 字幕ファイルから呼吸キューを抽出して JSON に保存する

使い方:
  python cue_extractor.py --url https://www.youtube.com/watch?v=XXXX -o cues.json
  python cue_extractor.py --url ... --whisper small -o cues.json     # 字幕が無い動画
  python cue_extractor.py --audio lesson.mp4 --whisper small -o cues.json
  python cue_extractor.py --subs lesson.srt -o cues.json

取得の優先順:
  1) YouTube の字幕（手動 > 自動生成）  youtube-transcript-api
  2) 音声を yt-dlp で取得 → faster-whisper で文字起こし（単語タイムスタンプ付き）
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from dataclasses import dataclass

from breath_core import Cue, find_phrases, finalize_cues, save_cues


@dataclass
class Segment:
    start: float
    end: float
    text: str
    words: list[tuple[float, float, str]] | None = None   # (start, end, word)

    def time_at_char(self, pos: int) -> float:
        """文中の文字位置に対応する時刻（単語タイムスタンプがあれば使い、無ければ線形補間）"""
        if self.words:
            n = 0
            for ws, _we, w in self.words:
                n += len(w)
                if n > pos:
                    return ws
            return self.words[-1][0]
        frac = pos / max(len(self.text), 1)
        return self.start + frac * (self.end - self.start)


def video_id(url: str) -> str:
    m = re.search(r"(?:v=|youtu\.be/|shorts/|embed/)([\w-]{11})", url)
    return m.group(1) if m else url


def from_youtube_subs(url: str, langs: list[str]) -> list[Segment]:
    from youtube_transcript_api import YouTubeTranscriptApi
    vid = video_id(url)
    if hasattr(YouTubeTranscriptApi, "get_transcript"):        # v0.x
        items = YouTubeTranscriptApi.get_transcript(vid, languages=langs)
        rows = [(d["start"], d["duration"], d["text"]) for d in items]
    else:                                                      # v1.x
        fetched = YouTubeTranscriptApi().fetch(vid, languages=langs)
        rows = [(s.start, s.duration, s.text) for s in fetched]
    return [Segment(s, s + d, t.replace("\n", " ")) for s, d, t in rows]


def download_audio(url: str, out_dir: str) -> str:
    import yt_dlp
    opts = {"format": "bestaudio/best", "outtmpl": os.path.join(out_dir, "audio.%(ext)s"),
            "quiet": True, "noprogress": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        return ydl.prepare_filename(info)


def from_whisper(path: str, model_size: str, lang: str) -> list[Segment]:
    from faster_whisper import WhisperModel
    model = WhisperModel(model_size, device="auto", compute_type="int8")
    # 呼吸指導語を出やすくする初期プロンプト
    prompt = "息を吸いながら、吐きながら、息を止めないで、お腹に力を入れて、力を抜いて。"
    segs, _ = model.transcribe(path, language=lang, word_timestamps=True,
                               initial_prompt=prompt, vad_filter=True)
    out = []
    for s in segs:
        words = [(w.start, w.end, w.word.strip()) for w in (s.words or [])]
        out.append(Segment(s.start, s.end, s.text.strip(), words or None))
    return out


def from_subtitle_file(path: str) -> list[Segment]:
    """SRT / WebVTT"""
    ts = r"(\d+):(\d+):(\d+)[,.](\d+)"
    to_sec = lambda h, m, s, ms: int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 10 ** len(ms)
    text = open(path, encoding="utf-8-sig").read()
    out = []
    for block in re.split(r"\n\s*\n", text):
        m = re.search(ts + r"\s*-->\s*" + ts, block)
        if not m:
            continue
        g = m.groups()
        body = block[m.end():].strip()
        body = re.sub(r"<[^>]+>", "", body).replace("\n", " ")
        out.append(Segment(to_sec(*g[:4]), to_sec(*g[4:]), body))
    return out


def extract_cues(segments: list[Segment], lead: float = 0.0) -> list[Cue]:
    cues = []
    for seg in segments:
        for pos, kind, phrase in find_phrases(seg.text):
            t = max(0.0, seg.time_at_char(pos) - lead)
            cues.append(Cue(t=round(t, 2), kind=kind, text=phrase, context=seg.text))
    return finalize_cues(cues)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--url", help="YouTube URL")
    src.add_argument("--audio", help="ローカル音声/動画ファイル（Whisper で文字起こし）")
    src.add_argument("--subs", help="SRT/VTT 字幕ファイル")
    ap.add_argument("--whisper", metavar="MODEL", help="Whisper を使う（tiny/base/small/medium/large-v3）")
    ap.add_argument("--lang", default="ja")
    ap.add_argument("--lead", type=float, default=0.0, help="キューを何秒早めて提示するか")
    ap.add_argument("-o", "--out", default="cues.json")
    a = ap.parse_args()

    if a.subs:
        segs = from_subtitle_file(a.subs)
    elif a.audio:
        segs = from_whisper(a.audio, a.whisper or "small", a.lang)
    elif a.whisper:
        with tempfile.TemporaryDirectory() as d:
            segs = from_whisper(download_audio(a.url, d), a.whisper, a.lang)
    else:
        try:
            segs = from_youtube_subs(a.url, [a.lang, "en"])
        except Exception as e:
            print(f"字幕を取得できませんでした（{type(e).__name__}）。Whisper にフォールバックします。",
                  file=sys.stderr)
            with tempfile.TemporaryDirectory() as d:
                segs = from_whisper(download_audio(a.url, d), "small", a.lang)

    cues = extract_cues(segs, a.lead)
    save_cues(a.out, cues, source=a.url or a.audio or a.subs)
    print(f"{len(segs)} 文から {len(cues)} 件のキューを抽出 → {a.out}")
    for c in cues:
        print(f"  {c.t:7.2f}–{c.end:7.2f}s  {c.kind:8s} 「{c.text}」  …{c.context[:40]}")


if __name__ == "__main__":
    main()
