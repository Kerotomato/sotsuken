"""GDX-RB 呼吸コーチング 共通ロジック

- キーフレーズ定義（CUE_RULES）とテキストからのキュー抽出
- 腹部張力（ベルト張力）の正規化・呼吸相推定（PhaseDetector）
- キューごとの目標と適合判定（evaluate）
"""
from __future__ import annotations

import json
import re
from collections import deque
from dataclasses import dataclass, asdict, field

import numpy as np

# ---------------------------------------------------------------------------
# キュー定義
# ---------------------------------------------------------------------------
# kind: (表示ラベル, チャネル, 既定の最大継続秒)
#   チャネル phase      : 呼吸相の指示（同チャネルの次キューで終了）
#   チャネル tension    : 腹部張力の指示
#   チャネル constraint : 「息を止めない」のような制約（HOLD で解除）
CUE_KINDS = {
    "NO_HOLD": ("息を止めない", "constraint", 20.0),
    "HOLD":    ("息を止める",   "phase",      8.0),
    "INHALE":  ("吸いながら",   "phase",      6.0),
    "EXHALE":  ("吐きながら",   "phase",      6.0),
    "BRACE":   ("お腹を固める", "tension",   10.0),
    "RELAX":   ("力を抜く",     "tension",    6.0),
}

# 上から優先。同じ位置で複数一致した場合は先のルールを採用する
# （「息を止めない」が「息を止め」より先に判定されるように並べている）
CUE_RULES: list[tuple[str, re.Pattern]] = [(k, re.compile(p, re.IGNORECASE)) for k, p in [
    ("NO_HOLD", r"(息|呼吸)[をは]?(止め|とめ|こらえ|詰め)(ない|ず|ないで)"
                r"|呼吸を?続け|(don'?t|do not|never) hold (your )?breath|keep breathing"),
    ("HOLD",    r"(息|呼吸)[をは]?(止め|とめ)(て|る|たまま|ます)|息を?こらえ|hold (your )?breath"),
    ("INHALE",  r"吸いながら|(息を?)?吸(って|う|い込|います)|breathe in|inhal"),
    ("EXHALE",  r"吐きながら|(息を?)?吐(いて|く|き切|きます)|breathe out|exhal"),
    ("BRACE",   r"お腹に力|腹圧|お腹を?(固め|締め|膨らま|張)|腹筋に力|ブレーシング|brac"),
    ("RELAX",   r"力を?抜|リラックス|脱力|緩め|ゆるめ|relax"),
]]


@dataclass
class Cue:
    t: float            # 動画時刻 [s]
    kind: str
    text: str           # 一致したフレーズ
    end: float = 0.0
    context: str = ""   # 元の字幕文

    @property
    def label(self) -> str:
        return CUE_KINDS[self.kind][0]

    @property
    def channel(self) -> str:
        return CUE_KINDS[self.kind][1]


def find_phrases(text: str) -> list[tuple[int, str, str]]:
    """テキスト中のキーフレーズを (文字位置, kind, 一致文字列) で返す（重なりは除外）"""
    hits = []
    for prio, (kind, pat) in enumerate(CUE_RULES):
        for m in pat.finditer(text):
            hits.append((m.start(), prio, m.end(), kind, m.group(0)))
    hits.sort()
    out, last_end = [], -1
    for start, _prio, end, kind, s in hits:
        if start < last_end:
            continue
        out.append((start, kind, s))
        last_end = end
    return out


def finalize_cues(cues: list[Cue], merge_sec: float = 2.0) -> list[Cue]:
    """重複除去と終了時刻の決定"""
    cues = sorted(cues, key=lambda c: c.t)
    merged: list[Cue] = []
    for c in cues:
        if merged and merged[-1].kind == c.kind and c.t - merged[-1].t < merge_sec:
            continue
        merged.append(c)
    for i, c in enumerate(merged):
        max_dur = CUE_KINDS[c.kind][2]
        end = c.t + max_dur
        for n in merged[i + 1:]:
            same_ch = n.channel == c.channel
            # 「息を止めて」が来たら「止めない」制約は解除
            breaks_nohold = c.kind == "NO_HOLD" and n.kind == "HOLD"
            if same_ch or breaks_nohold:
                end = min(end, n.t)
                break
        c.end = round(end, 2)
    return merged


def save_cues(path: str, cues: list[Cue], source: str = "") -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"source": source, "cues": [asdict(c) for c in cues]}, f,
                  ensure_ascii=False, indent=2)


def load_cues(path: str) -> list[Cue]:
    with open(path, encoding="utf-8") as f:
        return [Cue(**d) for d in json.load(f)["cues"]]


class CueTimeline:
    def __init__(self, cues: list[Cue]):
        self.cues = cues

    def active(self, t: float) -> list[Cue]:
        """時刻 t に有効なキュー（チャネルごとに最新の1つ）"""
        by_ch: dict[str, Cue] = {}
        for c in self.cues:
            if c.t <= t < c.end:
                by_ch[c.channel] = c
        return list(by_ch.values())

    def upcoming(self, t: float, horizon: float = 10.0) -> list[Cue]:
        return [c for c in self.cues if t < c.t <= t + horizon]


# ---------------------------------------------------------------------------
# 張力の正規化と呼吸相推定
# ---------------------------------------------------------------------------
PHASE_LABEL = {"INHALE": "吸気", "EXHALE": "呼気", "HOLD": "停止", "UNKNOWN": "--"}


@dataclass
class BreathState:
    force: float = 0.0       # 平滑化後の張力 [N]
    norm: float = 0.0        # 正規化張力 (安静呼気=0, 安静吸気=1)
    slope: float = 0.0       # 正規化張力の変化率 [1/s]
    phase: str = "UNKNOWN"
    hold_sec: float = 0.0    # 張力変化が止まっている継続時間
    rate_bpm: float = float("nan")


@dataclass
class PhaseDetector:
    fs: float = 10.0
    calib_sec: float = 30.0      # 正規化に使う直近区間
    slope_win: float = 0.6       # 傾き推定窓
    slope_th: float = 0.12       # 吸気/呼気とみなす傾き [norm/s]
    ema_alpha: float = 0.35
    _buf: deque = field(default_factory=deque)
    _ema: float | None = None
    _t_last_move: float = 0.0
    _inhale_onsets: deque = field(default_factory=lambda: deque(maxlen=6))
    lo: float = 0.0
    hi: float = 1.0
    frozen_calib: bool = False

    def recalibrate(self):
        self.frozen_calib = False

    def update(self, t: float, force: float) -> BreathState:
        self._ema = force if self._ema is None else (
            self.ema_alpha * force + (1 - self.ema_alpha) * self._ema)
        self._buf.append((t, self._ema))
        while self._buf and t - self._buf[0][0] > self.calib_sec:
            self._buf.popleft()
        arr = np.array(self._buf)
        if not self.frozen_calib and len(arr) > self.fs * 5:
            lo, hi = np.percentile(arr[:, 1], [10, 90])
            if hi - lo > 1e-3:
                self.lo, self.hi = lo, hi
            # 1周分以上たまったら基準を固定（コーチング中の「固める」で基準が動かないように）
            if t - arr[0, 0] >= self.calib_sec * 0.95:
                self.frozen_calib = True
        span = max(self.hi - self.lo, 1e-3)
        norm = (self._ema - self.lo) / span

        st = BreathState(force=self._ema, norm=norm)
        w = arr[arr[:, 0] >= t - self.slope_win]
        if len(w) >= 3:
            st.slope = np.polyfit(w[:, 0], (w[:, 1] - self.lo) / span, 1)[0]
        prev_phase = getattr(self, "_phase", "UNKNOWN")
        if st.slope > self.slope_th:
            st.phase = "INHALE"
        elif st.slope < -self.slope_th:
            st.phase = "EXHALE"
        else:
            st.phase = prev_phase if t - self._t_last_move < 0.8 else "HOLD"
        if abs(st.slope) > self.slope_th:
            self._t_last_move = t
        st.hold_sec = t - self._t_last_move
        if st.phase == "INHALE" and prev_phase != "INHALE":
            self._inhale_onsets.append(t)
        self._phase = st.phase
        if len(self._inhale_onsets) >= 3:
            iv = np.diff(self._inhale_onsets)
            iv = iv[iv > 1.0]
            if len(iv):
                st.rate_bpm = 60.0 / float(np.median(iv))
        return st


# ---------------------------------------------------------------------------
# 目標と適合判定
# ---------------------------------------------------------------------------
HOLD_LIMIT_SEC = 2.0
REACTION_SEC = 0.8   # キュー提示から判定開始までの猶予

# kind -> 正規化張力の目標帯 (下限, 上限)。None は帯なし
TARGET_BAND = {
    "INHALE": (0.6, 1.3),
    "EXHALE": (-0.3, 0.4),
    "HOLD":   None,
    "NO_HOLD": None,
    "BRACE":  (0.6, 1.6),
    "RELAX":  (-0.3, 0.35),
}


def evaluate(cue: Cue, t: float, st: BreathState) -> tuple[bool | None, str]:
    """(適合?, メッセージ)。猶予期間中は None"""
    if t - cue.t < REACTION_SEC:
        return None, cue.label
    k = cue.kind
    if k == "NO_HOLD":
        if st.hold_sec > HOLD_LIMIT_SEC:
            return False, f"息が止まっています（{st.hold_sec:.1f}s）→ 吐き続けて"
        return True, "呼吸継続 OK"
    if k == "HOLD":
        return (st.phase == "HOLD", "停止 OK" if st.phase == "HOLD" else "張力を保持して")
    if k == "INHALE":
        ok = st.phase == "INHALE" or st.norm >= TARGET_BAND[k][0]
        return ok, "吸気 OK" if ok else "お腹を膨らませるように吸って"
    if k == "EXHALE":
        ok = st.phase == "EXHALE" or st.norm <= TARGET_BAND[k][1]
        return ok, "呼気 OK" if ok else "ゆっくり吐いて"
    if k == "BRACE":
        ok = st.norm >= TARGET_BAND[k][0]
        return ok, "腹圧 OK" if ok else "お腹周りをもう少し張って"
    if k == "RELAX":
        ok = st.norm <= TARGET_BAND[k][1]
        return ok, "脱力 OK" if ok else "お腹の力を抜いて"
    return None, ""
