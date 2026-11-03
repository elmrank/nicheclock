"""The /google-updates/ page builder (hosted/site/google_updates.py): strict input schema, the banner for each state, noindex, no script."""
from __future__ import annotations

import copy
import datetime as dt
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

SITE = Path(__file__).resolve().parents[1] / "hosted" / "site"
spec = importlib.util.spec_from_file_location("google_updates", SITE / "google_updates.py")
gu = importlib.util.module_from_spec(spec)
sys.modules["google_updates"] = gu
spec.loader.exec_module(gu)

TODAY = dt.date(2026, 11, 3)
NAMES = {"css": "trending.abc.css", "google-sans-flex.woff2": "font.abc.woff2", "updates_css": "google-updates.abc.css", "beacon": "b.abc.js"}
ENDED = {"name": "September 2026 spam update", "type": "spam", "started": "2026-09-24", "ended": "2026-10-08", "duration_days": 14,
         "url": "https://status.search.google.com/incidents/A"}
ROLLING = {"name": "November 2026 core update", "type": "core", "started": "2026-11-01", "ended": None, "duration_days": None,
           "url": "https://status.search.google.com/incidents/B"}
DISCOVER = {"name": "February 2026 Discover update", "type": "discover", "started": "2026-02-05", "ended": "2026-02-27", "duration_days": 22,
            "url": "https://status.search.google.com/incidents/C"}
ITEM = {"date": "2026-10-26", "kind": "docs", "category": "Spam policy", "title": "Updated guidance on x \u2014 with a dash", "url": "https://developers.google.com/search/docs/x",
        "summary": "Updated the guide. Why: To help."}
BLOG = {"date": "2026-10-30", "kind": "blog", "category": "Blog", "title": "A post", "url": "https://developers.google.com/search/blog/2026/10/y", "summary": ""}


def doc(ranking=(ENDED,), data_date="2026-11-03", **over):
    ranking = copy.deepcopy(list(ranking))      # tests mutate what they get; the module-level rows must stay clean
    rolling = [{k: r[k] for k in ("name", "type", "started", "url")} for r in ranking if r["type"] in ("core", "spam") and r["ended"] is None]
    d = {"schema": 1, "data_date": data_date, "window": {"from": "2025-11-03", "to": "2026-11-03"},
         "attribution": {"text": "Source: Google Search Central, CC BY 4.0", "license_url": "https://creativecommons.org/licenses/by/4.0/",
                         "policy_url": "https://developers.google.com/terms/site-policies"},
         "rolling": rolling, "ranking": ranking, "items": copy.deepcopy([BLOG, ITEM])}
    d.update(over)
    return d


def write(tmp_path, d):
    p = tmp_path / "google-updates.json"
    p.write_text(json.dumps(d), encoding="utf-8")
    return p


def render(d, noindex=True):
    return gu.page_html(d, NAMES, noindex=noindex, today=TODAY, bar_links="", repo_note="Source code: x.")


def test_valid_file_loads(tmp_path):
    assert gu.load(write(tmp_path, doc()))["ranking"][0]["name"] == "September 2026 spam update"


@pytest.mark.parametrize("where, key", [("top", "run"), ("top", "sources"), ("ranking", "impact"), ("ranking", "alignment"), ("rolling", "tags"),
                                        ("items", "impact"), ("items", "alignment"), ("items", "tags"), ("items", "source"), ("items", "id")])
def test_internal_fields_stop_the_build(tmp_path, where, key):
    d = doc((ROLLING,))
    if where == "top":
        d[key] = "x"
    else:
        d[where][0][key] = "x"
    with pytest.raises(gu.DataError):
        gu.load(write(tmp_path, d))


def test_missing_fields_stop_the_build(tmp_path):
    d = doc()
    del d["items"][0]["summary"]
    with pytest.raises(gu.DataError):
        gu.load(write(tmp_path, d))


def test_a_url_outside_google_stops_the_build(tmp_path):
    d = doc()
    d["items"][0]["url"] = "https://example.org/x"
    with pytest.raises(gu.DataError, match="Google URL"):
        gu.load(write(tmp_path, d))


def test_a_category_that_is_not_public_stops_the_build(tmp_path):
    d = doc()
    d["items"][0]["category"] = "core-spam-updates"
    with pytest.raises(gu.DataError, match="public category"):
        gu.load(write(tmp_path, d))


def test_rolling_must_list_every_core_or_spam_update_that_started_and_did_not_end(tmp_path):
    d = doc((ROLLING, ENDED))
    d["rolling"] = []
    with pytest.raises(gu.DataError, match="rolling does not match"):
        gu.load(write(tmp_path, d))
    d = doc((ENDED,))
    d["rolling"] = [{"name": "Ghost", "type": "core", "started": "2026-11-01", "url": "https://status.search.google.com/incidents/Z"}]
    with pytest.raises(gu.DataError, match="rolling does not match"):
        gu.load(write(tmp_path, d))


def test_duration_must_match_the_dates(tmp_path):
    d = doc()
    d["ranking"][0]["duration_days"] = 3
    with pytest.raises(gu.DataError, match="duration_days"):
        gu.load(write(tmp_path, d))
    d = doc((dict(ROLLING, duration_days=3),))
    d["rolling"] = [{k: ROLLING[k] for k in ("name", "type", "started", "url")}]
    with pytest.raises(gu.DataError, match="duration without an end"):
        gu.load(write(tmp_path, d))


def test_banner_no_names_the_last_update_with_both_dates():
    page = render(doc((ENDED, DISCOVER)))
    banner = re.search(r'<section class="gu-banner.*?</section>', page, re.S).group(0)
    assert '<span class="gu-flag">No</span>' in banner
    assert "September 2026 spam update" in banner and "2026-09-24" in banner and "2026-10-08" in banner and "14 days" in banner
    assert "rolling" not in banner.split("gu-meta")[0].replace("rolling out right now", "")


def test_banner_yes_names_the_update_and_its_start_when_there_is_no_end():
    page = render(doc((ROLLING, ENDED)))
    banner = re.search(r'<section class="gu-banner.*?</section>', page, re.S).group(0)
    assert 'gu-banner rolling' in banner and '<span class="gu-flag">Yes</span>' in banner
    assert "November 2026 core update" in banner and "started on 2026-11-01" in banner and "has not published an end date" in banner
    assert "still rolling" in page      # the table row


def test_a_rolling_discover_update_is_in_the_table_but_the_banner_still_says_no():
    discover_open = dict(DISCOVER, started="2026-11-02", ended=None, duration_days=None)
    page = render(doc((discover_open, ENDED)))
    assert '<span class="gu-flag">No</span>' in page
    assert "still rolling" in page


def test_banner_without_any_core_or_spam_update_in_the_window():
    page = render(doc((DISCOVER,)))
    assert "No core or spam update appears in the last 12 months." in page


def test_stale_data_is_said_on_the_page():
    assert "last updated with data from 2026-10-29" in render(doc(data_date="2026-10-29"))
    assert "last updated with data from" not in render(doc(data_date="2026-11-01"))


def test_noindex_until_opened_and_no_script_no_inline_style():
    page = render(doc())
    assert '<meta name="robots" content="noindex,nofollow">' in page
    assert '<meta name="robots" content="index,follow">' in render(doc(), noindex=False)
    assert 'rel="canonical" href="https://nicheclock.elmrank.com/google-updates/"' in page
    assert "<script src=" in page and page.count("<script") == 1      # the page-count beacon only
    assert "ld+json" not in page and "style=" not in page


def test_attribution_and_licence_are_on_the_page():
    page = render(doc())
    assert "Source: Google Search Central, CC BY 4.0" in page
    assert "https://creativecommons.org/licenses/by/4.0/" in page
    assert "Portions of this page are modifications based on work created and" in page
    assert "not affiliated with Google" in page


def test_every_change_links_to_google_and_the_title_is_escaped():
    d = doc()
    d["items"][1]["title"] = "A <b>bold</b> & title"
    page = render(d)
    assert '<a class="gu-title" href="https://developers.google.com/search/docs/x" rel="noopener">A &lt;b&gt;bold&lt;/b&gt; &amp; title</a>' in page
    assert "<b>bold</b>" not in page


def test_none_of_our_own_prose_has_an_em_or_en_dash():
    page = render(doc((ROLLING, ENDED)))
    prose = re.sub(r"<table\b.*?</table>", "", page, flags=re.S)
    assert not re.search("[\u2013\u2014]", prose)
    assert len(gu.TITLE) <= 65 and 100 <= len(gu.DESC) <= 160


def test_the_timeline_bar_sits_at_the_start_date_and_runs_to_today_while_rolling():
    page = render(doc((ROLLING,)))
    span = (TODAY - dt.date(2025, 11, 3)).days + 1
    start = (dt.date(2026, 11, 1) - dt.date(2025, 11, 3)).days
    assert f'viewBox="0 0 {span} 10"' in page
    assert f'<rect x="{start}" y="2" width="{span - 1 - start}" height="6"/>' in page


def test_the_input_is_not_modified_by_rendering():
    d = doc((ROLLING, ENDED))
    before = copy.deepcopy(d)
    render(d)
    assert d == before
