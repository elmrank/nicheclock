"""Core's own subcommands: doctor (run, serve are added by later commits)."""

from __future__ import annotations

import argparse
import importlib
import sys
from datetime import date
from urllib.parse import urlsplit

from nicheclock import regions as rg
from nicheclock.config import (ConfigError, load_config, resolve_config_path, validate_config)
from nicheclock.i18n import t

# One representative URL per source host, used by `doctor` for the robots.txt reachability check.
SOURCE_PROBES = {
    "google_trends": "https://trends.google.com/trending/rss?geo=US",
    "wikipedia": "https://wikimedia.org/api/rest_v1/metrics/pageviews/top/en.wikipedia/all-access/2026/01/01",
    "hn": "https://hn.algolia.com/api/v1/search",
    "github": "https://api.github.com/search/repositories",
    "apple": "https://rss.marketingtools.apple.com/api/v2/us/apps/top-free/10/apps.json",
    "gdelt": "https://api.gdeltproject.org/api/v2/doc/doc",
    "federal_register": "https://www.federalregister.gov/api/v1/documents.json",
}


def cmd_doctor(args: argparse.Namespace) -> int:
    from nicheclock.http import Client, FetchError, IdentityError

    path = resolve_config_path(args.config)
    bad = 0

    def line(ok: bool | None, msg: str) -> None:
        nonlocal bad
        mark = t({True: "doctor.mark.ok", False: "doctor.mark.fail", None: "doctor.mark.warn"}[ok])
        if ok is False:
            bad += 1
        print(f"[{mark}] {msg}")

    try:
        cfg = load_config(path)
    except ConfigError as e:
        line(False, str(e))
        return 1
    line(True, t("doctor.config", path=path))
    errs = validate_config(cfg)
    for e in errs:
        line(False, e)
    if not errs:
        n = sum(len(p.niches) for p in cfg.projects)
        line(True, t("doctor.valid", projects=len(cfg.projects), niches=n))
    line(cfg.identity.is_set() or False,
         t("doctor.identity_set") if cfg.identity.is_set() else t("doctor.identity_unset"))
    line(True, t("doctor.dirs", data=cfg.data_dir, out=cfg.out_dir))
    _doctor_regions(cfg, line)
    try:
        notify = importlib.import_module("nicheclock.notify")
    except ModuleNotFoundError:
        notify = None
    if notify is not None:
        for p in cfg.projects:
            for e in notify.validate_channels(p):
                line(False, t("doctor.notify", project=p.name, problem=e))

    if cfg.identity.is_set() and not args.offline:
        client = Client(cfg)
        probes: dict[str, str] = {}
        for p in cfg.projects:
            for n in p.niches:
                for s in n.sources:
                    if s in SOURCE_PROBES:
                        probes[urlsplit(SOURCE_PROBES[s]).netloc] = SOURCE_PROBES[s]
                for u in n.rss + n.ics:
                    probes[urlsplit(u).netloc] = u
        for host, url in sorted(probes.items()):
            try:
                ok = client.robots_allowed(url)
                line(ok, t("doctor.robots_allows", host=host) if ok
                     else t("doctor.robots_disallows", host=host, path=urlsplit(url).path))
            except (FetchError, IdentityError) as e:
                line(False, t("doctor.robots_error", host=host, error=e))
    return 1 if bad else 0


def _doctor_regions(cfg, line) -> None:
    """What each niche's regions mean for its enabled sources (only for niches that ask for countries)."""
    from nicheclock.sources import load

    for p in cfg.projects:
        for n in p.niches:
            regs, mode = rg.effective(p, n)
            if not rg.countries(regs):
                continue
            parts = []
            for name in n.sources:
                mod = load(name)
                kind = getattr(mod, "REGION_SUPPORT", "global")
                key = "doctor.region_support.us" if getattr(mod, "FIXED_REGION", None) == "US" else f"doctor.region_support.{kind}"
                parts.append(t(key, source=name))
            line(True, t("doctor.regions", project=p.name, niche=n.name, regions=", ".join(regs), mode=mode,
                         support="; ".join(parts)))


def cmd_run(args: argparse.Namespace) -> int:
    from nicheclock.cli import load_for
    from nicheclock.http import Client, IdentityError
    from nicheclock.output import write_index
    from nicheclock.pipeline import run_project
    from nicheclock.store import Store

    cfg = load_for(args, require_identity=True)
    region = args.region.strip().upper() if getattr(args, "region", None) else None
    if region and rg.invalid([region]):
        print(t("cli.error", message=t("run.region_unknown", region=args.region)), file=sys.stderr)
        return 2
    projects = cfg.selected(args.project)
    client = Client(cfg, cache_dir=cfg.data_dir / "cache")
    day = date.fromisoformat(args.day) if args.day else None
    rc = 0
    with Store.open(cfg) as store:
        for p in projects:
            print(f"[{p.name}]")
            try:
                r = run_project(cfg, p, client=client, store=store, day=day, notify=not args.no_notify,
                                log=print, region=region)
            except IdentityError as e:
                print(t("cli.error", message=e), file=sys.stderr)
                return 2
            for niche, per in r.counts.items():
                print(t("run.niche_line", niche=niche, items=r.niche_items(niche),
                        sources=", ".join(f"{k}={v}" for k, v in per.items()) or t("run.no_signals")))
            print(t("run.summary", upcoming=len(r.feed.upcoming), gaps=len(r.feed.gaps),
                    requests=client.requests_made, seconds=r.seconds))
            if r.llm:
                m = r.llm
                cost = f", ~${m['cost_usd_est']:.4f}" if m["cost_usd_est"] else ""
                print(t("run.llm_usage", provider=m["provider"], model=m["model"], calls=m["calls"],
                        tokens_in=m["input_tokens"], tokens_out=m["output_tokens"], cost=cost)
                      + (t("run.llm_not_sent", count=m["not_sent"]) if m.get("not_sent") else ""))
            for n in r.notes:
                print(t("run.note", note=n))
            for e in r.errors:
                print(f"  ! {e}", file=sys.stderr)
            rc = 1 if r.errors and rc == 0 else rc
            print(t("run.wrote", path=cfg.out_dir / p.name))
        write_index([p.name for p in cfg.projects], cfg.out_dir)
    return rc


def cmd_serve(args: argparse.Namespace) -> int:
    import functools
    import http.server

    from nicheclock.cli import load_for

    cfg = load_for(args)
    cfg.out_dir.mkdir(parents=True, exist_ok=True)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(cfg.out_dir))
    with http.server.ThreadingHTTPServer((args.host, args.port), handler) as srv:
        print(t("serve.serving", path=cfg.out_dir, host=args.host, port=args.port))
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    r = sub.add_parser("run", help=t("cli.help.run"))
    r.add_argument("--project", help=t("cli.help.project"))
    r.add_argument("--no-notify", action="store_true", help=t("cli.help.no_notify"))
    r.add_argument("--day", help=t("cli.help.day"))
    r.add_argument("--region", help=t("cli.help.region"))
    r.set_defaults(func=cmd_run)
    s = sub.add_parser("serve", help=t("cli.help.serve"))
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8080)
    s.set_defaults(func=cmd_serve)
    p = sub.add_parser("doctor", help=t("cli.help.doctor"))
    p.add_argument("--offline", action="store_true", help=t("cli.help.offline"))
    p.set_defaults(func=cmd_doctor)
