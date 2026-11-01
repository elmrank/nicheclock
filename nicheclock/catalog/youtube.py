"""YouTube Data API v3 ``videos.list?chart=mostPopular`` per region (official API, needs a key).

Terms honoured (https://developers.google.com/youtube/terms/developer-policies): the chart is YouTube's own
ranking, so only *presence on the chart* and the raw ``viewCount`` YouTube reports are used; nothing is
computed from them (no ratio, no score strength). Data is refreshed on every daily build and nothing is kept
between builds. 1 quota unit per call; one call per region per day.

The key is read from ``YOUTUBE_API_KEY`` or from the one-line env file (``YOUTUBE_API_KEY=...``) named by
``YOUTUBE_KEY_FILE``, and is never printed or written anywhere. Without either, the channel is skipped.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from nicheclock.http import FetchError, RobotsDisallowed

NAME = "youtube"
LICENSE = "YouTube API Services Terms; chart and viewCount as reported by YouTube"
API = "https://www.googleapis.com/youtube/v3/videos"
UNIT = "video views (as reported by YouTube)"
CHART_SIZE = 50
DAILY_UNIT_BUDGET = 3000        # nicheclock's share of the 10,000-unit daily quota


def load_key(env: dict[str, str] | None = None) -> str | None:
    env = env if env is not None else os.environ
    if env.get("YOUTUBE_API_KEY"):
        return env["YOUTUBE_API_KEY"].strip()
    if not env.get("YOUTUBE_KEY_FILE"):
        return None
    path = Path(env["YOUTUBE_KEY_FILE"]).expanduser()
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("YOUTUBE_API_KEY="):
                return line.split("=", 1)[1].strip().strip("\"'") or None
    except OSError:
        return None
    return None


def parse_chart(data: dict[str, Any]) -> list[dict[str, Any]]:
    """[{rank, id, title, channel, views, url}] in chart order."""
    out = []
    for i, it in enumerate(data.get("items", []), start=1):
        sn, st = it.get("snippet") or {}, it.get("statistics") or {}
        if not it.get("id") or not sn.get("title"):
            continue
        views = st.get("viewCount")
        out.append({"rank": i, "id": it["id"], "title": sn["title"].strip(), "channel": sn.get("channelTitle", ""),
                    "views": int(views) if views is not None and str(views).isdigit() else None,
                    "url": f"https://www.youtube.com/watch?v={it['id']}"})
    return out


# videoCategoryId -> catalog category (only where YouTube's own category says what the video is about)
CATEGORY_MAP = {"2": "autos-vehicles", "15": "pets-animals", "17": "sports", "19": "travel-transportation", "20": "games",
                "26": "hobbies-leisure", "28": "technology", "27": "jobs-education", "25": "politics", "24": "entertainment",
                "1": "entertainment", "23": "entertainment", "10": "entertainment"}


def fetch_chart(client: Any, key: str, region: str, category: str | None = None) -> tuple[list[dict[str, Any]] | None, str]:
    """(videos or None, detail). Errors never contain the key. ``category``: a videoCategoryId."""
    params = {"part": "snippet,statistics", "chart": "mostPopular", "regionCode": region, "maxResults": CHART_SIZE, "key": key}
    if category:
        params["videoCategoryId"] = category
    try:
        data = client.get_json(API, params=params)
    except (FetchError, RobotsDisallowed) as e:
        return None, re.sub(re.escape(key), "***", str(e))
    if "error" in data:
        msg = (data["error"] or {}).get("message", "error")
        return None, re.sub(re.escape(key), "***", f"API error: {msg}")[:200]
    return parse_chart(data), "ok"


def video_row(v: dict[str, Any], category: str, category_label: str, region: dict[str, Any], end: str, yt_category: str) -> dict[str, Any]:
    """A topic row for a chart video (sidecar only: never written to the tracked files). It says what YouTube says:
    present on the most-popular chart of its category, with the raw views; no ratio, no score, steady/top."""
    ch = {"chart": True, "rank": v["rank"], "videos": 1, "value": v.get("views"), "unit": UNIT, "category_id": yt_category,
          "source": NAME, "source_url": v["url"], "license": LICENSE}
    return {
        "id": f"yt:{v['id']}", "qid": None, "label": v["title"], "native_label": None,
        "description": f"{v['channel']} (YouTube most popular, category {yt_category})".strip(),
        "category": category, "category_label": category_label,
        "category_via": {"kind": "source", "class": f"youtube-category-{yt_category}", "label": "YouTube video category"},
        "region": region["id"], "trend": "steady", "rise": None, "status": "steady", "ratio": None,
        "volume": v.get("views"), "volume_unit": UNIT, "rank_score": 0.0, "score": 0, "source_families": ["video"],
        "multi_source": False, "rising_channels": [], "seasonal": "unknown", "seasonal_detail": None, "first_seen": end,
        "series_end": end, "windows": {}, "channels": {"youtube": ch},
        "evidence": [{"source": NAME, "source_url": v["url"], "license": LICENSE, "title": v["title"], "value": v.get("views"),
                      "unit": UNIT, "rank": v["rank"]}],
        "source": NAME, "source_url": v["url"], "license": LICENSE, "sources": [NAME], "aliases": [], "breakdown": [],
        "wikipedia_url": None, "sidecar_only": True}
