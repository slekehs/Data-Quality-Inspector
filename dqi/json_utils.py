"""Strict JSON input and JSON output that preserves decimal numeric values."""

import json
import math
from decimal import Decimal, DecimalException


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key {key!r}.")
        result[key] = value
    return result


def _constant(value):
    raise ValueError(f"Nonstandard JSON number {value!r} is not allowed.")


def loads_json(text):
    try:
        return json.loads(text, parse_float=Decimal, object_pairs_hook=_object, parse_constant=_constant)
    except DecimalException as exc:
        raise ValueError("JSON number exceeds the supported decimal range.") from exc


def dumps_json(value, indent=2, *, sort_keys=False):
    """Encode finite Decimal values as JSON numbers, without float rounding."""
    if indent is not None and (not isinstance(indent, int) or indent < 0):
        raise ValueError("Indent must be a nonnegative integer or None.")

    def encode(item, depth):
        if item is None:
            return "null"
        if isinstance(item, bool):
            return "true" if item else "false"
        if isinstance(item, str):
            # Escape lone surrogates as well as ordinary Unicode for valid UTF-8 output.
            return json.dumps(item, ensure_ascii=True)
        if isinstance(item, Decimal):
            if not item.is_finite():
                raise ValueError("JSON numbers must be finite.")
            return str(item)
        if isinstance(item, int):
            return str(item)
        if isinstance(item, float):
            if not math.isfinite(item):
                raise ValueError("JSON numbers must be finite.")
            return json.dumps(item, allow_nan=False)
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise TypeError("JSON object keys must be strings.")
            keys = sorted(item) if sort_keys else item
            parts = [encode(key, depth + 1) + (": " if indent is not None else ":") + encode(item[key], depth + 1) for key in keys]
            left, right = "{", "}"
        elif isinstance(item, (list, tuple)):
            parts = [encode(val, depth + 1) for val in item]
            left, right = "[", "]"
        else:
            raise TypeError(f"Unsupported JSON value type: {type(item).__name__}.")
        if not parts:
            return left + right
        if indent is None:
            return left + ",".join(parts) + right
        prefix = " " * (indent * (depth + 1))
        return left + "\n" + prefix + (",\n" + prefix).join(parts) + "\n" + " " * (indent * depth) + right

    return encode(value, 0)
