"""Fakes for the model layer: scripted provider, canned API replies for the fake HTTP server, fake CLIs."""
from __future__ import annotations

import json
import stat
import sys
import textwrap
from pathlib import Path
from typing import Any, Callable

from nicheclock.llm.providers.base import Provider, Raw
from nicheclock.llm.session import LLMSession
from nicheclock.llm.settings import parse

KEY = "sk-test-SECRET-1234567890abcdef"


def settings(**kw: Any):
    raw = {"provider": "ollama", "model": "fake-model"}
    raw.update(kw)
    return parse(raw)


class FakeProvider(Provider):
    """``script(messages_json) -> reply text`` (called with the parsed JSON of the last user message)."""

    name = "fake"

    def __init__(self, script: Callable[[dict], str], st=None, metered: bool = True, model: str = "claude-haiku-4-5",
                 tokens: tuple[int, int] = (1000, 200)) -> None:
        super().__init__(st or settings(provider="ollama", model=model))
        self.script, self.metered, self.tokens = script, metered, tokens
        self.model = model
        self.calls: list[list] = []

    def _call(self, messages, schema, max_tokens, temperature) -> Raw:
        self.calls.append(list(messages))
        try:
            payload = json.loads(messages[-1].content)
        except ValueError:
            payload = {"raw": messages[-1].content}
        return Raw(self.script(payload), *self.tokens)


def fake_session(store, script, st=None, project="p", **kw) -> LLMSession:
    prov = FakeProvider(script, st, **kw)
    return LLMSession(prov.settings, prov, store, project)


# -- canned replies of the real APIs -------------------------------------------------------------

def api_reply(path: str, text: str, tin: int = 120, tout: int = 30) -> tuple[int, dict, bytes]:
    if path.endswith("/chat/completions"):
        out = {"choices": [{"message": {"content": text}}], "usage": {"prompt_tokens": tin, "completion_tokens": tout}}
    elif path.endswith("/v1/messages"):
        out = {"content": [{"type": "text", "text": text}],
               "usage": {"input_tokens": tin, "output_tokens": tout}}
    elif ":generateContent" in path:
        out = {"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}],
               "usageMetadata": {"promptTokenCount": tin, "candidatesTokenCount": tout}}
    elif path.endswith("/api/chat"):
        out = {"message": {"content": text}, "prompt_eval_count": tin, "eval_count": tout}
    else:
        return 404, {}, b"{}"
    return 200, {"Content-Type": "application/json"}, json.dumps(out).encode()


# -- fake command line tools ---------------------------------------------------------------------

def install_cli(bin_dir: Path, name: str, mode: dict[str, Any] | None = None) -> Path:
    """An executable ``name`` in ``bin_dir`` that logs argv/stdin/env-names to ``<name>.log`` (JSON lines)
    and answers like the real CLI. ``mode`` is read from ``<name>.mode`` at each call."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    (bin_dir / f"{name}.mode").write_text(json.dumps(mode or {}))
    body = textwrap.dedent(f'''\
        #!{sys.executable}
        import json, os, sys, time
        here = os.path.dirname(os.path.abspath(__file__))
        mode = json.load(open(os.path.join(here, "{name}.mode")))
        argv = sys.argv[1:]
        is_status = argv[:2] in (["auth", "status"], ["login", "status"])
        stdin = "" if is_status else sys.stdin.read()
        with open(os.path.join(here, "{name}.log"), "a") as f:
            f.write(json.dumps({{"argv": argv, "stdin": stdin, "env": sorted(os.environ), "cwd": os.getcwd()}}) + "\\n")
        if is_status:
            if mode.get("logged_out"):
                print("not logged in", file=sys.stderr); sys.exit(1)
            print(json.dumps({{"loggedIn": True, "authMethod": "claude.ai", "subscriptionType": "max"}}) if "{name}" == "claude"
                  else "Logged in using ChatGPT")
            sys.exit(0)
        if mode.get("hang"):
            time.sleep(60)
        reply = mode.get("reply", "ok")
        if "{name}" == "claude":
            if mode.get("garbage"):
                print("this is not json"); sys.exit(0)
            out = {{"type": "result", "subtype": "error_during_execution" if mode.get("error") else "success",
                   "is_error": bool(mode.get("error")), "result": mode.get("error") or reply,
                   "total_cost_usd": 0.0123, "usage": {{"input_tokens": 5, "cache_creation_input_tokens": 100,
                   "cache_read_input_tokens": 20, "output_tokens": 7}}}}
            print(json.dumps(out)); sys.exit(1 if mode.get("error") else 0)
        else:
            if mode.get("error"):
                print(json.dumps({{"type": "turn.failed", "error": {{"message": mode["error"]}}}})); sys.exit(1)
            print(json.dumps({{"type": "thread.started", "thread_id": "t"}}))
            print(json.dumps({{"type": "item.completed", "item": {{"id": "i0", "type": "agent_message", "text": reply}}}}))
            print(json.dumps({{"type": "turn.completed", "usage": {{"input_tokens": 210, "cached_input_tokens": 0, "output_tokens": 9}}}}))
        ''')
    p = bin_dir / name
    p.write_text(body)
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    return p


def cli_log(bin_dir: Path, name: str) -> list[dict]:
    f = bin_dir / f"{name}.log"
    return [json.loads(ln) for ln in f.read_text().splitlines()] if f.exists() else []


def set_mode(bin_dir: Path, name: str, mode: dict[str, Any]) -> None:
    (bin_dir / f"{name}.mode").write_text(json.dumps(mode))
