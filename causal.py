"""Causal layer: a PCMCI-style test and Convergent Cross Mapping (CCM).

Both work on series that already had the straight-line trend and the seasons removed, so they test whether the ups and downs of
one series lead the other, not whether two things merely rise together.

PCMCI-style: the same two steps as the published PCMCI method (Runge et al. 2019).
  1. PC1 step: for every variable, keep only the past values (lags 1..tau_max) that still matter after conditioning on the strongest others.
  2. MCI step: test each link "driver at lag k -> target" with a partial correlation that conditions on the parents of the target AND the parents of the driver.
  It uses linear partial correlation (ParCorr) and a t test. This is our own small implementation, not the tigramite package.
CCM (Sugihara et al. 2012): if X drives Y, the history of Y contains X, so Y's reconstructed state space can estimate X.
  We build the time-delay state space of Y, estimate X from nearest neighbours, and score the match (skill). Evidence needs the skill to grow
  with more data (convergence) and to beat phase-randomised copies of X (surrogates).
"""
from __future__ import annotations

import math

import numpy as np

import stats

MIN_N = 30            # shortest series both methods accept


def _p_from_r(r: float, dof: int) -> float:
    if dof < 3 or not math.isfinite(r):
        return 1.0
    r = max(-0.999999, min(0.999999, r))
    return float(stats.t_two_sided_p(r * math.sqrt(dof / (1.0 - r * r)), dof))


def _resid(y: np.ndarray, Z: np.ndarray | None) -> np.ndarray:
    if Z is None or Z.shape[1] == 0:
        return y - y.mean()
    A = np.column_stack([Z, np.ones(len(y))])
    beta, *_ = np.linalg.lstsq(A, y, rcond=None)
    return y - A @ beta


def _pcorr(a: np.ndarray, b: np.ndarray, Z: np.ndarray | None) -> tuple:
    ra, rb = _resid(a, Z), _resid(b, Z)
    sa, sb = ra.std(), rb.std()
    if sa < 1e-12 or sb < 1e-12:
        return 0.0, 1.0
    r = float(np.dot(ra - ra.mean(), rb - rb.mean()) / (len(a) * sa * sb))
    k = 0 if Z is None else Z.shape[1]
    return r, _p_from_r(r, len(a) - 2 - k)


# ------------------------------------------------------------------ PCMCI-style
class _Data:
    def __init__(self, X: np.ndarray, tau_max: int):
        self.X = (X - X.mean(0)) / np.where(X.std(0) > 1e-12, X.std(0), 1.0)
        self.T, self.N = X.shape
        self.tm = tau_max

    def col(self, var: int, lag: int) -> np.ndarray:        # values of var at time t-lag, for t = tm .. T-1
        return self.X[self.tm - lag: self.T - lag, var]

    def stack(self, items: list) -> np.ndarray | None:
        return np.column_stack([self.col(v, l) for v, l in items]) if items else None


def _pc1(D: _Data, j: int, alpha: float = 0.2, max_cond: int = 3) -> list:
    y = D.col(j, 0)
    cands = [(i, l) for i in range(D.N) for l in range(1, D.tm + 1)]
    score = {c: abs(_pcorr(D.col(*c), y, None)[0]) for c in cands}
    parents = sorted(cands, key=lambda c: -score[c])
    p = 0
    while p <= max_cond and len(parents) > p:
        keep = []
        for c in parents:
            others = [q for q in parents if q != c][:p]
            r, pv = _pcorr(D.col(*c), y, D.stack(others))
            score[c] = abs(r)
            if pv <= alpha:
                keep.append(c)
        parents = sorted(keep, key=lambda c: -score[c])
        p += 1
    return parents[:6]


def pcmci(Xres: np.ndarray, tau_max: int, target: int = 0) -> dict:
    """Xres: T x N residual series. Returns, for each non-target variable, the strongest link driver->target and target->driver."""
    D = _Data(Xres, tau_max)
    par = [_pc1(D, j) for j in range(D.N)]
    out = {}

    def mci(i, lag, j):
        cond = [q for q in par[j] if q != (i, lag)][:4]
        for (v, l) in par[i][:4]:
            sh = l + lag
            if sh <= D.tm and (v, sh) not in cond and (v, sh) != (i, lag):
                cond.append((v, sh))
        cols = [D.col(v, l) for v, l in cond if l <= D.tm]
        return _pcorr(D.col(i, lag), D.col(j, 0), np.column_stack(cols) if cols else None)

    for i in range(D.N):
        if i == target:
            continue
        best_f = min(((l, *mci(i, l, target)) for l in range(1, D.tm + 1)), key=lambda t: t[2])
        best_r = min(((l, *mci(target, l, i)) for l in range(1, D.tm + 1)), key=lambda t: t[2])
        out[i] = {"lag": best_f[0], "r": best_f[1], "p": min(1.0, best_f[2] * D.tm), "rev_lag": best_r[0], "rev_r": best_r[1], "rev_p": min(1.0, best_r[2] * D.tm)}
    return out


# ------------------------------------------------------------------ CCM
def _embed(y: np.ndarray, E: int, tau: int) -> tuple:
    n = len(y) - (E - 1) * tau
    M = np.column_stack([y[(E - 1) * tau - k * tau: (E - 1) * tau - k * tau + n] for k in range(E)])
    return M, np.arange((E - 1) * tau, (E - 1) * tau + n)


def _dist(M: np.ndarray) -> np.ndarray:
    d = np.sqrt(((M[:, None, :] - M[None, :, :]) ** 2).sum(-1))
    return d


def _cross_map(Dm: np.ndarray, x: np.ndarray, lib: np.ndarray, E: int, theiler: int) -> float:
    """Estimate x at every point from its E+1 nearest neighbours among lib points; return the correlation with the true x."""
    n = Dm.shape[0]
    d = Dm[:, lib].copy()
    idx = np.arange(n)
    d[np.abs(idx[:, None] - lib[None, :]) <= theiler] = np.inf
    k = E + 1
    nn = np.argpartition(d, k, axis=1)[:, :k]
    dn = np.take_along_axis(d, nn, axis=1)
    dmin = np.maximum(dn.min(axis=1, keepdims=True), 1e-12)
    w = np.exp(-dn / dmin)
    w = np.where(np.isfinite(dn), w, 0.0)
    ws = w.sum(axis=1)
    ok = ws > 0
    pred = (w * x[lib][nn]).sum(axis=1)[ok] / ws[ok]
    if ok.sum() < 5 or pred.std() < 1e-12:
        return 0.0
    return float(np.corrcoef(pred, x[ok])[0, 1])


def _phase_surrogate(x: np.ndarray, rng) -> np.ndarray:
    f = np.fft.rfft(x - x.mean())
    ph = rng.uniform(0, 2 * np.pi, len(f))
    ph[0] = 0
    if len(x) % 2 == 0:
        ph[-1] = 0
    s = np.fft.irfft(np.abs(f) * np.exp(1j * ph), n=len(x))
    return s


def ccm_one(y: np.ndarray, x: np.ndarray, E: int, tau: int, n_sur: int = 40, seed: int = 1) -> dict:
    """Does the state space of y (target) estimate x (driver)? Skill high and growing with library size = x leaves a mark in y."""
    M, tidx = _embed(y, E, tau)
    xa = x[tidx]
    Dm = _dist(M)
    n = len(tidx)
    theiler = E * tau
    full = np.arange(n)
    rho = _cross_map(Dm, xa, full, E, theiler)
    rng = np.random.default_rng(seed)
    small = max(E + 6, n // 8)
    r_small = float(np.mean([_cross_map(Dm, xa, np.sort(rng.choice(n, small, replace=False)), E, theiler) for _ in range(8)]))
    sur = [_cross_map(Dm, _phase_surrogate(x, rng)[tidx], full, E, theiler) for _ in range(n_sur)]
    p = (1 + sum(1 for s in sur if s >= rho)) / (n_sur + 1)
    return {"rho": rho, "rho_small": r_small, "p": p, "converges": bool(rho - r_small > 0.03 and rho > 0)}


def ccm(target: np.ndarray, driver: np.ndarray, mode: str) -> dict:
    E, tau = (3, 1) if mode == "month" else (2, 1)
    f = ccm_one(target, driver, E, tau)             # target's state space estimates the driver: driver -> target
    r = ccm_one(driver, target, E, tau, seed=2)     # driver's state space estimates the target: target -> driver
    return {"fwd": f, "rev": r}


# ------------------------------------------------------------------ put it together
def _label(pf: bool, pr: bool, cf: bool, cr: bool, rho_f: float, rho_r: float) -> tuple:
    """The PCMCI-style test decides the direction; CCM adds confidence (or doubt)."""
    cf_dir = cf and (not cr or rho_f > rho_r + 0.05)
    cr_dir = cr and (not cf or rho_r > rho_f + 0.05)
    if pf and not pr:
        if cf_dir:
            return "Strong cause-and-effect evidence", 3
        if cr_dir:
            return "Likely cause (CCM disagrees)", 2
        return "Likely cause", 2
    if pf and pr:
        return "Two-way link (feedback)", 2
    if pr:
        return "Looks like an effect, not a cause", 0
    if cf_dir:
        return "Weak sign (CCM only)", 1
    return "No cause-and-effect evidence", 0


def run(series: dict, names: list, mode: str) -> dict:
    """series: list of (t_index_list, residual_list) per name; index 0 is the target. Returns {name: result} for each driver."""
    maps = [dict(zip(t, v)) for t, v in series]
    ts = sorted(set.intersection(*[set(m) for m in maps]))
    if len(ts) < MIN_N:
        return {"available": False, "reason": f"Needs at least {MIN_N} values in a row at the same times; only {len(ts)} overlap."}
    # keep the longest run of consecutive time steps
    best, cur = [], [ts[0]]
    for a, b in zip(ts, ts[1:]):
        if b == a + 1:
            cur.append(b)
        else:
            best = cur if len(cur) > len(best) else best
            cur = [b]
    best = cur if len(cur) > len(best) else best
    if len(best) < MIN_N:
        return {"available": False, "reason": "Needs values in a row. Use every month of the year, or one value per year, over a longer period."}
    X = np.array([[m[t] for m in maps] for t in best], dtype=float)
    tau_max = 6 if mode == "month" else 2
    pm = pcmci(X, tau_max)
    items = []
    for i in range(1, X.shape[1]):
        items.append((i, pm[i]))
    # Benjamini-Hochberg across the drivers
    pf_adj = stats.benjamini_hochberg([it[1]["p"] for it in items]) if hasattr(stats, "benjamini_hochberg") else [it[1]["p"] for it in items]
    pr_adj = stats.benjamini_hochberg([it[1]["rev_p"] for it in items]) if hasattr(stats, "benjamini_hochberg") else [it[1]["rev_p"] for it in items]
    res = {}
    for (i, r), a, b in zip(items, pf_adj, pr_adj):
        cc = ccm(X[:, 0], X[:, i], mode)
        pf, pr = a < 0.05, b < 0.05
        cf = cc["fwd"]["p"] < 0.05 and cc["fwd"]["converges"]
        cr = cc["rev"]["p"] < 0.05 and cc["rev"]["converges"]
        label, level = _label(pf, pr, cf, cr, cc['fwd']['rho'], cc['rev']['rho'])
        res[names[i]] = {"label": label, "level": level,
                         "pcmci": {"lag": r["lag"], "r": r["r"], "p_adj": a, "supports": pf, "rev_lag": r["rev_lag"], "rev_r": r["rev_r"], "rev_p_adj": b, "rev_supports": pr},
                         "ccm": {"rho": cc["fwd"]["rho"], "rho_small": cc["fwd"]["rho_small"], "p": cc["fwd"]["p"], "converges": cc["fwd"]["converges"], "supports": cf,
                                 "rev_rho": cc["rev"]["rho"], "rev_p": cc["rev"]["p"], "rev_converges": cc["rev"]["converges"], "rev_supports": cr}}
    return {"available": True, "n": len(best), "tau_max": tau_max, "unit": "months" if mode == "month" else "years", "by_driver": res}
