"""Tolerant JSON extraction from a chatty reply, and a small JSON-Schema subset validator."""
from __future__ import annotations

import json
import re
from typing import Any

_FENCE = re.compile(r"^```[A-Za-z0-9_-]*\s*\n?|\n?```\s*$")


def extract_json(text: str) -> Any:
    """First JSON object/array in ``text`` (fences and prose around it are ignored).
    Raises ``ValueError`` when there is none."""
    s = _FENCE.sub("", text.strip()).strip()
    try:
        return json.loads(s)
    except ValueError:
        pass
    dec = json.JSONDecoder()
    for i, ch in enumerate(s):
        if ch in "{[":
            try:
                obj, _ = dec.raw_decode(s[i:])
                return obj
            except ValueError:
                continue
    raise ValueError("no JSON value in the reply")


_TYPES = {
    "object": dict, "array": list, "string": str, "boolean": bool,
}


def _is(value: Any, t: str) -> bool:
    if t == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if t == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if t == "null":
        return value is None
    return isinstance(value, _TYPES[t])


def validate(value: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    """Problems of ``value`` against ``schema`` (type, enum, properties, required, items, minimum,
    maximum, minLength, maxLength, minItems, maxItems). Empty list: valid."""
    errs: list[str] = []
    t = schema.get("type")
    if t is not None:
        types = [t] if isinstance(t, str) else list(t)
        if not any(_is(value, x) for x in types):
            return [f"{path}: expected {'/'.join(types)}, got {type(value).__name__}"]
    if "enum" in schema and value not in schema["enum"]:
        errs.append(f"{path}: not one of {schema['enum']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errs.append(f"{path}: below {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errs.append(f"{path}: above {schema['maximum']}")
    if isinstance(value, str):
        if "minLength" in schema and len(value.strip()) < schema["minLength"]:
            errs.append(f"{path}: shorter than {schema['minLength']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errs.append(f"{path}: longer than {schema['maxLength']}")
    if isinstance(value, dict):
        for k in schema.get("required", []):
            if k not in value:
                errs.append(f"{path}: missing {k!r}")
        for k, sub in (schema.get("properties") or {}).items():
            if k in value:
                errs += validate(value[k], sub, f"{path}.{k}")
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errs.append(f"{path}: fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errs.append(f"{path}: more than {schema['maxItems']} items")
        if "items" in schema:
            for i, v in enumerate(value):
                errs += validate(v, schema["items"], f"{path}[{i}]")
                if len(errs) > 8:
                    break
    return errs[:8]
