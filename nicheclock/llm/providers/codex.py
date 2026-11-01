"""``codex exec --json``: the user's Codex CLI sign-in (ChatGPT plan)."""
from __future__ import annotations

from typing import Any

from nicheclock.llm.errors import ProviderError, ProviderUnavailable
from nicheclock.llm.providers.base import Raw, split_system
from nicheclock.llm.providers.cli import CLIProvider, json_lines, run, transcript
from nicheclock.llm.types import Message


class CodexProvider(CLIProvider):
    name = "codex"
    binary = "codex"

    def argv(self) -> list[str]:
        a = [self.resolve(), "exec", "--json", "--color", "never", "--skip-git-repo-check",
             "--sandbox", "read-only",                # the model gets no write access, no network tools
             "--ephemeral", "--ignore-user-config", "--ignore-rules",
             "-c", 'approval_policy="never"']         # never wait for an approval prompt
        if self.model:
            a += ["--model", self.model]
        return a + ["-"]                              # prompt on stdin

    def available(self) -> tuple[bool, str]:
        try:
            exe = self.resolve()
            rc, out = self._status([exe, "login", "status"])
        except (ProviderUnavailable, ProviderError) as e:
            return False, str(e)
        if rc == 0:
            return True, "signed in (" + (out.strip().splitlines() or ["ok"])[0][:60] + ")"
        return False, "not signed in: run `codex login`"

    def _call(self, messages: list[Message], schema: Any, max_tokens: int, temperature: float | None) -> Raw:
        system, _ = split_system(messages)
        prompt = (system + "\n\n" if system else "") + transcript(messages)
        rc, out, err = run(self.argv(), prompt, self.settings.timeout_s)
        events = json_lines(out)
        text, tin, tout, failure = "", 0, 0, ""
        for ev in events:
            t = ev.get("type")
            if t == "item.completed" and (ev.get("item") or {}).get("type") == "agent_message":
                text = str(ev["item"].get("text") or "")
            elif t == "turn.completed":
                u = ev.get("usage") or {}
                tin, tout = int(u.get("input_tokens") or 0), int(u.get("output_tokens") or 0)
            elif t in ("turn.failed", "error"):
                m = ev.get("error") or ev.get("message") or ev
                failure = str(m.get("message") if isinstance(m, dict) else m)
        if failure:
            raise ProviderError(f"codex reported an error: {failure.strip()[-300:]}")
        if rc != 0 or not events:
            raise ProviderError(f"codex exited {rc}: {(err or out).strip()[-200:]}")
        return Raw(text, tin, tout)
