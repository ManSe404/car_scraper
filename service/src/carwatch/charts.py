"""A small server-side SVG step chart for an ad's price history (no JavaScript in the UI)."""

from __future__ import annotations

from datetime import datetime
from html import escape

import numpy as np

from carwatch import fmt


def price_chart_svg(
    points: list[tuple[datetime, int]], end: datetime, tz: str, width: int = 720, height: int = 220
) -> str:
    """Step chart: the price holds from one sighting until the next, and until ``end`` after the last one."""
    if not points:
        return ""
    points = sorted(points)
    pad_l, pad_r, pad_t, pad_b = 72, 16, 16, 32
    plot_w, plot_h = width - pad_l - pad_r, height - pad_t - pad_b

    t0 = points[0][0]
    t1 = max(end, points[-1][0])
    span = (t1 - t0).total_seconds() or 1.0

    prices = [p for _, p in points]
    lo, hi = min(prices), max(prices)
    margin = max((hi - lo) * 0.15, hi * 0.03, 50)
    y_lo, y_hi = lo - margin, hi + margin

    def x(t: datetime) -> float:
        return pad_l + (t - t0).total_seconds() / span * plot_w

    def y(p: float) -> float:
        return pad_t + (y_hi - p) / (y_hi - y_lo) * plot_h

    d = [f"M{x(points[0][0]):.1f},{y(points[0][1]):.1f}"]
    for t, p in points[1:]:
        d.append(f"H{x(t):.1f}V{y(p):.1f}")
    d.append(f"H{x(t1):.1f}")

    dots = []
    for t, p in points:
        label = escape(f"{fmt.date_time(t, tz)} — {fmt.money(p)}")
        dots.append(f'<circle class="dot" cx="{x(t):.1f}" cy="{y(p):.1f}" r="4"><title>{label}</title></circle>')

    grid = []
    for value in sorted({lo, hi}):
        gy = y(value)
        grid.append(f'<line class="grid" x1="{pad_l}" x2="{width - pad_r}" y1="{gy:.1f}" y2="{gy:.1f}"/>')
        grid.append(
            f'<text class="axis" x="{pad_l - 8}" y="{gy + 4:.1f}" text-anchor="end">{escape(fmt.money(value))}</text>'
        )
    axis_y = height - 10
    grid.append(f'<text class="axis" x="{pad_l}" y="{axis_y}">{escape(fmt.date(t0, tz))}</text>')
    grid.append(
        f'<text class="axis" x="{width - pad_r}" y="{axis_y}" text-anchor="end">{escape(fmt.date(t1, tz))}</text>'
    )

    return (
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" aria-label="Istorija cene">'
        f'{"".join(grid)}<path class="line" d="{"".join(d)}"/>{"".join(dots)}</svg>'
    )


# ---- multi-series charts for model comparison ------------------------------------------------------------
# Series colors come from CSS variables --s1..--s6 (fixed order, light and dark steps defined in base.html).


def _nice_ticks(lo: float, hi: float, count: int = 4) -> list[float]:
    if hi <= lo:
        return [lo]
    raw = (hi - lo) / count
    magnitude = 10 ** int(f"{raw:e}".split("e")[1])
    step = next(m * magnitude for m in (1, 2, 2.5, 5, 10) if m * magnitude >= raw)
    first = (lo // step + 1) * step
    ticks = []
    value = first
    while value < hi:
        ticks.append(value)
        value += step
    return ticks


def year_lines_svg(
    series: list[tuple[str, int, dict[int, tuple[float, int]]]], width: int = 760, height: int = 300
) -> str:
    """Median price by model year, one line per model. series: (label, color slot 1-6, {year: (median, n)})."""
    points = [(y, p) for _, _, by_year in series for y, (p, _) in by_year.items()]
    if not points:
        return ""
    pad_l, pad_r, pad_t, pad_b = 72, 120, 16, 36
    plot_w, plot_h = width - pad_l - pad_r, height - pad_t - pad_b
    x_lo, x_hi = min(y for y, _ in points), max(y for y, _ in points)
    if x_lo == x_hi:
        x_lo, x_hi = x_lo - 1, x_hi + 1
    y_lo, y_hi = 0.0, max(p for _, p in points) * 1.08

    def x(v: float) -> float:
        return pad_l + (v - x_lo) / (x_hi - x_lo) * plot_w

    def y(v: float) -> float:
        return pad_t + (y_hi - v) / (y_hi - y_lo) * plot_h

    parts = []
    for tick in _nice_ticks(y_lo, y_hi):
        ty = y(tick)
        parts.append(f'<line class="grid" x1="{pad_l}" x2="{pad_l + plot_w}" y1="{ty:.1f}" y2="{ty:.1f}"/>')
        parts.append(
            f'<text class="axis" x="{pad_l - 8}" y="{ty + 4:.1f}" text-anchor="end">{escape(fmt.money(tick))}</text>'
        )
    year_step = max(1, (x_hi - x_lo) // 8)
    for year in range(x_lo, x_hi + 1, year_step):
        parts.append(f'<text class="axis" x="{x(year):.1f}" y="{height - 12}" text-anchor="middle">{year}</text>')

    end_labels: list[tuple[float, float, str]] = []
    for label, slot, by_year in series:
        years = sorted(by_year)
        if not years:
            continue
        d = "M" + " L".join(f"{x(yr):.1f},{y(by_year[yr][0]):.1f}" for yr in years)
        parts.append(f'<path class="series" style="stroke:var(--s{slot})" d="{d}"/>')
        for yr in years:
            price, n = by_year[yr]
            tip = escape(f"{label} · {yr}: medijana {fmt.money(price)} (n={n})")
            parts.append(
                f'<circle class="mark" style="fill:var(--s{slot})" cx="{x(yr):.1f}" cy="{y(price):.1f}" r="4">'
                f"<title>{tip}</title></circle>"
            )
        last = years[-1]
        end_labels.append((y(by_year[last][0]), x(last), label))

    if len(series) <= 4:
        # Direct labels at line ends, nudged apart so they never overlap.
        end_labels.sort()
        placed: list[float] = []
        for ly, lx, label in end_labels:
            ly = max(ly, placed[-1] + 14) if placed else ly
            placed.append(ly)
            parts.append(f'<text class="label" x="{lx + 8:.1f}" y="{ly + 4:.1f}">{escape(label)}</text>')

    return (
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" aria-label="Medijana cene po godištu">'
        f"{''.join(parts)}</svg>"
    )


def km_scatter_svg(
    series: list[tuple[str, int, list[tuple[int, int, int]]]], width: int = 760, height: int = 300
) -> str:
    """Price against mileage, one color per model. series: (label, color slot, [(km, price, ad id)])."""
    points = [(k, p) for _, _, pts in series for k, p, _ in pts]
    if not points:
        return ""
    pad_l, pad_r, pad_t, pad_b = 72, 16, 16, 36
    plot_w, plot_h = width - pad_l - pad_r, height - pad_t - pad_b
    x_hi = float(np.percentile([k for k, _ in points], 98)) * 1.05 or 1.0
    y_hi = float(np.percentile([p for _, p in points], 98)) * 1.08 or 1.0

    def x(v: float) -> float:
        return pad_l + min(v, x_hi) / x_hi * plot_w

    def y(v: float) -> float:
        return pad_t + (y_hi - min(v, y_hi)) / y_hi * plot_h

    parts = []
    for tick in _nice_ticks(0, y_hi):
        ty = y(tick)
        parts.append(f'<line class="grid" x1="{pad_l}" x2="{pad_l + plot_w}" y1="{ty:.1f}" y2="{ty:.1f}"/>')
        parts.append(
            f'<text class="axis" x="{pad_l - 8}" y="{ty + 4:.1f}" text-anchor="end">{escape(fmt.money(tick))}</text>'
        )
    for tick in _nice_ticks(0, x_hi):
        parts.append(
            f'<text class="axis" x="{x(tick):.1f}" y="{height - 12}" text-anchor="middle">'
            f"{escape(fmt.km(int(tick)))}</text>"
        )
    for label, slot, pts in series:
        for km_value, price, ad_id in pts:
            tip = escape(f"{label}: {fmt.km(km_value)}, {fmt.money(price)}")
            parts.append(
                f'<a href="/ads/{ad_id}"><circle class="mark dot-scatter" style="fill:var(--s{slot})" '
                f'cx="{x(km_value):.1f}" cy="{y(price):.1f}" r="4"><title>{tip}</title></circle></a>'
            )
    return (
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" aria-label="Cena prema kilometraži">'
        f"{''.join(parts)}</svg>"
    )
