"""GDX-RB 呼吸コーチング: キーフレーズ定義（CUE_RULES）とテキストからのキュー抽出

ブラウザ版 gdx_rb_web/CoachCore.js と同じ規則。変更するときは両方を直し、
node gdx_rb_web/tests/test_coach_core.cjs で一致を確認すること。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict

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
