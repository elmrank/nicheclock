"""Push notifications: Telegram, Discord webhook, email (SMTP).

``dispatch(cfg, project, items)`` sends each channel in ``project.notify`` the top-N
not-yet-sent items per niche, then records them in ``<data_dir>/<project>/notify_sent.json``
(per channel, so one failing channel never suppresses another).
"""

from __future__ import annotations

from nicheclock.i18n import t as _t

import importlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from . import format as fmt
from .common import NotifyError, SentLog, channel_id, get, item_key

__all__ = ["dispatch", "validate_channels", "NotifyError", "CHANNELS"]

log = logging.getLogger("nicheclock.notify")

CHANNELS = {"telegram": "telegram", "discord": "discord", "email": "email"}


def _module(chan_type: str):
    if chan_type not in CHANNELS:
        raise NotifyError(_t("notify.unknown_type", type=chan_type, known=sorted(CHANNELS)))
    return importlib.import_module(f"{__name__}.{CHANNELS[chan_type]}")


def _project_obj(cfg: Any, project: Any) -> Any:
    if not isinstance(project, str):
        return project
    for p in get(cfg, "projects", []) or []:
        if get(p, "name") == project:
            return p
    raise NotifyError(_t("notify.project_missing", project=project))


def _channels(project: Any) -> list[dict]:
    out = []
    for c in get(project, "notify", []) or []:
        out.append(dict(c) if isinstance(c, dict) else dict(vars(c)))
    return out


def validate_channels(project: Any) -> list[str]:
    """Static config problems for ``doctor``; never touches the network or secrets."""
    problems: list[str] = []
    for chan in _channels(project):
        try:
            problems += _module(str(chan.get("type"))).validate(chan)
        except NotifyError as e:
            problems.append(str(e))
    return problems


def sent_log_path(cfg: Any, project_name: str) -> Path:
    return Path(str(get(cfg, "data_dir", "./data"))).expanduser() / project_name / "notify_sent.json"


def _select(items: Sequence[Any], chan: dict) -> list[Any]:
    kinds = chan.get("kinds")
    min_score = chan.get("min_score")
    out = []
    for it in items:
        if kinds and it.kind not in kinds:
            continue
        if min_score is not None and (it.score or 0.0) < float(min_score):
            continue
        out.append(it)
    return out


def dispatch(cfg: Any, project: Any, items: Iterable[Any], *, dry_run: bool = False,
             now: datetime | None = None) -> dict[str, Any]:
    """Send new items to every channel in ``project.notify``.

    Returns ``{"sent": n, "skipped": n, "errors": n, "channels": [{...}]}``. With
    ``dry_run`` nothing is sent or recorded and each channel entry carries the rendered
    ``messages``. Failures are reported per channel (never raised, secrets redacted).
    """
    now = now or datetime.now(timezone.utc)
    proj = _project_obj(cfg, project)
    name = str(get(proj, "name"))
    items = list(items)
    channels = _channels(proj)
    result: dict[str, Any] = {"sent": 0, "skipped": 0, "errors": 0, "channels": []}
    if not channels or not items:
        return result

    sent_log = SentLog(sent_log_path(cfg, name))
    day = now.date().isoformat()
    for chan in channels:
        cid = channel_id(chan)
        entry: dict[str, Any] = {"channel": cid, "type": chan.get("type"), "sent": 0,
                                 "skipped": 0, "error": None}
        result["channels"].append(entry)
        try:
            mod = _module(str(chan.get("type")))
            candidates = _select(items, chan)
            fresh = [it for it in candidates if not sent_log.seen(cid, item_key(it))]
            entry["skipped"] = len(candidates) - len(fresh)
            top_n = int(chan.get("top_n", fmt.DEFAULT_TOP_N))
            picked = [it for lst in fmt.group_top(fresh, top_n).values() for it in lst]
            if not picked:
                continue
            if dry_run:
                entry["messages"] = fmt.render_text(name, picked, top_n, day)
                entry["sent"] = len(picked)
                continue
            mod.send(chan, picked, cfg=cfg, day=day)
            sent_log.mark(cid, (item_key(it) for it in picked), now)
            sent_log.save(now)
            entry["sent"] = len(picked)
        except NotifyError as e:
            entry["error"] = str(e)
            log.warning("notify %s failed: %s", cid, e)
        except Exception as e:  # a channel must never break the run
            entry["error"] = f"{type(e).__name__}: {e}"
            log.warning("notify %s crashed: %s", cid, entry["error"])
    for entry in result["channels"]:
        result["sent"] += entry["sent"]
        result["skipped"] += entry["skipped"]
        result["errors"] += 1 if entry["error"] else 0
    return result
