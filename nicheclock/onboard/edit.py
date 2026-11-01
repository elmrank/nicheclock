"""`edit`: $EDITOR round trip with validation, plus non-interactive field edits."""
from __future__ import annotations

from nicheclock.i18n import t as _t

import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Sequence

import yaml

from nicheclock.config import (
    SCHEDULES,
    Config,
    ConfigError,
    Niche,
    Project,
    validate_config,
    validate_dict,
)
from nicheclock.match import normalize

# CLI flag stem -> Niche list field
LIST_FIELDS: dict[str, str] = {
    "keyword": "keywords",
    "entity": "entities",
    "format": "formats",
    "exclude": "exclude",
    "source": "sources",
    "rss": "rss",
    "watch": "watch",
    "ics": "ics",
}


def _same(a: str, b: str) -> bool:
    return a.strip() == b.strip() or (normalize(a) != "" and normalize(a) == normalize(b))


def add_items(values: list[str], new: Sequence[str]) -> list[str]:
    out = list(values)
    for v in new:
        v = v.strip()
        if v and not any(_same(v, x) for x in out):
            out.append(v)
    return out


def remove_items(values: list[str], drop: Sequence[str]) -> list[str]:
    return [x for x in values if not any(_same(x, d) for d in drop)]


def pick_niche(project: Project, name: str | None) -> Niche:
    if not project.niches:
        raise ConfigError(_t("edit.no_niches", project=project.name))
    if name is None:
        if len(project.niches) == 1:
            return project.niches[0]
        raise ConfigError(_t("edit.several_niches", names=", ".join(repr(n.name) for n in project.niches)))
    exact = [n for n in project.niches if n.name == name]
    loose = exact or [n for n in project.niches if name.lower() in n.name.lower()]
    if len(loose) == 1:
        return loose[0]
    if not loose:
        raise ConfigError(_t("edit.no_such_niche", name=name, project=project.name,
                             names=", ".join(repr(n.name) for n in project.niches)))
    raise ConfigError(_t("edit.ambiguous_niche", name=name, names=", ".join(repr(n.name) for n in loose)))


def apply_edits(cfg: Config, *, project: str | None = None, niche: str | None = None,
                add: dict[str, list[str]] | None = None, remove: dict[str, list[str]] | None = None,
                rename_niche: str | None = None, add_niche: str | None = None,
                remove_niche: bool = False, schedule: str | None = None, upcoming: bool | None = None,
                add_url: Sequence[str] = (), remove_url: Sequence[str] = (),
                add_notify: Sequence[str] = ()) -> list[str]:
    """Mutate cfg in place; returns human-readable change lines. Raises ConfigError on bad input."""
    proj = cfg.project(project)
    log: list[str] = []
    if add_niche:
        if proj.niche(add_niche):
            raise ConfigError(_t("edit.niche_exists", name=add_niche))
        proj.niches.append(Niche(name=add_niche, sources=["google_trends", "wikipedia", "gdelt"]))
        log.append(_t("edit.log.add_niche", name=add_niche))
        if niche is None and (add or remove or rename_niche or remove_niche or upcoming is not None):
            niche = add_niche
    needs_niche = any([add, remove, rename_niche, remove_niche, upcoming is not None])
    n = pick_niche(proj, niche) if needs_niche else None
    if n is not None:
        for stem, vals in (add or {}).items():
            fld = LIST_FIELDS[stem]
            before = getattr(n, fld)
            after = add_items(before, vals)
            setattr(n, fld, after)
            log += [_t("edit.log.add_value", niche=n.name, field=fld, value=v) for v in after if v not in before]
            if fld in ("rss", "watch") and after and "rss" not in n.sources:
                n.sources.append("rss")
                log.append(_t("edit.log.add_rss_source", niche=n.name))
        for stem, vals in (remove or {}).items():
            fld = LIST_FIELDS[stem]
            before = getattr(n, fld)
            after = remove_items(before, vals)
            setattr(n, fld, after)
            log += [_t("edit.log.remove_value", niche=n.name, field=fld, value=v) for v in before if v not in after]
            if fld in ("rss", "watch") and not (n.rss or n.watch) and "rss" in n.sources:
                n.sources.remove("rss")
                log.append(_t("edit.log.remove_rss_source", niche=n.name))
        if upcoming is not None:
            n.upcoming = upcoming
            log.append(_t("edit.log.upcoming", niche=n.name, value=upcoming))
        if rename_niche:
            if proj.niche(rename_niche) and proj.niche(rename_niche) is not n:
                raise ConfigError(_t("edit.niche_exists", name=rename_niche))
            log.append(_t("edit.log.rename", old=n.name, new=rename_niche))
            n.name = rename_niche
        if remove_niche:
            proj.niches.remove(n)
            log.append(_t("edit.log.remove_niche", name=n.name))
    if schedule:
        if schedule not in SCHEDULES:
            raise ConfigError(_t("edit.schedule", allowed=", ".join(SCHEDULES)))
        proj.schedule = schedule
        log.append(_t("edit.log.schedule", project=proj.name, value=schedule))
    proj.urls = add_items(proj.urls, add_url)
    log += [_t("edit.log.add_url", project=proj.name, url=u) for u in add_url]
    if remove_url:
        proj.urls = remove_items(proj.urls, remove_url)
        log += [_t("edit.log.remove_url", project=proj.name, url=u) for u in remove_url]
    for raw in add_notify:
        try:
            entry = json.loads(raw)
        except ValueError as e:
            raise ConfigError(_t("edit.notify_json", error=e)) from e
        if not isinstance(entry, dict) or "type" not in entry:
            raise ConfigError(_t("edit.notify_type"))
        proj.notify.append(entry)
        log.append(_t("edit.log.add_notify", project=proj.name, type=entry["type"]))
    errs = validate_config(cfg)
    if errs:
        raise ConfigError("edit would make the config invalid:\n  - " + "\n  - ".join(errs))
    return log


# ---------------------------------------------------------------- $EDITOR


def _editor_cmd() -> list[str]:
    ed = os.environ.get("VISUAL") or os.environ.get("EDITOR") or ""
    if not ed:
        for cand in ("nano", "vim", "vi"):
            from shutil import which

            if which(cand):
                ed = cand
                break
    if not ed:
        raise ConfigError("no editor found: set $EDITOR (or use --add-keyword/--remove-keyword/... flags)")
    return shlex.split(ed)


def validate_text(text: str) -> list[str]:
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as e:
        return [_t("edit.invalid_yaml", error=e)]
    if not isinstance(raw, dict):
        return [_t("edit.root_mapping")]
    return validate_dict(raw)


def edit_file(path: Path, *, run: Callable[[list[str]], int] | None = None,
              confirm: Callable[[str], bool] | None = None, interactive: bool | None = None) -> bool:
    """Open `path` in $EDITOR on a temp copy; write it back only when the result validates.

    Returns True when the file changed. On validation errors the user is asked whether to keep editing
    (tty only); otherwise the original is left untouched and ConfigError is raised with the problems.
    """
    run = run or (lambda cmd: subprocess.call(cmd))
    interactive = sys.stdin.isatty() if interactive is None else interactive
    confirm = confirm or (lambda msg: input(msg + " [Y/n] ").strip().lower() in ("", "y", "yes"))
    original = path.read_text(encoding="utf-8")
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", prefix="nicheclock-edit-", delete=False,
                                     encoding="utf-8") as tmp:
        tmp.write(original)
        tmp_path = Path(tmp.name)
    keep = False
    try:
        while True:
            rc = run(_editor_cmd() + [str(tmp_path)])
            if rc != 0:
                raise ConfigError(_t("edit.editor_status", status=rc, path=path))
            text = tmp_path.read_text(encoding="utf-8")
            errs = validate_text(text)
            if not errs:
                break
            msg = _t("edit.config_invalid") + "\n  - " + "\n  - ".join(errs)
            if interactive:
                print(msg, file=sys.stderr)
                if confirm(_t("edit.keep_editing")):
                    continue
                raise ConfigError(_t("edit.abandoned", path=path))
            keep = True
            raise ConfigError(msg + _t("edit.kept_tmp", tmp=tmp_path, path=path))
        if text == original:
            return False
        staged = path.with_name(path.name + ".tmp")
        staged.write_text(text, encoding="utf-8")
        staged.replace(path)
        return True
    finally:
        if not keep:
            try:
                tmp_path.unlink()
            except OSError:
                pass
