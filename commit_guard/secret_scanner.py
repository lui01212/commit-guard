"""
Content-level secret and credential scanner for git staged diffs and source files.
Inspired by AWS git-secrets and gitleaks.
Zero external dependencies.
"""

import re
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple, Union


@dataclass
class SecretFinding:
    rule_name: str
    file_path: str
    line_number: int
    secret_preview: str
    line_content: str

    def format_message(self) -> str:
        return (
            f"[SECRET LEAK] {self.file_path}:{self.line_number}: "
            f"Detected {self.rule_name} ('{self.secret_preview}')"
        )


def _mask_secret(secret: str) -> str:
    """Mask sensitive string leaving only small prefix and suffix visible."""
    if len(secret) <= 8:
        return "***"
    return secret[:4] + "..." + secret[-4:]


# Precompiled secret detection rules. A rule either matches the secret itself
# or captures it in a group named "value". Every pattern starts at a literal
# prefix or keyword and keeps its repetitions bounded or unambiguous, so a long
# minified line is scanned in linear time.
SECRET_RULES: List[Tuple[str, re.Pattern]] = [
    ("AWS Access Key ID", re.compile(r"\b(?:AKIA|ABIA|ACCA|ASIA)[A-Z0-9]{16}\b")),
    (
        "GitHub Token",
        re.compile(
            r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36}\b|\bgithub_pat_[A-Za-z0-9_]{82}\b"
        ),
    ),
    # Legacy keys, and the newer project, service-account and admin keys that
    # also use '_' and '-'. Anthropic keys (sk-ant-) have their own rule.
    (
        "OpenAI API Key",
        re.compile(r"\bsk-(?!ant-)(?:proj-|svcacct-|admin-)?[A-Za-z0-9_-]{20,}"),
    ),
    ("Anthropic API Key", re.compile(r"\bsk-ant-[a-zA-Z0-9_\-]{20,}")),
    # xoxb-/xoxp-/... tokens, rotating xoxe.xoxp- tokens and xapp- app tokens.
    (
        "Slack Token",
        re.compile(
            r"\b(?:xox[abeprs](?:\.xox[abeprs])?|xapp)-(?=[0-9A-Za-z-]{20})"
            r"[0-9A-Za-z]+(?:-[0-9A-Za-z]+)+"
        ),
    ),
    # Secret and restricted keys only: publishable pk_ keys are public by design.
    (
        "Stripe Secret / Live Key",
        re.compile(r"\b(?:sk|rk)_(?:test|live)_[0-9a-zA-Z]{24,}"),
    ),
    ("Google Cloud API Key", re.compile(r"\bAIza[0-9A-Za-z\-_]{35}\b")),
    (
        "PEM Private Key Header",
        re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"),
    ),
    # key = "value", "key": "value" (JSON), key: value (YAML), KEY=value (.env).
    (
        "Generic Hardcoded Password / Secret",
        re.compile(
            r"(?i)(?:password|passwd|secret|api_key|apikey|access_token|auth_token)"
            r"[\"']?[ \t]*(?::=|=>|[:=])[ \t]*"
            r"(?P<quote>[\"']?)(?P<value>[^\s\"'`]{8,})(?P=quote)"
        ),
    ),
    # scheme://user:password@host, as in database and broker URLs.
    (
        "Password in URL",
        re.compile(
            r"\b[A-Za-z][A-Za-z0-9+.\-]{1,30}://[^\s:/?#@\"'<>]{1,128}:"
            r"(?P<value>[^\s:/?#@\"'<>]{3,128})@[A-Za-z0-9\[]"
        ),
    ),
]

# Patterns that indicate false positives / test dummies / placeholders. They
# are matched against the secret value only, never the rest of the line, so a
# real key is still reported when, say, a URL on the same line says "example".
ALLOWLIST_PATTERNS = [
    re.compile(
        r"(?i)(?:example|placeholder|dummy|test_fake|<YOUR_|fake_|YOUR_KEY|REPLACE_ME)"
    ),
    re.compile(r"AKIAIOSFODNN7EXAMPLE"),
    # Masked values such as "********" or "xxxxxxxx".
    re.compile(r"[xX]{4,}|\*{4,}"),
]

# Values that only name the thing they stand in for: "user:password@host".
_KEYWORD_VALUES = frozenset(("password", "passwd", "pass", "pwd", "secret"))

# Characters that end the token around a match when checking for placeholders.
_TOKEN_STOP = frozenset(" \t\r\"'`=:,;()[]{}<>")
# Bound the token expansion so that many matches on one line stay cheap.
_TOKEN_REACH = 256


def _surrounding_token(text: str, start: int, end: int) -> str:
    """The run of non-separator characters around text[start:end]."""
    low = max(0, start - _TOKEN_REACH)
    while start > low and text[start - 1] not in _TOKEN_STOP:
        start -= 1
    high = min(len(text), end + _TOKEN_REACH)
    while end < high and text[end] not in _TOKEN_STOP:
        end += 1
    return text[start:end]


def _is_placeholder(value: str) -> bool:
    """Check if a secret value is an obvious mock or placeholder."""
    if value.lower() in _KEYWORD_VALUES:
        return True
    return any(pattern.search(value) for pattern in ALLOWLIST_PATTERNS)


def _is_reference(value: str) -> bool:
    """Values that point at a secret kept elsewhere: ${VAR}, {{ var }}, ..."""
    if value.startswith(("$", "%", "&", "*", "!")):
        return True
    return any(marker in value for marker in ("${", "{{", "%(", "#{"))


def _password_value(match: "re.Match") -> Optional[str]:
    value = match.group("value")
    if not match.group("quote"):
        # Unquoted: YAML, .env or ini, but also code such as
        # `password = settings.DB_PASSWORD` or `password=getpass()`.
        value = value.rstrip(",;")
        if len(value) < 8 or "(" in value or "[" in value:
            return None
        if not any(char.isdigit() for char in value) and all(
            char.isalnum() or char in "_." for char in value
        ):
            return None
    return None if _is_reference(value) else value


def _url_password_value(match: "re.Match") -> Optional[str]:
    value = match.group("value")
    return None if _is_reference(value) else value


_VALUE_EXTRACTORS: Dict[str, Callable[["re.Match"], Optional[str]]] = {
    "Generic Hardcoded Password / Secret": _password_value,
    "Password in URL": _url_password_value,
}


def _scan_line(text: str) -> Optional[Tuple[str, str]]:
    """Return (rule name, masked preview) for the first secret on a line."""
    for rule_name, regex in SECRET_RULES:
        extract = _VALUE_EXTRACTORS.get(rule_name)
        group: Union[str, int] = "value" if "value" in regex.groupindex else 0
        for match in regex.finditer(text):
            if extract is not None:
                value = extract(match)
                if value is None:
                    continue
            else:
                value = match.group(0)
            token = _surrounding_token(text, match.start(group), match.end(group))
            if _is_placeholder(value) or _is_placeholder(token):
                continue
            return rule_name, _mask_secret(match.group(0))
    return None


def scan_text(text: str, file_path: str = "<staged>") -> List[SecretFinding]:
    """Scan arbitrary text content line by line for secrets."""
    findings: List[SecretFinding] = []
    lines = text.splitlines()

    for idx, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped:
            continue

        hit = _scan_line(stripped)
        if hit is not None:
            findings.append(
                SecretFinding(
                    rule_name=hit[0],
                    file_path=file_path,
                    line_number=idx,
                    secret_preview=hit[1],
                    line_content=stripped[:120],
                )
            )

    return findings


_HUNK_HEADER_RE = re.compile(r"^@@ -\d+(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

_C_ESCAPES = {
    "a": 7,
    "b": 8,
    "t": 9,
    "n": 10,
    "v": 11,
    "f": 12,
    "r": 13,
    '"': 34,
    "\\": 92,
}


def _unquote_c(text: str) -> str:
    """Decode a path git wrote as a C-style quoted string."""
    out = bytearray()
    index = 1
    while index < len(text):
        char = text[index]
        if char == '"':
            return out.decode("utf-8", "replace")
        if char == "\\" and index + 1 < len(text):
            octal = text[index + 1 : index + 4]
            if len(octal) == 3 and all(c in "01234567" for c in octal):
                out.append(int(octal, 8) & 0xFF)
                index += 4
                continue
            if text[index + 1] in _C_ESCAPES:
                out.append(_C_ESCAPES[text[index + 1]])
                index += 2
                continue
        out.extend(char.encode("utf-8"))
        index += 1
    return text  # Unterminated, so not a quoted name after all.


def _diff_path(header: str) -> Optional[str]:
    """Path named by a '+++ ' header line, given without the marker."""
    # Git appends a tab to the name when it contains a space.
    if header.endswith("\t"):
        header = header[:-1]
    name = _unquote_c(header) if header.startswith('"') else header
    if name == "/dev/null":
        return None
    if name.startswith("b/"):
        name = name[2:]
    return name


class _FileDiff:
    __slots__ = ("path", "added")

    def __init__(self) -> None:
        self.path: Optional[str] = None
        self.added: List[Tuple[int, str]] = []


def _parse_diff(diff_output: str) -> List[_FileDiff]:
    """
    Split a unified diff into files and their added lines.

    A line is a header only between hunks. Inside a hunk, the line counts from
    the '@@' header decide where it ends, so an added line whose content is
    "++ x" or "diff --git" is still content.
    """
    files: List[_FileDiff] = []
    current: Optional[_FileDiff] = None
    old_left = new_left = 0
    line_no = 0

    # split("\n") rather than splitlines(): content may hold \r, \f or other
    # characters splitlines() breaks on, which would throw the counts off.
    for raw in diff_output.split("\n"):
        if old_left > 0 or new_left > 0:
            marker = raw[:1]
            if marker == "+":
                if current is not None:
                    current.added.append((line_no, raw[1:]))
                line_no += 1
                new_left -= 1
                continue
            if marker == "-":
                old_left -= 1
                continue
            if marker == " " or raw == "":
                line_no += 1
                old_left -= 1
                new_left -= 1
                continue
            if marker == "\\":  # "\ No newline at end of file"
                continue
            # Anything else means the counts were wrong; read it as a header.
            old_left = new_left = 0

        if raw.startswith("diff --git "):
            current = _FileDiff()
            files.append(current)
        elif raw.startswith("+++ "):
            if current is None or current.path is not None or current.added:
                current = _FileDiff()
                files.append(current)
            current.path = _diff_path(raw[4:])
        else:
            hunk = _HUNK_HEADER_RE.match(raw)
            if hunk:
                old_left = int(hunk.group(1) if hunk.group(1) is not None else 1)
                line_no = int(hunk.group(2))
                new_left = int(hunk.group(3) if hunk.group(3) is not None else 1)
    return files


# Git's own heuristic: a NUL byte in the first 8000 bytes means binary.
_BINARY_PROBE = 8000


def _looks_binary(added: List[Tuple[int, str]]) -> bool:
    seen = 0
    for _, content in added:
        if "\0" in content[: _BINARY_PROBE - seen]:
            return True
        seen += len(content) + 1
        if seen >= _BINARY_PROBE:
            break
    return False


def scan_git_diff(diff_output: str) -> List[SecretFinding]:
    """
    Parse unified git diff output (e.g. git diff --cached -U0) and scan only
    added lines ('+...') for committed secrets. Files whose added content is
    binary (a NUL byte near the start, as git decides) are skipped.
    """
    findings: List[SecretFinding] = []

    for file_diff in _parse_diff(diff_output):
        if not file_diff.added or _looks_binary(file_diff.added):
            continue
        file_name = file_diff.path or "<unknown>"
        for line_number, added_content in file_diff.added:
            hit = _scan_line(added_content)
            if hit is not None:
                findings.append(
                    SecretFinding(
                        rule_name=hit[0],
                        file_path=file_name,
                        line_number=line_number,
                        secret_preview=hit[1],
                        line_content=added_content.strip()[:120],
                    )
                )

    return findings
