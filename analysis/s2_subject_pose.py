"""段階2: 被験者動画の解析

  py -X utf8 analysis\\s2_subject_pose.py --subject 1 --condition 音声あり

処理:
  1. ffprobe で mov の作成日時（録画開始時刻）と長さを読む → video_info.json
  2. MediaPipe Pose で手本と同じ定義の関節角度を求める → subject_angles.csv（+ 関節点 .npz）
  3. 段階3の音声の相互相関用に、mov の音声を 8 kHz モノラルで取り出す → data/<被験者>/<条件>/video_audio.wav

出力先: results/<被験者>/<条件>/（.gitignore 済み）
仮定:
  - subject_angles.csv の t は動画の先頭からの時刻（OpenCV の再生位置）。絶対時刻は
    video_info.json の start（録画開始時刻）+ t とする（段階3）。
  - iPhone の縦向き動画は OpenCV が回転情報に従って正しい向きで読み込む（OpenCV 4.5 以降の既定）。
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import pose_angles
import sensors
from common import DATA_ROOT, RESULTS_DIR, WORK_DIR


def extract_audio(video: Path, out: Path, sr: int = 8000):
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-vn",
                    "-ac", "1", "-ar", str(sr), str(out)], check=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--subject", required=True, help="被験者番号（1〜5）")
    ap.add_argument("--condition", required=True, choices=list(sensors.CONDITIONS))
    ap.add_argument("--data", default=str(DATA_ROOT), help="被験者フォルダの親")
    ap.add_argument("--fps", type=float, default=15.0, help="姿勢推定のフレームレート（手本と同じにする）")
    ap.add_argument("--skip-pose", action="store_true", help="作成日時と音声の取り出しだけ行う")
    a = ap.parse_args()

    ses = sensors.find_session(Path(a.data), a.subject, a.condition)
    if not ses.video:
        raise SystemExit(f"動画が見つかりません: {ses.folder}")
    out = RESULTS_DIR / a.subject / a.condition
    out.mkdir(parents=True, exist_ok=True)

    vi = sensors.probe_video(ses.video)
    (out / "video_info.json").write_text(json.dumps({
        "file": ses.video.name, "start_unix": vi.start, "start_jst": sensors.fmt_jst(vi.start),
        "source": vi.source, "duration_s": vi.duration, "name_check": vi.name_check,
        "tags": {k: v for k, v in vi.tags.items() if "date" in k or "time" in k}},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[1] {ses.video.name}: 録画開始 {sensors.fmt_jst(vi.start)}（{vi.source}）、"
          f"長さ {vi.duration:.1f} s、ファイル名との照合: {vi.name_check}")

    wav = WORK_DIR / a.subject / a.condition / "video_audio.wav"
    extract_audio(ses.video, wav)
    print(f"[2] 音声: {wav}")

    if a.skip_pose:
        return
    df = pose_angles.analyze_video(ses.video, out / "subject_angles.csv",
                                   Path(__file__).parent / "models" / "pose_landmarker_full.task", a.fps)
    print(f"[3] 姿勢推定: {len(df)} フレーム（{a.fps:g} fps）、検出率 {df['detected'].mean() * 100:.1f}% "
          f"→ {out / 'subject_angles.csv'}")


if __name__ == "__main__":
    main()
