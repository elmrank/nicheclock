"""Provider request/response shapes against fake servers and fake CLIs; redaction; availability."""
from __future__ import annotations

import json
import logging

import pytest
from integ_helpers import FakeServer
from llm_fakes import KEY, api_reply, cli_log, install_cli, set_mode

from nicheclock.llm import (InvalidOutput, LLMConfigError, Message, ProviderError, ProviderUnavailable,
                            parse, redact)
from nicheclock.llm.providers import build

SCHEMA = {"type": "object", "required": ["n"], "properties": {"n": {"type": "integer", "minimum": 0}}}
MSGS = [Message("system", "be brief"), Message("user", "say 3")]


def mk(provider, server=None, **kw):
    raw = {"provider": provider, "model": "m-1", "api_key_env": "NC_TEST_KEY"}
    if server is not None:
        raw["base_url"] = server.url + ("/v1" if provider == "openai" else "")
    raw.update(kw)
    return build(parse(raw), sleep=lambda s: None)


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("NC_TEST_KEY", KEY)


def serve(text='{"n": 3}', **kw):
    s = FakeServer()
    s.responder = lambda path, js: api_reply(path, text, **kw)
    return s


def test_anthropic_request_and_response():
    with serve() as s:
        res = mk("anthropic", s).complete(MSGS, SCHEMA, max_tokens=77, temperature=0.2)
    (r,) = s.requests
    assert r["path"] == "/v1/messages"
    assert r["headers"]["x-api-key"] == KEY and r["headers"]["anthropic-version"] == "2023-06-01"
    body = r["json"]
    assert body["model"] == "m-1" and body["max_tokens"] == 77 and body["temperature"] == 0.2
    assert body["messages"] == [{"role": "user", "content": "say 3"}]
    assert body["system"].startswith("be brief") and "JSON Schema" in body["system"]
    assert res.json == {"n": 3} and res.text == '{"n": 3}'
    assert (res.usage.input, res.usage.output) == (120, 30)
    assert res.provider == "anthropic" and res.model == "m-1"


def test_anthropic_counts_cache_tokens_and_omits_temperature_by_default():
    s = FakeServer()
    s.responder = lambda p, j: (200, {}, json.dumps({"content": [{"type": "text", "text": "hi"}], "usage": {
        "input_tokens": 3, "cache_creation_input_tokens": 10, "cache_read_input_tokens": 4, "output_tokens": 2}}).encode())
    with s:
        res = mk("anthropic", s).complete(MSGS)
    assert "temperature" not in s.requests[0]["json"]
    assert res.usage.input == 17 and res.json is None


def test_openai_chat_completions_shape_and_json_mode():
    with serve() as s:
        res = mk("openai", s).complete(MSGS, SCHEMA, max_tokens=50)
    (r,) = s.requests
    assert r["path"] == "/v1/chat/completions" and r["headers"]["Authorization"] == f"Bearer {KEY}"
    b = r["json"]
    assert b["model"] == "m-1" and b["max_completion_tokens"] == 50
    assert b["response_format"] == {"type": "json_object"}
    assert [m["role"] for m in b["messages"]] == ["system", "user"]
    assert res.json == {"n": 3} and res.usage.output == 30


def test_openai_retries_without_response_format_when_the_endpoint_rejects_it():
    s = FakeServer()
    s.responses = [(400, {}, b'{"error": "response_format is not supported"}'),
                   api_reply("/chat/completions", '{"n": 1}')]
    with s:
        res = mk("openai", s).complete(MSGS, SCHEMA)
    assert len(s.requests) == 2 and "response_format" not in s.requests[1]["json"]
    assert res.json == {"n": 1}


def test_openai_compatible_server_without_key():
    with serve() as s:
        p = build(parse({"provider": "openai", "model": "local", "base_url": s.url + "/v1"}))
        p.complete(MSGS)
    assert "Authorization" not in s.requests[0]["headers"]


def test_gemini_key_in_header_not_url():
    with serve() as s:
        res = mk("gemini", s, model="gemini-2.5-flash").complete(MSGS, SCHEMA, max_tokens=90, temperature=0)
    (r,) = s.requests
    assert r["path"] == "/v1beta/models/gemini-2.5-flash:generateContent" and KEY not in r["path"]
    assert r["headers"]["x-goog-api-key"] == KEY
    b = r["json"]
    assert b["contents"] == [{"role": "user", "parts": [{"text": "say 3"}]}]
    assert b["systemInstruction"]["parts"][0]["text"].startswith("be brief")
    assert b["generationConfig"] == {"maxOutputTokens": 90, "temperature": 0, "responseMimeType": "application/json"}
    assert res.json == {"n": 3} and (res.usage.input, res.usage.output) == (120, 30)


def test_gemini_counts_thinking_tokens_as_output_and_reports_empty_reply():
    s = FakeServer()
    s.responder = lambda p, j: (200, {}, json.dumps({"candidates": [{"content": {"parts": [{"text": "x"}]}}], "usageMetadata": {
        "promptTokenCount": 5, "candidatesTokenCount": 2, "thoughtsTokenCount": 40}}).encode())
    with s:
        assert mk("gemini", s).complete(MSGS).usage.output == 42
    s2 = FakeServer()
    s2.responder = lambda p, j: (200, {}, json.dumps({"candidates": [{"finishReason": "MAX_TOKENS"}]}).encode())
    with s2, pytest.raises(ProviderError, match="MAX_TOKENS"):
        mk("gemini", s2).complete(MSGS)


def test_ollama_shape_is_free():
    with serve() as s:
        p = build(parse({"provider": "ollama", "model": "llama3", "base_url": s.url}))
        res = p.complete(MSGS, SCHEMA, max_tokens=33, temperature=0.1)
    (r,) = s.requests
    assert r["path"] == "/api/chat" and "Authorization" not in r["headers"]
    b = r["json"]
    assert b["stream"] is False and b["format"] == "json" and b["options"] == {"num_predict": 33, "temperature": 0.1}
    assert res.cost_usd_est == 0.0 and res.json == {"n": 3}


def test_retries_transient_errors_then_gives_up_with_a_redacted_message():
    s = FakeServer()
    s.responses = [(429, {"Retry-After": "0"}, b"slow down"), (529, {}, b"overloaded"),
                   (500, {}, f"boom, your key {KEY} was rejected".encode())]
    with s, pytest.raises(ProviderError) as e:
        mk("anthropic", s).complete(MSGS)
    assert len(s.requests) == 3 and "HTTP 500" in str(e.value)
    assert KEY not in str(e.value) and "***" in str(e.value)


def test_auth_errors_are_not_retried_and_hint_at_the_key():
    s = FakeServer()
    s.responses = [(401, {}, b'{"error":"invalid x-api-key"}')]
    with s, pytest.raises(ProviderError, match="check the key"):
        mk("anthropic", s).complete(MSGS)
    assert len(s.requests) == 1


def test_unreachable_server_message_has_no_key():
    with pytest.raises(ProviderError) as e:
        mk("anthropic", base_url="http://127.0.0.1:9").complete(MSGS)
    assert KEY not in str(e.value) and "request failed" in str(e.value)


def test_malformed_and_mismatching_json_raise_invalid_output_carrying_usage():
    with serve("Sure! not json at all") as s:
        with pytest.raises(InvalidOutput) as e:
            mk("ollama", s).complete(MSGS, SCHEMA)
    assert e.value.result.usage.input == 120 and e.value.result.text.startswith("Sure")
    with serve('{"n": -4}') as s, pytest.raises(InvalidOutput, match="below 0"):
        mk("ollama", s).complete(MSGS, SCHEMA)
    with serve('Here you go:\n```json\n{"n": 2}\n```\nenjoy') as s:      # chatty replies are tolerated
        assert mk("ollama", s).complete(MSGS, SCHEMA).json == {"n": 2}


def test_key_never_in_logs_or_errors(caplog):
    caplog.set_level(logging.DEBUG)
    with serve() as s:
        mk("openai", s).complete(MSGS, SCHEMA)
    s2 = FakeServer()
    s2.responses = [(403, {}, f"Incorrect API key provided: {KEY}".encode())]
    with s2, pytest.raises(ProviderError) as e:
        mk("openai", s2).complete(MSGS)
    assert KEY not in caplog.text and KEY not in str(e.value) and KEY not in repr(e.value)
    assert e.value.__cause__ is None and e.value.__suppress_context__


def test_redact_helper():
    assert redact(f"x {KEY} y", [KEY]) == "x *** y"
    assert "sk-ant-abcdefgh12345" not in redact("key sk-ant-abcdefgh12345")
    assert "AIzaSyA1234567890123456789012" not in redact("AIzaSyA1234567890123456789012")
    assert "tok12345678" not in redact("Authorization: Bearer tok12345678")
    assert redact("plain", ["a"]) == "plain"          # too short to be a secret: no blanking


def test_key_from_file_and_missing_keys(tmp_path, monkeypatch):
    f = tmp_path / "k.txt"
    f.write_text(KEY + "\n")
    with serve() as s:
        mk("anthropic", s, api_key_env=None, api_key_file=str(f)).complete(MSGS)
    assert s.requests[0]["headers"]["x-api-key"] == KEY
    monkeypatch.delenv("NC_TEST_KEY")
    with pytest.raises(ProviderUnavailable, match="NC_TEST_KEY"):
        mk("anthropic").complete(MSGS)
    with pytest.raises(ProviderUnavailable, match="cannot read"):
        mk("anthropic", api_key_env=None, api_key_file=str(tmp_path / "nope")).complete(MSGS)


def test_settings_validation():
    with pytest.raises(LLMConfigError, match="provider must be one of"):
        parse({"provider": "nope", "model": "x"})
    with pytest.raises(LLMConfigError, match="model is required"):
        parse({"provider": "ollama"})
    with pytest.raises(LLMConfigError, match="api_key_env"):
        parse({"provider": "anthropic", "model": "x"})
    with pytest.raises(LLMConfigError, match="only one"):
        parse({"provider": "anthropic", "model": "x", "api_key_env": "A", "api_key_file": "b"})
    with pytest.raises(LLMConfigError, match="CLI sign-in"):
        parse({"provider": "codex", "api_key_env": "A"})
    with pytest.raises(LLMConfigError, match="unknown key"):
        parse({"provider": "ollama", "model": "x", "api_key": "sk-inline"})      # keys never live in the YAML
    with pytest.raises(LLMConfigError, match="daily_budget_usd"):
        parse({"provider": "ollama", "model": "x", "daily_budget_usd": "lots"})
    with pytest.raises(LLMConfigError, match="tasks"):
        parse({"provider": "ollama", "model": "x", "tasks": ["fit", "dance"]})
    s = parse({"provider": "claude-code"})
    assert s.model == "" and s.tasks == ("fit", "junk", "merge", "gaps") and s.fit_weight == 0.6
    assert "SECRET" not in json.dumps(parse({"provider": "anthropic", "model": "m", "api_key_env": "NC_TEST_KEY"}).describe())


# -- CLI sign-in providers ------------------------------------------------------------------------

@pytest.fixture
def bin_dir(tmp_path, monkeypatch):
    d = tmp_path / "bin"
    d.mkdir()
    monkeypatch.setenv("PATH", f"{d}:/usr/bin:/bin")
    return d


def test_claude_code_command_line_result_and_env(bin_dir, monkeypatch):
    install_cli(bin_dir, "claude", {"reply": '{"n": 5}'})
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-not-leak-1234")
    monkeypatch.setenv("SMTP_PASSWORD", "hunter2hunter2")
    p = build(parse({"provider": "claude-code", "model": "haiku"}))
    res = p.complete(MSGS, SCHEMA, max_tokens=50)
    (call,) = [c for c in cli_log(bin_dir, "claude") if "-p" in c["argv"]]
    a = call["argv"]
    assert a[a.index("--output-format") + 1] == "json" and a[a.index("--model") + 1] == "haiku"
    assert a[a.index("--tools") + 1] == "" and a[a.index("--setting-sources") + 1] == ""   # no tools, no user settings
    assert "--no-session-persistence" in a and a[a.index("--permission-mode") + 1] == "dontAsk"
    assert a[a.index("--system-prompt") + 1].startswith("be brief")
    assert call["stdin"] == "say 3"                       # prompt on stdin, never in argv
    assert "ANTHROPIC_API_KEY" not in call["env"] and "SMTP_PASSWORD" not in call["env"]   # the sign-in pays
    assert "PATH" in call["env"] and not call["cwd"].startswith(str(bin_dir.parent / "data"))
    assert res.json == {"n": 5} and res.usage.input == 125 and res.usage.output == 7
    assert res.cost_usd_est == 0.0123 and res.provider == "claude-code"


def test_claude_code_availability_errors_and_timeout(bin_dir, monkeypatch):
    p = build(parse({"provider": "claude-code"}))
    ok, why = p.available()
    assert not ok and "not on PATH" in why
    with pytest.raises(ProviderUnavailable, match="not on PATH"):
        p.complete(MSGS)
    install_cli(bin_dir, "claude", {"logged_out": True})
    assert p.available() == (False, "not signed in: run `claude` once and log in")
    set_mode(bin_dir, "claude", {})
    ok, why = p.available()
    assert ok and "signed in" in why and "@" not in why         # never prints the account e-mail
    set_mode(bin_dir, "claude", {"error": "rate limit reached"})
    with pytest.raises(ProviderError, match="rate limit"):
        p.complete(MSGS)
    set_mode(bin_dir, "claude", {"garbage": True})
    with pytest.raises(ProviderError, match="without a JSON result"):
        p.complete(MSGS)
    set_mode(bin_dir, "claude", {"hang": True})
    slow = build(parse({"provider": "claude-code", "timeout_s": 1}))
    with pytest.raises(ProviderError, match="timed out"):
        slow.complete(MSGS)


def test_codex_exec_json_mode(bin_dir, monkeypatch):
    install_cli(bin_dir, "codex", {"reply": '{"n": 9}'})
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-should-not-leak-1234")
    p = build(parse({"provider": "codex", "model": "gpt-6-luna"}))
    assert p.available()[0]
    res = p.complete(MSGS, SCHEMA)
    (call,) = [c for c in cli_log(bin_dir, "codex") if c["argv"][:1] == ["exec"]]
    a = call["argv"]
    assert "--json" in a and a[-1] == "-" and a[a.index("--sandbox") + 1] == "read-only"
    assert a[a.index("--model") + 1] == "gpt-6-luna" and "--ephemeral" in a
    assert "be brief" in call["stdin"] and "say 3" in call["stdin"]
    assert "OPENAI_API_KEY" not in call["env"]
    assert res.json == {"n": 9} and (res.usage.input, res.usage.output) == (210, 9)
    assert res.cost_usd_est == pytest.approx((210 * 0.10 + 9 * 0.50) / 1e6)       # priced from the table


def test_codex_errors_and_logged_out(bin_dir):
    install_cli(bin_dir, "codex", {"logged_out": True})
    p = build(parse({"provider": "codex"}))
    assert p.available() == (False, "not signed in: run `codex login`")
    set_mode(bin_dir, "codex", {"error": "usage limit reached"})
    with pytest.raises(ProviderError, match="usage limit"):
        p.complete(MSGS)
    set_mode(bin_dir, "codex", {})
    assert p.complete(MSGS).cost_usd_est is None          # no model named: price unknown, tokens recorded
