"""``claude -p --output-format json``: the user's Claude Code sign-in (Pro/Max subscription)."""
from __future__ import annotations

import json
from typing import Any

from nicheclock.llm.errors import ProviderError, ProviderUnavailable
from nicheclock.llm.providers.base import Raw, split_system
from nicheclock.llm.providers.cli import CLIProvider, run, transcript
from nicheclock.llm.types import Message


class ClaudeCodeProvider(CLIProvider):
    name = "claude-code"
    binary = "claude"

    def argv(self, system: str) -> list[str]:
        a = [self.resolve(), "-p", "--output-format", "json",
             "--tools", "",                       # a model backend, not an agent: no Read/Bash/Edit
             "--setting-sources", "",             # ignore the user's hooks, CLAUDE.md, settings
             "--strict-mcp-config",               # ignore the user's MCP servers
             "--disable-slash-commands", "--no-session-persistence",
             "--permission-mode", "dontAsk"]      # never wait for an approval prompt
        if self.model:
            a += ["--model", self.model]
        if system:
            a += ["--system-prompt", system]
        return a

    def available(self) -> tuple[bool, str]:
        try:
            self.resolve()
            rc, out = self._status([self.resolve(), "auth", "status"])
        except (ProviderUnavailable, ProviderError) as e:
            return False, str(e)
        try:
            info = json.loads(out)
        except ValueError:
            info = {}
        if rc == 0 and info.get("loggedIn"):
            return True, f"signed in ({info.get('authMethod', '?')}, {info.get('subscriptionType', 'plan unknown')})"
        return False, "not signed in: run `claude` once and log in"

    def _call(self, messages: list[Message], schema: Any, max_tokens: int, temperature: float | None) -> Raw:
        system, _ = split_system(messages)
        rc, out, err = run(self.argv(system), transcript(messages), self.settings.timeout_s)
        data: Any = None
        try:
            data = json.loads(out)
        except ValueError:
            pass
        if isinstance(data, list):                      # stream-style output: take the result event
            data = next((e for e in reversed(data) if isinstance(e, dict) and e.get("type") == "result"), None)
        if not isinstance(data, dict):
            raise ProviderError(f"claude exited {rc} without a JSON result: {(err or out).strip()[-200:]}")
        if data.get("is_error") or rc != 0:
            raise ProviderError(f"claude reported an error ({data.get('subtype') or rc}): "
                                f"{str(data.get('result') or err).strip()[-300:]}")
        u = data.get("usage") or {}
        tin = (u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0) \
            + (u.get("cache_read_input_tokens") or 0)
        cost = data.get("total_cost_usd")
        return Raw(str(data.get("result") or ""), int(tin), int(u.get("output_tokens") or 0),
                   float(cost) if isinstance(cost, (int, float)) else None)
