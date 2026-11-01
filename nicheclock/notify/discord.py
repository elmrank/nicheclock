"""Discord incoming-webhook channel.

Config::

    {type: discord, webhook_env: DISCORD_WEBHOOK, top_n: 5}

``webhook_file`` works instead of ``webhook_env``; the webhook URL is a secret and
is never stored in the config.
"""

from __future__ import annotations

from nicheclock.i18n import t as _t

from typing import Any, Mapping, Sequence

from . import common, format as fmt

TERMS_URL = "https://discord.com/developers/docs/resources/webhook"
TERMS_NOTE = "Incoming webhooks are an official way to post messages to a channel."


def validate(chan: Mapping[str, Any]) -> list[str]:
    if not (chan.get("webhook_env") or chan.get("webhook_file")):
        return [_t("notify.discord.webhook")]
    return []


def build_messages(chan: Mapping[str, Any], items: Sequence[Any], day: str = "") -> list[dict]:
    project = items[0].project if items else ""
    texts = fmt.render_markdown(project, items, int(chan.get("top_n", fmt.DEFAULT_TOP_N)), day)
    return [
        {
            "content": t,
            "allowed_mentions": {"parse": []},  # never ping @everyone from feed text
            "flags": 4,  # SUPPRESS_EMBEDS: no link preview wall
            **({"username": str(chan["username"])} if chan.get("username") else {}),
        }
        for t in texts
    ]


def send(chan: Mapping[str, Any], items: Sequence[Any], *, cfg: Any = None, day: str = "") -> None:
    problems = validate(chan)
    if problems:
        raise common.NotifyError("; ".join(problems))
    url = common.secret(chan, "webhook")
    headers = {}
    ua = common.user_agent(cfg)
    if ua:
        headers["User-Agent"] = ua
    for payload in build_messages(chan, items, day):
        common.post_json(url, payload, headers=headers, secrets=[url])
