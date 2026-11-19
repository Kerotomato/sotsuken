"""時刻合わせの手法（息止めの検出、相互相関、音声の相互相関、DTW）

どれも「2つの時系列のずれ」を推定する関数で、ファイルの読み込みや作図はしない。
しきい値はファイル先頭にまとめ、根拠をコメントに書く。
"""
from __future__ import annotations

import numpy as np

# ---------------------------------------------------------------------------
# しきい値（根拠）
# ---------------------------------------------------------------------------
GRID_HZ = 10.0           # 呼吸の時刻合わせは 10 Hz で行う（呼吸ベルトのサンプリングと同じ）
HOLD_MIN_S = 3.0         # 息止めとみなす最短の平坦区間。手順は「5 秒息止め」だが、MCap は息止めの
                         # 途中から記録が始まることがあるので短めにする
HOLD_MAX_S = 12.0        # これより長い平坦区間は息止めではなく、静止・装着不良などとみなす
HOLD_SEARCH_S = 60.0     # 息止めは計測の冒頭にあるので、各記録の最初の 60 秒だけを探す
FLAT_WIN_S = 1.0         # 平坦さは 1 秒窓の値の幅（最大 - 最小）で測る
FLAT_REL = 0.15          # 「平坦」= 1 秒窓の幅が、呼吸1回分の幅（5 秒窓の幅の中央値）の 15% 未満。
                         # 被験者1・音声ありで、ベルトの息止め中は 0.3〜0.8 N、呼吸1回分は 6.7 N
                         # （比 0.05〜0.12）、安静呼吸中は 1.0 N 以上だったことから決めた
XCORR_SEARCH_S = 10.0    # 息止めで決めたずれの前後 ±10 秒を、相互相関で確かめる範囲
XCORR_MIN_R = 0.3        # 相互相関の最大値がこれ未満なら「合っていない」と報告する
HIGHPASS_S = 10.0        # 呼吸成分だけを比べるため、10 秒の移動平均を引いて姿勢変化などの遅い変動を除く
AUDIO_HZ = 8000          # 音声の相互相関のサンプリング周波数（声の帯域は残る）
AUDIO_WIN_S = 30.0       # 再生の一時停止や早送りがないかを確かめるための窓の長さ
AUDIO_TOL_S = 0.10       # 窓ごとのずれがこれ以上ばらつくと「連続再生でない可能性」として報告する


# ---------------------------------------------------------------------------
# 基本処理
# ---------------------------------------------------------------------------
def to_grid(t, x, hz=GRID_HZ, t0=None, t1=None):
    """不等間隔の時系列を等間隔に補間する（欠損は線形補間、範囲外は NaN）"""
    t = np.asarray(t, float)
    x = np.asarray(x, float)
    ok = ~np.isnan(x) & ~np.isnan(t)
    t0 = t[ok][0] if t0 is None else t0
    t1 = t[ok][-1] if t1 is None else t1
    g = np.arange(t0, t1 + 1e-9, 1 / hz)
    y = np.interp(g, t[ok], x[ok], left=np.nan, right=np.nan)
    return g, y


def moving_average(x, n):
    n = max(1, int(n))
    k = np.ones(n) / n
    pad = np.pad(x, (n // 2, n - 1 - n // 2), mode="edge")
    return np.convolve(pad, k, mode="valid")


def highpass(x, hz=GRID_HZ, win_s=HIGHPASS_S):
    x = np.where(np.isnan(x), np.nanmean(x), x)
    return x - moving_average(x, win_s * hz)


# ---------------------------------------------------------------------------
# 息止め（平坦区間）の検出
# ---------------------------------------------------------------------------
def find_hold(t, x, hz=GRID_HZ, search_s=HOLD_SEARCH_S, min_s=HOLD_MIN_S, max_s=HOLD_MAX_S,
              rel=FLAT_REL, win_s=FLAT_WIN_S):
    """記録の冒頭から、信号がほぼ一定になる区間（息止め）を探す

    1. 0.3 秒の移動平均で雑音を落とし、各時刻を中心とする win_s 秒窓の幅（最大 - 最小）を求める
    2. 幅が「呼吸1回分の幅」（5 秒窓の幅の、記録全体での中央値）× rel 未満の時刻を平坦とする
    3. 冒頭 search_s 秒の中で、min_s 以上 max_s 以下続く平坦区間のうち最も早いものを返す
       （手順上、息止めは計測の最初に行うため）
    窓の幅の分だけ区間の端がぼやけるので、端を窓の半分だけ外側に広げて返す。
    戻り値: {"start", "end", "length", "range", "threshold"}（時刻は t と同じ基準）、なければ None
    """
    from numpy.lib.stride_tricks import sliding_window_view as win
    g, y = to_grid(t, x, hz)
    y = np.where(np.isnan(y), np.nanmean(y), y)
    y = moving_average(y, 0.3 * hz)
    n = max(2, int(round(win_s * hz)))
    rng = np.ptp(win(np.pad(y, (n // 2, n - 1 - n // 2), mode="edge"), n), axis=1)
    breath = np.median(np.ptp(win(y, int(5 * hz)), axis=1))
    thr = rel * breath
    flat = (rng < thr) & (g <= g[0] + search_s)
    i = 0
    while i < len(flat):
        if flat[i]:
            j = i
            while j + 1 < len(flat) and flat[j + 1]:
                j += 1
            s0, s1 = max(g[0], g[i] - win_s / 2), min(g[-1], g[j] + win_s / 2)
            if min_s <= s1 - s0 <= max_s:
                return {"start": float(s0), "end": float(s1), "length": float(s1 - s0),
                        "range": float(np.max(rng[i:j + 1])), "threshold": float(thr)}
            i = j + 1
        else:
            i += 1
    return None


# ---------------------------------------------------------------------------
# 相互相関（候補の開始時刻を総当たり）
# ---------------------------------------------------------------------------
def best_start(ref_t, ref_x, rel_t, rel_x, candidates, hz=GRID_HZ, hp=True):
    """相対時刻の信号 rel を「開始時刻 = candidate」に置いたとき、基準 ref との相関が最大になる候補

    ref_t は絶対時刻、rel_t は 0 始まりの相対時刻。両方を hz の格子にそろえ、
    hp=True なら 10 秒の移動平均を引いて（遅い変動を除いて）から相関をとる。
    戻り値: (最良の開始時刻, 相関係数, 全候補の相関係数)
    """
    gr, yr = to_grid(ref_t, ref_x, hz)
    gs, ys = to_grid(rel_t, rel_x, hz)
    if hp:
        yr, ys = highpass(yr, hz), highpass(ys, hz)
    rs = []
    for c in candidates:
        yi = np.interp(gs + c, gr, yr, left=np.nan, right=np.nan)
        ok = ~np.isnan(yi) & ~np.isnan(ys)
        rs.append(np.corrcoef(yi[ok], ys[ok])[0, 1] if ok.sum() > 5 * hz else np.nan)
    rs = np.array(rs)
    k = int(np.nanargmax(rs))
    return float(candidates[k]), float(rs[k]), rs


# ---------------------------------------------------------------------------
# 音声の相互相関（GCC-PHAT）
# ---------------------------------------------------------------------------
def gcc_phat(ref, sig, fs=AUDIO_HZ, max_lag_s=None):
    """sig が ref の何秒目から始まっているか（sig[n] ≈ ref[n + lag·fs]）を返す: (lag 秒, ピークの鋭さ)

    GCC-PHAT は位相だけを使う相互相関で、スピーカー再生→マイク録音の反響や音色の違いに強い。
    ピークの鋭さ = 最大値 / 相関の標準偏差（大きいほど信頼できる。目安 10 以上）。
    """
    n = len(ref) + len(sig)
    nfft = 1 << (n - 1).bit_length()
    R = np.fft.rfft(ref, nfft)
    S = np.fft.rfft(sig, nfft)
    X = R * np.conj(S)
    X /= np.abs(X) + 1e-12
    cc = np.fft.irfft(X, nfft)
    # cc[k] は lag = k（k < nfft/2）、lag = k - nfft（それ以上）
    lags = np.arange(nfft)
    lags[lags >= nfft // 2] -= nfft
    if max_lag_s is not None:
        cc = np.where(np.abs(lags) <= max_lag_s * fs, cc, -np.inf)
    k = int(np.argmax(cc))
    finite = cc[np.isfinite(cc)]
    sharp = float(cc[k] / (np.std(finite) + 1e-12))
    return lags[k] / fs, sharp


def audio_offset(ref, sig, fs=AUDIO_HZ, win_s=AUDIO_WIN_S):
    """被験者動画の音声 sig が手本音声 ref のどこに当たるか

    全体で GCC-PHAT をとり、さらに win_s 秒ごとの窓でも求めて、再生が途中で止まったり
    飛んだりしていないか（窓ごとのずれが一定か）を確かめる。
    戻り値: {"offset": YouTube 時刻 = 動画時刻 + offset, "sharpness", "windows": [(窓の開始, offset, 鋭さ)]}
    """
    off, sharp = gcc_phat(ref, sig, fs)
    wins = []
    n = int(win_s * fs)
    for i in range(0, len(sig) - n + 1, n):
        o, s = gcc_phat(ref, sig[i:i + n], fs)
        wins.append((i / fs, o - i / fs, s))
    return {"offset": off, "sharpness": sharp, "windows": wins}


# ---------------------------------------------------------------------------
# 動きの時系列による対応づけ（音声なしの条件）
# ---------------------------------------------------------------------------
def zscore_cols(X):
    X = np.asarray(X, float)
    mu, sd = np.nanmean(X, axis=0), np.nanstd(X, axis=0)
    return (X - mu) / np.where(sd > 1e-9, sd, 1)


def motion_offset(ref_t, ref_X, sub_t, sub_X, candidates, hz=5.0):
    """複数の関節角度をまとめて相関をとり、全体のずれ（YouTube 時刻 = 動画時刻 + offset）を求める

    各列を z 得点にしてから、候補ごとに全列の相関係数の平均をとる。
    """
    cols = range(ref_X.shape[1])
    R = [to_grid(ref_t, ref_X[:, c], hz, ref_t[0], ref_t[-1])[1] for c in cols]
    gr = np.arange(ref_t[0], ref_t[-1] + 1e-9, 1 / hz)
    S = [to_grid(sub_t, sub_X[:, c], hz, sub_t[0], sub_t[-1])[1] for c in cols]
    gs = np.arange(sub_t[0], sub_t[-1] + 1e-9, 1 / hz)
    scores = []
    for off in candidates:
        rs = []
        for r, s in zip(R, S):
            yi = np.interp(gs + off, gr, r, left=np.nan, right=np.nan)
            ok = ~np.isnan(yi) & ~np.isnan(s)
            if ok.sum() > 10 * hz:
                rs.append(np.corrcoef(yi[ok], s[ok])[0, 1])
        scores.append(np.nanmean(rs) if rs else np.nan)
    scores = np.array(scores)
    k = int(np.nanargmax(scores))
    return float(candidates[k]), float(scores[k]), scores


def dtw_band(A, B, band):
    """多次元 DTW（ユークリッド距離）。A[i] と B[j] を |i - j| <= band の範囲で対応づける

    戻り値: 対応のパス [(i, j), ...]
    欠損（NaN）を含むフレームの距離は、使える列だけの平均二乗差で求める。
    """
    n, m = len(A), len(B)
    INF = np.inf
    D = np.full((n + 1, m + 1), INF)
    D[0, 0] = 0
    for i in range(1, n + 1):
        j0, j1 = max(1, i - band), min(m, i + band)
        d = (A[i - 1][None, :] - B[j0 - 1:j1]) ** 2
        cost = np.sqrt(np.nanmean(np.where(np.isnan(d), np.nan, d), axis=1))
        cost = np.where(np.isnan(cost), 0.0, cost)
        for k, j in enumerate(range(j0, j1 + 1)):
            D[i, j] = cost[k] + min(D[i - 1, j], D[i, j - 1], D[i - 1, j - 1])
    i, j = n, m
    path = [(i - 1, j - 1)]
    while i > 1 or j > 1:
        steps = [(D[i - 1, j - 1], i - 1, j - 1), (D[i - 1, j], i - 1, j), (D[i, j - 1], i, j - 1)]
        _, i, j = min(steps, key=lambda s: s[0])
        path.append((i - 1, j - 1))
    return path[::-1]
