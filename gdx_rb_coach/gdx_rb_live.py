"""GDX-RB (Vernier Go Direct Respiration Belt) リアルタイムモニタ + 呼吸コーチング

  python gdx_rb_live.py                       # センサー（BLE/USB 自動検出）でモニタ
  python gdx_rb_live.py --sim                 # センサーなしのシミュレーション
  python gdx_rb_live.py --cues cues.json      # コーチングモード（動画と同時に再生）
  python gdx_rb_live.py --sim --cues cues.json

キー操作（コーチングモード）:
  Space  動画再生と同時に押してタイムライン開始 / 一時停止
  ← / →  タイムラインを 0.5 秒ずらす（動画とのずれ補正）
  c      張力の基準（安静呼吸）を取り直す
  q      終了（セッション CSV と結果サマリを保存）
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import queue
import random
import threading
import time
from collections import deque
from datetime import datetime

import matplotlib
import numpy as np

from breath_core import (CUE_KINDS, PHASE_LABEL, TARGET_BAND, CueTimeline, PhaseDetector,
                         evaluate, load_cues)

FS = 10.0          # サンプリング [Hz]
WINDOW_SEC = 30.0  # グラフ表示幅


# ---------------------------------------------------------------------------
# データソース
# ---------------------------------------------------------------------------
class GoDirectSource:
    """godirect ライブラリで GDX-RB を読む。sensor 1 = Force [N], sensor 2 = Respiration Rate [bpm]"""

    def __init__(self, use_ble=True, use_usb=True, period_ms=int(1000 / FS)):
        from godirect import GoDirect
        self.gd = GoDirect(use_ble=use_ble, use_usb=use_usb)
        self.dev = self.gd.get_device(threshold=-100)
        if self.dev is None:
            raise RuntimeError("Go Direct デバイスが見つかりません")
        self.dev.open(auto_start=False)
        self.dev.enable_sensors([1, 2])
        self.period_ms = period_ms
        self.name = str(self.dev)

    def run(self, q: queue.Queue, stop: threading.Event):
        self.dev.start(period=self.period_ms)
        n = 0
        try:
            while not stop.is_set():
                if not self.dev.read():
                    continue
                sensors = self.dev.get_enabled_sensors()
                force = list(sensors[0].values)
                rr = sensors[1].values[-1] if len(sensors) > 1 and sensors[1].values else float("nan")
                for s in sensors:
                    s.clear()
                for f in force:
                    q.put((n * self.period_ms / 1000.0, float(f), float(rr)))
                    n += 1
        finally:
            self.dev.stop()
            self.dev.close()
            self.gd.quit()


class SimSource:
    """呼吸ベルトのシミュレータ。timeline を渡すとキューに（たまに失敗しつつ）従う"""
    name = "Simulator"

    def __init__(self, timeline_clock=None, miss_rate=0.25, seed=1):
        self.clock = timeline_clock   # () -> (動画時刻, 有効キュー) or None
        self.miss_rate = miss_rate
        self.rng = random.Random(seed)

    def run(self, q: queue.Queue, stop: threading.Event):
        x, t, mode, t_mode = 0.0, 0.0, "EXHALE", 0.0
        last_cue_ids: set = set()
        miss = False
        while not stop.is_set():
            cues = self.clock()[1] if self.clock else []
            ids = {id(c) for c in cues}
            if ids - last_cue_ids:                     # 新しいキューごとに失敗するか抽選
                miss = self.rng.random() < self.miss_rate
            last_cue_ids = ids
            kinds = {c.kind for c in cues}
            phase_k = next((k for k in ("INHALE", "EXHALE", "HOLD") if k in kinds), None)
            if miss and kinds:
                goal, rate = x, 0.0                    # 失敗例: 息を止めてしまう
            elif phase_k == "INHALE":
                goal, rate = 1.0, 0.5
            elif phase_k == "EXHALE":
                goal, rate = 0.0, 0.35
            elif phase_k == "HOLD":
                goal, rate = x, 0.0
            elif "BRACE" in kinds:
                goal, rate = 0.85 + 0.15 * math.sin(t * 1.5), 1.0
            elif "RELAX" in kinds:
                goal, rate = 0.1, 0.5
            else:                                      # 自由呼吸 (吸気2s/呼気3s)
                if t - t_mode > (2.0 if mode == "INHALE" else 3.0):
                    mode, t_mode = ("EXHALE" if mode == "INHALE" else "INHALE"), t
                goal, rate = (1.0, 0.6) if mode == "INHALE" else (0.0, 0.45)
            x += float(np.clip(goal - x, -rate / FS, rate / FS))
            force = 4.0 + 3.0 * x + self.rng.gauss(0, 0.02)
            q.put((t, force, float("nan")))
            t += 1 / FS
            time.sleep(1 / FS)


# ---------------------------------------------------------------------------
# コーチング時計
# ---------------------------------------------------------------------------
class CoachClock:
    def __init__(self, timeline: CueTimeline | None):
        self.timeline = timeline
        self.running = False
        self.offset = 0.0
        self._t_start = 0.0
        self._paused_at = 0.0
        self.lock = threading.Lock()

    def now(self) -> float:
        with self.lock:
            if not self.running:
                return self._paused_at + self.offset
            return time.monotonic() - self._t_start + self.offset

    def toggle(self):
        with self.lock:
            if self.running:
                self._paused_at = time.monotonic() - self._t_start
            else:
                self._t_start = time.monotonic() - self._paused_at
            self.running = not self.running

    def state(self):
        t = self.now()
        return t, (self.timeline.active(t) if self.timeline and self.running else [])


# ---------------------------------------------------------------------------
# 表示
# ---------------------------------------------------------------------------
def setup_fonts():
    import logging
    logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)
    import matplotlib.font_manager as fm
    names = {f.name for f in fm.fontManager.ttflist}
    for cand in ["Noto Sans CJK JP", "Yu Gothic", "Meiryo", "Hiragino Sans", "MS Gothic",
                 "IPAexGothic", "TakaoGothic", "WenQuanYi Zen Hei"]:
        if cand in names:
            matplotlib.rcParams["font.family"] = cand
            break
    matplotlib.rcParams["axes.unicode_minus"] = False


CUE_COLOR = {"INHALE": "#2f7fd8", "EXHALE": "#e08a1e", "HOLD": "#8c56c9",
             "NO_HOLD": "#16a085", "BRACE": "#c0392b", "RELAX": "#7f8c8d"}
PHASE_COLOR = {"INHALE": "#2f7fd8", "EXHALE": "#e08a1e", "HOLD": "#c0392b", "UNKNOWN": "#999999"}


class LiveApp:
    def __init__(self, args):
        self.args = args
        self.timeline = CueTimeline(load_cues(args.cues)) if args.cues else None
        self.clock = CoachClock(self.timeline)
        self.det = PhaseDetector(fs=FS, calib_sec=args.calib)
        self.q: queue.Queue = queue.Queue()
        self.stop = threading.Event()
        n = int(WINDOW_SEC * FS)
        self.ts, self.fs_, self.norms, self.phases = (deque(maxlen=n) for _ in range(4))
        self.st = None
        self.dev_rr = float("nan")
        self.cue_stats: dict[int, list[int]] = {}       # id(cue) -> [ok, total]
        self.msg, self.msg_ok = "", None
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_path = f"session_{stamp}.csv"
        self.log_f = open(self.log_path, "w", newline="", encoding="utf-8")
        self.log = csv.writer(self.log_f)
        self.log.writerow(["t_sensor", "t_video", "force_N", "norm", "slope", "phase",
                           "hold_sec", "rr_bpm", "cues", "ok"])

        if args.sim:
            self.src = SimSource(self.clock.state if self.timeline else None, miss_rate=args.miss)
        else:
            self.src = GoDirectSource(use_ble=not args.usb_only, use_usb=not args.ble_only)
        self._autostart = bool(self.timeline and args.autostart)

    # --- 取り込み ---
    def pump(self):
        while True:
            try:
                t, f, rr = self.q.get_nowait()
            except queue.Empty:
                break
            if not math.isnan(rr):
                self.dev_rr = rr
            st = self.det.update(t, f)
            self.st = st
            self.ts.append(t)
            self.fs_.append(st.force)
            self.norms.append(st.norm)
            self.phases.append(st.phase)
            if self._autostart and self.det.frozen_calib:   # 基準取得が終わったら開始
                self._autostart = False
                self.clock.toggle()

            tv, active = self.clock.state()
            oks = []
            msgs = []
            for c in active:
                ok, m = evaluate(c, tv, st)
                msgs.append((ok, m))
                if ok is not None:
                    s = self.cue_stats.setdefault(id(c), [0, 0])
                    s[0] += int(ok)
                    s[1] += 1
                    oks.append(ok)
            # NG を優先して表示
            if msgs:
                bad = [m for ok, m in msgs if ok is False]
                self.msg = bad[0] if bad else " / ".join(m for _, m in msgs)
                self.msg_ok = False if bad else (True if oks else None)
            else:
                self.msg, self.msg_ok = "", None
            rr = self.dev_rr if not math.isnan(self.dev_rr) else st.rate_bpm
            self.log.writerow([f"{t:.2f}", f"{tv:.2f}" if self.timeline else "", f"{st.force:.4f}",
                               f"{st.norm:.3f}", f"{st.slope:.3f}", st.phase, f"{st.hold_sec:.2f}",
                               f"{rr:.1f}", "+".join(c.kind for c in active),
                               "" if not oks else int(all(oks))])

    # --- 画面 ---
    def build(self):
        import matplotlib.pyplot as plt
        setup_fonts()
        coach = self.timeline is not None
        self.fig = plt.figure(figsize=(13, 7.5 if coach else 6))
        self.fig.canvas.manager.set_window_title("GDX-RB Live Coach")
        gs = self.fig.add_gridspec(3 if coach else 2, 2, width_ratios=[6, 1],
                                   height_ratios=([1.1, 3, 1] if coach else [0.5, 3]),
                                   hspace=0.35, wspace=0.12)
        self.ax_head = self.fig.add_subplot(gs[0, :]); self.ax_head.axis("off")
        self.ax_f = self.fig.add_subplot(gs[1, 0])
        self.ax_g = self.fig.add_subplot(gs[1, 1])
        if coach:
            self.ax_tl = self.fig.add_subplot(gs[2, :])

        self.ax_f.set_ylabel("腹部張力 [N]")
        self.ax_f.set_xlabel("時間 [s]")
        self.ax_f.grid(alpha=0.3)
        (self.line,) = self.ax_f.plot([], [], color="#333", lw=1.6)
        self.scat = self.ax_f.scatter([], [], s=6, zorder=3)
        self.band_f = None

        self.ax_g.set_xlim(0, 1); self.ax_g.set_ylim(-0.4, 1.6)
        self.ax_g.set_xticks([]); self.ax_g.set_yticks([0, 0.5, 1.0])
        self.ax_g.set_yticklabels(["安静\n呼気", "", "安静\n吸気"], fontsize=8)
        self.ax_g.set_title("腹部張力", fontsize=10)
        from matplotlib.patches import Rectangle
        self.g_band = self.ax_g.add_patch(Rectangle((0, 0), 1, 0, color="#2ecc71", alpha=0.25))
        self.g_bar = self.ax_g.bar([0.5], [0], width=0.6, bottom=-0.4, color="#2f7fd8")[0]

        self.t_cue = self.ax_head.text(0.0, 0.62, "", fontsize=30, weight="bold", va="center")
        self.t_msg = self.ax_head.text(0.0, 0.08, "", fontsize=15, va="center")
        self.t_stat = self.ax_head.text(1.0, 0.62, "", fontsize=11, ha="right", va="center")
        self.t_help = self.ax_head.text(1.0, 0.08, "", fontsize=9, ha="right", color="#777")
        if coach:
            self.t_help.set_text("Space:開始/停止  ←→:ずれ補正  c:基準再取得  q:終了")
        self.fig.canvas.mpl_connect("key_press_event", self.on_key)
        self.fig.canvas.mpl_connect("close_event", lambda e: self.stop.set())

    def draw_timeline(self, tv):
        ax = self.ax_tl
        ax.clear()
        ax.set_xlim(tv - 5, tv + 15); ax.set_ylim(0, 3)
        ax.set_yticks([0.5, 1.5, 2.5]); ax.set_yticklabels(["制約", "張力", "呼吸相"], fontsize=9)
        ax.axvline(tv, color="k", lw=2)
        ax.set_xlabel("動画時刻 [s]" + ("" if self.clock.running else "   （停止中：Space で開始）"))
        row = {"constraint": 0, "tension": 1, "phase": 2}
        for c in self.timeline.cues:
            if c.end < tv - 5 or c.t > tv + 15:
                continue
            y = row[c.channel]
            ax.barh(y + 0.5, c.end - c.t, left=c.t, height=0.8, color=CUE_COLOR[c.kind], alpha=0.8)
            ax.text(max(c.t, tv - 5) + 0.1, y + 0.5, c.label, va="center", fontsize=9, color="white",
                    weight="bold", clip_on=True)

    def update(self, _frame):
        self.pump()
        if not self.ts:
            self.t_cue.set_text("センサー待機中…")
            return []
        t = np.array(self.ts)
        f = np.array(self.fs_)
        st = self.st
        self.line.set_data(t, f)
        self.scat.set_offsets(np.c_[t, f])
        self.scat.set_color([PHASE_COLOR[p] for p in self.phases])
        self.ax_f.set_xlim(max(0, t[-1] - WINDOW_SEC), max(WINDOW_SEC, t[-1]))
        pad = max(0.2, (f.max() - f.min()) * 0.15)
        self.ax_f.set_ylim(f.min() - pad, f.max() + pad)

        tv, active = self.clock.state()
        # 目標帯（ゲージとグラフ）
        band = None
        for c in active:
            if TARGET_BAND.get(c.kind):
                band = TARGET_BAND[c.kind]
        if self.band_f is not None:
            self.band_f.remove(); self.band_f = None
        if band:
            lo, hi = band
            self.g_band.set_y(lo); self.g_band.set_height(hi - lo)
            span = self.det.hi - self.det.lo
            self.band_f = self.ax_f.axhspan(self.det.lo + lo * span, self.det.lo + hi * span,
                                            color="#2ecc71", alpha=0.12)
        else:
            self.g_band.set_height(0)
        self.g_bar.set_height(float(np.clip(st.norm, -0.4, 1.6)) + 0.4)
        self.g_bar.set_color("#2ecc71" if self.msg_ok else ("#e74c3c" if self.msg_ok is False
                                                             else PHASE_COLOR[st.phase]))

        rr = self.dev_rr if not math.isnan(self.dev_rr) else st.rate_bpm
        calib = "基準固定" if self.det.frozen_calib else "基準取得中"
        self.t_stat.set_text(f"{self.src.name}\n張力 {st.force:5.2f} N   相 {PHASE_LABEL[st.phase]}\n"
                             f"呼吸数 {rr:4.1f} /分   停止 {st.hold_sec:3.1f}s   [{calib}]")
        if self.timeline:
            labels = " ＋ ".join(c.label for c in sorted(active, key=lambda c: c.channel))
            self.t_cue.set_text(labels or "（キューなし）")
            self.t_cue.set_color(CUE_COLOR[active[0].kind] if active else "#999")
            self.t_msg.set_text(self.msg)
            self.t_msg.set_color({True: "#1e8449", False: "#c0392b"}.get(self.msg_ok, "#555"))
            self.draw_timeline(tv)
        else:
            self.t_cue.set_text(f"{PHASE_LABEL[st.phase]}")
            self.t_cue.set_color(PHASE_COLOR[st.phase])
            if st.hold_sec > 2.0:
                self.t_msg.set_text(f"息が止まっています（{st.hold_sec:.1f}s）")
                self.t_msg.set_color("#c0392b")
            else:
                self.t_msg.set_text("")
        return []

    def on_key(self, e):
        if e.key == " ":
            self.clock.toggle()
        elif e.key == "left":
            self.clock.offset -= 0.5
        elif e.key == "right":
            self.clock.offset += 0.5
        elif e.key == "c":
            self.det.recalibrate()
        elif e.key == "q":
            import matplotlib.pyplot as plt
            plt.close(self.fig)

    def summary(self):
        if not self.timeline:
            return
        rows = []
        for c in self.timeline.cues:
            ok, n = self.cue_stats.get(id(c), [0, 0])
            if n:
                rows.append({"t": c.t, "kind": c.kind, "label": c.label, "text": c.text,
                             "compliance": round(ok / n, 3)})
        path = self.log_path.replace(".csv", "_summary.json")
        with open(path, "w", encoding="utf-8") as fp:
            json.dump(rows, fp, ensure_ascii=False, indent=2)
        print("\n=== キュー適合率 ===")
        for r in rows:
            bar = "█" * int(r["compliance"] * 20)
            print(f"{r['t']:7.1f}s {r['label']:<8} {r['compliance'] * 100:5.1f}% {bar}")
        by_kind: dict[str, list[float]] = {}
        for r in rows:
            by_kind.setdefault(r["kind"], []).append(r["compliance"])
        for k, v in by_kind.items():
            print(f"  {CUE_KINDS[k][0]:<8} 平均 {np.mean(v) * 100:5.1f}%  ({len(v)} 回)")
        print(f"保存: {self.log_path}, {path}")

    def run(self):
        import matplotlib.pyplot as plt
        from matplotlib.animation import FuncAnimation
        th = threading.Thread(target=self.src.run, args=(self.q, self.stop), daemon=True)
        th.start()
        self.build()
        try:
            if self.args.snapshot:      # 動作確認用: 指定秒数後に画像保存して終了
                t_end = time.monotonic() + self.args.duration
                while time.monotonic() < t_end:
                    time.sleep(0.1)
                    self.update(0)
                self.fig.savefig(self.args.snapshot, dpi=90)
            else:
                self.anim = FuncAnimation(self.fig, self.update, interval=100, cache_frame_data=False)
                plt.show()
        finally:
            self.stop.set()
            th.join(timeout=2)
            self.log_f.close()
            self.summary()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sim", action="store_true", help="シミュレーションデータを使う")
    ap.add_argument("--cues", help="cue_extractor.py が出力した JSON（コーチングモード）")
    ap.add_argument("--calib", type=float, default=30.0, help="基準取得に使う秒数")
    ap.add_argument("--ble-only", action="store_true")
    ap.add_argument("--usb-only", action="store_true")
    ap.add_argument("--miss", type=float, default=0.25, help="[sim] キューに失敗する確率")
    ap.add_argument("--autostart", action="store_true", help="基準取得の完了後にタイムラインを自動開始")
    ap.add_argument("--snapshot", help="[テスト] duration 秒後に画面を PNG 保存して終了")
    ap.add_argument("--duration", type=float, default=20.0)
    args = ap.parse_args()
    if args.snapshot:
        matplotlib.use("Agg")
    LiveApp(args).run()


if __name__ == "__main__":
    main()
