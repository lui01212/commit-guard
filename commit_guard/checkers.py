"""
Core validation logic for commit messages and staged files.
Zero external dependencies (uses only standard library).
"""

import fnmatch
import os
import re
from typing import List, Optional, Sequence, Tuple

from commit_guard.config import DEFAULT_TYPES, Config

# Kept for backward compatibility with code that imported these in v0.1.x.
# The authoritative values now live in commit_guard.config.
CONVENTIONAL_TYPES = set(DEFAULT_TYPES)

# Regex pattern for Conventional Commits
CONVENTIONAL_REGEX = re.compile(
    r"^(?P<type>[a-zA-Z]+)"  # type
    r"(?:\((?P<scope>[a-zA-Z0-9_\-./,\s]+)\))?"  # optional scope
    r"(?P<breaking>!)?"  # optional breaking marker
    r":[ ]+"  # separator
    r"(?P<subject>.+)$"  # commit subject
)

_DEFAULT_CONFIG = Config()


def _config_or_default(config: Optional[Config]) -> Config:
    return config if config is not None else _DEFAULT_CONFIG


def is_skippable_message(msg: str, config: Optional[Config] = None) -> bool:
    """
    Report whether a commit message is one git generates rather than one the
    author wrote: merges, reverts, and rebase fixup/squash markers.

    Validating these blocks ordinary git workflows, since their format is not
    the author's to control.
    """
    cfg = _config_or_default(config)
    if not cfg.skip_merge_commits:
        return False

    header = _first_meaningful_line(msg)
    if header is None:
        return False

    lowered = header.lower()
    return any(lowered.startswith(prefix) for prefix in cfg.skip_prefixes)


def _first_meaningful_line(msg: str) -> Optional[str]:
    """Return the first non-blank, non-comment line, or None."""
    for line in msg.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped
    return None


def check_commit_message(
    msg: str,
    max_header_len: Optional[int] = None,
    config: Optional[Config] = None,
) -> Tuple[bool, List[str]]:
    """
    Validate commit message against Conventional Commits standard.
    Returns (is_valid, list_of_error_messages).

    ``max_header_len`` overrides the configured limit; it is kept as a named
    parameter because v0.1.x callers passed it positionally.
    """
    cfg = _config_or_default(config)
    header_limit = max_header_len if max_header_len is not None else cfg.max_header_len

    # Generated merge/revert/fixup messages are not the author's to format.
    if is_skippable_message(msg, cfg):
        return True, []

    errors: List[str] = []

    # Strip comments (lines starting with #) and trailing whitespace
    lines = [
        line.strip()
        for line in msg.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]

    if not lines:
        return False, ["Commit message cannot be empty."]

    header = lines[0]

    if header_limit and len(header) > header_limit:
        errors.append(
            "Commit header too long ({0} chars > {1} limit).".format(
                len(header), header_limit
            )
        )

    match = CONVENTIONAL_REGEX.match(header)
    if not match:
        errors.append(
            "Header does not follow Conventional Commits format: "
            "'<type>(<scope>): <description>'.\n"
            "  Received: '{0}'\n"
            "  Allowed types: {1}".format(header, ", ".join(sorted(cfg.types)))
        )
    else:
        errors.extend(_check_header_parts(match, cfg))

    errors.extend(_check_body(msg, cfg))
    return len(errors) == 0, errors


def _check_header_parts(match: "re.Match", cfg: Config) -> List[str]:
    """Validate the type, scope and subject of a parsed header."""
    errors: List[str] = []

    ctype = match.group("type")
    if ctype not in cfg.types:
        # Catch the common near-miss of an uppercase or mixed-case type.
        if ctype.lower() in cfg.types:
            errors.append(
                "Commit type must be lowercase: '{0}' should be '{1}'.".format(
                    ctype, ctype.lower()
                )
            )
        else:
            errors.append(
                "Unknown commit type: '{0}'. Allowed: {1}".format(
                    ctype, ", ".join(sorted(cfg.types))
                )
            )

    scope = match.group("scope")
    if cfg.require_scope and not scope:
        errors.append(
            "A scope is required. Allowed scopes: {0}".format(
                ", ".join(sorted(cfg.scopes))
            )
        )
    elif scope and cfg.scopes:
        # A comma-separated scope list is valid Conventional Commits.
        for part in (p.strip() for p in scope.split(",")):
            if part and part not in cfg.scopes:
                errors.append(
                    "Unknown scope: '{0}'. Allowed: {1}".format(
                        part, ", ".join(sorted(cfg.scopes))
                    )
                )

    subject = match.group("subject").strip()
    if len(subject) < cfg.subject_min_len:
        errors.append(
            "Commit description is too short (minimum {0} characters).".format(
                cfg.subject_min_len
            )
        )
    if subject.endswith(".") and not cfg.allow_trailing_period:
        errors.append("Commit description should not end with a period ('.').")

    return errors


def _check_body(msg: str, cfg: Config) -> List[str]:
    """Validate the blank-line separator and body line lengths."""
    errors: List[str] = []

    raw_lines = [line for line in msg.splitlines() if not line.strip().startswith("#")]

    # Drop leading blank lines so the header is at index 0.
    while raw_lines and not raw_lines[0].strip():
        raw_lines.pop(0)

    if len(raw_lines) > 1 and raw_lines[1].strip() != "":
        errors.append(
            "There must be an empty blank line between commit header and body."
        )

    if cfg.max_body_line_len:
        for number, line in enumerate(raw_lines[2:], start=3):
            stripped = line.rstrip()
            if len(stripped) <= cfg.max_body_line_len:
                continue
            # Long URLs and footer trailers cannot be wrapped usefully.
            if _is_unwrappable(stripped):
                continue
            errors.append(
                "Body line {0} too long ({1} chars > {2} limit).".format(
                    number, len(stripped), cfg.max_body_line_len
                )
            )

    return errors


_TRAILER_RE = re.compile(r"^[A-Za-z][A-Za-z\-]*:\s")


def _is_unwrappable(line: str) -> bool:
    """Lines that cannot reasonably be wrapped: URLs, trailers, code."""
    if "://" in line:
        return True
    if _TRAILER_RE.match(line):
        return True
    return line.startswith(("    ", "\t", "|", ">"))


def is_allowlisted(path: str, allowlist: Sequence[str]) -> bool:
    """
    Report whether a path is exempt from sensitive-name matching.

    Each entry is a glob. It is matched against the full path and against the
    basename, so 'docs/keys/*' and '*.example' both behave as expected.
    """
    norm = path.replace("\\", "/")
    base = os.path.basename(norm)
    for pattern in allowlist:
        clean = pattern.replace("\\", "/")
        if fnmatch.fnmatch(norm, clean) or fnmatch.fnmatch(base, clean):
            return True
        # A bare directory entry exempts everything beneath it.
        if clean.endswith("/") and norm.startswith(clean):
            return True
    return False


def check_file_path(
    path: str,
    max_size_mb: Optional[float] = None,
    config: Optional[Config] = None,
    size_bytes: Optional[int] = None,
) -> Tuple[bool, List[str]]:
    """
    Validate an individual file path for size and sensitive filename patterns.
    Returns (is_valid, list_of_warning_messages).

    ``size_bytes`` supplies the staged blob size; when omitted the working-tree
    file is measured instead.
    """
    cfg = _config_or_default(config)
    size_limit = max_size_mb if max_size_mb is not None else cfg.max_size_mb

    issues: List[str] = []
    norm_path = path.replace("\\", "/")

    if not is_allowlisted(norm_path, cfg.allowlist):
        for pattern in cfg.sensitive_patterns:
            try:
                compiled = re.compile(pattern, re.IGNORECASE)
            except re.error as exc:
                issues.append(
                    "Invalid sensitive pattern {0!r}: {1}".format(pattern, exc)
                )
                continue
            if compiled.search(norm_path):
                issues.append(
                    "Potentially sensitive file detected: '{0}'. "
                    "Avoid committing secrets.".format(path)
                )
                break

    measured = size_bytes
    if measured is None and os.path.isfile(path):
        measured = os.path.getsize(path)

    if measured is not None and size_limit:
        size_mb = measured / (1024 * 1024)
        if size_mb > size_limit:
            issues.append(
                "File '{0}' exceeds max allowed size ({1:.2f} MB > {2} MB limit). "
                "Consider using Git LFS.".format(path, size_mb, size_limit)
            )

    return len(issues) == 0, issues
