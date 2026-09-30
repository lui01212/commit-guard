"""
Content-level secret and credential scanner for git staged diffs and source files.
Inspired by AWS git-secrets and gitleaks.
Zero external dependencies.
"""

from dataclasses import dataclass
import re
from typing import List, Optional, Tuple


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


# Precompiled secret detection rules
SECRET_RULES: List[Tuple[str, re.Pattern]] = [
    (
        "AWS Access Key ID",
        re.compile(r"\b(?:AKIA|ABIA|ACCA|ASIA)[A-Z0-9]{16}\b")
    ),
    (
        "GitHub Token",
        re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36}\b|\bgithub_pat_[A-Za-z0-9_]{82}\b")
    ),
    (
        "OpenAI API Key",
        re.compile(r"\bsk-[a-zA-Z0-9]{20,64}\b")
    ),
    (
        "Anthropic API Key",
        re.compile(r"\bsk-ant-[a-zA-Z0-9_\-]{20,128}\b")
    ),
    (
        "Slack Token",
        re.compile(r"\bxox[baprs](?:-[0-9a-zA-Z]{5,48})+\b")
    ),
    (
        "Stripe Secret / Live Key",
        re.compile(r"\b(?:sk|pk)_(?:test|live)_[0-9a-zA-Z]{24,34}\b")
    ),
    (
        "Google Cloud API Key",
        re.compile(r"\bAIza[0-9A-Za-z\-_]{35}\b")
    ),
    (
        "PEM Private Key Header",
        re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----")
    ),
    (
        "Generic Hardcoded Password / Secret",
        re.compile(
            r"""(?i)(?:[a-zA-Z0-9_\-\.]*(?:password|passwd|secret|api_key|apikey|access_token|auth_token))\s*[:=]\s*['"]([A-Za-z0-9_\-!@#$%^&*()+=]{8,})['"]"""
        )
    ),
]

# Patterns that indicate false positives / test dummies / placeholders
ALLOWLIST_PATTERNS = [
    re.compile(r"(?i)(?:example|placeholder|dummy|test_fake|<YOUR_|fake_|YOUR_KEY|REPLACE_ME)"),
    re.compile(r"AKIAIOSFODNN7EXAMPLE"),
]


def _is_false_positive(line: str, matched_secret: str) -> bool:
    """Check if match appears to be an obvious mock/placeholder or comment."""
    for pattern in ALLOWLIST_PATTERNS:
        if pattern.search(line) or pattern.search(matched_secret):
            return True
    return False


def scan_text(text: str, file_path: str = "<staged>") -> List[SecretFinding]:
    """Scan arbitrary text content line by line for secrets."""
    findings: List[SecretFinding] = []
    lines = text.splitlines()

    for idx, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped:
            continue

        for rule_name, regex in SECRET_RULES:
            match = regex.search(stripped)
            if match:
                matched_secret = match.group(0)
                if not _is_false_positive(stripped, matched_secret):
                    findings.append(
                        SecretFinding(
                            rule_name=rule_name,
                            file_path=file_path,
                            line_number=idx,
                            secret_preview=_mask_secret(matched_secret),
                            line_content=stripped[:120],
                        )
                    )
                    break

    return findings


def scan_git_diff(diff_output: str) -> List[SecretFinding]:
    """
    Parse unified git diff output (e.g. git diff --cached -U0) and scan only
    added lines ('+...') for committed secrets.
    """
    findings: List[SecretFinding] = []
    current_file: Optional[str] = None
    current_line_num: int = 0

    diff_file_re = re.compile(r"^\+\+\+\s+b/(.*)$")
    hunk_header_re = re.compile(r"^@@\s+-\d+(?:,\d+)?\s+\+(\d+)(?:,\d+)?\s+@@")

    for raw_line in diff_output.splitlines():
        # Check for new file header: +++ b/path/to/file
        file_match = diff_file_re.match(raw_line)
        if file_match:
            current_file = file_match.group(1).strip()
            continue

        # Check for hunk header: @@ -1,1 +10,1 @@
        hunk_match = hunk_header_re.match(raw_line)
        if hunk_match:
            current_line_num = int(hunk_match.group(1))
            continue

        # Check for added lines
        if raw_line.startswith("+") and not raw_line.startswith("+++"):
            added_content = raw_line[1:]  # strip leading '+'
            file_name = current_file or "<unknown>"

            # Scan the line for secrets
            for rule_name, regex in SECRET_RULES:
                match = regex.search(added_content)
                if match:
                    matched_secret = match.group(0)
                    if not _is_false_positive(added_content, matched_secret):
                        findings.append(
                            SecretFinding(
                                rule_name=rule_name,
                                file_path=file_name,
                                line_number=current_line_num,
                                secret_preview=_mask_secret(matched_secret),
                                line_content=added_content.strip()[:120],
                            )
                        )
                        break

            current_line_num += 1
        elif not raw_line.startswith("-"):
            current_line_num += 1

    return findings
