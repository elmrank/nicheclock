"""Shared plumbing of the CLI sign-in providers (``claude -p``, ``codex exec``).

Design: tools off, user settings and MCP servers ignored, no session kept, no interactive prompt
possible, a scrubbed child environment so the sign-in (not an inherited API key) pays.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import tempfile
from typing import Any, Sequence

from nicheclock.llm.errors import ProviderError, ProviderUnavailable
from nicheclock.llm.providers.base import Provider
from nicheclock.llm.types import Message

# Variables a CLI child may inherit. Everything else (SMTP/bot tokens, API keys of other providers,
# NICHECLOCK_*) stays out, and with it any ANTHROPIC_API_KEY / OPENAI_API_KEY that would make the CLI
# bill an API account instead of the subscription.
_PASS = ("PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LANGUAGE", "TERM", "TMPDIR", "TZ",
         "SSL_CERT_FILE", "SSL_CERT_DIR", "NODE_EXTRA_CA_CERTS", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
         "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "no_proxy",
         "CLAUDE_CONFIG_DIR", "CODEX_HOME", "SYSTEMROOT", "COMSPEC", "PATHEXT", "APPDATA", "USERPROFILE")
_PASS_PREFIX = ("LC_", "XDG_", "NVM_", "VOLTA_", "FNM_")
STATUS_TIMEOUT = 20.0


def child_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k in _PASS or k.startswith(_PASS_PREFIX)}
    env["CI"] = "1"           # machine-readable, no spinners
    return env


def run(argv: Sequence[str], stdin_text: str, timeout: float) -> tuple[int, str, str]:
    """Run a child in a scratch directory, prompt on stdin, whole process group killed on timeout."""
    with tempfile.TemporaryDirectory(prefix="nicheclock-llm-") as cwd:
        try:
            p = subprocess.Popen(list(argv), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                                 cwd=cwd, env=child_env(), start_new_session=True)
        except OSError as e:
            raise ProviderUnavailable(f"cannot start {os.path.basename(argv[0])}: {e.strerror or e}") from None
        try:
            out, err = p.communicate(stdin_text, timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except (OSError, AttributeError):
                p.kill()
            p.communicate()
            raise ProviderError(f"{os.path.basename(argv[0])} timed out after {timeout:.0f}s") from None
        return p.returncode, out, err


def transcript(messages: Sequence[Message]) -> str:
    """The non-system turns as one prompt (a single user turn is passed through unchanged)."""
    turns = [m for m in messages if m.role != "system"]
    if len(turns) == 1:
        return turns[0].content
    return "\n\n".join(f"{'Assistant' if m.role == 'assistant' else 'User'}: {m.content}" for m in turns)


class CLIProvider(Provider):
    metered = False
    binary = ""

    def resolve(self) -> str:
        found = shutil.which(self.settings.cli_path or self.binary)
        if not found:
            raise ProviderUnavailable(
                f"the `{self.settings.cli_path or self.binary}` CLI is not on PATH: install it and sign in "
                f"once by hand (`{self.binary}`), or set llm.cli_path")
        return found

    def _status(self, argv: list[str]) -> tuple[int, str]:
        rc, out, err = run(argv, "", STATUS_TIMEOUT)
        return rc, out + err


def json_lines(text: str) -> list[dict[str, Any]]:
    out = []
    for ln in text.splitlines():
        ln = ln.strip()
        if ln.startswith("{"):
            try:
                v = json.loads(ln)
            except ValueError:
                continue
            if isinstance(v, dict):
                out.append(v)
    return out
