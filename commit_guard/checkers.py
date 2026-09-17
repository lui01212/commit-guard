"""
Core validation logic for commit messages and staged files.
Zero external dependencies (uses only standard library).
"""

import os
import re
from typing import Tuple, List

# Conventional Commit types
CONVENTIONAL_TYPES = {
    "feat",      # New feature
    "fix",       # Bug fix
    "docs",      # Documentation only
    "style",     # Formatting, missing semi colons, etc
    "refactor",  # Code change that neither fixes a bug nor adds a feature
    "perf",      # Performance improvement
    "test",      # Adding or fixing tests
    "build",     # Build system or external dependencies
    "ci",        # CI configuration files and scripts
    "chore",     # Maintenance tasks, toolings
    "revert",    # Reverting a previous commit
}

# Regex pattern for Conventional Commits
CONVENTIONAL_REGEX = re.compile(
    r"^(?P<type>[a-z]+)"                # type (lowercase)
    r"(?:\((?P<scope>[a-zA-Z0-9_\-./]+)\))?"  # optional scope
    r"(?P<breaking>!)?"                  # optional breaking change indicator
    r":\s+"                             # separator
    r"(?P<subject>.+)$"                  # commit subject
)

# Sensitive patterns that should never be committed
SENSITIVE_PATTERNS = [
    re.compile(r"(^|[/\\])\.env($|\..*)"),          # .env, .env.local, .env.prod
    re.compile(r"(^|[/\\]).*\.(pem|key|pkcs12|pfx|p12)$", re.IGNORECASE), # Private keys / certificates
    re.compile(r"(^|[/\\])id_(rsa|dsa|ed25519|ecdsa)($|\..*)", re.IGNORECASE), # SSH private keys
    re.compile(r"(^|[/\\]).*(secret|password|credential).*\.(json|yaml|yml|txt)$", re.IGNORECASE),
]

def check_commit_message(msg: str, max_header_len: int = 72) -> Tuple[bool, List[str]]:
    """
    Validate commit message against Conventional Commits standard.
    Returns (is_valid, list_of_error_messages).
    """
    errors = []
    
    # Strip comments (lines starting with #) and trailing whitespace
    lines = [
        line.strip() for line in msg.splitlines() 
        if line.strip() and not line.strip().startswith("#")
    ]
    
    if not lines:
        return False, ["Commit message cannot be empty."]
        
    header = lines[0]
    
    # Check header length
    if len(header) > max_header_len:
        errors.append(
            f"Commit header too long ({len(header)} chars > {max_header_len} limit)."
        )
        
    # Check regex match
    match = CONVENTIONAL_REGEX.match(header)
    if not match:
        errors.append(
            f"Header does not follow Conventional Commits format: '<type>(<scope>): <description>'.\n"
            f"  Received: '{header}'\n"
            f"  Allowed types: {', '.join(sorted(CONVENTIONAL_TYPES))}"
        )
    else:
        ctype = match.group("type")
        if ctype not in CONVENTIONAL_TYPES:
            errors.append(
                f"Unknown commit type: '{ctype}'. Allowed: {', '.join(sorted(CONVENTIONAL_TYPES))}"
            )
            
        subject = match.group("subject").strip()
        if len(subject) < 3:
            errors.append("Commit description is too short (minimum 3 characters).")
            
        if subject.endswith("."):
            errors.append("Commit description should not end with a period ('.').")
            
    # Check blank line between header and body if body exists
    raw_lines = [l for l in msg.splitlines() if not l.strip().startswith("#")]
    if len(raw_lines) > 1 and raw_lines[1].strip() != "":
        errors.append("There must be an empty blank line between commit header and body.")
        
    return len(errors) == 0, errors


def check_file_path(path: str, max_size_mb: float = 10.0) -> Tuple[bool, List[str]]:
    """
    Validate an individual file path for size and sensitive filename patterns.
    Returns (is_valid, list_of_warning_messages).
    """
    issues = []
    
    # Check sensitive filename patterns
    norm_path = path.replace("\\", "/")
    for pattern in SENSITIVE_PATTERNS:
        if pattern.search(norm_path):
            issues.append(f"Potentially sensitive file detected: '{path}'. Avoid committing secrets.")
            break
            
    # Check file size if file exists locally
    if os.path.isfile(path):
        size_bytes = os.path.getsize(path)
        size_mb = size_bytes / (1024 * 1024)
        if size_mb > max_size_mb:
            issues.append(
                f"File '{path}' exceeds max allowed size ({size_mb:.2f} MB > {max_size_mb} MB limit). "
                f"Consider using Git LFS."
            )
            
    return len(issues) == 0, issues
