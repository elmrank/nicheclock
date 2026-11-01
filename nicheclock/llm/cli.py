"""``nicheclock llm test | usage`` and ``nicheclock eval export``."""
from __future__ import annotations

from nicheclock.i18n import t as _t

import argparse
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from nicheclock.llm import LLMError, open_session
from nicheclock.llm.types import messages_of


def _err(msg: str) -> None:
    print(_t("llm.cli.error", message=msg), file=sys.stderr)


def cmd_test(args: argparse.Namespace) -> int:
    from nicheclock.cli import load_for
    from nicheclock.store import Store

    cfg = load_for(args)
    if not cfg.llm:
        _err(_t("llm.cli.no_section"))
        return 2
    with Store.open(cfg) as store:
        try:
            session = open_session(cfg, store, "")
            assert session is not None
            shown = session.settings.describe()
            print(_t("llm.cli.provider", provider=shown["provider"], model=shown["model"], key=shown["key_source"]))
            ok, why = session.provider.available()
            print(_t("llm.cli.available", yes_no=_t("llm.cli.yes") if ok else _t("llm.cli.no"), why=why))
            if not ok:
                return 1
            res = session.call("test", messages_of("You answer with one word.", "Reply with the single word: ok"),
                               max_tokens=64)
        except LLMError as e:
            _err(_t("llm.cli.test_failed", error=e))
            return 1
        cost = _t("llm.cli.cost_unknown") if res.cost_usd_est is None \
            else _t("llm.cli.cost_estimate", cost=res.cost_usd_est)
        print(_t("llm.cli.latency", latency=res.latency_s, tokens_in=res.usage.input,
                 tokens_out=res.usage.output, cost=cost))
        b = cfg.llm.get("daily_budget_usd")
        print(_t("llm.cli.today_spent", spent=session.spent_today())
              + (_t("llm.cli.of_budget", budget=b) if b is not None else _t("llm.cli.no_budget")))
        print(_t("llm.cli.reply", reply=res.text.strip()[:60]))
    return 0


def cmd_usage(args: argparse.Namespace) -> int:
    from nicheclock.cli import load_for
    from nicheclock.llm.session import utc_day
    from nicheclock.store import Store

    cfg = load_for(args)
    since = (datetime.now(timezone.utc).date() - timedelta(days=max(args.days, 1) - 1)).isoformat()
    with Store.open(cfg) as store:
        rows = store.llm_usage(since)
        today = store.llm_spent(utc_day())
    if not rows:
        print(_t("llm.cli.no_calls", since=since))
    else:
        h = _t("llm.cli.table_head").split("|")
        print(f"{h[0]:<11}{h[1]:<13}{h[2]:<26}{h[3]:<9}{h[4]:>6}{h[5]:>10}{h[6]:>9}{h[7]:>10}")
        tot_c = tot_i = tot_o = 0
        tot_cost = 0.0
        unpriced = 0
        for r in rows:
            cost = r["cost_usd_est"]
            print(f"{r['day']:<11}{r['provider']:<13}{r['model'][:25] or _t('llm.cli.default_model'):<26}{r['task']:<9}{r['calls']:>6}"
                  f"{r['input_tokens']:>10}{r['output_tokens']:>9}{('%.4f' % cost) if cost is not None else '?':>10}")
            tot_c += r["calls"]; tot_i += r["input_tokens"]; tot_o += r["output_tokens"]
            tot_cost += cost or 0.0
            unpriced += r["unpriced"] or 0
        print(f"{_t('llm.cli.total'):<59}{tot_c:>6}{tot_i:>10}{tot_o:>9}{tot_cost:>10.4f}")
        if unpriced:
            print(_t("llm.cli.unpriced", count=unpriced))
    b = (cfg.llm or {}).get("daily_budget_usd")
    print(_t("llm.cli.today_utc", today=today)
          + (_t("llm.cli.of_budget", budget=b) + (_t("llm.cli.budget_reached") if today >= b else "")
             if b is not None else _t("llm.cli.no_budget")))
    return 0


def cmd_eval_export(args: argparse.Namespace) -> int:
    from nicheclock.cli import load_for
    from nicheclock.llm import evalexport
    from nicheclock.store import Store

    cfg = load_for(args)
    proj = cfg.project(args.project)
    out = Path(args.out) if args.out else Path(cfg.out_dir) / proj.name / f"eval-{date.today().isoformat()}.csv"
    with Store.open(cfg) as store:
        try:
            path = evalexport.export(store, proj.name, args.days, args.top, out)
        except evalexport.EvalError as e:
            _err(str(e))
            return 1
    print(_t("llm.cli.wrote", path=path))
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("llm", help=_t("llm.cli.help.llm"))
    s = p.add_subparsers(dest="llm_cmd", required=True, metavar="<test|usage>")
    t = s.add_parser("test", help=_t("llm.cli.help.test"))
    t.set_defaults(func=cmd_test)
    u = s.add_parser("usage", help=_t("llm.cli.help.usage"))
    u.add_argument("--days", type=int, default=7, help=_t("llm.cli.help.days"))
    u.set_defaults(func=cmd_usage)

    e = sub.add_parser("eval", help=_t("llm.cli.help.eval"))
    es = e.add_subparsers(dest="eval_cmd", required=True, metavar="<export>")
    x = es.add_parser("export", help=_t("llm.cli.help.export"))
    x.add_argument("--project", help=_t("llm.cli.help.project"))
    x.add_argument("--days", type=int, default=7, help=_t("llm.cli.help.topic_days"))
    x.add_argument("--top", type=int, default=20, help=_t("llm.cli.help.top"))
    x.add_argument("--out", help=_t("llm.cli.help.out"))
    x.set_defaults(func=cmd_eval_export)
