"""
Git interaction helpers.

Every function here either returns a trustworthy answer or raises
:class:`GitError`. Callers must not treat a git failure as "nothing to check":
v0.1.0 did, which made the file checks silently pass whenever git was missing,
the repository was unusable, or the command changed behaviour.
"""

import os
import subprocess
from typing import List, Optional

__all__ = [
    "GitError",
    "run_git",
    "is_git_repo",
    "git_dir",
    "repo_root",
    "staged_files",
    "staged_blob_size",
    "staged_diff",
]


class GitError(Exception):
    """Raised when a git command cannot be run or fails."""


def run_git(args: List[str], cwd: Optional[str] = None) -> str:
    """Run a git command and return stdout, raising GitError on any failure."""
    try:
        completed = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise GitError("git executable not found on PATH") from exc
    except OSError as exc:
        raise GitError("cannot run git: {0}".format(exc)) from exc

    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", "replace").strip()
        raise GitError(
            "git {0} failed (exit {1}){2}".format(
                " ".join(args),
                completed.returncode,
                ": " + detail if detail else "",
            )
        )
    return completed.stdout.decode("utf-8", "replace")


def is_git_repo(cwd: Optional[str] = None) -> bool:
    """Report whether cwd is inside a git working tree."""
    try:
        return run_git(["rev-parse", "--is-inside-work-tree"], cwd).strip() == "true"
    except GitError:
        return False


def git_dir(cwd: Optional[str] = None) -> str:
    """Absolute path to the repository's .git directory."""
    path = run_git(["rev-parse", "--absolute-git-dir"], cwd).strip()
    if not path:
        raise GitError("could not determine the .git directory")
    return path


def repo_root(cwd: Optional[str] = None) -> str:
    """Absolute path to the top level of the working tree."""
    path = run_git(["rev-parse", "--show-toplevel"], cwd).strip()
    if not path:
        raise GitError("could not determine the repository root")
    return os.path.normpath(path)


def staged_files(cwd: Optional[str] = None) -> List[str]:
    """
    Paths staged for commit, added/copied/modified only.

    Uses -z so that paths containing spaces, quotes or non-ASCII characters
    survive intact; the default output quotes and escapes such names.
    """
    out = run_git(["diff", "--cached", "--name-only", "--diff-filter=ACM", "-z"], cwd)
    return [entry for entry in out.split("\0") if entry]


def staged_blob_size(path: str, cwd: Optional[str] = None) -> Optional[int]:
    """
    Size in bytes of the staged blob for ``path``.

    This is the content git would actually commit, which is not necessarily
    what is on disk: the working-tree file may have been modified or deleted
    after ``git add``. Returns None when the path has no staged blob.
    """
    spec = ":{0}".format(path.replace("\\", "/"))
    try:
        out = run_git(["cat-file", "-s", spec], cwd)
    except GitError:
        return None
    try:
        return int(out.strip())
    except ValueError:
        return None


def staged_diff(cwd: Optional[str] = None) -> str:
    """Return unified diff of staged changes for secret scanning."""
    try:
        return run_git(["diff", "--cached", "-U0"], cwd)
    except GitError:
        return ""
