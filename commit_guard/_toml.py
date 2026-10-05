"""
Minimal TOML reader for commit-guard configuration files.

Python 3.11+ ships ``tomllib``; on older interpreters we fall back to the
parser in this module. It reads the TOML found in real configuration files,
including the parts of a ``pyproject.toml`` that belong to other tools:

* comments
* table headers, including dotted ones (``[tool.commit-guard]``), and arrays
  of tables (``[[tool.mypy.overrides]]``)
* basic, literal and multi-line strings
* integers, floats, booleans and dates/times
* arrays, written on one line or across several, and inline tables
  (``license = { text = "MIT" }``)

Anything it cannot read raises :class:`TomlError` rather than being silently
misread. This is not a validating TOML implementation and is not intended to
become one.
"""

import datetime
import re
import sys
from typing import Any, Dict, List, Optional, Set, Tuple

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


def loads(text: str) -> Dict[str, Any]:
    """Parse TOML text into nested dictionaries."""
    if _tomllib is not None:
        try:
            return _tomllib.loads(text)
        except Exception as exc:  # tomllib raises TOMLDecodeError
            raise TomlError(str(exc)) from exc
    return _fallback_loads(text)


def _fallback_loads(text: str) -> Dict[str, Any]:
    return _Parser(text).parse()


_BARE_KEY_RE = re.compile(r"[A-Za-z0-9_-]+")
_INT_RE = re.compile(
    r"[+-]?(?:0|[1-9](?:_?[0-9])*)$"
    r"|0x[0-9A-Fa-f](?:_?[0-9A-Fa-f])*$"
    r"|0o[0-7](?:_?[0-7])*$"
    r"|0b[01](?:_?[01])*$"
)
_FLOAT_RE = re.compile(
    r"[+-]?(?:0|[1-9](?:_?[0-9])*)"
    r"(?:\.[0-9](?:_?[0-9])*(?:[eE][+-]?[0-9](?:_?[0-9])*)?|[eE][+-]?[0-9](?:_?[0-9])*)$"
)
_DATETIME_RE = re.compile(
    r"(?P<date>(\d{4})-(\d{2})-(\d{2}))?"
    r"(?:(?(date)[Tt ])(?P<time>(\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?)"
    r"(?P<tz>[Zz]|[+-]\d{2}:\d{2})?)?"
)
_VALUE_END = frozenset(",]} \t\r\n#")

_ESCAPES = {
    "b": "\b",
    "t": "\t",
    "n": "\n",
    "f": "\f",
    "r": "\r",
    '"': '"',
    "\\": "\\",
}


class _Parser:
    def __init__(self, text: str) -> None:
        self.text = text
        self.pos = 0
        # Tables that were opened by a header, to reject a second header.
        self.defined: Set[int] = set()

    # -- low-level helpers -------------------------------------------------

    def error(self, message: str) -> TomlError:
        line = self.text.count("\n", 0, self.pos) + 1
        return TomlError("{0} (line {1})".format(message, line))

    def peek(self, count: int = 1) -> str:
        return self.text[self.pos : self.pos + count]

    def at_end(self) -> bool:
        return self.pos >= len(self.text)

    def skip_spaces(self) -> None:
        while self.peek() in (" ", "\t"):
            self.pos += 1

    def skip_comment(self) -> None:
        if self.peek() == "#":
            end = self.text.find("\n", self.pos)
            self.pos = len(self.text) if end < 0 else end

    def skip_newline(self) -> bool:
        if self.peek(2) == "\r\n":
            self.pos += 2
            return True
        if self.peek() == "\n":
            self.pos += 1
            return True
        return False

    def skip_blank(self) -> None:
        """Skip whitespace, comments and newlines (inside arrays)."""
        while True:
            self.skip_spaces()
            self.skip_comment()
            if not self.skip_newline():
                return

    def end_of_line(self) -> None:
        self.skip_spaces()
        self.skip_comment()
        if not self.at_end() and not self.skip_newline():
            raise self.error("expected end of line, found {0!r}".format(self.peek()))

    # -- document structure ------------------------------------------------

    def parse(self) -> Dict[str, Any]:
        root: Dict[str, Any] = {}
        table = root
        while True:
            self.skip_blank()
            if self.at_end():
                return root
            if self.peek(2) == "[[":
                self.pos += 2
                path = self.parse_key()
                self.skip_spaces()
                if self.peek(2) != "]]":
                    raise self.error("unterminated array-of-tables header")
                self.pos += 2
                table = self.append_table(root, path)
            elif self.peek() == "[":
                self.pos += 1
                path = self.parse_key()
                self.skip_spaces()
                if self.peek() != "]":
                    raise self.error("unterminated table header")
                self.pos += 1
                table = self.open_table(root, path)
            else:
                key = self.parse_key()
                self.skip_spaces()
                if self.peek() != "=":
                    raise self.error("expected '=' after key {0!r}".format(key))
                self.pos += 1
                self.skip_spaces()
                self.assign(table, key, self.parse_value())
            self.end_of_line()

    def descend(self, node: Dict[str, Any], part: str) -> Dict[str, Any]:
        existing = node.get(part)
        if existing is None:
            existing = {}
            node[part] = existing
        elif isinstance(existing, list) and existing and isinstance(existing[-1], dict):
            # Inside an array of tables, headers refer to its latest entry.
            existing = existing[-1]
        elif not isinstance(existing, dict):
            raise self.error("cannot redefine {0!r} as a table".format(part))
        return existing

    def open_table(self, root: Dict[str, Any], path: List[str]) -> Dict[str, Any]:
        node = root
        for part in path:
            node = self.descend(node, part)
        if id(node) in self.defined:
            raise self.error("table [{0}] defined twice".format(".".join(path)))
        self.defined.add(id(node))
        return node

    def append_table(self, root: Dict[str, Any], path: List[str]) -> Dict[str, Any]:
        node = root
        for part in path[:-1]:
            node = self.descend(node, part)
        entries = node.setdefault(path[-1], [])
        if not isinstance(entries, list):
            raise self.error("cannot redefine {0!r} as an array".format(path[-1]))
        table: Dict[str, Any] = {}
        entries.append(table)
        return table

    def assign(self, table: Dict[str, Any], key: List[str], value: Any) -> None:
        node = table
        for part in key[:-1]:
            node = self.descend(node, part)
        if key[-1] in node:
            raise self.error("duplicate key {0!r}".format(".".join(key)))
        node[key[-1]] = value

    # -- keys --------------------------------------------------------------

    def parse_key(self) -> List[str]:
        """A possibly dotted key, as its list of parts."""
        parts: List[str] = []
        while True:
            self.skip_spaces()
            char = self.peek()
            if char == '"':
                parts.append(self.parse_basic_string())
            elif char == "'":
                parts.append(self.parse_literal_string())
            else:
                match = _BARE_KEY_RE.match(self.text, self.pos)
                if not match:
                    raise self.error("invalid key")
                parts.append(match.group(0))
                self.pos = match.end()
            self.skip_spaces()
            if self.peek() != ".":
                return parts
            self.pos += 1

    # -- values ------------------------------------------------------------

    def parse_value(self) -> Any:
        char = self.peek()
        if not char or char in "\r\n#":
            raise self.error("missing value")
        if self.peek(3) == '"""':
            return self.parse_multiline_string('"""')
        if self.peek(3) == "'''":
            return self.parse_multiline_string("'''")
        if char == '"':
            return self.parse_basic_string()
        if char == "'":
            return self.parse_literal_string()
        if char == "[":
            return self.parse_array()
        if char == "{":
            return self.parse_inline_table()
        return self.parse_scalar()

    def parse_array(self) -> List[Any]:
        self.pos += 1  # [
        items: List[Any] = []
        while True:
            self.skip_blank()
            if self.peek() == "]":
                self.pos += 1
                return items
            if self.at_end():
                raise self.error("unterminated array")
            items.append(self.parse_value())
            self.skip_blank()
            if self.peek() == ",":
                self.pos += 1
            elif self.peek() != "]":
                raise self.error("expected ',' or ']' in array")

    def parse_inline_table(self) -> Dict[str, Any]:
        self.pos += 1  # {
        table: Dict[str, Any] = {}
        self.skip_spaces()
        if self.peek() == "}":
            self.pos += 1
            return table
        while True:
            key = self.parse_key()
            if self.peek() != "=":
                raise self.error("expected '=' in inline table")
            self.pos += 1
            self.skip_spaces()
            self.assign(table, key, self.parse_value())
            self.skip_spaces()
            if self.peek() == "}":
                self.pos += 1
                return table
            if self.peek() != ",":
                raise self.error("expected ',' or '}' in inline table")
            self.pos += 1

    def parse_basic_string(self) -> str:
        self.pos += 1  # opening quote
        out: List[str] = []
        while True:
            char = self.peek()
            if not char or char == "\n":
                raise self.error("unterminated string")
            self.pos += 1
            if char == '"':
                return "".join(out)
            if char == "\\":
                out.append(self.parse_escape())
            else:
                out.append(char)

    def parse_literal_string(self) -> str:
        end = self.text.find("'", self.pos + 1)
        newline = self.text.find("\n", self.pos + 1)
        if end < 0 or (0 <= newline < end):
            raise self.error("unterminated string")
        value = self.text[self.pos + 1 : end]
        self.pos = end + 1
        return value

    def parse_multiline_string(self, delimiter: str) -> str:
        self.pos += 3
        # A newline straight after the opening delimiter is not content.
        self.skip_newline()
        out: List[str] = []
        while True:
            if self.at_end():
                raise self.error("unterminated multi-line string")
            if self.text.startswith(delimiter, self.pos):
                # Up to two quotes may sit right before the closing delimiter.
                extra = 0
                while extra < 2 and self.text.startswith(
                    delimiter[0], self.pos + 3 + extra
                ):
                    extra += 1
                out.append(delimiter[0] * extra)
                self.pos += 3 + extra
                return "".join(out)
            char = self.text[self.pos]
            self.pos += 1
            if char == "\\" and delimiter == '"""':
                # A backslash at the end of a line trims the following
                # whitespace and newlines.
                rest = self.pos
                while self.peek() in (" ", "\t"):
                    self.pos += 1
                if self.peek() in ("\n", "\r"):
                    while self.peek() in (" ", "\t", "\n", "\r"):
                        self.pos += 1
                    continue
                self.pos = rest
                out.append(self.parse_escape())
            elif char == "\r" and self.peek() == "\n":
                continue  # normalise CRLF inside the string
            else:
                out.append(char)

    def parse_escape(self) -> str:
        char = self.peek()
        self.pos += 1
        if char in _ESCAPES:
            return _ESCAPES[char]
        if char in ("u", "U"):
            width = 4 if char == "u" else 8
            digits = self.text[self.pos : self.pos + width]
            if len(digits) != width or not all(
                d in "0123456789abcdefABCDEF" for d in digits
            ):
                raise self.error("invalid unicode escape")
            self.pos += width
            try:
                return chr(int(digits, 16))
            except ValueError as exc:
                raise self.error("invalid unicode escape") from exc
        raise self.error("unsupported escape sequence")

    def parse_scalar(self) -> Any:
        start = self.pos
        while not self.at_end() and self.text[self.pos] not in _VALUE_END:
            self.pos += 1
        token = self.text[start : self.pos]
        if token == "true":
            return True
        if token == "false":
            return False
        if _INT_RE.match(token):
            return self.to_int(token)
        if _FLOAT_RE.match(token):
            return float(token.replace("_", ""))
        if token in ("inf", "+inf", "-inf", "nan", "+nan", "-nan"):
            return float(token)
        moment = self.parse_datetime(start)
        if moment is not None:
            return moment
        raise self.error("unsupported value: {0!r}".format(token))

    @staticmethod
    def to_int(token: str) -> int:
        clean = token.replace("_", "")
        for prefix, base in (("0x", 16), ("0o", 8), ("0b", 2)):
            if clean.startswith(prefix):
                return int(clean[2:], base)
        return int(clean)

    def parse_datetime(self, start: int) -> Optional[Any]:
        match = _DATETIME_RE.match(self.text, start)
        if not match or match.end() == start:
            return None
        end = match.end()
        if end < len(self.text) and self.text[end] not in _VALUE_END:
            return None
        groups = match.groups()
        try:
            date: Optional[datetime.date] = None
            if match.group("date"):
                date = datetime.date(int(groups[1]), int(groups[2]), int(groups[3]))
            if not match.group("time"):
                if date is None:
                    return None
                self.pos = end
                return date
            fraction = (groups[8] or "")[:6].ljust(6, "0")
            clock: Tuple[int, int, int, int] = (
                int(groups[5]),
                int(groups[6]),
                int(groups[7]),
                int(fraction),
            )
            tzinfo = self.parse_offset(match.group("tz"))
            if date is None:
                if tzinfo is not None:
                    return None
                self.pos = end
                return datetime.time(*clock)
            self.pos = end
            return datetime.datetime(
                date.year, date.month, date.day, *clock, tzinfo=tzinfo
            )
        except ValueError as exc:
            raise self.error("invalid date or time") from exc

    @staticmethod
    def parse_offset(text: Optional[str]) -> Optional[datetime.tzinfo]:
        if not text:
            return None
        if text in ("Z", "z"):
            return datetime.timezone.utc
        sign = -1 if text[0] == "-" else 1
        hours, minutes = int(text[1:3]), int(text[4:6])
        return datetime.timezone(
            sign * datetime.timedelta(hours=hours, minutes=minutes)
        )
