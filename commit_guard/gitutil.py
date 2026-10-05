"""
Git interaction helpers.

Every function here either returns a trustworthy answer or raises
:class:`GitError`. Callers must not treat a git failure as "nothing to check":
v0.1.0 did, which made the file checks silently pass whenever git was missing,
the repository was unusable, or the command changed behaviour.
"""

import os
import subprocess
from typing import Any, List, Optional, Sequence

__all__ = [
    "GitError",
    "run_git",
    "is_git_repo",
    "git_dir",
    "repo_root",
    "staged_files",
    "staged_blob_size",
    "staged_diff",
    "git_config",
    "hooks_dir",
]


class GitError(Exception):
    """Raised when a git command cannot be run or fails."""


def _spawn(args: List[str], cwd: Optional[str]) -> Any:
    try:
        return subprocess.run(
            ["git"] + args,
            cwd=cwd,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise GitError("git executable not found on PATH") from exc
    except OSError as exc:
        raise GitError("cannot run git: {0}".format(exc)) from exc


def _display_args(args: List[str]) -> List[str]:
    """Drop the ``-c key=value`` pairs pinned for parsing from error messages."""
    shown: List[str] = []
    skip = False
    for arg in args:
        if skip:
            skip = False
        elif arg == "-c":
            skip = True
        else:
            shown.append(arg)
    return shown


def run_git(args: List[str], cwd: Optional[str] = None) -> str:
    """Run a git command and return stdout, raising GitError on any failure."""
    completed = _spawn(args, cwd)
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", "replace").strip()
        raise GitError(
            "git {0} failed (exit {1}){2}".format(
                " ".join(_display_args(args)),
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


def git_config(key: str, cwd: Optional[str] = None) -> Optional[str]:
    """Value of a git config key, or None when it is not set."""
    completed = _spawn(["config", "--get", key], cwd)
    # Exit 1 is git's way of saying "not set"; anything else is a failure.
    if completed.returncode == 1:
        return None
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", "replace").strip()
        raise GitError(
            "git config --get {0} failed (exit {1}){2}".format(
                key, completed.returncode, ": " + detail if detail else ""
            )
        )
    return completed.stdout.decode("utf-8", "replace").strip()


def hooks_dir(cwd: Optional[str] = None) -> str:
    """
    Absolute path of the directory git runs hooks from.

    ``--git-path hooks`` honours core.hooksPath and, in a linked worktree,
    points at the hooks shared with the main repository rather than the
    per-worktree git directory, where git never looks for hooks.
    """
    root = repo_root(cwd)
    path = run_git(["rev-parse", "--git-path", "hooks"], root).strip()
    if not path:
        raise GitError("could not determine the hooks directory")
    if not os.path.isabs(path):
        # The output is relative to the directory git ran in: the top level.
        path = os.path.join(root, path)
    return os.path.normpath(path)


# Pin every setting that changes the shape of diff output, so parsing does not
# depend on the user's git configuration.
_DIFF_CONFIG = [
    "-c",
    "core.quotePath=false",
    "-c",
    "diff.relative=false",
    "-c",
    "diff.noprefix=false",
    "-c",
    "diff.mnemonicPrefix=false",
]


def staged_files(cwd: Optional[str] = None) -> List[str]:
    """
    Paths staged for commit: added, copied, modified, renamed (new name) or
    changed in type.

    Uses -z so that paths containing spaces, quotes or non-ASCII characters
    survive intact; the default output quotes and escapes such names.
    """
    out = run_git(
        _DIFF_CONFIG + ["diff", "--cached", "--name-only", "--diff-filter=ACMRT", "-z"],
        cwd,
    )
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


def staged_diff(
    paths: Optional[Sequence[str]] = None, cwd: Optional[str] = None
) -> str:
    """
    Unified diff of staged changes for secret scanning, with no context lines.

    ``paths`` restricts the diff to those files; they are literal paths, not
    glob patterns. ``--text`` makes git show the content of files marked
    ``-diff`` in .gitattributes, which git would otherwise hide behind
    "Binary files differ"; the scanner skips files that really are binary.
    Raises GitError rather than returning an empty diff on failure.
    """
    args = _DIFF_CONFIG + ["--literal-pathspecs"]
    args += [
        "diff",
        "--cached",
        "-U0",
        "--text",
        "--no-color",
        "--no-ext-diff",
        "--no-textconv",
        "--submodule=short",
        "--src-prefix=a/",
        "--dst-prefix=b/",
        "--diff-filter=ACMRT",
    ]
    if paths is not None:
        args.append("--")
        args.extend(paths)
    return run_git(args, cwd)
