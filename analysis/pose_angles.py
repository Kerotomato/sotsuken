"""動画から MediaPipe Pose で関節角度の時系列を求める

  py -X utf8 analysis\\pose_angles.py 動画.mp4 -o angles.csv [--fps 15]

出力 CSV の列（角度はすべて度）:
  t                 動画の先頭からの時刻 [s]
  trunk_incl        体幹の傾き。股関節中点→肩中点のベクトルと鉛直のなす角
                    （0 = 直立、90 = 水平、90 超 = 肩が股関節より下＝深い前屈）
  hip_L / hip_R     股関節角 = 肩–股関節–膝 の角（180 = 伸展、小さいほど屈曲）
  knee_L / knee_R   膝関節角 = 股関節–膝–足首 の角（180 = 伸展）
  shoulder_L / _R   肩の挙上角 = 股関節–肩–肘 の角（0 = 腕を下ろす、180 = 真上に挙げる）
  elbow_L / elbow_R 肘関節角 = 肩–肘–手首 の角（180 = 伸展）
  vis_mean          使用した関節点の visibility の平均（検出の信頼度の目安）
  detected          その時刻に人物を検出できたか（0/1）

仮定・注意（論文の手法の章用）:
  - MediaPipe Pose Landmarker（Tasks API、pose_landmarker_full）を VIDEO モードで使う。
  - 体幹の傾きは画像座標（2D）で求める。カメラが水平に設置され、画像の縦方向が鉛直と
    一致すると仮定している。カメラが傾いているとその分だけ誤差になる。
  - 関節角（股・膝・肩・肘）は world landmarks（股関節中心を原点とする 3D 推定、単位 m）で
    求める。単眼推定の奥行きは誤差が大きく、カメラに対して真横・正面以外の向きでは
    角度がずれる可能性がある。
  - 関節点の visibility が VIS_MIN 未満の角度は欠損（NaN）にする。
  - 左右（_L/_R）は MediaPipe が推定した「本人の」左右。手本がカメラに向いている場合と
    被験者の向きが違うと左右が入れ替わって見えるので、比較時に考慮する（段階4で扱う）。
"""
from __future__ import annotations

import argparse
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
             "pose_landmarker_full/float16/latest/pose_landmarker_full.task")
VIS_MIN = 0.5   # これ未満の関節点は信頼できないとみなす（MediaPipe の既定の検出しきい値と同じ値）

# MediaPipe Pose の関節点番号
LM = {"shoulder_L": 11, "shoulder_R": 12, "elbow_L": 13, "elbow_R": 14, "wrist_L": 15, "wrist_R": 16,
      "hip_L": 23, "hip_R": 24, "knee_L": 25, "knee_R": 26, "ankle_L": 27, "ankle_R": 28}

# 関節角の定義: 名前 -> (端点A, 頂点, 端点B)
JOINTS = {}
for s in ("L", "R"):
    JOINTS[f"hip_{s}"] = (f"shoulder_{s}", f"hip_{s}", f"knee_{s}")
    JOINTS[f"knee_{s}"] = (f"hip_{s}", f"knee_{s}", f"ankle_{s}")
    JOINTS[f"shoulder_{s}"] = (f"hip_{s}", f"shoulder_{s}", f"elbow_{s}")
    JOINTS[f"elbow_{s}"] = (f"shoulder_{s}", f"elbow_{s}", f"wrist_{s}")
ANGLE_COLUMNS = ["trunk_incl"] + list(JOINTS)


def ensure_model(path: Path) -> Path:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        print(f"MediaPipe のモデルをダウンロード: {path.name}")
        urllib.request.urlretrieve(MODEL_URL, path)
    return path


def extract_landmarks(video: Path, model: Path, fps: float | None = 15.0):
    """動画 → (時刻[s], 画像座標 [N,33,4] (x_px, y_px, z, vis), world [N,33,3])

    fps を指定するとフレームを間引く（None なら全フレーム）。検出できないフレームは NaN。
    """
    import cv2
    import mediapipe as mp
    from mediapipe.tasks.python import BaseOptions
    from mediapipe.tasks.python import vision

    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise RuntimeError(f"動画を開けません: {video}")
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    step = max(1, round(src_fps / fps)) if fps else 1
    opts = vision.PoseLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=str(model)),
        running_mode=vision.RunningMode.VIDEO, num_poses=1)
    ts, img_lm, world_lm = [], [], []
    with vision.PoseLandmarker.create_from_options(opts) as lmk:
        i = 0
        while True:
            ok = cap.grab()
            if not ok:
                break
            if i % step == 0:
                ok, frame = cap.retrieve()
                if not ok:
                    break
                # 再生位置のタイムスタンプを使う（可変フレームレートの .mov でも時刻がずれないように）
                t_ms = cap.get(cv2.CAP_PROP_POS_MSEC)
                h, w = frame.shape[:2]
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                res = lmk.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb),
                                           int(round(t_ms)))
                a = np.full((33, 4), np.nan)
                b = np.full((33, 3), np.nan)
                if res.pose_landmarks:
                    a = np.array([[p.x * w, p.y * h, p.z, p.visibility] for p in res.pose_landmarks[0]])
                    b = np.array([[p.x, p.y, p.z] for p in res.pose_world_landmarks[0]])
                ts.append(t_ms / 1000.0)
                img_lm.append(a)
                world_lm.append(b)
                if len(ts) % 300 == 0:
                    print(f"  {i}/{n_frames} フレーム処理")
            i += 1
    cap.release()
    return np.array(ts), np.array(img_lm), np.array(world_lm)


def _angle(a, b, c):
    """頂点 b における角 abc [deg]。a,b,c は [..., D]"""
    v1, v2 = a - b, c - b
    cos = np.sum(v1 * v2, axis=-1) / (np.linalg.norm(v1, axis=-1) * np.linalg.norm(v2, axis=-1))
    return np.degrees(np.arccos(np.clip(cos, -1, 1)))


def compute_angles(ts, img_lm, world_lm) -> pd.DataFrame:
    vis = img_lm[:, :, 3]
    df = pd.DataFrame({"t": ts})
    # 体幹の傾き（画像座標。y 軸は下向きなので -dy が上向き）
    mid_sh = (img_lm[:, LM["shoulder_L"], :2] + img_lm[:, LM["shoulder_R"], :2]) / 2
    mid_hip = (img_lm[:, LM["hip_L"], :2] + img_lm[:, LM["hip_R"], :2]) / 2
    d = mid_sh - mid_hip
    trunk = np.degrees(np.arctan2(np.abs(d[:, 0]), -d[:, 1]))
    trunk_vis = vis[:, [LM["shoulder_L"], LM["shoulder_R"], LM["hip_L"], LM["hip_R"]]].min(axis=1)
    df["trunk_incl"] = np.where(trunk_vis >= VIS_MIN, trunk, np.nan)
    used = set()
    for name, (a, b, c) in JOINTS.items():
        ia, ib, ic = LM[a], LM[b], LM[c]
        ang = _angle(world_lm[:, ia], world_lm[:, ib], world_lm[:, ic])
        ok = np.minimum.reduce([vis[:, ia], vis[:, ib], vis[:, ic]]) >= VIS_MIN
        df[name] = np.where(ok, ang, np.nan)
        used |= {ia, ib, ic}
    df["vis_mean"] = np.nanmean(vis[:, sorted(used)], axis=1)
    df["detected"] = (~np.isnan(img_lm[:, 0, 0])).astype(int)
    return df


def analyze_video(video: Path, out_csv: Path, model: Path, fps: float | None = 15.0) -> pd.DataFrame:
    ts, img_lm, world_lm = extract_landmarks(video, ensure_model(model), fps)
    df = compute_angles(ts, img_lm, world_lm)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_csv, index=False, float_format="%.3f")
    # 段階3（動きによる時刻合わせ）で使うため、関節点そのものも保存しておく
    np.savez_compressed(out_csv.with_suffix(".landmarks.npz"), t=ts, image=img_lm, world=world_lm)
    return df


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("-o", "--out", required=True, help="出力 CSV")
    ap.add_argument("--fps", type=float, default=15.0, help="解析するフレームレート（0 で全フレーム）")
    ap.add_argument("--model", default=str(Path(__file__).parent / "models" / "pose_landmarker_full.task"))
    a = ap.parse_args()
    df = analyze_video(Path(a.video), Path(a.out), Path(a.model), a.fps or None)
    print(f"{len(df)} フレーム、検出率 {df['detected'].mean() * 100:.1f}% → {a.out}")


if __name__ == "__main__":
    main()
