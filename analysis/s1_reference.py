"""段階1: 手本動画の解析

  py -X utf8 analysis\\s1_reference.py                    # 字幕 → なければ Whisper
  py -X utf8 analysis\\s1_reference.py --whisper small    # 字幕を使わず Whisper で文字起こし
  py -X utf8 analysis\\s1_reference.py --skip-download    # ダウンロード済みのものを使う

処理:
  1. yt-dlp で手本動画（映像 mp4、音声 wav 16 kHz モノラル）をダウンロード → data/reference/
  2. cue_extractor.py の関数で呼吸キュー（INHALE / EXHALE / RELAX など、t〜end 秒）を抽出
     → results/reference/cues.json、文字起こしは transcript.csv（抽出漏れの確認用）
  3. MediaPipe Pose で関節角度の時系列 → results/reference/reference_angles.csv
  4. 確認用グラフ（関節角度とキュー区間）→ results/reference/reference_overview.png

必要なもの: yt-dlp, ffmpeg（音声の変換に使う。winget install Gyan.FFmpeg）, mediapipe, opencv-python
仮定:
  - 字幕は日本語の手動字幕 → 自動生成字幕の順に使う（youtube-transcript-api の言語優先順）。
    自動生成字幕は句読点がなく文の区切りが不正確なため、キューの時刻は字幕1行の中で
    文字位置から線形補間した推定値になる（cue_extractor.py の仕様）。
  - Whisper を使う場合は単語のタイムスタンプからキュー時刻を決める。
"""
from __future__ import annotations

import argparse
import csv
import subprocess
from collections import Counter
from pathlib import Path

import common
from common import REFERENCE_URL, RESULTS_DIR, WORK_DIR


def download(url: str, out_dir: Path) -> tuple[Path, Path]:
    import yt_dlp
    out_dir.mkdir(parents=True, exist_ok=True)
    video = out_dir / "reference.mp4"
    wav = out_dir / "reference.wav"
    if not video.exists():
        # 姿勢推定には 720p で十分。mp4 にまとめて OpenCV で読めるようにする
        opts = {"format": "bv*[height<=720][ext=mp4]+ba[ext=m4a]/b[height<=720][ext=mp4]/b",
                "merge_output_format": "mp4", "outtmpl": str(out_dir / "reference.%(ext)s"),
                "quiet": True, "noprogress": True}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
        (out_dir / "reference_info.txt").write_text(
            f"title: {info.get('title')}\nduration: {info.get('duration')}\nfps: {info.get('fps')}\n"
            f"resolution: {info.get('width')}x{info.get('height')}\nurl: {url}\n", encoding="utf-8")
    if not wav.exists():
        # 段階3の音声相互相関用。16 kHz モノラルにそろえる
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video),
                        "-ac", "1", "-ar", "16000", str(wav)], check=True)
    return video, wav


def get_segments(url: str, wav: Path, whisper: str | None, lang: str):
    import cue_extractor as ce
    if not whisper:
        try:
            return ce.from_youtube_subs(url, [lang]), "YouTube 字幕"
        except Exception as e:
            print(f"字幕を取得できませんでした（{type(e).__name__}）。Whisper で文字起こしします。")
    return ce.from_whisper(str(wav), whisper or "small", lang), f"Whisper ({whisper or 'small'})"


def plot_overview(df, cues, out: Path):
    plt = common.setup_matplotlib()
    rows = [("体幹の傾き", ["trunk_incl"]), ("股関節", ["hip_L", "hip_R"]),
            ("膝", ["knee_L", "knee_R"]), ("肩の挙上", ["shoulder_L", "shoulder_R"])]
    fig, axes = plt.subplots(len(rows) + 1, 1, figsize=(16, 11), sharex=True,
                             gridspec_kw={"height_ratios": [1] * len(rows) + [0.35]})
    for ax, (title, cols) in zip(axes, rows):
        for col in cols:
            ax.plot(df["t"], df[col], lw=0.9, label=col)
        common.shade_cues(ax, cues)
        ax.set_ylabel(f"{title} [°]")
        ax.legend(loc="upper right", fontsize=8)
        ax.grid(alpha=0.3)
    ax = axes[-1]
    ax.fill_between(df["t"], 0, df["detected"], step="mid", color="#999", alpha=0.5)
    common.shade_cues(ax, cues, label=True)
    ax.set_ylabel("検出")
    ax.set_yticks([])
    ax.set_xlabel("手本動画の再生時刻 [s]")
    axes[0].set_title("手本動画の関節角度と呼吸キュー（青=吸う、緑=吐く、灰=力を抜く）")
    fig.tight_layout()
    fig.savefig(out, dpi=110)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=REFERENCE_URL)
    ap.add_argument("--whisper", metavar="MODEL", help="字幕を使わず Whisper で文字起こし（tiny/base/small/medium）")
    ap.add_argument("--lang", default="ja")
    ap.add_argument("--fps", type=float, default=15.0, help="姿勢推定のフレームレート")
    ap.add_argument("--skip-download", action="store_true")
    ap.add_argument("--skip-pose", action="store_true", help="キュー抽出だけ行う")
    a = ap.parse_args()

    work = WORK_DIR / "reference"
    out = RESULTS_DIR / "reference"
    out.mkdir(parents=True, exist_ok=True)
    if a.skip_download:
        video, wav = work / "reference.mp4", work / "reference.wav"
    else:
        video, wav = download(a.url, work)
    print(f"[1] 手本動画: {video}")

    import cue_extractor as ce
    from breath_core import save_cues
    segs, how = get_segments(a.url, wav, a.whisper, a.lang)
    cues = ce.extract_cues(segs)
    save_cues(str(out / "cues.json"), cues, source=f"{a.url} ({how})")
    with open(out / "transcript.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["start_s", "end_s", "text"])
        for s in segs:
            w.writerow([f"{s.start:.2f}", f"{s.end:.2f}", s.text])
    print(f"[2] {how}: {len(segs)} 文から {len(cues)} 件のキュー {dict(Counter(c.kind for c in cues))}")

    if a.skip_pose:
        return
    import pose_angles
    df = pose_angles.analyze_video(video, out / "reference_angles.csv",
                                   Path(__file__).parent / "models" / "pose_landmarker_full.task", a.fps)
    print(f"[3] 姿勢推定: {len(df)} フレーム（{a.fps:g} fps）、検出率 {df['detected'].mean() * 100:.1f}%")
    plot_overview(df, cues, out / "reference_overview.png")
    print(f"[4] 確認用グラフ: {out / 'reference_overview.png'}")


if __name__ == "__main__":
    main()
