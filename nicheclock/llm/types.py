"""Plain types shared by providers, the session and the tasks."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Message:
    role: str            # "system" | "user" | "assistant"
    content: str


@dataclass
class Usage:
    input: int = 0
    output: int = 0

    def to_dict(self) -> dict[str, int]:
        return {"input": self.input, "output": self.output}


@dataclass
class Result:
    """One model answer. ``json`` is set when a schema was given and the reply validated."""

    text: str
    json: Any = None
    usage: Usage = field(default_factory=Usage)
    cost_usd_est: float | None = None    # None: unknown model price (tokens are still recorded)
    provider: str = ""
    model: str = ""
    latency_s: float = 0.0


def messages_of(system: str, user: str) -> list[Message]:
    return [Message("system", system), Message("user", user)]
