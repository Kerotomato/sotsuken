"""段階3: 時刻合わせ（全センサーを JST の絶対時刻にそろえ、手本動画の再生時刻と対応づける）

  py -X utf8 analysis\\s3_sync.py --subject 1 --condition 音声あり

前提: 段階1（results/reference/）と段階2（results/<被験者>/<条件>/subject_angles.csv, video_info.json,
      data/<被験者>/<条件>/video_audio.wav）が済んでいること。

時刻の決め方（論文の手法の章用。詳細は sensors.py / sync.py のコメント）:
  IMU / RAWACC  各行の timestamp_iso8601（UTC）→ JST
  MCap          ヘッダーの Capture Start Time + Time (Seconds)
  動画          mov の作成日時（ffprobe）+ 再生位置
  呼吸ベルト    開始時刻の記録がないため、計測冒頭の約5秒の息止めを、ベルトの力(N) と
                MCap の胸の前後径（STRN–T10 距離）の両方で検出し、「息止めの終わり
                （呼吸の再開）」が同時刻になるようにベルトの開始時刻を決める。
                確認として、姿勢変化の区間を除いた相互相関でもずれを求めて記録する。
                息止めが片方でも見つからない場合は相互相関の結果を使う。
  手本動画      音声あり: mov の音声と手本の音声の相互相関（GCC-PHAT）
                音声なし: 関節角度の時系列の相関で全体のずれを求め、DTW で局所的なずれを求める
確認:
  - IMU（右手首の角速度）と MCap・動画の右手首の速さの相互相関で、各機器の時計のずれを確かめる。
    ずれが 1 秒を超えたら警告する（1 秒 = 動画のファイル名・作成日時の分解能）。

出力（results/<被験者>/<条件>/）:
  sync.json            各センサーの開始時刻、方法、推定したずれ、確認の結果
  belt_aligned.csv     呼吸ベルト（絶対時刻・動画時刻・YouTube 時刻つき）
  mocap_angles.csv     MCap の角度・胸の前後径（絶対時刻つき）
  motion_lag.csv       動画時刻ごとの手本とのずれ（DTW）
  sync_overview.png    全センサーを同じ時間軸に並べた確認用グラフ
  sync_belt_hold.png   息止め付近の拡大（ベルトと胸の動き）
  sync_audio.png       音声のずれを 30 秒ごとに求めた結果（音声ありのみ）
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

import common
import sensors
import sync as sy
from common import DATA_ROOT, JST, RESULTS_DIR, WORK_DIR

POSTURE_VEL = 10.0       # 体幹の傾きがこれ以上の速さ [deg/s] の区間（前後 1 秒）は姿勢変化とみなし、
                         # 呼吸の相互相関から除く（胸の前後径が姿勢で 30 mm 近く変わるため）
CLOCK_TOL_S = 1.0        # 機器間の時計のずれの許容値（動画の作成日時が秒単位のため）
CLOCK_SEARCH_S = 5.0     # 時計の確認で探す範囲 ±5 秒
MOTION_HZ = 5.0          # 動きの対応づけのサンプリング（姿勢の変化は 1 Hz 程度より遅い）
DTW_BAND_S = 5.0         # DTW で許す局所的なずれ ±5 秒（初心者の動き出しの遅れを想定）
MOTION_COLS = ["trunk_incl", "hip", "knee", "shoulder", "elbow"]   # 左右は平均（向きによる左右の入れ替わりを避ける）


# ---------------------------------------------------------------------------
# 呼吸ベルト
# ---------------------------------------------------------------------------
def posture_mask(mc: pd.DataFrame) -> np.ndarray:
    g, tr = sy.to_grid(mc.t, mc.trunk_incl)
    tr = np.where(np.isnan(tr), np.nanmean(tr), tr)
    vel = np.abs(np.gradient(sy.moving_average(tr, 0.5 * sy.GRID_HZ), 1 / sy.GRID_HZ))
    moving = sy.moving_average((vel > POSTURE_VEL).astype(float), 2 * sy.GRID_HZ) > 0
    idx = np.searchsorted(g, mc.t.to_numpy()).clip(0, len(g) - 1)
    return moving[idx]


def align_belt(belt: pd.DataFrame, mc: pd.DataFrame, take: sensors.Take, prior: tuple[float, float]):
    res = {"hold_belt": sy.find_hold(belt.t, belt.force),
           "hold_mocap": sy.find_hold(mc.t, mc.chest_ap_mm)}
    hb, hc = res["hold_belt"], res["hold_mocap"]
    if hb and hc:
        res["start_hold"] = take.start + hc["end"] - hb["end"]
        cands = np.arange(res["start_hold"] - sy.XCORR_SEARCH_S, res["start_hold"] + sy.XCORR_SEARCH_S, 0.05)
    else:
        cands = np.arange(prior[0], prior[1], 0.1)
    chest = mc.chest_ap_mm.to_numpy().copy()
    chest[posture_mask(mc)] = np.nan
    best, r, _ = sy.best_start(mc.abs_t.to_numpy(), chest, belt.t.to_numpy(), belt.force.to_numpy(), cands)
    res.update(start_xcorr=best, xcorr_r=r)
    if "start_hold" in res:
        res["method"] = "息止めの終わり（ベルトと MCap の胸の前後径）"
        res["start"] = res["start_hold"]
        res["xcorr_minus_hold_s"] = best - res["start_hold"]
        res["status"] = "ok" if abs(best - res["start_hold"]) < 0.5 and r >= sy.XCORR_MIN_R else "要確認"
    else:
        res["method"] = "相互相関（息止めが見つからなかったため）"
        res["start"] = best
        res["status"] = "ok" if r >= sy.XCORR_MIN_R else "合わせられない（相関が低い）"
    return res


# ---------------------------------------------------------------------------
# 機器間の時計の確認（右手首の動き）
# ---------------------------------------------------------------------------
def _env(x, hz=sy.GRID_HZ):
    x = np.where(np.isnan(x), np.nanmedian(x), x)
    return sy.moving_average(np.abs(x), 0.5 * hz)


def clock_check(imu: pd.DataFrame, rel_t, rel_speed, nominal_start: float):
    """IMU の角速度の大きさを基準に、右手首の速さ（相対時刻）の最適な開始時刻を探す"""
    gyro = np.sqrt(imu.rotation_rate_x_rad_s ** 2 + imu.rotation_rate_y_rad_s ** 2 + imu.rotation_rate_z_rad_s ** 2)
    gi, yi = sy.to_grid(imu.abs_t, gyro)
    gs, ys = sy.to_grid(rel_t, rel_speed)
    cands = np.arange(nominal_start - CLOCK_SEARCH_S, nominal_start + CLOCK_SEARCH_S, 0.05)
    best, r, _ = sy.best_start(gi, _env(yi), gs, _env(ys), cands, hp=False)
    d = best - nominal_start
    return {"offset_s": d, "r": r,
            "status": "ok" if abs(d) <= CLOCK_TOL_S and r >= sy.XCORR_MIN_R
            else ("相関が低く判定できない" if r < sy.XCORR_MIN_R else "要確認（時計がずれている可能性）")}


# ---------------------------------------------------------------------------
# 手本動画との対応
# ---------------------------------------------------------------------------
def motion_features(df: pd.DataFrame) -> np.ndarray:
    cols = []
    for c in MOTION_COLS:
        if c in df:
            cols.append(df[c].to_numpy(float))
        else:
            cols.append(np.nanmean(np.stack([df[f"{c}_L"], df[f"{c}_R"]]), axis=0))
    return np.stack(cols, axis=1)


def youtube_by_audio(ref_wav: Path, mov_wav: Path):
    from scipy.io import wavfile
    from scipy.signal import resample_poly

    def load(p):
        sr, x = wavfile.read(p)
        x = x.astype(float)
        if x.ndim > 1:
            x = x.mean(axis=1)
        if sr != sy.AUDIO_HZ:
            g = np.gcd(sr, sy.AUDIO_HZ)
            x = resample_poly(x, sy.AUDIO_HZ // g, sr // g)
        return x / (np.std(x) + 1e-12)

    ref, mov = load(ref_wav), load(mov_wav)
    r = sy.audio_offset(ref, mov)
    offs = np.array([w[1] for w in r["windows"] if w[2] >= 10])
    spread = float(np.ptp(offs)) if len(offs) else np.nan
    r.update(method="音声の相互相関（GCC-PHAT）", windows_used=int(len(offs)), window_spread_s=spread,
             status="ok" if r["sharpness"] >= 10 and (len(offs) == 0 or spread <= sy.AUDIO_TOL_S)
             else "要確認（ピークが弱い、または再生が連続していない可能性）")
    return r


def youtube_by_motion(ref: pd.DataFrame, sub: pd.DataFrame, center: float | None = None, half: float = 30.0):
    """関節角度の相関で全体のずれ（YouTube 時刻 = 動画時刻 + offset）を求める"""
    R, S = sy.zscore_cols(motion_features(ref)), sy.zscore_cols(motion_features(sub))
    rt, st = ref.t.to_numpy(), sub.t.to_numpy()
    if center is None:
        cands = np.arange(-st[-1] + 10, rt[-1] - 10, 0.2)
    else:
        cands = np.arange(center - half, center + half, 0.1)
    off, score, _ = sy.motion_offset(rt, R, st, S, cands, MOTION_HZ)
    return {"offset": off, "score": score, "method": "関節角度の相関（左右平均、z 得点）"}


def motion_lag(ref: pd.DataFrame, sub: pd.DataFrame, offset: float) -> pd.DataFrame:
    """全体のずれで重ねた後、DTW で動画時刻ごとの局所的なずれを求める

    lag_s > 0 は、被験者が手本より遅れて同じ姿勢になっていることを表す。
    """
    R, S = sy.zscore_cols(motion_features(ref)), sy.zscore_cols(motion_features(sub))
    gs = np.arange(sub.t.iloc[0], sub.t.iloc[-1], 1 / MOTION_HZ)
    Sg = np.stack([np.interp(gs, sub.t, S[:, c], left=np.nan, right=np.nan) for c in range(S.shape[1])], 1)
    Rg = np.stack([np.interp(gs + offset, ref.t, R[:, c], left=np.nan, right=np.nan) for c in range(R.shape[1])], 1)
    path = sy.dtw_band(Sg, Rg, int(DTW_BAND_S * MOTION_HZ))
    match = {}
    for i, j in path:
        match.setdefault(i, []).append(j)
    j_of_i = np.array([np.mean(match[i]) for i in range(len(gs))])
    lag = (np.arange(len(gs)) - j_of_i) / MOTION_HZ
    # どちらかの角度が全部欠けている時刻は、対応づけの根拠がないので欠損にする
    lag[np.isnan(Sg).all(axis=1) | np.isnan(Rg).all(axis=1)] = np.nan
    return pd.DataFrame({"t_video": gs, "yt_time_linear": gs + offset,
                         "yt_time_dtw": gs + offset - lag, "lag_s": lag})


# ---------------------------------------------------------------------------
# 図
# ---------------------------------------------------------------------------
def _dt(ts):
    return [datetime.fromtimestamp(float(x), JST) for x in np.atleast_1d(ts)]


def plot_overview(out: Path, title, belt_abs, belt, mc, imu, sub_abs, sub, ref, yt_off, video_start,
                  cues, lag, spans):
    plt = common.setup_matplotlib()
    import matplotlib.dates as mdates
    fig, ax = plt.subplots(6, 1, figsize=(17, 15), sharex=True,
                           gridspec_kw={"height_ratios": [1.2, 1.2, 1, 1, 0.8, 0.7]})
    # 1. 呼吸
    a = ax[0]
    a.plot(_dt(belt_abs), belt.force, color="#2f86b5", lw=1, label="ベルト 力(N)")
    a.set_ylabel("ベルト [N]", color="#2f86b5")
    a2 = a.twinx()
    a2.plot(_dt(mc.abs_t[::5]), mc.chest_ap_mm[::5], color="#b5523b", lw=0.8)
    a2.set_ylabel("胸 STRN–T10 [mm]", color="#b5523b")
    a.set_title("呼吸: ベルト（青）と MCap の胸の前後径（赤）")
    # 2. 体幹の傾き
    a = ax[1]
    a.plot(_dt(mc.abs_t[::5]), mc.trunk_incl[::5], color="k", lw=1, label="MCap")
    if sub is not None:
        a.plot(_dt(sub_abs), sub.trunk_incl, color="#e08a1e", lw=1, label="MediaPipe（被験者動画）")
    if ref is not None and yt_off is not None:
        a.plot(_dt(ref.t - yt_off + video_start), ref.trunk_incl, color="#3c9a6a", lw=1, ls="--",
               label="手本（YouTube 時刻を合わせたもの）")
    a.set_ylabel("体幹の傾き [°]")
    a.legend(loc="upper right", fontsize=8)
    # 3. 右手首の向き
    a = ax[2]
    a.plot(_dt(imu.abs_t[::10]), imu.pitch_deg[::10], color="#7c8f9b", lw=0.8, label="IMU pitch")
    a.plot(_dt(imu.abs_t[::10]), imu.roll_deg[::10], color="#b07a2a", lw=0.8, label="IMU roll")
    a.set_ylabel("IMU [°]")
    if sub is not None:
        a2 = a.twinx()
        a2.plot(_dt(sub_abs), sub.forearm_incl_R, color="#e08a1e", lw=0.9)
        a2.set_ylabel("MediaPipe 右前腕の傾き [°]", color="#e08a1e")
    a.legend(loc="upper left", fontsize=8)
    a.set_title("右手首: IMU の Roll/Pitch（灰・茶）と MediaPipe の右前腕（橙）")
    # 4. 右手首の動きの大きさ（時計の確認）
    a = ax[3]
    z = lambda x: (x - np.nanmean(x)) / (np.nanstd(x) + 1e-9)
    gyro = np.sqrt(imu.rotation_rate_x_rad_s ** 2 + imu.rotation_rate_y_rad_s ** 2 + imu.rotation_rate_z_rad_s ** 2)
    a.plot(_dt(imu.abs_t[::10]), z(gyro.rolling(50, min_periods=1).mean().to_numpy()[::10]), color="#7c8f9b",
           lw=0.8, label="IMU 角速度")
    a.plot(_dt(mc.abs_t[::10]), z(mc.r_wrist_speed.rolling(50, min_periods=1).mean().to_numpy()[::10]),
           color="k", lw=0.8, label="MCap 右手首の速さ")
    if sub is not None:
        a.plot(_dt(sub_abs), z(sub.r_wrist_speed.rolling(8, min_periods=1).mean().to_numpy()), color="#e08a1e",
               lw=0.8, label="動画 右手首の速さ")
    a.set_ylabel("z 得点")
    a.legend(loc="upper right", fontsize=8)
    a.set_title("右手首の動きの大きさ（山の位置がそろっていれば、IMU・MCap・動画の時計は合っている）")
    # 5. 呼吸キューと動きのずれ
    a = ax[4]
    if yt_off is not None:
        for c in cues:
            s0, s1 = c.t - yt_off + video_start, c.end - yt_off + video_start
            a.axvspan(*_dt([s0, s1]), color=common.CUE_COLOR.get(c.kind, "#999"), alpha=0.35, lw=0)
    if lag is not None:
        a.plot(_dt(lag.t_video + video_start), lag.lag_s, color="#333", lw=1)
        a.axhline(0, color="#999", lw=0.5)
    a.set_ylabel("動きの遅れ [s]")
    a.set_title("手本の呼吸キュー（青=吸う、緑=吐く、灰=力を抜く）と、手本に対する動きの遅れ（DTW）")
    # 6. 記録の範囲
    a = ax[5]
    for k, (name, s0, s1) in enumerate(spans):
        a.barh(k, (s1 - s0) / 86400, left=mdates.date2num(_dt(s0)[0]), color="#9db1bd")
        a.text(mdates.date2num(_dt(s0)[0]), k, f" {name}", va="center", fontsize=8)
    a.set_yticks([])
    a.set_title("各センサーの記録範囲")
    ax[-1].xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S", tz=JST))
    ax[-1].set_xlabel("時刻（JST）")
    for x in ax:
        x.grid(alpha=0.3)
    fig.suptitle(title)
    fig.tight_layout()
    fig.savefig(out, dpi=100)
    plt.close(fig)


def plot_hold(out: Path, belt, mc, take, res):
    plt = common.setup_matplotlib()
    fig, ax = plt.subplots(figsize=(13, 4.5))
    st = res["start"]
    t0 = take.start
    ax.plot(mc.t, mc.chest_ap_mm, color="#b5523b", lw=1)
    ax.set_ylabel("胸 STRN–T10 [mm]", color="#b5523b")
    a2 = ax.twinx()
    a2.plot(belt.t + st - t0, belt.force, color="#2f86b5", lw=1.3)
    a2.set_ylabel("ベルト 力 [N]", color="#2f86b5")
    for h, col in ((res["hold_mocap"], "#b5523b"), (res["hold_belt"], "#2f86b5")):
        if h:
            shift = 0 if col == "#b5523b" else st - t0
            ax.axvspan(h["start"] + shift, h["end"] + shift, color=col, alpha=0.12)
    end = (res["hold_mocap"] or {"end": 10})["end"]
    ax.set_xlim(end - 20, end + 25)
    ax.set_xlabel("MCap の Take 内の時刻 [s]")
    ax.set_title(f"息止め付近（帯 = 検出した息止め、赤 = MCap、青 = ベルト）  方法: {res['method']}")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out, dpi=100)
    plt.close(fig)


def plot_audio(out: Path, r):
    plt = common.setup_matplotlib()
    fig, ax = plt.subplots(figsize=(10, 3.5))
    w = np.array(r["windows"]) if r["windows"] else np.zeros((0, 3))
    if len(w):
        sc = ax.scatter(w[:, 0], w[:, 1], c=w[:, 2], cmap="viridis")
        fig.colorbar(sc, label="ピークの鋭さ")
    ax.axhline(r["offset"], color="#b5523b", lw=1, label=f"全体: {r['offset']:.2f} s")
    ax.set_xlabel("被験者動画の時刻 [s]（30 秒窓の開始）")
    ax.set_ylabel("YouTube 時刻 - 動画時刻 [s]")
    ax.set_title("音声によるずれ（窓ごとの値が一定なら、YouTube は途中で止めずに再生されている）")
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out, dpi=100)
    plt.close(fig)


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--subject", required=True)
    ap.add_argument("--condition", required=True, choices=list(sensors.CONDITIONS))
    ap.add_argument("--data", default=str(DATA_ROOT))
    a = ap.parse_args()

    ses = sensors.find_session(Path(a.data), a.subject, a.condition)
    out = RESULTS_DIR / a.subject / a.condition
    out.mkdir(parents=True, exist_ok=True)
    report = {"subject": a.subject, "condition": a.condition, "folder": str(ses.folder),
              "missing_files": ses.missing}
    J = sensors.fmt_jst

    # --- 動画（段階2の結果）
    vinfo = json.loads((out / "video_info.json").read_text(encoding="utf-8"))
    video_start = vinfo["start_unix"]
    sub = pd.read_csv(out / "subject_angles.csv") if (out / "subject_angles.csv").exists() else None
    report["video"] = {k: vinfo[k] for k in ("file", "start_jst", "source", "duration_s", "name_check")}
    print(f"動画      {J(video_start)}  {vinfo['source']}（{vinfo['name_check']}）")

    # --- IMU
    imu = sensors.load_imu(ses.imu)
    nm = sensors.imu_name_times(ses.imu)
    report["imu"] = {"file": ses.imu.name, "start_jst": J(imu.abs_t.iloc[0]), "end_jst": J(imu.abs_t.iloc[-1]),
                     "source": "各行の timestamp_iso8601（UTC → JST）",
                     "name_vs_data_s": None if nm is None else round(imu.abs_t.iloc[0] - nm[0], 3),
                     "rate_hz": round(1 / np.median(np.diff(imu.abs_t)), 2)}
    print(f"IMU       {J(imu.abs_t.iloc[0])} – {J(imu.abs_t.iloc[-1])}")

    # --- MCap
    take = sensors.load_take(ses.take)
    mc = sensors.mocap_angles(take)
    mc.to_csv(out / "mocap_angles.csv", index=False, float_format="%.4f")
    report["mocap"] = {"file": ses.take.name, "start_jst": J(take.start), "source": take.start_source,
                       "duration_s": float(take.t[-1]),
                       "gap_filled": {k: v for k, v in take.filled.items() if v}}
    print(f"MCap      {J(take.start)} – {J(take.abs_t[-1])}  {take.start_source}")

    # --- 呼吸ベルト
    belt = sensors.load_belt(ses.belt)
    starts = [video_start, imu.abs_t.iloc[0], take.start]
    br = align_belt(belt, mc, take, (min(starts) - 60, max(starts) + 60))
    belt_abs = br["start"] + belt.t.to_numpy()
    report["belt"] = {"file": ses.belt.name, "start_jst": J(br["start"]), "method": br["method"],
                      "status": br["status"], "hold_belt": br["hold_belt"], "hold_mocap": br["hold_mocap"],
                      "start_by_hold_jst": J(br["start_hold"]) if "start_hold" in br else None,
                      "start_by_xcorr_jst": J(br["start_xcorr"]), "xcorr_r": round(br["xcorr_r"], 3),
                      "xcorr_minus_hold_s": round(br.get("xcorr_minus_hold_s", np.nan), 2)}
    print(f"ベルト    {J(br['start'])}  {br['method']}  [{br['status']}]"
          + (f"  相互相関との差 {br['xcorr_minus_hold_s']:+.2f} s (r={br['xcorr_r']:.2f})"
             if "xcorr_minus_hold_s" in br else f"  r={br['xcorr_r']:.2f}"))

    # --- 時計の確認
    chk = {"mocap_vs_imu": clock_check(imu, take.t, mc.r_wrist_speed.to_numpy(), take.start)}
    if sub is not None:
        chk["video_vs_imu"] = clock_check(imu, sub.t.to_numpy(), sub.r_wrist_speed.to_numpy(), video_start)
    report["clock_checks"] = chk
    for k, v in chk.items():
        print(f"時計確認  {k}: {v['offset_s']:+.2f} s (r={v['r']:.2f}) [{v['status']}]")

    # --- 手本動画との対応
    ref_dir = RESULTS_DIR / "reference"
    ref = pd.read_csv(ref_dir / "reference_angles.csv") if (ref_dir / "reference_angles.csv").exists() else None
    from breath_core import load_cues
    cues = load_cues(str(ref_dir / "cues.json")) if (ref_dir / "cues.json").exists() else []
    yt, lag = None, None
    if a.condition == "音声あり":
        mov_wav = WORK_DIR / a.subject / a.condition / "video_audio.wav"
        yt = youtube_by_audio(WORK_DIR / "reference" / "reference.wav", mov_wav)
        plot_audio(out / "sync_audio.png", yt)
        if ref is not None and sub is not None:
            mo = youtube_by_motion(ref, sub, center=yt["offset"])
            yt["motion_offset"] = mo["offset"]
            yt["motion_score"] = mo["score"]
            # 被験者が手本より d 秒遅れて動くと、動きで合わせたずれは音声のずれより d 小さくなる
            yt["movement_delay_s"] = yt["offset"] - mo["offset"]
    elif ref is not None and sub is not None:
        yt = youtube_by_motion(ref, sub)
        yt["status"] = "ok" if yt["score"] >= sy.XCORR_MIN_R else "要確認（相関が低い）"
    if yt is not None and ref is not None and sub is not None:
        lag = motion_lag(ref, sub, yt["offset"])
        lag.to_csv(out / "motion_lag.csv", index=False, float_format="%.3f")
    if yt is not None:
        yt["youtube_time_at_video_start"] = yt["offset"]
        report["youtube"] = {k: v for k, v in yt.items() if k != "windows"}
        report["youtube"]["windows"] = [[round(x, 3) for x in w] for w in yt.get("windows", [])]
        msg = f"YouTube   動画時刻 + {yt['offset']:.2f} s = YouTube 時刻  {yt['method']} [{yt.get('status')}]"
        if "movement_delay_s" in yt:
            msg += f"  動きの遅れ（全体）{yt['movement_delay_s']:+.2f} s"
        print(msg)

    # --- 保存
    bdf = belt.assign(abs_t=belt_abs, t_video=belt_abs - video_start)
    if yt is not None:
        bdf["yt_time"] = bdf.t_video + yt["offset"]
    bdf.to_csv(out / "belt_aligned.csv", index=False, float_format="%.3f")
    report["notes"] = [
        "時刻はすべて JST。各センサーの開始時刻の根拠は source / method に記載。",
        "呼吸ベルトの開始時刻は推定値（息止めの終わりを MCap と合わせたもの）。",
        "時計の確認は右手首の動きの相互相関。r が低い場合は判定できていない。",
    ]
    (out / "sync.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=float),
                                   encoding="utf-8")

    sub_abs = sub.t.to_numpy() + video_start if sub is not None else None
    spans = [("動画", video_start, video_start + vinfo["duration_s"]),
             ("IMU", imu.abs_t.iloc[0], imu.abs_t.iloc[-1]),
             ("MCap", take.start, take.abs_t[-1]),
             ("ベルト（推定）", belt_abs[0], belt_abs[-1])]
    plot_overview(out / "sync_overview.png", f"被験者{a.subject}・{a.condition}: 時刻合わせの確認",
                  belt_abs, belt, mc, imu, sub_abs, sub, ref, yt["offset"] if yt else None, video_start,
                  cues, lag, spans)
    plot_hold(out / "sync_belt_hold.png", belt, mc, take, br)
    print(f"→ {out}")


if __name__ == "__main__":
    main()
