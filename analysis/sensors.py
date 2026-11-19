"""被験者データ（呼吸ベルト・IMU・MCap・動画）の読み込みと時刻の統一

時刻はすべて「UNIX 時刻（秒、float）」で持つ。表示するときだけ JST に直す。
各センサーの開始時刻をどう決めたか（根拠）は、戻り値の source に文字列で残す。

仮定・注意（論文の手法の章用）:
  - IMU / RAWACC: 行ごとの timestamp_iso8601（UTC）をそのまま使う（Apple Watch の時計）。
  - MCap: ヘッダーの Capture Start Time（ミリ秒まで、午前/午後表記）を JST とみなして開始時刻にし、
    各フレームの時刻 = 開始時刻 + Time (Seconds)。MCap PC の時計は NTP 同期されていると仮定する。
  - 動画: ffprobe で読んだ mov の作成日時を録画開始時刻とする（iPhone の
    com.apple.quicktime.creationdate を優先し、なければ creation_time（UTC））。
    ファイル名の分・秒と照合して、食い違いがあれば警告する。
  - 呼吸ベルト: 開始時刻の記録がないため、ここでは相対時刻（0 始まり）のまま返す。
    絶対時刻は sync.py で MCap と合わせて決める。
"""
from __future__ import annotations

import csv
import json
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from common import JST

CONDITIONS = {"音声あり": "N2", "音声なし": "N1"}   # 呼吸ベルトのファイル名の末尾（全被験者共通）
ZEN = str.maketrans("0123456789", "０１２３４５６７８９")


def fmt_jst(ts: float, ms: bool = True) -> str:
    d = datetime.fromtimestamp(ts, JST)
    return d.strftime("%H:%M:%S.%f")[:-3] if ms else d.strftime("%H:%M:%S")


# ---------------------------------------------------------------------------
# ファイルの特定
# ---------------------------------------------------------------------------
@dataclass
class Session:
    subject: str
    condition: str
    folder: Path
    belt: Path | None = None
    imu: Path | None = None
    rawacc: Path | None = None
    take: Path | None = None
    video: Path | None = None
    missing: list[str] = field(default_factory=list)


def find_session(data_root: Path, subject: str, condition: str) -> Session:
    if condition not in CONDITIONS:
        raise ValueError(f"条件は {list(CONDITIONS)} のどちらか")
    subj_dir = data_root / subject
    if not subj_dir.exists():   # フォルダ名が全角数字（１〜５）の場合
        subj_dir = data_root / subject.translate(ZEN)
    folder = subj_dir / condition
    if not folder.exists():
        raise FileNotFoundError(folder)
    s = Session(subject, condition, folder)

    def one(pattern, attr):
        hits = sorted(p for p in folder.glob(pattern) if p.is_file())
        if hits:
            setattr(s, attr, hits[0])
        else:
            s.missing.append(pattern)

    one(f"*{CONDITIONS[condition]}.csv", "belt")
    one("IMU_*.csv", "imu")
    one("RAWACC_*.csv", "rawacc")
    one("Take *.csv", "take")
    one("*.mov", "video")
    if not s.video:
        one("*.MOV", "video")
    return s


# ---------------------------------------------------------------------------
# 呼吸ベルト
# ---------------------------------------------------------------------------
def load_belt(path: Path) -> pd.DataFrame:
    """列: t（ベルト内の相対時刻 s）, force（N）, rr（呼吸数 bpm、ほとんど空欄）"""
    df = pd.read_csv(path, encoding="utf-8-sig")
    cols = {c: c.split(":")[-1] for c in df.columns}
    df = df.rename(columns=cols)
    out = pd.DataFrame({"t": df["時間(s)"].astype(float), "force": df["力(N)"].astype(float)})
    out["rr"] = pd.to_numeric(df.get("呼吸数(bpm)"), errors="coerce")
    return out


# ---------------------------------------------------------------------------
# IMU（Apple Watch、右手首）
# ---------------------------------------------------------------------------
def _iso_to_unix(s: pd.Series) -> np.ndarray:
    # pandas の版によって datetime の分解能（ns/us）が違うので、差分の秒数で求める
    d = pd.to_datetime(s, utc=True, format="ISO8601")
    return (d - pd.Timestamp("1970-01-01", tz="UTC")).dt.total_seconds().to_numpy()


def load_imu(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, encoding="utf-8-sig")
    df = df[df["timestamp_iso8601"].notna()].copy()
    df.insert(0, "abs_t", _iso_to_unix(df["timestamp_iso8601"]))
    return df.reset_index(drop=True)


def imu_name_times(path: Path) -> tuple[float, float] | None:
    """ファイル名 IMU_AW07_YYYYMMDD_HHMMSS_YYYYMMDD_HHMMSS（JST）→ (開始, 終了)"""
    m = re.search(r"(\d{8})_(\d{6})_(\d{8})_(\d{6})", path.name)
    if not m:
        return None
    f = lambda d, t: datetime.strptime(d + t, "%Y%m%d%H%M%S").replace(tzinfo=JST).timestamp()
    return f(m.group(1), m.group(2)), f(m.group(3), m.group(4))


# ---------------------------------------------------------------------------
# MCap（OptiTrack Motive, Format 1.24）
# ---------------------------------------------------------------------------
def _parse_ampm(text: str) -> float:
    """'2026-11-05 01.57.59.618 午後' / '... PM' → UNIX 時刻（JST とみなす）"""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2}) (\d{1,2})\.(\d{2})\.(\d{2})(?:\.(\d+))?\s*(午前|午後|AM|PM)?",
                 text.strip())
    if not m:
        raise ValueError(f"時刻を読めません: {text}")
    y, mo, d, h, mi, s, frac, ap = m.groups()
    h = int(h)
    if ap in ("午後", "PM") and h != 12:
        h += 12
    if ap in ("午前", "AM") and h == 12:
        h = 0
    base = datetime(int(y), int(mo), int(d), h, int(mi), int(s), tzinfo=JST).timestamp()
    return base + (float("0." + frac) if frac else 0.0)


@dataclass
class Take:
    start: float               # UNIX 時刻
    start_source: str
    t: np.ndarray              # Take 内の時刻 [s]
    header: dict
    markers: dict[str, np.ndarray]   # 名前 -> [N,3] mm（Marker、欠損は fill_gaps の方法で補完済み）
    filled: dict[str, dict]          # 名前 -> {補完の方法: フレーム数}
    bones: dict[str, np.ndarray]     # 名前 -> [N,3] mm（Bone の位置）
    bone_rot: dict[str, np.ndarray]  # 名前 -> [N,4] 四元数 (x, y, z, w)

    @property
    def abs_t(self) -> np.ndarray:
        return self.start + self.t


def load_take(path: Path) -> Take:
    with open(path, encoding="utf-8-sig", newline="") as f:
        head = [next(csv.reader([f.readline()])) for _ in range(12)]
    kv = head[0]
    header = {kv[i]: kv[i + 1] for i in range(0, len(kv) - 1, 2)}
    h = next(i for i, r in enumerate(head) if r and r[0] == "Frame")
    typ, name, kind, axis = head[h - 4], head[h - 3], head[h - 1], head[h]

    if "Capture Start Time" in header:
        start, src = _parse_ampm(header["Capture Start Time"]), "ヘッダーの Capture Start Time"
    else:   # 古い書き出しなどでヘッダーにない場合はファイル名（秒まで）
        m = re.search(r"Take (\d{4}-\d{2}-\d{2} \d{1,2}\.\d{2}\.\d{2} [AP]M)", path.name)
        start, src = _parse_ampm(m.group(1)), "ファイル名（秒まで）"

    data = pd.read_csv(path, skiprows=h + 1, header=None, encoding="utf-8-sig", low_memory=False)
    t = data[1].to_numpy(float)

    def group(type_name, k_name="Position", axes="XYZ"):
        out = {}
        for i, (ty, nm, k, ax) in enumerate(zip(typ, name, kind, axis)):
            if ty == type_name and k == k_name and ax in axes:
                out.setdefault(nm.split(":")[-1], {})[ax] = i
        return {nm: data[[c[a] for a in axes]].to_numpy(float) for nm, c in out.items() if len(c) == len(axes)}

    marker, bone_marker = group("Marker"), group("Bone Marker")
    bones, bone_rot = group("Bone"), group("Bone", "Rotation", "XYZW")
    # ルートの Bone 名は "Skeleton 001" のようにスケルトン名と同じ。骨盤として "Hips" の別名を付ける
    root = next((nm for nm in bones if nm.startswith("Skeleton")), None)
    if root:
        bones["Hips"], bone_rot["Hips"] = bones[root], bone_rot[root]
    markers, filled = {}, {}
    for nm in sorted(set(marker) | set(bone_marker)):
        markers[nm], filled[nm] = fill_gaps(nm, marker.get(nm), bone_marker.get(nm), bones, bone_rot)
    return Take(start, src, t, header, markers, filled, bones, bone_rot)


# マーカー → それが乗っている体節の Bone（Motive の Baseline 39 マーカー）
MARKER_BONE = {
    **{m: "Head" for m in ("LFHD", "RFHD", "LBHD", "RBHD")},
    **{m: "Chest" for m in ("C7", "T10", "CLAV", "STRN", "RBAK")},
    **{m: "Hips" for m in ("LASI", "RASI", "LPSI", "RPSI")},
}
for _s in "LR":
    MARKER_BONE.update({f"{_s}SHO": f"{_s}Shoulder", f"{_s}UPA": f"{_s}UArm", f"{_s}ELB": f"{_s}UArm",
                        f"{_s}FRM": f"{_s}FArm", f"{_s}WRA": f"{_s}FArm", f"{_s}WRB": f"{_s}FArm",
                        f"{_s}FIN": f"{_s}Hand", f"{_s}THI": f"{_s}Thigh", f"{_s}KNE": f"{_s}Thigh",
                        f"{_s}TIB": f"{_s}Shin", f"{_s}ANK": f"{_s}Shin", f"{_s}HEE": f"{_s}Foot",
                        f"{_s}TOE": f"{_s}Toe"})


def _quat_to_mat(q):
    """[N,4] (x, y, z, w) → [N,3,3]"""
    x, y, z, w = q.T
    return np.stack([
        np.stack([1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)], -1),
        np.stack([2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)], -1),
        np.stack([2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)], -1)], -2)


def fill_gaps(nm, m, bm, bones, bone_rot):
    """実測 Marker の欠損を補う。戻り値: (位置 [N,3], {方法: フレーム数})

    1. Bone Marker（Motive がスケルトンから求めたマーカー位置）。ただしこのデータでは
       Bone Marker は実測 Marker と同じ値・同じ欠損で、補完には使えないことを確認している。
    2. 剛体としての補完: マーカーが見えているフレームで、そのマーカーが乗る Bone の座標系での
       相対位置（中央値）を求め、欠損フレームでは Bone の位置・回転からマーカー位置を復元する。
       皮膚の動き（軟部組織のずれ）は表せないので、復元値には数 mm〜1 cm 程度の誤差があり得る。
    """
    stats = {}
    if m is None:
        m = np.full_like(bm, np.nan) if bm is not None else None
    if m is None:
        return None, stats
    m = m.copy()
    miss = np.isnan(m).any(axis=1)
    if bm is not None and miss.any():
        ok = miss & ~np.isnan(bm).any(axis=1)
        m[ok] = bm[ok]
        if ok.sum():
            stats["Bone Marker"] = int(ok.sum())
        miss &= ~ok
    bone = MARKER_BONE.get(nm)
    if miss.any() and bone in bones and bone in bone_rot:
        p, q = bones[bone], bone_rot[bone]
        good = ~np.isnan(m).any(axis=1) & ~np.isnan(p).any(axis=1) & ~np.isnan(q).any(axis=1)
        if good.sum() >= 10:
            R = _quat_to_mat(np.nan_to_num(q))
            local = np.einsum("nji,nj->ni", R[good], m[good] - p[good])   # R^T (m - p)
            off = np.median(local, axis=0)
            can = miss & ~np.isnan(p).any(axis=1) & ~np.isnan(q).any(axis=1)
            m[can] = p[can] + np.einsum("nij,j->ni", R[can], off)
            if can.sum():
                stats[f"剛体補完（{bone}）"] = int(can.sum())
            miss &= ~can
    if miss.sum():
        stats["欠損のまま"] = int(miss.sum())
    return m, stats


# ---------------------------------------------------------------------------
# MCap の角度（MediaPipe と同じ定義）と胸の動き
# ---------------------------------------------------------------------------
def _mean(*arrs):
    return np.nanmean(np.stack(arrs), axis=0)


def hip_joint_centers(mk: dict[str, np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    """ASIS/PSIS から股関節中心を推定する（Harrington et al., 2007 の回帰式）

    骨盤座標系: 原点 = 左右 ASIS の中点、z = 右方向、x = 前方、y = 上方（ISB 準拠）。
    PW = ASIS 間距離、PD = ASIS 中点と PSIS 中点の距離 [mm]
      x = -0.24·PD - 9.9,  y = -0.30·PW - 10.9,  z = ±(0.33·PW + 7.3)
    マーカーの皮膚上の位置ずれ・軟部組織の厚みにより数 cm の誤差があり得る。
    """
    lasi, rasi, lpsi, rpsi = mk["LASI"], mk["RASI"], mk["LPSI"], mk["RPSI"]
    o = (lasi + rasi) / 2
    mpsi = (lpsi + rpsi) / 2
    z = rasi - lasi
    pw = np.linalg.norm(z, axis=1, keepdims=True)
    z = z / pw
    xa = o - mpsi
    pd_ = np.linalg.norm(xa, axis=1, keepdims=True)
    x = xa - np.sum(xa * z, axis=1, keepdims=True) * z
    x /= np.linalg.norm(x, axis=1, keepdims=True)
    y = np.cross(z, x)
    base = o + (-0.24 * pd_ - 9.9) * x + (-0.30 * pw - 10.9) * y
    lat = (0.33 * pw + 7.3) * z
    return base - lat, base + lat   # 左, 右


def _angle(a, b, c):
    v1, v2 = a - b, c - b
    cos = np.sum(v1 * v2, axis=-1) / (np.linalg.norm(v1, axis=-1) * np.linalg.norm(v2, axis=-1))
    return np.degrees(np.arccos(np.clip(cos, -1, 1)))


def mocap_angles(take: Take) -> pd.DataFrame:
    """MediaPipe（pose_angles.py）と同じ列名・定義の角度 [deg]

    使う点: 肩 = LSHO/RSHO（肩峰）、肘 = LELB/RELB（外側上顆）、手首 = WRA/WRB の中点、
    股関節 = Harrington 推定の股関節中心、膝 = LKNE/RKNE（外側）、足首 = LANK/RANK（外果）。
    MediaPipe の関節点は関節中心付近を推定するのに対し、マーカーは体表（外側）にあるため、
    同じ定義でも系統的な差が出る可能性がある。
    """
    mk = take.markers
    hip = dict(zip("LR", hip_joint_centers(mk)))
    pelvis = _mean(mk["LASI"], mk["RASI"], mk["LPSI"], mk["RPSI"])
    chest = _mean(mk["C7"], mk["CLAV"], mk["STRN"], mk["T10"])
    v = chest - pelvis
    df = pd.DataFrame({"abs_t": take.abs_t, "t": take.t})
    # 体幹の傾き: 骨盤中心→胸の中心 と 鉛直（+Y）のなす角
    df["trunk_incl"] = np.degrees(np.arccos(np.clip(v[:, 1] / np.linalg.norm(v, axis=1), -1, 1)))
    for s in "LR":
        sh, el, kn, an = mk[f"{s}SHO"], mk[f"{s}ELB"], mk[f"{s}KNE"], mk[f"{s}ANK"]
        wr = _mean(mk[f"{s}WRA"], mk[f"{s}WRB"])
        df[f"hip_{s}"] = _angle(sh, hip[s], kn)
        df[f"knee_{s}"] = _angle(hip[s], kn, an)
        df[f"shoulder_{s}"] = _angle(hip[s], sh, el)
        df[f"elbow_{s}"] = _angle(sh, el, wr)
    # 胸の前後径（STRN–T10 距離）: 吸気で大きくなる。呼吸ベルトとの時刻合わせに使う
    df["chest_ap_mm"] = np.linalg.norm(mk["STRN"] - mk["T10"], axis=1)
    # 右手首の速さ（IMU・動画との照合用）
    wr = _mean(mk["RWRA"], mk["RWRB"])
    df["r_wrist_speed"] = np.r_[np.nan, np.linalg.norm(np.diff(wr, axis=0), axis=1) / np.diff(take.t)] / 1000
    return df


# ---------------------------------------------------------------------------
# 動画の開始時刻
# ---------------------------------------------------------------------------
@dataclass
class VideoInfo:
    start: float
    source: str
    duration: float
    name_check: str
    tags: dict


def probe_video(path: Path) -> VideoInfo:
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format",
                          "-show_streams", str(path)], capture_output=True, text=True, encoding="utf-8",
                         check=True).stdout
    info = json.loads(out)
    tags = dict(info["format"].get("tags", {}))
    for st in info.get("streams", []):
        for k, v in st.get("tags", {}).items():
            tags.setdefault(f"stream.{k}", v)
    duration = float(info["format"].get("duration", "nan"))
    apple = tags.get("com.apple.quicktime.creationdate")
    if apple:   # 例: 2026-11-05T13:57:44+0900（端末のローカル時刻とオフセット付き）
        start = pd.Timestamp(apple).timestamp()
        src = "com.apple.quicktime.creationdate"
    elif "creation_time" in tags:   # 例: 2026-11-05T04:57:44.000000Z（UTC）
        start = pd.Timestamp(tags["creation_time"]).timestamp()
        src = "creation_time"
    else:
        raise ValueError(f"{path.name}: 作成日時のタグがありません")
    # ファイル名（ビデオ-YYYYMMDD-HHMMSS-...）の分・秒と照合する。時はずれている可能性がある
    m = re.search(r"-(\d{8})-(\d{2})(\d{2})(\d{2})-", path.name)
    check = "ファイル名に時刻なし"
    if m:
        d = datetime.fromtimestamp(start, JST)
        name_ms = int(m.group(3)) * 60 + int(m.group(4))
        diff = (d.minute * 60 + d.second - name_ms + 1800) % 3600 - 1800
        hour_diff = (d.hour - int(m.group(2))) % 24
        check = (f"分・秒の差 {diff:+d} s、時の差 {hour_diff} 時間"
                 + ("" if abs(diff) <= 2 else "（要確認: 分・秒が一致しません）"))
    return VideoInfo(start, src, duration, check, tags)
