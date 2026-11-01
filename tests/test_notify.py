from __future__ import annotations

import json
import socket
import subprocess
import threading
from datetime import datetime, timezone

import pytest

Controller = pytest.importorskip("aiosmtpd.controller", reason="install aiosmtpd to run notify tests").Controller

from integ_helpers import FakeServer, make_cfg, sample_items
from nicheclock import notify
from nicheclock.notify import common, format as fmt

NOW = datetime(2026, 10, 31, 9, 0, tzinfo=timezone.utc)


# ---- formatting ---------------------------------------------------------------------

def test_group_top_n_per_niche_sorted_by_score():
    g = fmt.group_top(sample_items(), top_n=2)
    assert list(g) == ["birdwatching", "bird photography"]  # niche with best score first
    assert [i.title for i in g["birdwatching"]] == ["Snow Goose demo goes live", "Merlin Bird ID patch 1.4 notes"]


def test_render_text_has_why_and_link_and_kind():
    [msg] = fmt.render_text("mysite", sample_items(), top_n=3, day="2026-10-31")
    assert msg.splitlines()[0] == "nicheclock · mysite · 2026-10-31"
    assert "== birdwatching (3)" in msg
    assert "1. [TREND] Snow Goose demo goes live" in msg
    assert "why: Google Trends rank 3 (200K+ searches)" in msg
    assert "https://example.com/snow-goose" in msg
    assert "Low-score thing" not in msg
    assert "[GAP] bird treaty act compliance checklist" in msg


def test_render_html_escapes():
    items = sample_items()
    items[0].title = "A <b>&</b> B"
    items[0].url = 'https://x.test/?a=1&b="2"'
    [msg] = fmt.render_html("mysite", items, 3)
    assert "A &lt;b&gt;&amp;&lt;/b&gt; B" in msg
    assert 'href="https://x.test/?a=1&amp;b=&quot;2&quot;"' in msg


def test_render_chunks_respect_limit_and_repeat_niche_header():
    from integ_helpers import Item
    items = [Item("p", "n1", "topic", f"title {i} " + "x" * 80, f"https://e.test/{i}", "why " * 20, 100 - i)
             for i in range(30)]
    chunks = fmt.render_text("p", items, top_n=30, limit=700)
    assert len(chunks) > 1
    assert all(len(c) <= 700 for c in chunks)
    assert all("== n1 (30)" in c for c in chunks)
    joined = "\n".join(chunks)
    assert all(f"https://e.test/{i}" in joined for i in range(30))


def test_empty_items_render_nothing():
    assert fmt.render_text("p", []) == []


# ---- dispatch + dedupe --------------------------------------------------------------

def test_dispatch_dedupes_across_runs_and_channels(tmp_path, monkeypatch):
    sent = []
    from nicheclock.notify import discord
    monkeypatch.setattr(discord, "send", lambda chan, items, **kw: sent.append([i.title for i in items]))
    cfg = make_cfg(tmp_path, [{"type": "discord", "webhook_env": "X", "top_n": 2}])

    r1 = notify.dispatch(cfg, "mysite", sample_items(), now=NOW)
    assert r1["sent"] == 3 and r1["errors"] == 0  # 2 bird watching + 1 gap
    assert sent[0] == ["Snow Goose demo goes live", "Merlin Bird ID patch 1.4 notes", "bird treaty act compliance checklist"]

    # same items again: top-2 of the unsent remainder go out; nothing repeats
    r2 = notify.dispatch(cfg, cfg.projects[0], sample_items(), now=NOW)
    assert sent[1] == ["Spring Bird Festival 2027", "Low-score thing"]
    assert r2["skipped"] == 3

    r3 = notify.dispatch(cfg, "mysite", sample_items(), now=NOW)
    assert r3["sent"] == 0 and r3["skipped"] == 5 and len(sent) == 2

    log = json.loads((tmp_path / "data/mysite/notify_sent.json").read_text())
    assert len(log["sent"]["discord:X"]) == 5


def test_dedupe_is_per_channel_and_failure_not_recorded(tmp_path, monkeypatch):
    from nicheclock.notify import discord, telegram
    calls = {"d": 0, "t": 0}

    def bad(chan, items, **kw):
        calls["t"] += 1
        raise common.NotifyError("boom")

    monkeypatch.setattr(telegram, "send", bad)
    monkeypatch.setattr(discord, "send", lambda *a, **k: calls.__setitem__("d", calls["d"] + 1))
    cfg = make_cfg(tmp_path, [{"type": "telegram", "chat_id": "1", "token_env": "T"},
                              {"type": "discord", "webhook_env": "W"}])
    r = notify.dispatch(cfg, "mysite", sample_items(), now=NOW)
    assert r["errors"] == 1 and r["channels"][0]["error"] == "boom" and r["channels"][1]["sent"] == 5
    notify.dispatch(cfg, "mysite", sample_items(), now=NOW)
    assert calls == {"d": 1, "t": 2}  # telegram retried next run, discord not re-sent


def test_kinds_and_min_score_filter(tmp_path):
    cfg = make_cfg(tmp_path, [{"type": "telegram", "chat_id": "1", "token_env": "T",
                               "kinds": ["topic"], "min_score": 7}])
    r = notify.dispatch(cfg, "mysite", sample_items(), dry_run=True, now=NOW)
    [ch] = r["channels"]
    assert ch["sent"] == 2
    assert "Spring Bird Festival" not in ch["messages"][0] and "bird treaty act" not in ch["messages"][0]
    assert not (tmp_path / "data").exists()  # dry run records nothing


def test_no_notify_config_is_a_noop(tmp_path):
    cfg = make_cfg(tmp_path, [])
    assert notify.dispatch(cfg, "mysite", sample_items())["sent"] == 0


def test_unknown_type_reported_not_raised(tmp_path):
    cfg = make_cfg(tmp_path, [{"type": "pager"}])
    r = notify.dispatch(cfg, "mysite", sample_items(), now=NOW)
    assert r["errors"] == 1 and "unknown notify type" in r["channels"][0]["error"]


def test_validate_channels():
    p = make_cfg(None, [{"type": "telegram"}, {"type": "discord"}, {"type": "email", "host": "h"}]).projects[0]
    probs = notify.validate_channels(p)
    assert "telegram: chat_id missing" in probs
    assert "discord: webhook_env or webhook_file missing" in probs
    assert "email: to missing" in probs


# ---- secrets ------------------------------------------------------------------------

def test_secret_env_then_file(tmp_path, monkeypatch):
    f = tmp_path / "tok"
    f.write_text("from-file\n")
    chan = {"type": "telegram", "token_env": "NC_T", "token_file": str(f)}
    monkeypatch.delenv("NC_T", raising=False)
    assert common.secret(chan, "token") == "from-file"
    monkeypatch.setenv("NC_T", "from-env")
    assert common.secret(chan, "token") == "from-env"


def test_secret_missing_names_the_variable_not_a_value(monkeypatch):
    monkeypatch.delenv("NC_MISSING", raising=False)
    with pytest.raises(common.NotifyError, match="NC_MISSING"):
        common.secret({"type": "telegram", "token_env": "NC_MISSING"}, "token")


def test_inline_secret_is_ignored():
    with pytest.raises(common.NotifyError):
        common.secret({"type": "telegram", "token": "abc"}, "token")


# ---- telegram / discord against a fake HTTP server ----------------------------------

def test_telegram_payload(tmp_path, monkeypatch):
    monkeypatch.setenv("NC_TG", "123:SECRET")
    with FakeServer() as srv:
        cfg = make_cfg(tmp_path, [{"type": "telegram", "token_env": "NC_TG", "chat_id": "-100200",
                                   "api_base": srv.url, "top_n": 2}])
        r = notify.dispatch(cfg, "mysite", sample_items(), now=NOW)
    assert r["errors"] == 0, r
    [req] = srv.requests
    assert req["path"] == "/bot123:SECRET/sendMessage"
    p = req["json"]
    assert p["chat_id"] == "-100200" and p["parse_mode"] == "HTML" and p["disable_web_page_preview"] is True
    assert "<b>nicheclock · mysite · 2026-10-31</b>" in p["text"]
    assert '<a href="https://example.com/snow-goose">Snow Goose demo goes live</a>' in p["text"]
    assert "Merlin Bird ID" in p["text"] and "Spring Bird Festival" not in p["text"]
    assert req["headers"]["User-Agent"].startswith("nicheclock-test/0")


def test_telegram_error_redacts_token(tmp_path, monkeypatch):
    monkeypatch.setenv("NC_TG", "123:SECRET")
    with FakeServer() as srv:
        srv.responses.append((401, {}, b'{"ok":false,"description":"bad token 123:SECRET"}'))
        cfg = make_cfg(tmp_path, [{"type": "telegram", "token_env": "NC_TG", "chat_id": "1", "api_base": srv.url}])
        r = notify.dispatch(cfg, "mysite", sample_items(), now=NOW)
    err = r["channels"][0]["error"]
    assert "401" in err and "SECRET" not in err
    assert not (tmp_path / "data/mysite/notify_sent.json").exists()


def test_telegram_connection_error_redacts_token(tmp_path, monkeypatch):
    monkeypatch.setenv("NC_TG", "123:SECRET")
    cfg = make_cfg(tmp_path, [{"type": "telegram", "token_env": "NC_TG", "chat_id": "1",
                               "api_base": "http://127.0.0.1:9"}])
    r = notify.dispatch(cfg, "mysite", sample_items(), now=NOW)
    assert r["errors"] == 1 and "SECRET" not in r["channels"][0]["error"]


def test_telegram_429_retry_after_then_success(tmp_path, monkeypatch):
    monkeypatch.setenv("NC_TG", "t")
    slept = []
    monkeypatch.setattr(common, "_sleep", slept.append)
    with FakeServer() as srv:
        srv.responses.append((429, {}, b'{"ok":false,"parameters":{"retry_after":2}}'))
        cfg = make_cfg(tmp_path, [{"type": "telegram", "token_env": "NC_TG", "chat_id": "1", "api_base": srv.url}])
        r = notify.dispatch(cfg, "mysite", sample_items(), now=NOW)
    assert r["errors"] == 0 and slept == [2.0] and len(srv.requests) == 2


def test_discord_payload(tmp_path, monkeypatch):
    with FakeServer() as srv:
        monkeypatch.setenv("NC_DISCORD", srv.url + "/api/webhooks/1/tok")
        cfg = make_cfg(tmp_path, [{"type": "discord", "webhook_env": "NC_DISCORD", "top_n": 3}])
        r = notify.dispatch(cfg, "mysite", sample_items(), now=NOW)
    assert r["errors"] == 0, r
    [req] = srv.requests
    assert req["path"] == "/api/webhooks/1/tok"
    p = req["json"]
    assert p["allowed_mentions"] == {"parse": []}
    assert p["content"].startswith("**nicheclock · mysite · 2026-10-31**")
    assert "[Snow Goose demo goes live](https://example.com/snow-goose)" in p["content"]
    assert len(p["content"]) <= 2000


def test_discord_long_message_splits_under_2000(tmp_path, monkeypatch):
    from integ_helpers import Item
    items = [Item("mysite", "n", "topic", f"t{i} " + "y" * 150, f"https://e.test/{i}", "w" * 250, 50 - i)
             for i in range(30)]
    with FakeServer() as srv:
        monkeypatch.setenv("NC_DISCORD", srv.url + "/hook")
        cfg = make_cfg(tmp_path, [{"type": "discord", "webhook_env": "NC_DISCORD", "top_n": 30}])
        notify.dispatch(cfg, "mysite", items, now=NOW)
    assert len(srv.requests) > 1
    assert all(len(r["json"]["content"]) <= 2000 for r in srv.requests)


# ---- SMTP ---------------------------------------------------------------------------

class _Capture:
    def __init__(self):
        self.messages = []

    async def handle_DATA(self, server, session, envelope):
        self.messages.append(envelope)
        return "250 OK"


def _free_controller(handler, **kw):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    c = Controller(handler, hostname="127.0.0.1", port=port, **kw)
    c.start()
    c.port_ = port
    return c


def test_email_plain_smtp_stub(tmp_path):
    h = _Capture()
    c = _free_controller(h)
    try:
        cfg = make_cfg(tmp_path, [{"type": "email", "host": "127.0.0.1", "port": c.port_,
                                   "security": "none", "from": "radar@example.com",
                                   "to": ["me@example.com", "you@example.com"], "top_n": 3}])
        r = notify.dispatch(cfg, "mysite", sample_items(), now=NOW)
    finally:
        c.stop()
    assert r["errors"] == 0, r
    [env] = h.messages
    assert env.mail_from == "radar@example.com" and env.rcpt_tos == ["me@example.com", "you@example.com"]
    from email import message_from_bytes, policy
    msg = message_from_bytes(env.original_content, policy=policy.default)
    assert msg["Subject"] == "nicheclock · mysite: 4 new across 2 niche(s)" or "nicheclock · mysite" in msg["Subject"]
    body = msg.get_body(("plain",)).get_content()
    assert "why: Google Trends rank 3" in body and "https://example.com/snow-goose" in body
    assert "<a href=" in msg.get_body(("html",)).get_content()


def _selfsigned(tmp_path):
    key, crt = tmp_path / "k.pem", tmp_path / "c.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key),
                    "-out", str(crt), "-days", "2", "-subj", "/CN=localhost",
                    "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1"],
                   check=True, capture_output=True)
    return key, crt


def test_email_starttls_with_auth(tmp_path, monkeypatch):
    import ssl
    from aiosmtpd.smtp import AuthResult, LoginPassword
    key, crt = _selfsigned(tmp_path)
    sctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    sctx.load_cert_chain(crt, key)
    creds = []

    def authenticator(server, session, envelope, mechanism, auth_data):
        assert isinstance(auth_data, LoginPassword)
        creds.append((auth_data.login, auth_data.password))
        ok = auth_data.password == b"s3cret"
        return AuthResult(success=ok, handled=False)

    h = _Capture()
    c = _free_controller(h, tls_context=sctx, require_starttls=True, authenticator=authenticator,
                         auth_required=True, auth_require_tls=True)
    monkeypatch.setenv("NC_SMTP_PW", "s3cret")
    try:
        port = c.port_
        chan = {"type": "email", "host": "127.0.0.1", "port": port, "from": "radar@example.com",
                "to": "me@example.com", "username": "radar", "password_env": "NC_SMTP_PW",
                "ca_file": str(crt)}
        cfg = make_cfg(tmp_path, [chan])
        r = notify.dispatch(cfg, "mysite", sample_items(), now=NOW)
        assert r["errors"] == 0, r
        assert creds == [(b"radar", b"s3cret")] and len(h.messages) == 1

        # wrong password -> reported, password never echoed, nothing recorded
        monkeypatch.setenv("NC_SMTP_PW", "wrong-pw")
        cfg2 = make_cfg(tmp_path / "other", [chan])
        r2 = notify.dispatch(cfg2, "mysite", sample_items(), now=NOW)
        assert r2["errors"] == 1 and "wrong-pw" not in r2["channels"][0]["error"]

        # untrusted cert (no ca_file) -> TLS verification fails, nothing sent
        bad = dict(chan); bad.pop("ca_file")
        r3 = notify.dispatch(make_cfg(tmp_path / "o3", [bad]), "mysite", sample_items(), now=NOW)
        assert r3["errors"] == 1 and len(h.messages) == 1
    finally:
        c.stop()


def test_email_refuses_credentials_over_plaintext_remote():
    probs = notify.validate_channels(make_cfg(None, [{
        "type": "email", "host": "smtp.example.com", "security": "none", "from": "a@b.c", "to": "d@e.f",
        "username": "u", "password_env": "P"}]).projects[0])
    assert any("unencrypted" in p for p in probs)
