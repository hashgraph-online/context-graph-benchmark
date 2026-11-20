"""Bounded strict JSON and cgb.json.v1 canonical bytes; no source normalization."""

import hashlib
import json
import math
from pathlib import Path

MAX_JSON = 64 * 1024 * 1024
MAX_LINE = 8 * 1024 * 1024
MAX_DEPTH = 32


class Invalid(ValueError):
    """Invalid contract or evidence. Messages must not contain source bodies."""


def require(condition, message):
    if not condition:
        raise Invalid(message)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate JSON member")
        result[key] = value
    return result


def _constant(value):
    raise Invalid("nonfinite JSON number")


def _check(value, depth=0):
    require(depth <= MAX_DEPTH, "JSON nesting limit")
    if isinstance(value, str):
        try:
            value.encode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise Invalid("invalid Unicode scalar") from exc
    elif type(value) is float:
        require(math.isfinite(value), "nonfinite JSON number")
    elif isinstance(value, dict):
        for key, item in value.items():
            require(isinstance(key, str), "JSON object keys must be strings")
            _check(key, depth + 1)
            _check(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _check(item, depth + 1)
    else:
        require(value is None or type(value) in (int, bool), "invalid JSON value")


def decode(payload, maximum=MAX_JSON):
    require(len(payload) <= maximum, "JSON byte limit")
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_pairs,
                           parse_constant=_constant)
        _check(value)
        return value
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise Invalid("malformed JSON") from exc


def encoded(value):
    _check(value)
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def digest(payload):
    return hashlib.sha256(payload).hexdigest()


def read_json(path, maximum=MAX_JSON):
    path = Path(path)
    require(path.is_file() and not path.is_symlink(), "JSON must be a regular file")
    with path.open("rb") as stream:
        return decode(stream.read(maximum + 1), maximum)


def write_new(path, payload):
    path = Path(path)
    require(not path.exists() and not path.is_symlink(), "output already exists")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(payload)


def write_json(path, value):
    write_new(path, encoded(value))
