"""The ``llm:`` section of ``nicheclock.yaml``.

    llm:
      provider: anthropic | openai | gemini | ollama | claude-code | codex
      model: claude-haiku-4-5            # optional for claude-code / codex (the CLI's default)
      api_key_env: ANTHROPIC_API_KEY     # or api_key_file: ~/.config/nicheclock/anthropic.key
      base_url: null                     # OpenAI-compatible servers, proxies, a remote Ollama
      daily_budget_usd: 1.0              # hard stop for the UTC day; null = no cap
      max_items_per_run: 40              # topics the model judges per run (all projects)
      send_gsc_rows: false               # true: Search Console rows (page, clicks, position) go to the model
      tasks: [fit, junk, merge, gaps]    # which jobs the model does
      fit_weight: 0.6                    # share of the model's fit score in the feed score
      timeout_s: 120
      price_per_mtok: [input, output]    # USD per million tokens, for a model the table does not know
      denylist: [example.com]            # more domains never sent to the model (added to the built-in list)

Keys come from the environment variable or the file named here, never from the YAML itself.
"""
from __future__ import annotations

from nicheclock.i18n import t as _t

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nicheclock.llm.errors import LLMConfigError, ProviderUnavailable

API_PROVIDERS = ("anthropic", "openai", "gemini", "ollama")
CLI_PROVIDERS = ("claude-code", "codex")
PROVIDERS = API_PROVIDERS + CLI_PROVIDERS
TASKS = ("fit", "junk", "merge", "gaps")
_KEYS = {"provider", "model", "api_key_env", "api_key_file", "base_url", "daily_budget_usd",
         "max_items_per_run", "send_gsc_rows", "tasks", "fit_weight", "timeout_s", "price_per_mtok",
         "cli_path", "denylist"}


@dataclass
class LLMSettings:
    provider: str
    model: str = ""
    api_key_env: str | None = None
    api_key_file: str | None = None
    base_url: str | None = None
    daily_budget_usd: float | None = None
    max_items_per_run: int = 40
    send_gsc_rows: bool = False
    tasks: tuple[str, ...] = TASKS
    fit_weight: float = 0.6
    timeout_s: float = 120.0
    price_per_mtok: tuple[float, float] | None = None
    cli_path: str | None = None                 # claude-code / codex: binary name or path
    denylist: tuple[str, ...] = ()              # extra domains kept out of every prompt
    base_dir: Path = field(default_factory=Path.cwd)

    # -- secrets ---------------------------------------------------------------------------
    def api_key(self) -> str:
        """The key from ``api_key_env`` or ``api_key_file`` ('' when neither is set)."""
        if self.api_key_env:
            key = os.environ.get(self.api_key_env, "").strip()
            if not key:
                raise ProviderUnavailable(
                    f"environment variable {self.api_key_env} (llm.api_key_env) is empty or unset")
            return key
        if self.api_key_file:
            p = Path(os.path.expandvars(self.api_key_file)).expanduser()
            p = p if p.is_absolute() else self.base_dir / p
            try:
                key = p.read_text(encoding="utf-8").strip()
            except OSError as e:
                raise ProviderUnavailable(f"cannot read llm.api_key_file {p}: {e.strerror or 'error'}") from None
            if not key:
                raise ProviderUnavailable(f"llm.api_key_file {p} is empty")
            return key
        return ""

    @property
    def needs_key(self) -> bool:
        return self.provider in ("anthropic", "gemini") or (self.provider == "openai" and not self.base_url)

    def describe(self) -> dict[str, Any]:
        """Non-secret view for logs and `llm test`."""
        return {"provider": self.provider, "model": self.model or "(CLI default)",
                "base_url": self.base_url, "key_source": (
                    f"env:{self.api_key_env}" if self.api_key_env else
                    f"file:{self.api_key_file}" if self.api_key_file else "none"),
                "daily_budget_usd": self.daily_budget_usd, "tasks": list(self.tasks)}


def problems(raw: Any) -> list[str]:
    """Human-readable problems of a raw ``llm:`` mapping (empty: valid)."""
    if not isinstance(raw, dict):
        return [_t("llm.settings.not_mapping")]
    errs = [_t("llm.settings.unknown_key", key=k, known=", ".join(sorted(_KEYS))) for k in raw if k not in _KEYS]
    prov = raw.get("provider")
    if prov not in PROVIDERS:
        errs.append(_t("llm.settings.provider", providers=", ".join(PROVIDERS)))
        return errs
    if prov in API_PROVIDERS and not raw.get("model"):
        errs.append(_t("llm.settings.model_required"))
    if raw.get("api_key_env") and raw.get("api_key_file"):
        errs.append(_t("llm.settings.one_key"))
    needs_key = prov in ("anthropic", "gemini") or (prov == "openai" and not raw.get("base_url"))
    if needs_key and not (raw.get("api_key_env") or raw.get("api_key_file")):
        errs.append(_t("llm.settings.key_required"))
    if prov in CLI_PROVIDERS and (raw.get("api_key_env") or raw.get("api_key_file")):
        errs.append(_t("llm.settings.cli_key", provider=prov))
    b = raw.get("daily_budget_usd")
    if b is not None and (isinstance(b, bool) or not isinstance(b, (int, float)) or b < 0):
        errs.append(_t("llm.settings.budget"))
    m = raw.get("max_items_per_run")
    if m is not None and (isinstance(m, bool) or not isinstance(m, int) or m < 0):
        errs.append(_t("llm.settings.max_items"))
    if "send_gsc_rows" in raw and not isinstance(raw["send_gsc_rows"], bool):
        errs.append(_t("llm.settings.send_gsc"))
    t = raw.get("tasks")
    if t is not None:
        bad = [x for x in (t if isinstance(t, list) else [t]) if x not in TASKS]
        if bad or not isinstance(t, list):
            errs.append(_t("llm.settings.tasks", tasks=", ".join(TASKS)))
    w = raw.get("fit_weight")
    if w is not None and (isinstance(w, bool) or not isinstance(w, (int, float)) or not 0 <= w <= 1):
        errs.append(_t("llm.settings.fit_weight"))
    to = raw.get("timeout_s")
    if to is not None and (isinstance(to, bool) or not isinstance(to, (int, float)) or to <= 0):
        errs.append(_t("llm.settings.timeout"))
    d = raw.get("denylist")
    if d is not None and not (isinstance(d, list) and all(isinstance(x, str) and x.strip() for x in d)):
        errs.append(_t("llm.settings.denylist"))
    p = raw.get("price_per_mtok")
    if p is not None and not (isinstance(p, list) and len(p) == 2 and all(
            isinstance(x, (int, float)) and not isinstance(x, bool) and x >= 0 for x in p)):
        errs.append(_t("llm.settings.price"))
    return errs


def parse(raw: Any, base_dir: Path | None = None) -> LLMSettings:
    errs = problems(raw)
    if errs:
        raise LLMConfigError("; ".join(errs))
    p = raw.get("price_per_mtok")
    return LLMSettings(
        provider=raw["provider"], model=str(raw.get("model") or ""),
        api_key_env=raw.get("api_key_env") or None, api_key_file=raw.get("api_key_file") or None,
        base_url=(str(raw["base_url"]).rstrip("/") if raw.get("base_url") else None),
        daily_budget_usd=None if raw.get("daily_budget_usd") is None else float(raw["daily_budget_usd"]),
        max_items_per_run=40 if raw.get("max_items_per_run") is None else int(raw["max_items_per_run"]),
        send_gsc_rows=bool(raw.get("send_gsc_rows", False)),
        tasks=tuple(raw["tasks"]) if raw.get("tasks") is not None else TASKS,
        fit_weight=0.6 if raw.get("fit_weight") is None else float(raw["fit_weight"]),
        timeout_s=120.0 if raw.get("timeout_s") is None else float(raw["timeout_s"]),
        price_per_mtok=(float(p[0]), float(p[1])) if p else None,
        cli_path=raw.get("cli_path") or None, denylist=tuple(raw.get("denylist") or ()), base_dir=Path(base_dir) if base_dir else Path.cwd())
