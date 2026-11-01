"""Per-entity scoring: ratio against a 14-day median per channel, source families, seasonal-vs-new.

Everything here is a pure function of numbers, so it is tested without network access.

Units are never mixed: each channel keeps its own unit (pageviews/day, articles/day, HN points).

Ranking is volume-aware on purpose: a 40x rise on 300 views a day (a Main Page feature, a viral stub) must not
outrank 4x on 450,000. ``rank_score = log10(volume)^2 * log10(min(ratio, RATIO_CAP))``, times a bonus per extra
source family; every channel also has a minimum level (``floor``) below which it never counts as rising.
"""

from __future__ import annotations

import calendar
import math
import statistics
from datetime import date
from typing import Any, Sequence

# Windows of the "Trending now" table: label -> days averaged at the end of the series.
WINDOWS = {"24h": 1, "48h": 2, "7d": 7}
BASELINE_DAYS = 14
ACTIVE_RATIO = 1.5            # a channel/window is "rising" at 1.5x its 14-day median
BASELINE_FRACTION = 10.0      # baseline_ok(): a baseline below floor/10 is too thin for a ratio
FLOORS = {"wiki": 1000.0, "news": 50.0}     # default minimum recent level (pageviews/day, estimated articles/day)
# Wikipedia edition floors, pageviews/day: "en >= 5k, scaled for smaller editions". A lower level is noise on
# these audiences (a Main Page slot or a viral stub), not a trend.
WIKI_FLOORS = {"en": 5000.0, "es": 2000.0, "ru": 2500.0, "zh": 2000.0, "pt": 2000.0, "hi": 1500.0, "id": 1500.0,
               "ur": 600.0, "bn": 600.0, "am": 150.0, "ha": 150.0, "yo": 150.0}
RATIO_CAP = 10.0              # beyond 10x the rank gains nothing: "+1,000%+" is as good as it gets
SCORE_SCALE = 2.5             # score = min(100, 2.5 x rank_score): 450,000 views at 10x is 80, two families 100
FAMILY_BONUS = 0.25           # each extra rising family adds 25% to the rank score
OTD_FLOOR_FACTOR = 3.0        # pages on the Main Page's "On this day" must clear 3x the floor
NEWS_RATIO = 2.0              # news volume is spikier than pageviews: needs 2x
HN_MIN_POINTS = 50.0
FAMILIES = {"wikipedia": "wiki", "news": "news", "hn": "community", "youtube": "video"}


def window_stats(series: Sequence[float], days: int) -> dict[str, float] | None:
    """Mean of the last ``days`` against the median of the 14 days before that window.

    ``series`` is daily, oldest first, ending on the last complete day. None when there is not enough
    history (needs ``days`` + 14 values)."""
    if len(series) < days + BASELINE_DAYS:
        return None
    recent = statistics.fmean(series[-days:])
    base = statistics.median(series[-days - BASELINE_DAYS:-days])
    return {"value": round(recent, 1), "baseline": round(base, 1), "ratio": round(recent / max(base, 1.0), 2)}


def channel_windows(series: Sequence[float]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for label, n in WINDOWS.items():
        st = window_stats(series, n)
        if st:
            out[label] = {"ratio": st["ratio"], "active": st["ratio"] >= ACTIVE_RATIO}
    return out


PEAK_DAYS = 14
FADING_BELOW = 0.5            # last day below half of the 14-day peak: the rise is over, the topic is "fading"


def peak_info(series: Sequence[float]) -> dict[str, Any]:
    """{peak, peak_days_ago, from_peak}: the highest of the last ``PEAK_DAYS`` days, how long ago it was, and the last
    day as a share of it (1.0 = at the peak). ``series`` is daily, oldest first."""
    tail = list(series[-PEAK_DAYS:])
    peak = max(tail)
    idx = len(tail) - 1 - max(i for i, v in enumerate(tail) if v == peak)
    return {"peak": round(peak, 1), "peak_days_ago": idx, "from_peak": round(tail[-1] / peak, 3) if peak else 1.0}


def past_peak(ch: dict[str, Any]) -> bool:
    return ch.get("from_peak") is not None and ch["from_peak"] < FADING_BELOW


def baseline_ok(ch: dict[str, Any]) -> bool:
    """False when the channel's 14-day median is below floor/10: a page that had (almost) no readers before
    (a Main Page slot, a brand-new article) shows a huge ratio that means nothing. Channels without a baseline
    (Hacker News, charts) are not judged here."""
    base = ch.get("baseline")
    return base is None or base >= ch.get("floor", 0.0) / BASELINE_FRACTION


def family_rising(channel: str, ch: dict[str, Any]) -> bool:
    """Is one channel rising? ``ch`` carries ``windows`` (wiki/news) or ``value`` (hn)."""
    if channel == "hn":
        return float(ch.get("value") or 0) >= HN_MIN_POINTS
    if channel == "youtube":          # on YouTube's own most-popular chart; no number of ours is involved
        return bool(ch.get("chart"))
    if not baseline_ok(ch):
        return False
    w = ch.get("windows") or {}
    floor = ch.get("floor", FLOORS[FAMILIES[channel]])
    need = NEWS_RATIO if channel == "news" else ACTIVE_RATIO
    for label in ("24h", "48h"):
        if label in w and w[label]["ratio"] >= need and _level(ch, label) >= floor:
            return True
    return False


def _level(ch: dict[str, Any], label: str) -> float:
    """Recent level of a window: the series tail averaged over the window's days."""
    s = ch.get("series") or []
    n = WINDOWS[label]
    return statistics.fmean(s[-n:]) if len(s) >= n else float(ch.get("value") or 0)


def rank_score(volume: float | None, ratio: float | None, families: int) -> float:
    """log10(volume)^2 x log10(min(ratio, cap)), +25% per extra rising family. 0 without a ratio and volume.
    Squaring the volume term makes the list lean toward what many people read (Google Trends sorts by volume)."""
    if not volume or not ratio or ratio <= 1:
        return 0.0
    base = math.log10(max(volume, 10.0)) ** 2 * math.log10(min(ratio, RATIO_CAP))
    return base * (1.0 + FAMILY_BONUS * max(0, families - 1))


def score_topic(channels: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """{ratio, volume, volume_unit, rank_score, score, families, multi_source, rising_channels}.

    ``ratio``: largest 24h/48h ratio among the rising channels that have a baseline (the "% increase"
    column). ``volume``: the Wikipedia channel's last-day value, else the news channel's (their units differ,
    ``volume_unit`` says which); None when only a chart or a Hacker News story exists. ``rank_score`` orders
    the default list; ``score`` = min(100, 2.5 x rank_score). ``multi_source``: at least two families rising."""
    rising = [c for c, ch in channels.items() if family_rising(c, ch)]
    fams = sorted({FAMILIES[c] for c in rising})
    best = None                      # (ratio, window, channel): where the row's ratio comes from
    for c in rising:
        w = channels[c].get("windows") or {}
        for k in ("24h", "48h"):
            if k in w and (best is None or w[k]["ratio"] > best[0]):
                best = (w[k]["ratio"], k, c)
    ratio = best[0] if best else None
    vol_src = next((c for c in ("wikipedia", "news") if c in channels and channels[c].get("value")), None)
    volume = channels[vol_src]["value"] if vol_src else None
    rank = rank_score(volume, ratio, len(fams))
    if rank == 0.0 and "hn" in rising:          # a Hacker News story alone: ranked by its points
        rank = math.log10(max(channels["hn"]["value"], 10.0)) ** 2 * math.log10(2.0)
    return {"ratio": ratio, "ratio_window": best[1] if best else None, "ratio_channel": best[2] if best else None, "volume": volume, "volume_unit": channels[vol_src]["unit"] if vol_src else None,
            "rank_score": round(rank, 3), "score": min(100, round(SCORE_SCALE * rank)), "families": fams,
            "multi_source": len(fams) >= 2, "rising_channels": sorted(rising)}


MILD_RATIO = 1.2              # a "mild" riser: >= 1.2x in some window, used only to fill a thin category


def trend_of(channels: dict[str, dict[str, Any]]) -> tuple[str, str | None, str] | None:
    """(trend, rise, status) or None when the topic is below every floor.

    * ("rising", "strong", "active"|"ended"): the strict rule (``status_of``);
    * ("rising", "mild", "active"): not strong, but >= MILD_RATIO in a window with the level above the floor;
    * ("steady", None, "steady"): Wikipedia or news level above the floor and no real rise.
    The caller decides how many of each a category gets; nothing is made up."""
    st = status_of(channels)
    if st == "ended":
        return "fading", None, "ended"
    if st == "active":
        # a channel past its peak is not rising any more: Wikipedia and news are judged on the last day against the
        # 14-day peak; Hacker News (a snapshot) and charts have no peak and keep a topic rising
        live = [c for c, ch in channels.items() if family_rising(c, ch) and (c not in ("wikipedia", "news") or not past_peak(ch))]
        if not live:
            return "fading", None, "fading"
    if st:
        return "rising", "strong", st
    for c in ("wikipedia", "news"):
        ch = channels.get(c)
        if not ch or not baseline_ok(ch):
            continue
        w = ch.get("windows") or {}
        for label in WINDOWS:
            if label in w and w[label]["ratio"] >= MILD_RATIO and _level(ch, label) >= ch.get("floor", FLOORS[FAMILIES[c]]):
                if past_peak(ch):
                    return "fading", None, "fading"
                return "rising", "mild", "active"
    for c in ("wikipedia", "news"):
        ch = channels.get(c)
        if ch and baseline_ok(ch) and _level(ch, "48h") >= ch.get("floor", FLOORS[FAMILIES[c]]):
            return "steady", None, "steady"
    return None


def status_of(channels: dict[str, dict[str, Any]]) -> str | None:
    """"active": a channel is rising in the last 24-48 h. "ended": it rose within 7 days but is back
    near baseline. None: neither (not listed)."""
    if any(family_rising(c, ch) for c, ch in channels.items()):
        return "active"
    for c in ("wikipedia", "news"):
        w = (channels.get(c) or {}).get("windows") or {}
        if c in channels and not baseline_ok(channels[c]):
            continue
        if "7d" in w and w["7d"]["ratio"] >= ACTIVE_RATIO and _level(channels[c], "7d") >= channels[c].get("floor", FLOORS[FAMILIES[c]]):
            return "ended"
    return None


# -- seasonal vs new ------------------------------------------------------------------------

def _daily_avg(month_views: dict[tuple[int, int], int], y: int, m: int, today: date) -> float | None:
    v = month_views.get((y, m))
    if v is None:
        return None
    days = calendar.monthrange(y, m)[1]
    if (y, m) == (today.year, today.month):
        days = max(1, today.day)
    return v / days


def seasonality(month_views: dict[tuple[int, int], int], today: date, min_years: int = 2) -> dict[str, Any]:
    """Verdict from monthly pageviews: does this calendar month recur as a peak in earlier years?

    ``month_views`` maps (year, month) -> views. For each earlier year with data we compare the daily
    average of the current calendar month (+-1 month, so a peak that shifts a few days still counts)
    with that year's median monthly daily average. A year "peaks" when the ratio is >= 1.8.
    * seasonal: at least 2 earlier years peak, and at least half of those with data
    * new: the current month is >= 2x this year's typical month but no earlier year peaked
    * unknown: fewer than ``min_years`` earlier years of data."""
    y0, m0 = today.year, today.month
    cur = _daily_avg(month_views, y0, m0, today)
    years = sorted({y for (y, _m) in month_views if y < y0})
    # a year only counts when it has most of its months (a page created in October has no season)
    usable = [y for y in years if sum(1 for m in range(1, 13) if (y, m) in month_views) >= 9]
    if len(usable) < min_years:
        return {"verdict": "unknown", "years_checked": len(usable), "peak_months": []}
    peaked, peak_months = 0, []
    for y in usable:
        daily = {m: _daily_avg(month_views, y, m, today) for m in range(1, 13)}
        vals = [v for v in daily.values() if v is not None]
        med = max(statistics.median(vals), 1.0)
        around = [daily[((m0 - 1 + k) % 12) + 1] for k in (-1, 0, 1)]
        around = [v for v in around if v is not None]
        if around and max(around) / med >= 1.8:
            peaked += 1
        top = max(daily, key=lambda m: daily[m] or 0)
        peak_months.append(top)
    this_year = [v for m in range(1, 13) if (v := _daily_avg(month_views, y0, m, today)) is not None]
    typical = max(statistics.median(this_year[:-1]) if len(this_year) > 1 else 1.0, 1.0)
    if peaked >= 2 and peaked * 2 >= len(usable):
        verdict = "seasonal"
    elif cur is not None and cur / typical >= 2.0:
        verdict = "new"
    else:
        verdict = "unknown"
    top_months = [m for m, _n in sorted(((m, peak_months.count(m)) for m in set(peak_months)), key=lambda x: -x[1])[:2]]
    return {"verdict": verdict, "years_checked": len(usable), "years_peaking": peaked, "peak_months": sorted(top_months)}
