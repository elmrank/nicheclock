"""Shared HTTP plumbing of the API providers: one POST with bounded retries and redacted errors."""
from __future__ import annotations

import time
from typing import Any, Callable

import requests

from nicheclock.llm.errors import ProviderError
from nicheclock.llm.providers.base import Provider
from nicheclock.llm.settings import LLMSettings

RETRY_STATUS = {429, 500, 502, 503, 504, 529}
MAX_RETRIES = 2
MAX_WAIT = 20.0


class HTTPProvider(Provider):
    default_base = ""

    def __init__(self, settings: LLMSettings, session: Any = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        super().__init__(settings)
        self.session = session or requests.Session()
        self.sleep = sleep
        self.base = (settings.base_url or self.default_base).rstrip("/")

    def _secret(self) -> str:
        key = self.settings.api_key()
        if key:
            self._secrets.append(key)
        return key

    def _post(self, path: str, headers: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
        url = self.base + path
        last = ""
        for attempt in range(MAX_RETRIES + 1):
            try:
                r = self.session.post(url, json=body, headers=headers, timeout=self.settings.timeout_s)
            except requests.RequestException as e:
                last = f"{self.name} request failed: {type(e).__name__}"
                if attempt < MAX_RETRIES:
                    self.sleep(min(2.0 * (attempt + 1), MAX_WAIT))
                    continue
                raise ProviderError(f"{last} ({self.redact(e)[:200]})") from None
            if r.status_code in RETRY_STATUS and attempt < MAX_RETRIES:
                try:
                    wait = float(r.headers.get("retry-after", ""))
                except ValueError:
                    wait = 2.0 * (attempt + 1)
                self.sleep(min(max(wait, 0.0), MAX_WAIT))
                continue
            if r.status_code >= 400:
                hint = " (check the key)" if r.status_code in (401, 403) else ""
                raise ProviderError(f"{self.name} HTTP {r.status_code}{hint}: {self.redact(r.text)[:300]}",)
            try:
                data = r.json()
            except ValueError:
                raise ProviderError(f"{self.name}: reply is not JSON: {self.redact(r.text)[:200]}") from None
            if not isinstance(data, dict):
                raise ProviderError(f"{self.name}: unexpected reply shape")
            return data
        raise ProviderError(last or f"{self.name}: request failed")  # pragma: no cover
