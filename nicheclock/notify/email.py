"""Email channel over SMTP (STARTTLS by default).

Config::

    {type: email, host: smtp.example.com, port: 587, username: me@example.com,
     password_env: SMTP_PASSWORD, from: me@example.com, to: [me@example.com],
     security: starttls, top_n: 5}

``security`` is ``starttls`` (default, port 587), ``ssl`` (implicit TLS, port 465) or
``none`` (only allowed without credentials or for localhost relays). ``ca_file``
optionally pins a CA bundle for private relays. ``password_file`` works instead of
``password_env``.
"""

from __future__ import annotations

from nicheclock.i18n import t as _t

import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Any, Mapping, Sequence

from . import common, format as fmt

TERMS_URL = "https://www.rfc-editor.org/rfc/rfc6409"
TERMS_NOTE = "Message submission over SMTP to the user's own relay with their own credentials."

_LOCAL = {"localhost", "127.0.0.1", "::1"}


def _recipients(chan: Mapping[str, Any]) -> list[str]:
    to = chan.get("to") or []
    return [to] if isinstance(to, str) else [str(t) for t in to]


def validate(chan: Mapping[str, Any]) -> list[str]:
    problems = []
    for key in ("host", "from"):
        if not chan.get(key):
            problems.append(_t("notify.email.missing", key=key))
    if not _recipients(chan):
        problems.append(_t("notify.email.to_missing"))
    sec = chan.get("security", "starttls")
    if sec not in ("starttls", "ssl", "none"):
        problems.append(_t("notify.email.security", security=sec))
    if sec == "none" and chan.get("host") not in _LOCAL and chan.get("username"):
        problems.append(_t("notify.email.plain_creds"))
    if chan.get("username") and not (chan.get("password_env") or chan.get("password_file")):
        problems.append(_t("notify.email.password"))
    return problems


def build_message(chan: Mapping[str, Any], items: Sequence[Any], day: str = "") -> EmailMessage:
    project = items[0].project if items else ""
    top_n = int(chan.get("top_n", fmt.DEFAULT_TOP_N))
    text = "\n\n".join(fmt.render_text(project, items, top_n, day, limit=10**9))
    html_body = "\n\n".join(fmt.render_html(project, items, top_n, day, limit=10**9))
    msg = EmailMessage()
    n = len({i.niche for i in items})
    msg["Subject"] = _t("notify.subject", project=project, count=len(items), niches=n)
    msg["From"] = str(chan["from"])
    msg["To"] = ", ".join(_recipients(chan))
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid(domain="nicheclock.local")
    msg.set_content(text)
    msg.add_alternative(
        "<html><body><pre style=\"font-family:inherit;white-space:pre-wrap\">"
        + html_body
        + "</pre></body></html>",
        subtype="html",
    )
    return msg


def send(chan: Mapping[str, Any], items: Sequence[Any], *, cfg: Any = None, day: str = "") -> None:
    problems = validate(chan)
    if problems:
        raise common.NotifyError("; ".join(problems))
    password = common.secret(chan, "password") if chan.get("username") else None
    msg = build_message(chan, items, day)
    sec = chan.get("security", "starttls")
    host = str(chan["host"])
    port = int(chan.get("port") or {"ssl": 465}.get(sec, 587 if sec == "starttls" else 25))
    ctx = ssl.create_default_context(cafile=chan.get("ca_file") or None)
    try:
        if sec == "ssl":
            smtp: smtplib.SMTP = smtplib.SMTP_SSL(host, port, timeout=common.HTTP_TIMEOUT, context=ctx)
        else:
            smtp = smtplib.SMTP(host, port, timeout=common.HTTP_TIMEOUT)
        with smtp:
            smtp.ehlo()
            if sec == "starttls":
                smtp.starttls(context=ctx)
                smtp.ehlo()
            if password:
                smtp.login(str(chan["username"]), password)
            smtp.send_message(msg, to_addrs=_recipients(chan))
    except (smtplib.SMTPException, OSError, ssl.SSLError) as e:
        raise common.NotifyError(
            common.redact(f"smtp {host}:{port}: {type(e).__name__}: {e}", [password or ""])
        ) from None
