import argparse
import json
from pathlib import Path

import pytest
import yaml
from integ_helpers import FakeServer
from llm_fakes import api_reply
from onboard_fakes import HOST, FakeClient, FakeWiki, site_files

from nicheclock.config import ConfigError, load_config, validate_config
from nicheclock.store import Store
from nicheclock.onboard import cli as C
from nicheclock.onboard import edit as E
from nicheclock.onboard import extract as X
from nicheclock.onboard import llm as LL
from nicheclock.onboard.niches import NicheDraft
from nicheclock.onboard.preview import Item, preview_niches, render


def draft(name="n", kw=("snowy owl", "free download", "kestrel"), ents=("Nuthatch",)):
    return NicheDraft(name=name, keywords=list(kw), entities=list(ents), formats=["tier list"], exclude=["tips"],
                      kind="games", sources=["google_trends"], upcoming_kinds=[], phrases=list(kw))


# ---------------------------------------------------------------- llm profile merge + fake model server


PROPOSAL = {"niches": [
    {"name": "Raptor watching", "keywords": ["Snowy Owl", "boss guide"], "formats": ["boss guide"],
     "exclude": ["free download", "snowy owl"]},
    {"name": "Backyard feeders", "keywords": ["feeder cam", "backyard feeder"], "formats": [], "exclude": []},
    {"name": "Raptor watching", "keywords": ["unrelated"], "formats": [], "exclude": []}]}


@pytest.fixture()
def server():
    srv = FakeServer()
    srv.seen = srv.requests
    srv.reply = PROPOSAL

    def responder(path, js):
        text = json.dumps(srv.reply)
        return api_reply(path, text)

    srv.responder = responder
    with srv:
        yield srv


def test_merge_proposals_enriches_matching_drafts_and_adds_new_niches():
    d = draft("snowy owl", kw=("snowy owl", "kestrel"), ents=("Nuthatch",))
    out = LL.merge_proposals([d], PROPOSAL["niches"], max_niches=4)
    assert [n.name for n in out] == ["Raptor watching", "Backyard feeders"]
    first, second = out
    assert first.keywords == ["snowy owl", "kestrel", "boss guide"]          # draft's terms first, model's added
    assert first.formats == ["tier list", "boss guide"] and first.exclude == ["tips", "free download"]
    assert first.entities == ["Nuthatch"]                                       # untouched
    assert second.keywords == ["feeder cam", "backyard feeder"] and second.sources and second.section == "proposed by your model"
    assert len(LL.merge_proposals([d], PROPOSAL["niches"], max_niches=1)) == 1  # never beyond --max-niches
    assert [n.name for n in LL.merge_proposals([d], [], 4)] == ["snowy owl"]


def test_pages_for_prompt_caps_at_fifty_titles_and_slugs():
    from nicheclock.onboard.crawl import Page, SiteCrawl
    c = SiteCrawl(base="https://x.test", host="x.test")
    c.pages = [Page(f"https://x.test/guides/p{i}", f"Title {i}") for i in range(80)]
    got = LL.pages_for_prompt([c])
    assert len(got) == 50 and got[0] == ("Title 0", "guides/p0")
    assert LL.site_profile([c]) == {}



# ---------------------------------------------------------------- edit


def make_cfg(tmp_path) -> Path:
    raw = {"identity": {"user_agent": "ua/1 (+mailto:a@b.co)", "contact_email": "a@b.co"},
           "projects": [{"name": "p", "urls": [HOST], "niches": [
               {"name": "birds", "keywords": ["bird watching", "bird feeder"], "entities": ["Merlin Bird ID"],
                "formats": ["tier list"], "exclude": ["tips"], "sources": ["google_trends"]},
               {"name": "decks", "keywords": ["starter"], "sources": ["wikipedia"]}]}]}
    path = tmp_path / "nicheclock.yaml"
    path.write_text("# my comment\n" + yaml.safe_dump(raw))
    return path


def test_apply_edits_add_remove_normalised(tmp_path):
    cfg = load_config(make_cfg(tmp_path))
    log = E.apply_edits(cfg, niche="birds", add={"keyword": ["Bird Watching", "local multiplayer"], "source": ["hn"]},
                        remove={"keyword": ["bird feeder"], "exclude": ["tips"]})
    n = cfg.projects[0].niches[0]
    assert n.keywords == ["bird watching", "local multiplayer"]      # "Bird Watching" is a duplicate of "bird watching"; bird feeder removed
    assert n.sources == ["google_trends", "hn"] and n.exclude == []
    assert any("local multiplayer" in ln for ln in log)


def test_apply_edits_rss_toggles_source_and_niche_selection(tmp_path):
    cfg = load_config(make_cfg(tmp_path))
    with pytest.raises(ConfigError, match="several niches"):
        E.apply_edits(cfg, add={"keyword": ["x"]})
    E.apply_edits(cfg, niche="dec", add={"rss": ["https://feed.test/rss"]})
    d = cfg.projects[0].niches[1]
    assert d.rss == ["https://feed.test/rss"] and "rss" in d.sources
    E.apply_edits(cfg, niche="decks", remove={"rss": ["https://feed.test/rss"]})
    assert d.rss == [] and "rss" not in d.sources


def test_apply_edits_validation_and_misc(tmp_path):
    cfg = load_config(make_cfg(tmp_path))
    with pytest.raises(ConfigError, match="unknown source"):
        E.apply_edits(cfg, niche="birds", add={"source": ["reddit"]})
    cfg = load_config(make_cfg(tmp_path))
    with pytest.raises(ConfigError, match="at least one keyword"):
        E.apply_edits(cfg, niche="decks", remove={"keyword": ["starter"]})
    cfg = load_config(make_cfg(tmp_path))
    E.apply_edits(cfg, niche="decks", rename_niche="bnc decks", schedule="weekly", upcoming=False)
    E.apply_edits(cfg, add_niche="new one", add={"keyword": ["foo"]})
    E.apply_edits(cfg, niche="birds", remove_niche=True, add_url=["https://x.test"],
                  add_notify=['{"type": "discord", "webhook_env": "DW"}'])
    p = cfg.projects[0]
    assert [n.name for n in p.niches] == ["bnc decks", "new one"]
    assert p.schedule == "weekly" and p.niches[0].upcoming is False and p.niches[1].keywords == ["foo"]
    assert p.urls == [HOST, "https://x.test"] and p.notify == [{"type": "discord", "webhook_env": "DW"}]
    with pytest.raises(ConfigError, match="JSON"):
        E.apply_edits(cfg, niche="bnc decks", add_notify=["not json"])
    with pytest.raises(ConfigError, match="schedule"):
        E.apply_edits(cfg, niche="bnc decks", schedule="yearly")


def test_edit_file_applies_valid_edit_and_keeps_comments(tmp_path, monkeypatch):
    path = make_cfg(tmp_path)
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv("EDITOR", "fake-editor")

    def run(cmd):
        assert cmd[0] == "fake-editor"
        f = Path(cmd[1])
        f.write_text(f.read_text().replace("starter", "starter\n    - edh"))
        return 0
    assert E.edit_file(path, run=run, interactive=False) is True
    text = path.read_text()
    assert text.startswith("# my comment") and "edh" in text
    assert not validate_config(load_config(path))


def test_edit_file_rejects_invalid_and_keeps_original(tmp_path, monkeypatch):
    path = make_cfg(tmp_path)
    before = path.read_text()
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv("EDITOR", "fake-editor")

    def breaks(cmd):
        Path(cmd[1]).write_text(Path(cmd[1]).read_text().replace("google_trends", "reddit"))
        return 0
    with pytest.raises(ConfigError, match="unknown source"):
        E.edit_file(path, run=breaks, interactive=False)
    assert path.read_text() == before

    def bad_yaml(cmd):
        Path(cmd[1]).write_text("projects: [unclosed")
        return 0
    with pytest.raises(ConfigError, match="invalid YAML"):
        E.edit_file(path, run=bad_yaml, interactive=False)
    assert path.read_text() == before


def test_edit_file_retry_on_tty(tmp_path, monkeypatch):
    path = make_cfg(tmp_path)
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv("EDITOR", "e")
    calls = []

    def run(cmd):
        f = Path(cmd[1])
        calls.append(1)
        f.write_text(f.read_text().replace("google_trends", "reddit") if len(calls) == 1
                     else path.read_text().replace("tips", "tricks"))
        return 0
    assert E.edit_file(path, run=run, confirm=lambda m: True, interactive=True) is True
    assert len(calls) == 2 and "tricks" in path.read_text()


def test_edit_file_unchanged_and_editor_failure(tmp_path, monkeypatch):
    path = make_cfg(tmp_path)
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv("EDITOR", "e")
    assert E.edit_file(path, run=lambda c: 0, interactive=False) is False
    with pytest.raises(ConfigError, match="status 3"):
        E.edit_file(path, run=lambda c: 3, interactive=False)


# ---------------------------------------------------------------- preview


def test_preview_matches_and_excludes():
    from nicheclock.config import Niche

    n = Niche(name="birds", keywords=["bird watching"], entities=["Merlin Bird ID"], exclude=["lfg"])
    items = [Item("Best bird-watching 2026", "google_trends"), Item("Merlin Bird ID sale", "wikipedia"),
             Item("bird watching lfg tonight", "google_trends"), Item("stock market", "google_trends")]
    (pv,) = preview_niches([n], items, limit=10)
    assert [i.text for i, _ in pv.matched] == ["Best bird-watching 2026", "Merlin Bird ID sale"]
    assert [(i.text, ex) for i, _, ex in pv.excluded] == [("bird watching lfg tonight", ["lfg"])]
    out = render([pv], 4, "test")
    assert "2 would match, 1 excluded" in out and "excluded by: lfg" in out
    (pv,) = preview_niches([n], items, limit=1)
    assert len(pv.matched) == 1 and pv.n_matched == 2


# ---------------------------------------------------------------- cli init end to end


def run_init(monkeypatch, tmp_path, argv, files=None, wiki=None, sub_args=()):
    client = FakeClient(files or site_files(), wiki=wiki or FakeWiki(
        pages={"Snowy Owl": "ok", "Kestrel": "2021 mobile app"}, redirects={"Snowy owl": "Snowy Owl"}))
    monkeypatch.setattr(C, "_make_client", lambda cfg: client)
    monkeypatch.setattr(X, "default_cache_path", lambda: tmp_path / "wiki-cache.json")
    p = argparse.ArgumentParser()
    p.add_argument("--config", default=None)
    sub = p.add_subparsers(dest="cmd")
    C.register(sub)
    args = p.parse_args(["--config", str(tmp_path / "nicheclock.yaml"), *argv])
    return args.func(args), client


def test_init_writes_valid_config(monkeypatch, tmp_path, capsys):
    rc, client = run_init(monkeypatch, tmp_path, [
        "init", "https://birdsite.test", "--user-agent", "ua/1 (+mailto:a@b.co)", "--contact-email", "a@b.co",
        "--max-pages", "20", "--max-niches", "3"])
    assert rc == 0
    cfg = load_config(tmp_path / "nicheclock.yaml")
    assert not validate_config(cfg, require_identity=True)
    (proj,) = cfg.projects
    assert proj.name == "birdsite" and proj.urls == [HOST] and 1 <= len(proj.niches) <= 3
    assert "Snowy Owl" in {e for n in proj.niches for e in n.entities}
    out = capsys.readouterr().out
    assert "niche:" in out and "keywords" in out and "page fetches" in out
    page_fetches = [r for r in client.requests if "/birds/" in r or "/gear/" in r]
    assert len(page_fetches) <= 19


def test_init_refuses_without_identity_and_on_clash(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv(C.ENV_UA, raising=False)
    monkeypatch.delenv(C.ENV_EMAIL, raising=False)
    with pytest.raises(ConfigError, match="identity"):
        run_init(monkeypatch, tmp_path, ["init", HOST])
    ident = ["--user-agent", "ua/1 (+mailto:a@b.co)", "--contact-email", "a@b.co", "--max-pages", "8"]
    assert run_init(monkeypatch, tmp_path, ["init", HOST, *ident])[0] == 0
    assert run_init(monkeypatch, tmp_path, ["init", HOST])[0] == 1                     # project exists
    assert run_init(monkeypatch, tmp_path, ["init", HOST, "--force", "--max-pages", "8"])[0] == 0
    assert run_init(monkeypatch, tmp_path, ["init", HOST, "--project", "other", "--max-pages", "8"])[0] == 0
    assert [p.name for p in load_config(tmp_path / "nicheclock.yaml").projects] == ["birdsite", "other"]


def test_init_dry_run_and_no_url_site(monkeypatch, tmp_path, capsys):
    ident = ["--user-agent", "ua/1 (+mailto:a@b.co)", "--contact-email", "a@b.co", "--max-pages", "6"]
    rc, _ = run_init(monkeypatch, tmp_path, ["init", HOST, "--dry-run", "--no-entities", *ident])
    assert rc == 0 and not (tmp_path / "nicheclock.yaml").exists()
    assert "nothing written" in capsys.readouterr().out
    rc, _ = run_init(monkeypatch, tmp_path, ["init", "https://empty.test", *ident], files={})
    assert rc == 1 and not (tmp_path / "nicheclock.yaml").exists()


def test_init_llm_flag_only_with_llm_config(monkeypatch, tmp_path, capsys, server):
    ident = ["--user-agent", "ua/1 (+mailto:a@b.co)", "--contact-email", "a@b.co", "--max-pages", "10"]
    rc, _ = run_init(monkeypatch, tmp_path, ["init", HOST, "--llm", *ident])
    assert rc == 0 and server.seen == []                                              # no llm: section -> never called
    assert "--llm ignored" in capsys.readouterr().err
    path = tmp_path / "nicheclock.yaml"
    raw = yaml.safe_load(path.read_text())
    raw["llm"] = {"provider": "ollama", "model": "m", "base_url": server.url}
    path.write_text(yaml.safe_dump(raw))
    rc, _ = run_init(monkeypatch, tmp_path, ["init", HOST, "--llm", "--force", "--max-pages", "10"])
    assert rc == 0 and len(server.seen) == 1
    sent = json.dumps(server.seen[0]["json"])
    assert "free download" not in sent and "https://" not in sent.replace(server.url, "")   # titles + slugs + host only
    assert "birds/" in sent and "gear/" in sent                                    # titles and slugs of both sections
    cfg = load_config(path)
    names = [n.name for n in cfg.projects[0].niches]
    assert "Raptor watching" in names and "Backyard feeders" in names
    assert not validate_config(cfg, require_identity=True)
    assert cfg.llm["provider"] == "ollama"                                              # llm section survives rewrite
    assert "proposed 3 niche(s)" in capsys.readouterr().out
    with Store.open(cfg) as st:                                                           # the call is in the ledger
        assert st.db.execute("SELECT task FROM llm_usage").fetchall()[0]["task"] == "profile"


def test_init_llm_failure_keeps_the_rule_based_result(monkeypatch, tmp_path, capsys, server):
    server.reply = {"nonsense": 1}
    ident = ["--user-agent", "ua/1 (+mailto:a@b.co)", "--contact-email", "a@b.co", "--max-pages", "10"]
    run_init(monkeypatch, tmp_path, ["init", HOST, *ident])
    path = tmp_path / "nicheclock.yaml"
    raw = yaml.safe_load(path.read_text())
    raw["llm"] = {"provider": "ollama", "model": "m", "base_url": server.url}
    path.write_text(yaml.safe_dump(raw))
    before = [n.name for n in load_config(path).projects[0].niches]
    rc, _ = run_init(monkeypatch, tmp_path, ["init", HOST, "--llm", "--force", "--max-pages", "10"])
    assert rc == 0 and "--llm failed, keeping the rule-based result" in capsys.readouterr().err
    assert [n.name for n in load_config(path).projects[0].niches] == before


def test_edit_cli_flags(monkeypatch, tmp_path, capsys):
    path = make_cfg(tmp_path)
    p = argparse.ArgumentParser()
    p.add_argument("--config", default=None)
    C.register(p.add_subparsers(dest="cmd"))
    args = p.parse_args(["--config", str(path), "edit", "--niche", "birds", "--add-keyword", "backyard bird watching", "lan",
                         "--remove-keyword", "bird feeder", "--add-rss", "https://f.test/rss", "--schedule", "hourly"])
    assert args.func(args) == 0
    cfg = load_config(path)
    n = cfg.projects[0].niches[0]
    assert n.keywords == ["bird watching", "backyard bird watching", "lan"] and "rss" in n.sources and cfg.projects[0].schedule == "hourly"
    assert "+ birds: keywords += 'backyard bird watching'" in capsys.readouterr().out


def test_items_live_from_trends_and_wikipedia_top():
    from datetime import date

    from nicheclock.onboard.preview import items_live

    rss = ('<rss xmlns:ht="https://trends.google.com/trending/rss"><channel>'
           '<item><title>peregrine falcon</title><ht:approx_traffic>200K+</ht:approx_traffic></item>'
           '<item><title>weather today</title></item></channel></rss>').encode()
    top = json.dumps({"items": [{"articles": [
        {"article": "Main_Page", "rank": 1, "views": 9}, {"article": "Snowy_Owl", "rank": 2, "views": 500},
        {"article": "Special:Search", "rank": 3, "views": 5}]}]}).encode()
    files = {"https://trends.google.com/trending/rss": rss,
             "https://wikimedia.org/api/rest_v1/metrics/pageviews/top/en.wikipedia/all-access/2026/10/30": top}
    items, warns = items_live(FakeClient(files), day=date(2026, 10, 31))
    assert warns == []
    assert [(i.text, i.source) for i in items] == [("peregrine falcon", "google_trends"), ("weather today", "google_trends"),
                                                   ("Snowy Owl", "wikipedia")]
    # missing days fall back up to three days, and failures become warnings rather than errors
    items, warns = items_live(FakeClient({}), day=date(2026, 10, 31))
    assert items == [] and len(warns) == 4


def test_preview_command_reads_store_via_core_cli(tmp_path, capsys):
    from nicheclock.cli import main
    from nicheclock.models import Signal
    from nicheclock.store import Store

    path = make_cfg(tmp_path)
    cfg = load_config(path)
    with Store.open(cfg) as st:
        st.add_signals("p", "birds", [Signal("google_trends", "bird feeder party games"),
                                      Signal("google_trends", "bird watching lfg tonight"),
                                      Signal("wikipedia", "Unrelated thing")])
    assert main(["-c", str(path), "preview"]) == 0
    out = capsys.readouterr().out
    assert "local store" in out
    assert "+ bird feeder party games" in out and "+ bird watching lfg tonight" in out   # no `lfg` exclude yet
    assert main(["-c", str(path), "edit", "--niche", "birds", "--add-exclude", "lfg"]) == 0
    assert main(["-c", str(path), "preview", "--niche", "birds"]) == 0
    out = capsys.readouterr().out
    assert "- bird watching lfg tonight" in out and "excluded by: lfg" in out
    assert main(["-c", str(path), "preview", "--niche", "nope"]) == 2


def _feed_xml():
    from email.utils import format_datetime
    from datetime import datetime, timezone
    now = format_datetime(datetime.now(timezone.utc))
    heads = ["Snowy Owl boss guide", "Peregrine Falcon tier list", "Kestrel patch notes", "Nuthatch review", "Chickadee builds",
             "Trailview model review", "Ridgeline beginner checklist", "Meadowlark starter kit", "Harborview waterproofing guide"]
    items = "".join(f"<item><title>{h}</title><link>https://x/{i}</link><pubDate>{now}</pubDate></item>"
                    for i, h in enumerate(heads))
    return f'<rss version="2.0"><channel><title>Birding news</title>{items}</channel></rss>'.encode()


def _site_with_links():
    files = site_files()
    for u in list(files):
        if "/birds/" in u or "/gear/" in u:
            body = files[u].decode().replace("<body>", '<body><a href="https://gameblog.test/post">x</a>'
                                             '<a href="https://www.reddit.com/r/x">r</a><a href="https://twitter.com/x">t</a>')
            if "/gear/" in u:
                body = body.replace("gameblog.test", "deckblog.test")
            files[u] = body.encode()
    for dom in ("gameblog.test", "deckblog.test"):
        files[f"https://{dom}/"] = (f'<html><head><link rel="alternate" type="application/rss+xml" '
                                    f'href="https://{dom}/feed.xml"></head></html>').encode()
        files[f"https://{dom}/feed.xml"] = _feed_xml()
    files["https://www.reddit.com/"] = b'<link rel="alternate" type="application/rss+xml" href="/r.rss">'
    return files


def test_init_writes_discovered_feeds_with_terms_note(monkeypatch, tmp_path, capsys):
    ident = ["--user-agent", "ua/1 (+mailto:a@b.co)", "--contact-email", "a@b.co", "--max-pages", "30", "--no-entities"]
    rc, client = run_init(monkeypatch, tmp_path, ["init", HOST, *ident], files=_site_with_links())
    assert rc == 0
    path = tmp_path / "nicheclock.yaml"
    text = path.read_text()
    assert text.count(C.RSS_NOTE) >= 1 and "responsible for each feed's terms" in text
    cfg = load_config(path)
    assert not validate_config(cfg, require_identity=True)
    feeds = {u for n in cfg.projects[0].niches for u in n.rss}
    assert feeds == {"https://gameblog.test/feed.xml", "https://deckblog.test/feed.xml"}
    assert all("rss" in n.sources for n in cfg.projects[0].niches if n.rss)
    assert not any("reddit" in r or "twitter" in r for r in client.requests)
    out = capsys.readouterr().out
    assert "you are responsible for each feed's terms" in out and "feeds.xml" not in out


def test_init_no_feeds_flag_and_edit_remove_rss(monkeypatch, tmp_path, capsys):
    ident = ["--user-agent", "ua/1 (+mailto:a@b.co)", "--contact-email", "a@b.co", "--max-pages", "30", "--no-entities"]
    rc, client = run_init(monkeypatch, tmp_path, ["init", HOST, "--no-feeds", *ident], files=_site_with_links())
    assert rc == 0 and not any("gameblog" in r for r in client.requests)
    assert all(not n.rss for n in load_config(tmp_path / "nicheclock.yaml").projects[0].niches)
    rc, _ = run_init(monkeypatch, tmp_path, ["init", HOST, "--force", *ident], files=_site_with_links())
    path = tmp_path / "nicheclock.yaml"
    cfg = load_config(path)
    niche = next(n for n in cfg.projects[0].niches if n.rss)
    victim = niche.rss[0]
    p = argparse.ArgumentParser()
    p.add_argument("--config", default=None)
    C.register(p.add_subparsers(dest="cmd"))
    args = p.parse_args(["--config", str(path), "edit", "--niche", niche.name, "--remove-rss", victim])
    assert args.func(args) == 0
    after = next(n for n in load_config(path).projects[0].niches if n.name == niche.name)
    assert victim not in after.rss
    out = capsys.readouterr().out
    assert niche.name in out
    if not after.rss:
        assert "rss" not in after.sources


def test_preview_shows_feeds_for_confirmation(tmp_path, capsys):
    from nicheclock.cli import main

    raw = {"identity": {"user_agent": "ua/1 (+mailto:a@b.co)", "contact_email": "a@b.co"},
           "projects": [{"name": "p", "urls": [HOST], "niches": [
               {"name": "birds", "keywords": ["bird watching"], "sources": ["google_trends", "rss"],
                "rss": ["https://gameblog.test/feed.xml"]}]}]}
    path = tmp_path / "nicheclock.yaml"
    path.write_text(yaml.safe_dump(raw))
    from nicheclock.models import Signal
    from nicheclock.store import Store
    with Store.open(load_config(path)) as st:
        st.add_signals("p", "birds", [Signal("google_trends", "bird watching night")])
    assert main(["-c", str(path), "preview"]) == 0
    out = capsys.readouterr().out
    assert "you are responsible for each feed's terms" in out and "* https://gameblog.test/feed.xml" in out


def test_annotate_rss_is_idempotent_and_skips_empty_lists():
    text = "projects:\n- niches:\n  - name: a\n    rss: []\n  - name: b\n    rss:\n    - https://f.test/x\n"
    once = C.annotate_rss(text)
    assert once.count(C.RSS_NOTE) == 1 and "    " + C.RSS_NOTE in once
    assert C.annotate_rss(once) == once


def test_init_watch_feeds_go_to_watch_list_and_edit_can_manage_them(monkeypatch, tmp_path, capsys):
    files = _site_with_links()
    files["https://industry.test/"] = (b'<link rel="alternate" type="application/rss+xml" href="/feed.xml">')
    files["https://industry.test/feed.xml"] = _feed_xml().replace(b"Birding news", b"Giveaway deals")   # watch keeps even deal-named feeds
    ident = ["--user-agent", "ua/1 (+mailto:a@b.co)", "--contact-email", "a@b.co", "--max-pages", "30", "--no-entities"]
    rc, client = run_init(monkeypatch, tmp_path, ["init", HOST, *ident, "--watch", "https://industry.test",
                                                  "https://www.reddit.com/r/games", HOST], files=files)
    assert rc == 0
    path = tmp_path / "nicheclock.yaml"
    cfg = load_config(path)
    assert not validate_config(cfg, require_identity=True)
    for n in cfg.projects[0].niches:
        assert n.watch == ["https://industry.test/feed.xml"] and "rss" in n.sources
        assert "https://industry.test/feed.xml" not in n.rss
    assert not any("reddit" in r for r in client.requests)
    assert C.RSS_NOTE in path.read_text()
    cap = capsys.readouterr()
    assert "never-ship" in cap.err and "your own site" in cap.err and "watch  https://industry.test/feed.xml" in cap.out
    p = argparse.ArgumentParser()
    p.add_argument("--config", default=None)
    C.register(p.add_subparsers(dest="cmd"))
    name = cfg.projects[0].niches[0].name
    args = p.parse_args(["--config", str(path), "edit", "--niche", name, "--remove-watch", "https://industry.test/feed.xml"])
    assert args.func(args) == 0
    n0 = load_config(path).projects[0].niches[0]
    assert n0.watch == [] and (n0.rss or "rss" not in n0.sources)


def test_context_is_written_to_config(monkeypatch, tmp_path):
    # "Kestrel" is described as a city here, so it is a broad entity: context, not entities
    wiki = FakeWiki(pages={"Snowy Owl": "2022 mobile app", "Kestrel": "City in Norway"}, redirects={"Snowy owl": "Snowy Owl"})
    ident = ["--user-agent", "ua/1 (+mailto:a@b.co)", "--contact-email", "a@b.co", "--max-pages", "30"]
    rc, _ = run_init(monkeypatch, tmp_path, ["init", HOST, *ident, "--no-feeds"], wiki=wiki)
    assert rc == 0
    cfg = load_config(tmp_path / "nicheclock.yaml")
    assert not validate_config(cfg, require_identity=True)
    niches = cfg.projects[0].niches
    assert "Kestrel" in {c for n in niches for c in n.context}
    assert "Kestrel" not in {e for n in niches for e in n.entities}
    assert "Snowy Owl" in {e for n in niches for e in n.entities}
    assert "context:" in (tmp_path / "nicheclock.yaml").read_text()


def test_init_suggests_watch_only_between_5_and_10_percent_and_keeps_named_feeds_global(monkeypatch, tmp_path):
    from email.utils import format_datetime
    from datetime import datetime, timezone
    now = format_datetime(datetime.now(timezone.utc))

    def feed(on, total=20):
        titles = [f"Snowy Owl story {i}" if i < on else f"Other {i}" for i in range(total)]
        items = "".join(f"<item><title>{t}</title><link>https://x/{i}</link><pubDate>{now}</pubDate></item>"
                        for i, t in enumerate(titles))
        return f'<rss version="2.0"><channel><title>C</title>{items}</channel></rss>'.encode()
    files = site_files()
    for u in list(files):
        if "/birds/" in u or "/gear/" in u:
            body = files[u].decode().replace("<body>", "<body>" + "".join(
                f'<a href="https://{d}.test/x">x</a>' for d in ("strong", "mid", "weak")))
            files[u] = body.encode()
    for dom, on in (("strong", 6), ("mid", 1), ("weak", 0)):          # 30%, 5%, 0% overlap
        files[f"https://{dom}.test/"] = f'<link rel="alternate" type="application/rss+xml" href="/feed.xml">'.encode()
        files[f"https://{dom}.test/feed.xml"] = feed(on)
    files["https://named.test/"] = b'<link rel="alternate" type="application/rss+xml" href="/feed.xml">'
    files["https://named.test/feed.xml"] = feed(0)
    ident = ["--user-agent", "ua/1 (+mailto:a@b.co)", "--contact-email", "a@b.co", "--max-pages", "30", "--no-entities"]
    rc, _ = run_init(monkeypatch, tmp_path, ["init", HOST, *ident, "--watch", "https://named.test"], files=files)
    assert rc == 0
    cfg = load_config(tmp_path / "nicheclock.yaml")
    assert not validate_config(cfg, require_identity=True)
    niches = cfg.projects[0].niches
    for n in niches:
        assert n.watch[0] == "https://named.test/feed.xml"            # named feeds first and in every niche
        assert "https://weak.test/feed.xml" not in n.rss + n.watch     # 0% overlap: dropped
        assert "https://mid.test/feed.xml" not in n.rss                # 5-10%: never in rss
    games = next(n for n in niches if "snowy owl" in n.keywords)
    assert "https://strong.test/feed.xml" in games.rss                 # >= 10%: rss
    assert "https://mid.test/feed.xml" in games.watch                  # 5%: suggested watch
