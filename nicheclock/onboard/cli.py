"""`nicheclock init | preview | edit`. Core wires this module through `register(subparsers)`."""
from __future__ import annotations

from nicheclock.i18n import t as _t

import argparse
import os
import sys
import re
import time
from dataclasses import replace
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

from nicheclock.config import (
    Config,
    ConfigError,
    Identity,
    config_from_dict,
    load_config,
    resolve_config_path,
    save_config,
    validate_config,
)

from . import edit as edit_mod
from . import feeds as feeds_mod
from . import llm as llm_mod
from .crawl import MAX_PAGES, crawl_site
from .extract import EntityResolver, load_background
from .niches import NicheDraft, project_dict, project_from_crawls
from .preview import items_from_store, items_live, preview_niches, render, render_feeds

ENV_UA = "NICHECLOCK_USER_AGENT"
ENV_EMAIL = "NICHECLOCK_CONTACT_EMAIL"


def _make_client(cfg: Config):
    from nicheclock.http import Client

    return Client(cfg)


def _say(msg: str = "") -> None:
    print(msg, flush=True)


def _err(msg: str) -> None:
    print(_t("onboard.err", message=msg), file=sys.stderr)


def _project_name(url: str) -> str:
    from .crawl import normalize_base

    host = normalize_base(url).split("//", 1)[1].removeprefix("www.")
    label = host.split(".")[0] if "." in host else host
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in label) or "site"


RSS_NOTE = ("# You are responsible for each feed's terms of use. Drop one with: "
            "nicheclock edit --niche NAME --remove-rss URL")


def annotate_rss(text: str) -> str:
    """Put the terms-of-use note above every non-empty `rss:` list (comments do not survive yaml.safe_dump)."""
    lines = text.split("\n")
    out: list[str] = []
    for i, ln in enumerate(lines):
        m = re.match(r"^(\s*)(?:rss|watch):\s*$", ln)
        if m and i + 1 < len(lines) and lines[i + 1].lstrip().startswith("- ") and not (
                out and out[-1].strip() == RSS_NOTE):
            out.append(m.group(1) + RSS_NOTE)
        out.append(ln)
    return "\n".join(out)


def _save(cfg: Config, path: Path) -> None:
    save_config(cfg, path)
    text = path.read_text(encoding="utf-8")
    path.write_text(annotate_rss(text), encoding="utf-8")


# ================================================================ init


def _load_or_new(path: Path, args: argparse.Namespace) -> Config:
    if path.exists():
        cfg = load_config(path)
    else:
        cfg = Config(base_dir=path.resolve().parent, path=path.resolve())
    ua = args.user_agent or os.environ.get(ENV_UA)
    email = args.contact_email or os.environ.get(ENV_EMAIL)
    if ua or email:
        cfg.identity = Identity(ua or cfg.identity.user_agent, email or cfg.identity.contact_email)
    if not cfg.identity.is_set():
        raise ConfigError(
            "init reads your site, so it needs an identity first: pass --user-agent and --contact-email "
            f"(or set {ENV_UA} / {ENV_EMAIL}, or fill `identity:` in {path}). There is no default identity.")
    return cfg


def _group_urls(args: argparse.Namespace) -> list[tuple[str, list[str]]]:
    if args.project:
        return [(args.project, list(args.urls))]
    groups: dict[str, list[str]] = {}
    for u in args.urls:
        groups.setdefault(_project_name(u), []).append(u)
    return list(groups.items())


def describe_niche(d: NicheDraft, top: int = 10) -> str:
    lines = [_t("onboard.describe.niche", name=d.name, kind=d.kind),
             _t("onboard.describe.keywords", count=len(d.keywords), items=", ".join(d.keywords[:top]) or "-")]
    if d.entities:
        lines.append(_t("onboard.describe.entities", count=len(d.entities), items=", ".join(d.entities[:top])))
    if d.context:
        lines.append(_t("onboard.describe.context", count=len(d.context), items=", ".join(d.context[:top])))
    lines += [_t("onboard.describe.formats", items=", ".join(d.formats) or "-"),
              _t("onboard.describe.exclude", items=", ".join(d.exclude) or "-"),
              _t("onboard.describe.sources", items=", ".join(d.sources)),
              _t("onboard.describe.upcoming", items=", ".join(d.upcoming_kinds))]
    if d.section:
        lines.insert(1, _t("onboard.describe.section", section=d.section))
    if d.feeds or d.watch:
        lines.append(_t("onboard.describe.feeds"))
        lines += [_t("onboard.describe.feed_rss", url=f.url, domain=f.domain, items=f.items,
                     overlap=f"{f.overlap:.0%}") for f in d.feeds]
        lines += [_t("onboard.describe.feed_watch", suggested=_t("onboard.describe.suggested") if f.suggested else "",
                     url=f.url, domain=f.domain, items=f.items, overlap=f"{f.overlap:.0%}") for f in d.watch]
    return "\n".join(lines)


def _llm_profile(cfg: Config, name: str, crawls: list, drafts: list[NicheDraft], max_niches: int) -> list[NicheDraft]:
    """Merge the niches YOUR model proposes from <=50 page titles + slugs into the rule-based drafts."""
    from nicheclock.llm import LLMError, open_session
    from nicheclock.llm.tasks import profile as profile_task
    from nicheclock.store import Store

    try:
        with Store.open(cfg) as store:
            session = open_session(cfg, store, name)
            proposals = profile_task.propose(session, llm_mod.site_info(crawls), llm_mod.pages_for_prompt(crawls))
            used = session.summary()
    except LLMError as e:
        _err(_t("onboard.llm_failed", name=name, error=e))
        return drafts
    merged = llm_mod.merge_proposals(drafts, proposals, max_niches)
    _say(_t("onboard.llm_proposed", name=name, provider=used["provider"], count=len(proposals),
            tokens_in=used["input_tokens"], tokens_out=used["output_tokens"], merged=len(merged)))
    return merged


def cmd_init(args: argparse.Namespace) -> int:
    path = resolve_config_path(getattr(args, "config", None))
    cfg = _load_or_new(path, args)
    client = _make_client(cfg)
    bg = load_background(args.background)
    if not bg.df:
        _err(_t("onboard.bg_missing", meta=bg.meta))
    resolver = None if args.no_entities else EntityResolver(client, args.entity_cache)
    backend = "sklearn" if args.sklearn else "auto"
    use_llm = bool(args.llm)
    if use_llm and not cfg.llm:
        _err(_t("onboard.llm_ignored"))
        use_llm = False

    new_projects = []
    for name, urls in _group_urls(args):
        t0 = time.monotonic()
        crawls = []
        for u in urls:
            _say(_t("onboard.reading", name=name, url=u))
            c = crawl_site(client, u, max_pages=args.max_pages)
            crawls.append(c)
            _say(_t("onboard.crawled", name=name, urls=len(c.urls), sitemaps=len(c.sitemaps), fetches=c.fetches,
                    usable=len(c.pages), seconds=c.seconds))
            for w in c.warnings[:5]:
                _say(_t("onboard.crawl_note", name=name, warning=w))
        if not any(c.urls for c in crawls):
            _err(_t("onboard.no_urls", name=name))
            continue
        proj, drafts, docs = project_from_crawls(
            name, crawls, bg, resolver, backend=backend, max_niches=args.max_niches,
            entity_limit=args.entity_limit)
        if not drafts:
            _err(_t("onboard.nothing_to_extract", name=name))
            continue
        if not args.no_feeds:
            site_links: Counter = Counter()
            for c in crawls:
                site_links += c.link_counts()
            own = {c.host.removeprefix("www.") for c in crawls}
            doms = feeds_mod.top_domains(site_links, 15, exclude=own)
            _say(_t("onboard.feeds_linked", name=name, count=len(doms)))
            found = feeds_mod.discover_feeds(client, [d for d, _ in doms], own=own)
            watch: list = []
            if args.watch:
                _say(_t("onboard.feeds_watched", name=name, count=len(args.watch)))
                watch, problems = feeds_mod.discover_watch(client, args.watch, own=own)
                for pr in problems:
                    _err(_t("onboard.watch_warning", name=name, warning=pr))
            for d in drafts:
                niche_links: Counter = Counter()
                for c in crawls:
                    niche_links += c.link_counts(set(d.urls))
                cands = feeds_mod.feeds_for_niche(found, niche_links, site_links, d.keywords, d.entities,
                                                  args.max_feeds * 3, min_overlap=feeds_mod.WATCH_OVERLAP)
                d.feeds = [f for f in cands if f.overlap >= feeds_mod.MIN_OVERLAP][: args.max_feeds]
                # 5-10% on topic: not good enough for `rss:`, worth a lower-floor `watch:` suggestion
                sugg = [replace(f, watch=True, suggested=True) for f in cands if f.overlap < feeds_mod.MIN_OVERLAP][:3]
                # feeds the user named with --watch are global: in every niche, shown with that niche's overlap
                named = [replace(f, overlap=feeds_mod.overlap(f, d.keywords, d.entities)) for f in watch]
                d.watch = named + [f for f in sugg if all(f.url != g.url for g in named)]
        if use_llm:
            drafts = _llm_profile(cfg, name, crawls, drafts, args.max_niches)
        proj = project_dict(name, [c.base for c in crawls], drafts)
        prof = llm_mod.site_profile(crawls)
        if prof:
            proj["profile"] = prof
        _say(_t("onboard.analysed", name=name, pages=len(docs), niches=len(drafts), seconds=time.monotonic() - t0))
        for d in drafts:
            _say(describe_niche(d))
        new_projects.append(proj)
    if not new_projects:
        return 1

    existing = {p.name for p in cfg.projects}
    clash = [p["name"] for p in new_projects if p["name"] in existing]
    if clash and not args.force:
        _err(_t("onboard.clash", names=", ".join(clash), path=path))
        return 1
    added = config_from_dict({"projects": new_projects}).projects
    cfg.projects = [p for p in cfg.projects if p.name not in set(clash)] + added
    errs = [e for e in validate_config(cfg, require_identity=True)]
    if errs:
        _err(_t("onboard.invalid_generated") + "\n  - " + "\n  - ".join(errs))
        return 1
    if args.preview:
        _run_preview(cfg, client, [p.name for p in added], days=7, live=True, niche=None, limit=10)
    if args.dry_run:
        _say(_t("onboard.dry_run") + annotate_rss(yaml.safe_dump(cfg.to_dict(), sort_keys=False, allow_unicode=True, width=100)))
        return 0
    _save(cfg, path)
    _say(_t("onboard.wrote", path=path, count=len(added)))
    return 0


# ================================================================ preview


def _run_preview(cfg: Config, client: Any, project_names: list[str], *, days: int, live: bool,
                 niche: str | None, limit: int) -> int:
    for pname in project_names:
        proj = cfg.project(pname)
        niches = proj.niches if niche is None else [edit_mod.pick_niche(proj, niche)]
        items, origin = [], ""
        if not live:
            items = items_from_store(cfg, pname, days)
            origin = f"the last {days} days of the local store ({len(items)} signals)"
        if not items:
            if not cfg.identity.is_set():
                raise ConfigError("no stored signals yet and identity is not set: fill identity.user_agent and "
                                  "identity.contact_email so preview may pull Google Trends + Wikipedia live")
            if client is None:
                client = _make_client(cfg)
            items, warns = items_live(client)
            for w in warns:
                _err(_t("onboard.note", warning=w))
            origin = "a live pull of Google Trends trending + Wikipedia top articles"
        _say(_t("onboard.preview_project", name=pname))
        _say(render(preview_niches(niches, items, limit), len(items), origin, niches))
    return 0


def cmd_preview(args: argparse.Namespace) -> int:
    from nicheclock.cli import load_for

    cfg = load_for(args, require_identity=False)
    names = [p.name for p in cfg.selected(args.project)]
    client = _make_client(cfg) if args.live or cfg.identity.is_set() else None
    return _run_preview(cfg, client, names, days=args.days, live=args.live, niche=args.niche, limit=args.limit)


# ================================================================ edit


def cmd_edit(args: argparse.Namespace) -> int:
    path = resolve_config_path(getattr(args, "config", None))
    add = {stem: getattr(args, f"add_{stem}") for stem in edit_mod.LIST_FIELDS if getattr(args, f"add_{stem}")}
    remove = {stem: getattr(args, f"remove_{stem}") for stem in edit_mod.LIST_FIELDS
              if getattr(args, f"remove_{stem}")}
    upcoming = None if args.upcoming is None else bool(args.upcoming)
    noninteractive = bool(add or remove or args.rename_niche or args.add_niche or args.remove_niche
                          or args.schedule or upcoming is not None or args.add_url or args.remove_url
                          or args.add_notify)
    if not path.exists():
        raise ConfigError(f"config not found: {path} (run `nicheclock init <url>` first)")
    if noninteractive:
        cfg = load_config(path)
        log = edit_mod.apply_edits(
            cfg, project=args.project, niche=args.niche, add=add, remove=remove,
            rename_niche=args.rename_niche, add_niche=args.add_niche, remove_niche=args.remove_niche,
            schedule=args.schedule, upcoming=upcoming, add_url=args.add_url or (),
            remove_url=args.remove_url or (), add_notify=args.add_notify or ())
        if not log:
            _say(_t("onboard.nothing_to_change"))
            return 0
        _save(cfg, path)
        for ln in log:
            _say(ln)
        if any(stem in ("rss", "watch") for stem in list(add) + list(remove)):
            proj = cfg.project(args.project)
            n = edit_mod.pick_niche(proj, args.niche or args.add_niche)
            _say(_t("onboard.niche_header", name=n.name))
            for ln in render_feeds(n):
                _say(ln)
    else:
        changed = edit_mod.edit_file(path)
        _say(_t("onboard.updated", path=path) if changed else _t("onboard.no_changes"))
        cfg = load_config(path)
    if args.preview:
        errs = validate_config(cfg)
        if errs:
            raise ConfigError("invalid config:\n  - " + "\n  - ".join(errs))
        _run_preview(cfg, None, [p.name for p in cfg.selected(args.project)], days=7, live=False,
                     niche=args.niche, limit=10)
    return 0


# ================================================================ wiring


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("init", help=_t("onboard.help.init.init_cmd"))
    p.add_argument("urls", nargs="+", metavar="URL")
    p.add_argument("--project", help=_t("onboard.help.init.project"))
    p.add_argument("--user-agent", help=_t("onboard.help.init.user_agent", env=ENV_UA))
    p.add_argument("--contact-email", help=_t("onboard.help.init.contact_email", env=ENV_EMAIL))
    p.add_argument("--max-pages", type=int, default=MAX_PAGES, help=_t("onboard.help.init.max_pages"))
    p.add_argument("--max-niches", type=int, default=4, choices=range(1, 5), metavar="1-4")
    p.add_argument("--watch", action="extend", nargs="+", metavar="URL",
                   help=_t("onboard.help.init.watch"))
    p.add_argument("--no-feeds", action="store_true", help=_t("onboard.help.init.no_feeds"))
    p.add_argument("--max-feeds", type=int, default=5, help=_t("onboard.help.init.max_feeds"))
    p.add_argument("--entity-limit", type=int, default=30, help=_t("onboard.help.init.entity_limit"))
    p.add_argument("--no-entities", action="store_true", help=_t("onboard.help.init.no_entities"))
    p.add_argument("--entity-cache", help=_t("onboard.help.init.entity_cache"))
    p.add_argument("--background", help=_t("onboard.help.init.background"))
    p.add_argument("--sklearn", action="store_true", help=_t("onboard.help.init.sklearn"))
    p.add_argument("--llm", action="store_true", help=_t("onboard.help.init.llm"))
    p.add_argument("--preview", action="store_true", help=_t("onboard.help.init.preview"))
    p.add_argument("--dry-run", action="store_true", help=_t("onboard.help.init.dry_run"))
    p.add_argument("--force", action="store_true", help=_t("onboard.help.init.force"))
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("preview", help=_t("onboard.help.preview.preview_cmd"))
    p.add_argument("--project")
    p.add_argument("--niche")
    p.add_argument("--days", type=int, default=7, help=_t("onboard.help.preview.days"))
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--live", action="store_true", help=_t("onboard.help.preview.live"))
    p.set_defaults(func=cmd_preview)

    p = sub.add_parser("edit", help=_t("onboard.help.edit.edit_cmd"))
    p.add_argument("--project")
    p.add_argument("--niche", help=_t("onboard.help.edit.niche"))
    for stem, fld in edit_mod.LIST_FIELDS.items():
        p.add_argument(f"--add-{stem}", action="extend", nargs="+", metavar="V", help=_t("onboard.help.edit.add", field=fld))
        p.add_argument(f"--remove-{stem}", action="extend", nargs="+", metavar="V", help=_t("onboard.help.edit.remove", field=fld))
    p.add_argument("--add-niche", metavar="NAME")
    p.add_argument("--rename-niche", metavar="NEW")
    p.add_argument("--remove-niche", action="store_true")
    p.add_argument("--schedule", choices=("daily", "hourly", "weekly"))
    p.add_argument("--upcoming", dest="upcoming", action="store_true", default=None)
    p.add_argument("--no-upcoming", dest="upcoming", action="store_false")
    p.add_argument("--add-url", action="extend", nargs="+", metavar="URL")
    p.add_argument("--remove-url", action="extend", nargs="+", metavar="URL")
    p.add_argument("--add-notify", action="append", metavar="JSON", help=_t("onboard.help.edit.add_notify"))
    p.add_argument("--preview", action="store_true", help=_t("onboard.help.edit.preview"))
    p.set_defaults(func=cmd_edit)
