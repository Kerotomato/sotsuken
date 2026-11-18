"""ヨガ手本比較解析の共通設定・ユーティリティ

パスの既定値、時刻の扱い、グラフの日本語フォントなど、各段階のスクリプトで共有するもの。
"""
from __future__ import annotations

import sys
from datetime import timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
# cue_extractor.py / breath_core.py を import できるようにする
sys.path.insert(0, str(REPO / "gdx_rb_coach"))

# 手本動画（研究で使う YouTube 動画）
REFERENCE_URL = "https://www.youtube.com/watch?v=Da2L6PfR8DY"

# 被験者データの置き場所（Windows / OneDrive）。--data で変更できる
DATA_ROOT = Path(r"C:\Users\farms\OneDrive\学校\卒研\被験者")

# 出力先。どちらも .gitignore 済み（被験者由来のデータを公開リポジトリに push しないため）
WORK_DIR = REPO / "data"        # ダウンロードした手本動画など、大きな中間ファイル
RESULTS_DIR = REPO / "results"  # 評価 CSV・アドバイス・確認用グラフ

JST = timezone(timedelta(hours=9))

# グラフで使うキューの色（gdx_rb_web と同じ配色）
CUE_COLOR = {"INHALE": "#2f86b5", "EXHALE": "#3c9a6a", "HOLD": "#b07a2a",
             "NO_HOLD": "#137c80", "BRACE": "#b5523b", "RELAX": "#7c8f9b"}


def setup_matplotlib():
    """Agg バックエンドと日本語フォントを設定して pyplot を返す"""
    import logging
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.font_manager as fm
    import matplotlib.pyplot as plt
    logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)
    names = {f.name for f in fm.fontManager.ttflist}
    for cand in ["Yu Gothic", "Meiryo", "MS Gothic", "Noto Sans CJK JP", "Hiragino Sans",
                 "IPAexGothic", "WenQuanYi Zen Hei"]:
        if cand in names:
            matplotlib.rcParams["font.family"] = cand
            break
    matplotlib.rcParams["axes.unicode_minus"] = False
    return plt


def shade_cues(ax, cues, t0: float = 0.0, label: bool = False):
    """キュー区間を背景色で示す（t0 を引いた時刻で描く）"""
    for c in cues:
        ax.axvspan(c.t - t0, c.end - t0, color=CUE_COLOR.get(c.kind, "#999"), alpha=0.18, lw=0)
        if label:
            ax.text(c.t - t0, 1.0, c.kind[:2], transform=ax.get_xaxis_transform(),
                    fontsize=6, color=CUE_COLOR.get(c.kind, "#999"), va="bottom")
