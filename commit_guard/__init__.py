"""
commit-shield: Lightweight Git commit message, secret leak and staged file linter.
"""

from commit_guard.checkers import check_commit_message, check_file_path
from commit_guard.secret_scanner import scan_git_diff, scan_text

__version__ = "0.3.0"
__all__ = [
    "check_commit_message",
    "check_file_path",
    "scan_git_diff",
    "scan_text",
]
