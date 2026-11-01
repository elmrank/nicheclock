"""Telegram Bot API ``sendMessage`` channel.

Config (``projects[].notify`` entry)::

    {type: telegram, token_env: TG_TOKEN, chat_id: "123", top_n: 5}

``token_file`` works instead of ``token_env``. ``api_base`` (default
https://api.telegram.org) and ``message_thread_id`` are optional.
"""

from __future__ import annotations

from nicheclock.i18n import t as _t

from typing import Any, Mapping, Sequence

from . import common, format as fmt

TERMS_URL = "https://core.telegram.org/bots/api"
TERMS_NOTE = "Bot API: bots may call sendMessage on chats that started/added them."
DEFAULT_API = "https://api.telegram.org"


def validate(chan: Mapping[str, Any]) -> list[str]:
    problems = []
    if not chan.get("chat_id"):
        problems.append(_t("notify.telegram.chat_id"))
    if not (chan.get("token_env") or chan.get("token_file")):
        problems.append(_t("notify.telegram.token"))
    return problems


def build_messages(chan: Mapping[str, Any], items: Sequence[Any], day: str = "") -> list[dict]:
    project = items[0].project if items else ""
    texts = fmt.render_html(project, items, int(chan.get("top_n", fmt.DEFAULT_TOP_N)), day)
    payloads = []
    for text in texts:
        p: dict[str, Any] = {
            "chat_id": str(chan["chat_id"]),
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if chan.get("message_thread_id"):
            p["message_thread_id"] = int(chan["message_thread_id"])
        payloads.append(p)
    return payloads


def send(chan: Mapping[str, Any], items: Sequence[Any], *, cfg: Any = None, day: str = "") -> None:
    problems = validate(chan)
    if problems:
        raise common.NotifyError("; ".join(problems))
    token = common.secret(chan, "token")
    base = str(chan.get("api_base") or DEFAULT_API).rstrip("/")
    url = f"{base}/bot{token}/sendMessage"
    headers = {}
    ua = common.user_agent(cfg)
    if ua:
        headers["User-Agent"] = ua
    for payload in build_messages(chan, items, day):
        resp = common.post_json(url, payload, headers=headers, secrets=[token])
        try:
            ok = resp.json().get("ok", False)
        except ValueError:
            ok = False
        if not ok:
            raise common.NotifyError(
                common.redact(_t("notify.telegram.rejected", body=resp.text[:200]), [token])
            )
