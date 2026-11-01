"""Shared helpers for notification channels: config access, secrets, HTTP, sent-log."""

from __future__ import annotations

from nicheclock.i18n import t as _t

import hashlib
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import requests

SENT_LOG_NAME = "notify_sent.json"
SENT_TTL_DAYS = 90
HTTP_TIMEOUT = 20
MAX_RETRY_AFTER = 30

_sleep = time.sleep  # patched in tests


class NotifyError(RuntimeError):
    """A channel is misconfigured or a send failed. Never contains secret values."""


def get(obj: Any, key: str, default: Any = None) -> Any:
    """Read ``key`` from a mapping or an attribute-style config object."""
    if obj is None:
        return default
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


def user_agent(cfg: Any) -> str | None:
    return get(get(cfg, "identity"), "user_agent")


def secret(chan: Mapping[str, Any], name: str, *, required: bool = True) -> str | None:
    """Resolve secret ``name`` from ``<name>_env`` (env var) or ``<name>_file`` (path).

    Secrets are never read from inline config values, so they cannot be committed by
    accident.
    """
    env_key = chan.get(f"{name}_env")
    file_key = chan.get(f"{name}_file")
    if env_key:
        val = os.environ.get(str(env_key), "").strip()
        if val:
            return val
    if file_key:
        p = Path(str(file_key)).expanduser()
        try:
            val = p.read_text(encoding="utf-8").strip()
        except OSError as e:
            raise NotifyError(_t("notify.cannot_read", name=name, path=p, error=e.strerror)) from None
        if val:
            return val
    if not required:
        return None
    hint = " or ".join(
        x for x in (_t("notify.secret_env", name=env_key) if env_key else "",
                    _t("notify.secret_file", name=file_key) if file_key else "") if x
    )
    raise NotifyError(
        _t("notify.secret_missing", channel=chan.get("type", "channel"), name=name)
        + (_t("notify.secret_hint", hint=hint) if hint else _t("notify.secret_hint_config", name=name))
    )


def redact(text: str, secrets: Iterable[str]) -> str:
    for s in secrets:
        if s:
            text = text.replace(s, "***")
    return text


def post_json(url: str, payload: dict, *, headers: dict | None = None,
              secrets: Iterable[str] = (), session: requests.Session | None = None) -> requests.Response:
    """POST JSON; honour one 429 Retry-After (≤30 s); raise NotifyError with redacted detail."""
    secrets = [s for s in secrets if s]
    sess = session or requests
    for attempt in (0, 1):
        try:
            resp = sess.post(url, json=payload, headers=headers or {}, timeout=HTTP_TIMEOUT)
        except requests.RequestException as e:
            raise NotifyError(redact(f"{type(e).__name__}: {e}", secrets)) from None
        if resp.status_code == 429 and attempt == 0:
            wait = _retry_after(resp)
            if wait is not None and wait <= MAX_RETRY_AFTER:
                _sleep(wait)
                continue
        if resp.status_code >= 400:
            body = redact(resp.text[:300].replace("\n", " "), secrets)
            raise NotifyError(f"HTTP {resp.status_code}: {body}")
        return resp
    raise NotifyError("unreachable")  # pragma: no cover


def _retry_after(resp: requests.Response) -> float | None:
    h = resp.headers.get("Retry-After")
    if h:
        try:
            return float(h)
        except ValueError:
            pass
    try:
        data = resp.json()
    except ValueError:
        return None
    ra = data.get("retry_after") or (data.get("parameters") or {}).get("retry_after")
    try:
        return float(ra) if ra is not None else None
    except (TypeError, ValueError):
        return None


# ---- sent-log -------------------------------------------------------------------------

_norm_re = re.compile(r"\s+")


def item_key(it: Any) -> str:
    """Stable identity of a feed item: project/niche/kind/title/url (not score or time)."""
    parts = [str(getattr(it, f, "") or "") for f in ("project", "niche", "kind", "title", "url")]
    raw = "\x1f".join(_norm_re.sub(" ", p).strip().lower() for p in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def channel_id(chan: Mapping[str, Any]) -> str:
    """Identify a channel by type + destination (never by secret value)."""
    t = str(chan.get("type", ""))
    if t == "telegram":
        dest = str(chan.get("chat_id", ""))
    elif t == "discord":
        dest = str(chan.get("webhook_env") or chan.get("webhook_file") or "")
    elif t == "email":
        to = chan.get("to") or []
        dest = ",".join(sorted([to] if isinstance(to, str) else map(str, to)))
    else:
        dest = ""
    return f"{t}:{dest}"


class SentLog:
    """Tiny JSON file ``{channel_id: {item_key: iso_ts}}``; atomic writes, 90-day TTL."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.data: dict[str, dict[str, str]] = {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self.data = {c: dict(v) for c, v in raw.get("sent", {}).items()}
        except (OSError, ValueError, AttributeError):
            self.data = {}

    def seen(self, chan_id: str, key: str) -> bool:
        return key in self.data.get(chan_id, {})

    def mark(self, chan_id: str, keys: Iterable[str], now: datetime) -> None:
        bucket = self.data.setdefault(chan_id, {})
        ts = now.astimezone(timezone.utc).isoformat(timespec="seconds")
        for k in keys:
            bucket[k] = ts

    def save(self, now: datetime) -> None:
        cutoff = (now - timedelta(days=SENT_TTL_DAYS)).astimezone(timezone.utc).isoformat()
        pruned = {
            c: {k: t for k, t in v.items() if t >= cutoff} for c, v in self.data.items()
        }
        self.data = {c: v for c, v in pruned.items() if v}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"version": 1, "sent": self.data}, indent=1, sort_keys=True),
                       encoding="utf-8")
        os.replace(tmp, self.path)
