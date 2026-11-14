"""Basic statistics for a yearly time series, written without extra packages.

Three ways to ask "is this series really going up or down?" (all at the 95% level):
  1. Linear regression + t-test      slope per year, its 95% confidence interval, and a p-value from Student's t distribution
  2. Mann-Kendall test               does not assume a straight line or bell-shaped noise; counts how often later years are higher than earlier years
  3. Theil-Sen slope + interval      the median slope between all pairs of years, with a 95% confidence interval
The final verdict uses the Mann-Kendall test (the usual choice for climate series). The other two are shown as a cross-check.
"""
from __future__ import annotations

import math
import statistics

ALPHA = 0.05
MIN_YEARS = 4          # fewest yearly values the tests will run on (a 10-year or longer record is much more reliable)
MIN_MONTHS = 12        # fewest monthly values in 'every month' mode


def min_points(mode: str) -> int:
    return MIN_YEARS if mode == 'year' else MIN_MONTHS
_ND = statistics.NormalDist()


# ---------------------------------------------------------------- distributions
def _betacf(a: float, b: float, x: float) -> float:
    tiny, qab, qap, qam = 1e-30, a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        de = d * c
        h *= de
        if abs(de - 1.0) < 3e-14:
            break
    return h


def _betai(a: float, b: float, x: float) -> float:
    """Regularised incomplete beta function I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    bt = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def t_two_sided_p(t: float, df: int) -> float:
    """Two-sided p-value of Student's t with df degrees of freedom."""
    if df <= 0:
        return 1.0
    return min(1.0, _betai(df / 2.0, 0.5, df / (df + t * t)))


def t_critical(df: int, conf: float = 0.95) -> float:
    """t such that P(|T| > t) = 1 - conf."""
    alpha = 1.0 - conf
    lo, hi = 0.0, 1000.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if t_two_sided_p(mid, df) > alpha:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


# ---------------------------------------------------------------- the three methods
def linear_regression(x: list, y: list) -> dict:
    n = len(x)
    mx, my = statistics.fmean(x), statistics.fmean(y)
    sxx = sum((a - mx) ** 2 for a in x)
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    b = sxy / sxx
    a = my - b * mx
    res = [yy - (a + b * xx) for xx, yy in zip(x, y)]
    sse = sum(r * r for r in res)
    sst = sum((yy - my) ** 2 for yy in y)
    df = n - 2
    s2 = sse / df if df > 0 else 0.0
    se = math.sqrt(s2 / sxx) if sxx > 0 else 0.0
    if se > 0:
        t = b / se
        p = t_two_sided_p(t, df)
        tc = t_critical(df)
    else:                                   # perfect line or no variation
        t = math.copysign(float("inf"), b) if b != 0 else 0.0
        p = 0.0 if b != 0 else 1.0
        tc = 0.0
    return {"slope": b, "intercept": a, "se": se, "t": t, "df": df, "p": p, "ci_low": b - tc * se, "ci_high": b + tc * se,
            "r2": (1.0 - sse / sst) if sst > 0 else 0.0, "residuals": res}


def mann_kendall(y: list, var_scale: float = 1.0) -> dict:
    """Mann-Kendall test with the tie correction, two-sided, normal approximation with continuity correction.
    var_scale > 1 inflates the variance of S to allow for autocorrelation (Hamed and Rao 1998)."""
    n = len(y)
    s = sum((y[j] > y[i]) - (y[j] < y[i]) for i in range(n - 1) for j in range(i + 1, n))
    counts: dict = {}
    for v in y:
        counts[v] = counts.get(v, 0) + 1
    var = (n * (n - 1) * (2 * n + 5) - sum(t * (t - 1) * (2 * t + 5) for t in counts.values())) / 18.0 * var_scale
    if var <= 0:
        return {"s": s, "var": 0.0, "z": 0.0, "p": 1.0, "tau": 0.0}
    z = (s - 1) / math.sqrt(var) if s > 0 else ((s + 1) / math.sqrt(var) if s < 0 else 0.0)
    return {"s": s, "var": var, "z": z, "p": min(1.0, 2.0 * _ND.cdf(-abs(z))), "tau": s / (n * (n - 1) / 2.0)}


def hamed_rao_scale(xs: list, ys: list, slope: float) -> float:
    """Variance inflation factor n / n* for Mann-Kendall (Hamed and Rao 1998): detrend with the Sen slope, rank the residuals, and add up the autocorrelations that are significant at 95%.
    The larger of that and the lag-1 effective-sample-size factor is used. Never below 1, so the correction can only make the test more cautious."""
    n = len(ys)
    if n < 8:
        return 1.0
    res = [y - slope * x for x, y in zip(xs, ys)]
    order = sorted(range(n), key=lambda i: res[i])
    rank = [0.0] * n
    i = 0
    while i < n:                                  # average ranks for ties
        j = i
        while j + 1 < n and res[order[j + 1]] == res[order[i]]:
            j += 1
        for k in range(i, j + 1):
            rank[order[k]] = (i + j) / 2.0 + 1.0
        i = j + 1
    m = statistics.fmean(rank)
    den = sum((v - m) ** 2 for v in rank)
    if den <= 0:
        return 1.0
    total, lim = 0.0, 1.96 / math.sqrt(n)
    for k in range(1, n - 1):
        rho = sum((rank[t] - m) * (rank[t + k] - m) for t in range(n - k)) / den
        if abs(rho) > lim:
            total += (n - k) * (n - k - 1) * (n - k - 2) * rho
    hr = 1.0 + 2.0 / (n * (n - 1) * (n - 2)) * total
    r1 = sum((rank[t] - m) * (rank[t + 1] - m) for t in range(n - 1)) / den       # also the plain lag-1 effective sample size, which is safer for short, strongly alike series
    ar = (1.0 + r1) / (1.0 - r1) if 0 < r1 < 0.98 else 1.0
    return max(1.0, hr, ar)


def auto_rate(span_years: float) -> tuple:
    """(length of the rate unit in years, its name). Short records are quoted per week or month, not per decade."""
    if span_years < 0.25:
        return 7.0 / 365.25, "week"
    if span_years < 2.0:
        return 1.0 / 12.0, "month"
    return 10.0, "decade"


def sen_from_slopes(sl: list, var_s: float, conf: float = 0.95) -> dict:
    """Median of pairwise slopes with the distribution-free confidence interval (Gilbert 1987)."""
    sl = sorted(sl)
    n = len(sl)
    b = statistics.median(sl)
    c = _ND.inv_cdf(1 - (1 - conf) / 2) * math.sqrt(max(var_s, 0.0))
    m1, m2 = (n - c) / 2.0, (n + c) / 2.0

    def at(pos):
        pos = min(max(pos, 0.0), n - 1.0)
        i = int(math.floor(pos))
        j = min(i + 1, n - 1)
        return sl[i] + (sl[j] - sl[i]) * (pos - i)
    return {"slope": b, "ci_low": at(m1 - 1.0), "ci_high": at(m2)}


def theil_sen(x: list, y: list, var_s: float, conf: float = 0.95) -> dict:
    sl = [(y[j] - y[i]) / (x[j] - x[i]) for i in range(len(x) - 1) for j in range(i + 1, len(x)) if x[j] != x[i]]
    r = sen_from_slopes(sl, var_s, conf)
    r["intercept"] = statistics.median(v - r["slope"] * t for t, v in zip(x, y))
    return r


def seasonal_mann_kendall(groups: list) -> dict:
    """Seasonal Mann-Kendall (Hirsch): the test is done inside each calendar month across the years, then the results are added up."""
    s_tot, v_tot, pairs = 0, 0.0, 0
    for g in groups:
        n = len(g)
        if n < 3:
            continue
        r = mann_kendall(g)
        s_tot += r["s"]
        v_tot += r["var"]
        pairs += n * (n - 1) / 2
    if v_tot <= 0:
        return {"s": s_tot, "var": 0.0, "z": 0.0, "p": 1.0, "tau": 0.0}
    z = (s_tot - 1) / math.sqrt(v_tot) if s_tot > 0 else ((s_tot + 1) / math.sqrt(v_tot) if s_tot < 0 else 0.0)
    return {"s": s_tot, "var": v_tot, "z": z, "p": min(1.0, 2.0 * _ND.cdf(-abs(z))), "tau": s_tot / pairs if pairs else 0.0}


def lag1(res: list) -> float:
    m = statistics.fmean(res)
    d = sum((r - m) ** 2 for r in res)
    return sum((res[i] - m) * (res[i + 1] - m) for i in range(len(res) - 1)) / d if d > 0 else 0.0


# ---------------------------------------------------------------- one series
def fmt(v: float, nd: int = 2) -> str:
    a = abs(v)
    if a != 0 and a < 10 ** -nd:
        return f"{v:.{nd + 2}f}"
    return f"{v:.{nd}f}"


def pfmt(p: float) -> str:
    return "< 0.001" if p < 0.001 else f"{p:.3f}"


def analyse(xs_in: list, ys_in: list, name: str = "This variable", unit: str = "", percent_ok: bool = False, seasons: list | None = None, min_n: int = MIN_YEARS, autocorr: bool = False, rate: tuple | None = None) -> dict | None:
    """One series. xs are years (or decimal years for monthly values). When `seasons` (the calendar month of each value) is given, the monthly seasonal tests are used."""
    pts = [(float(a), float(b), (seasons[i] if seasons else None)) for i, (a, b) in enumerate(zip(xs_in, ys_in)) if b is not None and not (isinstance(b, float) and math.isnan(b))]
    if len(pts) < min_n:
        return None
    xs, ys, ss = [p[0] for p in pts], [p[1] for p in pts], [p[2] for p in pts]
    n, span = len(pts), xs[-1] - xs[0]
    if seasons:
        groups, slopes, by = [], [], {}
        for x, y, m in pts:
            by.setdefault(m, []).append((x, y))
        for g in by.values():
            g.sort()
            groups.append([v for _, v in g])
            slopes += [(g[j][1] - g[i][1]) / (g[j][0] - g[i][0]) for i in range(len(g) - 1) for j in range(i + 1, len(g)) if g[j][0] != g[i][0]]
        if len(slopes) < 3:
            return None
        mk = seasonal_mann_kendall(groups)
        sen = sen_from_slopes(slopes, mk["var"])
        means = {m: statistics.fmean(v for _, v in g) for m, g in by.items()}
        ols = linear_regression(xs, [y - means[m] for _, y, m in pts])
        mx, my = statistics.fmean(xs), statistics.fmean(ys)
        ols["intercept"] = my - ols["slope"] * mx
        sen["intercept"] = statistics.median(y - sen["slope"] * x for x, y, _ in pts)
    else:
        ols = linear_regression(xs, ys)
        mk = mann_kendall(ys)
        sen = theil_sen(xs, ys, mk["var"])
        if autocorr:
            scale = hamed_rao_scale(xs, ys, sen["slope"])
            if scale > 1.0:
                mk = mann_kendall(ys, scale)
                sen = theil_sen(xs, ys, mk["var"])
                n_eff = max(3.0, n / scale)
                df_eff = max(1, int(n_eff) - 2)
                if math.isfinite(ols["t"]):
                    ols["t"] = ols["t"] / math.sqrt(scale)                     # the standard error grows by sqrt(scale)
                    ols["p"] = t_two_sided_p(ols["t"], df_eff)
                    half = (ols["ci_high"] - ols["ci_low"]) / 2.0 * math.sqrt(scale) * t_critical(df_eff) / t_critical(ols["df"])
                    mid = (ols["ci_high"] + ols["ci_low"]) / 2.0
                    ols["ci_low"], ols["ci_high"], ols["df"] = mid - half, mid + half, df_eff
    r1 = lag1(ols["residuals"])
    sig_mk = mk["p"] < ALPHA
    sig_t = ols["p"] < ALPHA
    sig_ci = not (sen["ci_low"] <= 0.0 <= sen["ci_high"])
    direction = "rising" if sen["slope"] > 0 else "falling" if sen["slope"] < 0 else "flat"
    agree = (sig_mk == sig_t == sig_ci)
    p = mk["p"]
    strength = "very strong" if p < 0.01 else "strong" if p < 0.05 else "weak hint only" if p < 0.10 else "none"
    start, end = sen["intercept"] + sen["slope"] * xs[0], sen["intercept"] + sen["slope"] * xs[-1]
    pct = (sen["slope"] * span / abs(start) * 100.0) if percent_ok and abs(start) > 1e-9 else None
    ry, rl = rate or auto_rate(span)
    d = sen["slope"] * ry
    scale_used = locals().get("scale", 1.0)
    kind = "Seasonal Mann-Kendall" if seasons else ("Mann-Kendall (autocorrelation-corrected)" if autocorr else "Mann-Kendall")
    if sig_mk:
        sentence = (f"{name} is {direction}: about {fmt(abs(d))} {unit} per {rl} (95% range {fmt(sen['ci_low'] * ry)} to {fmt(sen['ci_high'] * ry)}). "
                    f"Significant ({kind} p {('= ' + pfmt(p)) if p >= 0.001 else pfmt(p)}).")
    else:
        sentence = (f"No significant trend. Best estimate {fmt(d)} {unit} per {rl} (95% range {fmt(sen['ci_low'] * ry)} to "
                    f"{fmt(sen['ci_high'] * ry)}; {kind} p = {pfmt(p)}).")
    warn = []
    if len(pts) < 10:
        warn.append(f"Only {len(pts)} values. Rough indication.")
    if span < 1.0:
        warn.append("Under a year. Shows weather, not climate.")
    elif span < 15:
        warn.append(f"Only about {int(round(span))} years. Less reliable.")
    if autocorr and scale_used > 1.0:
        warn.append(f"Values are alike over time, so p was corrected: about {max(3.0, n / scale_used):.0f} independent values of {n}.")
    elif abs(r1) > 2.0 / math.sqrt(n):
        warn.append("Close values look alike, so p may look too sure.")
    if not agree:
        warn.append("Methods disagree: borderline.")
    whole = lambda v: int(v) if not seasons else v  # noqa: E731
    return {"n": n, "first": whole(xs[0]), "last": whole(xs[-1]), "mean": statistics.fmean(ys), "significant": sig_mk, "direction": direction, "strength": strength,
            "agree": agree, "verdict": "Significant" if sig_mk else "Not significant", "sentence": sentence, "warnings": warn, "pct": pct, "test_name": kind,
            "rate_label": rl, "rate_years": ry, "slope_decade": d, "total": sen["slope"] * span, "p": p, "lag1": r1, "monthly": bool(seasons),
            "ols": {"slope_decade": ols["slope"] * ry, "ci_low": ols["ci_low"] * ry, "ci_high": ols["ci_high"] * ry, "t": ols["t"] if math.isfinite(ols["t"]) else None, "df": ols["df"],
                    "p": ols["p"], "r2": ols["r2"], "significant": sig_t, "line": [[xs[0], ols["intercept"] + ols["slope"] * xs[0]], [xs[-1], ols["intercept"] + ols["slope"] * xs[-1]]]},
            "mk": {"s": mk["s"], "z": mk["z"], "p": mk["p"], "tau": mk["tau"], "significant": sig_mk},
            "sen": {"slope_decade": d, "ci_low": sen["ci_low"] * ry, "ci_high": sen["ci_high"] * ry, "significant": sig_ci, "line": [[xs[0], start], [xs[-1], end]]}}
