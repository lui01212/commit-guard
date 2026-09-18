"""
Minimal TOML reader for commit-guard configuration files.

Python 3.11+ ships ``tomllib``; on older interpreters we fall back to the
parser in this module. It deliberately supports only the subset of TOML that
commit-guard configuration actually uses:

* comments
* table headers, including dotted ones (``[tool.commit-guard]``)
* string, integer, float and boolean values
* arrays of strings/numbers, written on one line or across several

Anything outside that subset raises :class:`TomlError` rather than being
silently misread. This is not a general-purpose TOML implementation and is not
intended to become one.
"""

import re
import sys
from typing import Any, Dict, List, Optional, Tuple

__all__ = ["loads", "TomlError"]


class TomlError(ValueError):
    """Raised when configuration text cannot be parsed."""


# Typed as Optional[Any] so both branches type-check: tomllib does not
# exist below Python 3.11, where this falls back to the parser below.
_tomllib: Optional[Any]
if sys.version_info >= (3, 11):
    import tomllib as _tomllib_mod

    _tomllib = _tomllib_mod
else:
    _tomllib = None


_TABLE_RE = re.compile(r"^\[([^\[\]]+)\]$")
_KEY_RE = re.compile(r"^([A-Za-z0-9_\-.]+)\s*=\s*(.*)$")


def loads(text: str) -> Dict[str, Any]:
    """Parse TOML text into nested dictionaries."""
    if _tomllib is not None:
        try:
            return _tomllib.loads(text)
        except Exception as exc:  # tomllib raises TOMLDecodeError
            raise TomlError(str(exc)) from exc
    return _fallback_loads(text)


def _fallback_loads(text: str) -> Dict[str, Any]:
    root: Dict[str, Any] = {}
    table = root
    lines = text.splitlines()
    index = 0

    while index < len(lines):
        raw = lines[index]
        index += 1
        line = _strip_comment(raw).strip()
        if not line:
            continue

        table_match = _TABLE_RE.match(line)
        if table_match:
            table = _resolve_table(root, table_match.group(1).strip())
            continue

        key_match = _KEY_RE.match(line)
        if not key_match:
            raise TomlError("cannot parse line: {0!r}".format(raw.strip()))

        key, value_text = key_match.group(1), key_match.group(2).strip()

        # An array may continue over later lines until brackets balance.
        if value_text.startswith("[") and not _brackets_balanced(value_text):
            parts = [value_text]
            while index < len(lines):
                parts.append(_strip_comment(lines[index]).strip())
                index += 1
                if _brackets_balanced(" ".join(parts)):
                    break
            else:
                raise TomlError("unterminated array for key {0!r}".format(key))
            value_text = " ".join(parts)

        _assign(table, key, _parse_value(value_text))

    return root


def _strip_comment(line: str) -> str:
    """Remove a trailing comment, ignoring '#' inside quoted strings."""
    out: List[str] = []
    quote = ""
    for char in line:
        if quote:
            out.append(char)
            if char == quote:
                quote = ""
            continue
        if char == '"' or char == "'":
            quote = char
            out.append(char)
            continue
        if char == "#":
            break
        out.append(char)
    return "".join(out)


def _brackets_balanced(text: str) -> bool:
    depth = 0
    quote = ""
    for char in text:
        if quote:
            if char == quote:
                quote = ""
            continue
        if char == '"' or char == "'":
            quote = char
        elif char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
    return depth == 0


def _resolve_table(root: Dict[str, Any], header: str) -> Dict[str, Any]:
    node = root
    for part in _split_dotted(header):
        existing = node.get(part)
        if existing is None:
            existing = {}
            node[part] = existing
        elif not isinstance(existing, dict):
            raise TomlError("cannot redefine {0!r} as a table".format(part))
        node = existing
    return node


def _assign(table: Dict[str, Any], key: str, value: Any) -> None:
    parts = _split_dotted(key)
    node = table
    for part in parts[:-1]:
        existing = node.get(part)
        if existing is None:
            existing = {}
            node[part] = existing
        elif not isinstance(existing, dict):
            raise TomlError("cannot assign into non-table {0!r}".format(part))
        node = existing
    node[parts[-1]] = value


def _split_dotted(name: str) -> List[str]:
    """Split a dotted key or header, honouring quoted segments."""
    parts: List[str] = []
    current: List[str] = []
    quote = ""
    for char in name:
        if quote:
            if char == quote:
                quote = ""
            else:
                current.append(char)
            continue
        if char == '"' or char == "'":
            quote = char
            continue
        if char == ".":
            parts.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    parts.append("".join(current).strip())

    cleaned = [part for part in parts if part]
    if not cleaned:
        raise TomlError("empty key or table name: {0!r}".format(name))
    return cleaned


def _parse_value(text: str) -> Any:
    if not text:
        raise TomlError("missing value")

    if text.startswith("["):
        return _parse_array(text)
    if text[0] == '"' or text[0] == "'":
        value, rest = _parse_string(text)
        if rest.strip():
            raise TomlError("trailing text after string: {0!r}".format(rest))
        return value
    if text == "true" or text == "false":
        return text == "true"

    try:
        return int(text.replace("_", ""))
    except ValueError:
        pass
    try:
        return float(text.replace("_", ""))
    except ValueError:
        pass
    raise TomlError("unsupported value: {0!r}".format(text))


def _parse_array(text: str) -> List[Any]:
    if not text.startswith("[") or not text.endswith("]"):
        raise TomlError("malformed array: {0!r}".format(text))
    body = text[1:-1].strip()
    if not body:
        return []

    items: List[Any] = []
    rest = body
    while rest:
        rest = rest.lstrip()
        if not rest:
            break
        if rest[0] == '"' or rest[0] == "'":
            value, rest = _parse_string(rest)
            items.append(value)
        else:
            chunk, _, rest = rest.partition(",")
            chunk = chunk.strip()
            if chunk:
                items.append(_parse_value(chunk))
            continue
        rest = rest.lstrip()
        if rest.startswith(","):
            rest = rest[1:]
        elif rest:
            raise TomlError("expected a comma in array near {0!r}".format(rest))
    return items


def _parse_string(text: str) -> Tuple[str, str]:
    """Read one string literal, returning it plus the unconsumed remainder."""
    quote = text[0]
    literal = quote == "'"
    out: List[str] = []
    index = 1
    while index < len(text):
        char = text[index]
        if not literal and char == "\\":
            index += 1
            if index >= len(text):
                raise TomlError("dangling escape in string")
            out.append(_unescape(text[index]))
        elif char == quote:
            return "".join(out), text[index + 1 :]
        else:
            out.append(char)
        index += 1
    raise TomlError("unterminated string: {0!r}".format(text))


_ESCAPES = {
    "n": "\n",
    "t": "\t",
    "r": "\r",
    '"': '"',
    "\\": "\\",
    "'": "'",
}


def _unescape(char: str) -> str:
    if char not in _ESCAPES:
        raise TomlError("unsupported escape sequence")
    return _ESCAPES[char]
