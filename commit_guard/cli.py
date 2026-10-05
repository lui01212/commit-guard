"""
CLI entry-point for commit-guard.
Supports standalone usage, git hook usage, and pre-commit framework integration.
"""

import argparse
import json
import os
import stat
import sys
from typing import List, Optional, Set

from commit_guard import __version__
from commit_guard.checkers import check_commit_message, check_file_path
from commit_guard.config import Config, ConfigError, load_config
from commit_guard.gitutil import (
    GitError,
    git_config,
    git_dir,
    hooks_dir,
    is_git_repo,
    repo_root,
    staged_blob_size,
    staged_diff,
    staged_files,
)
from commit_guard.secret_scanner import scan_git_diff
from commit_guard.wizard import run_wizard

EXIT_OK = 0
EXIT_FAIL = 1
# Distinct from a validation failure so scripts can tell "the commit is bad"
# apart from "commit-guard could not do its job".
EXIT_ERROR = 2

HOOK_MARKER = "# commit-guard automatic hook"
# Names the backup an install made of a foreign hook, so that uninstall
# restores that file and never one commit-guard did not create.
BACKUP_MARKER = "# commit-guard-backup:"


def _resolve_config(args: argparse.Namespace) -> Config:
    """Load configuration, honouring --config and --no-config."""
    return load_config(
        config_path=getattr(args, "config", None),
        use_files=not getattr(args, "no_config", False),
    )


def _fail(message: str) -> int:
    print("[commit-guard] Error: {0}".format(message), file=sys.stderr)
    return EXIT_ERROR


def cmd_check_msg(args: argparse.Namespace) -> int:
    """Validate a commit message file or text argument."""
    try:
        config = _resolve_config(args)
    except ConfigError as exc:
        return _fail(str(exc))

    if args.file:
        if not os.path.exists(args.file):
            return _fail("commit message file not found: {0}".format(args.file))
        try:
            with open(args.file, encoding="utf-8", errors="replace") as handle:
                msg = handle.read()
        except OSError as exc:
            return _fail("cannot read {0}: {1}".format(args.file, exc))
    elif args.message is not None:
        msg = args.message
    else:
        msg = sys.stdin.read()

    is_valid, errors = check_commit_message(
        msg, max_header_len=args.max_header_len, config=config
    )

    if getattr(args, "json", False):
        res = {
            "valid": is_valid,
            "errors": errors,
            "message": msg.strip(),
        }
        print(json.dumps(res, indent=2))
        return EXIT_OK if is_valid else EXIT_FAIL

    if is_valid:
        if not args.quiet:
            print("[commit-guard] Commit message valid. [OK]")
        return EXIT_OK

    print("\n[commit-guard] Commit message validation failed:", file=sys.stderr)
    for err in errors:
        print("  - {0}".format(err), file=sys.stderr)
    print(
        "\nGuidelines: Format must be '<type>(<optional-scope>): <description>'",
        file=sys.stderr,
    )
    print("Example: feat(auth): add google oauth2 login provider\n", file=sys.stderr)
    return EXIT_FAIL


def cmd_check_files(args: argparse.Namespace) -> int:
    """Validate files for size and sensitive patterns."""
    try:
        config = _resolve_config(args)
    except ConfigError as exc:
        return _fail(str(exc))

    from_index = False
    if args.files:
        files_to_check: List[str] = list(args.files)
    else:
        # No paths given, so ask git what is staged. A git failure here must
        # not be mistaken for an empty changeset.
        try:
            files_to_check = staged_files()
            from_index = True
        except GitError as exc:
            return _fail("{0}\nRun with explicit file paths to skip git.".format(exc))

    has_issues = False
    all_issues: List[str] = []
    for path in files_to_check:
        # Prefer the staged blob size: the working tree may have moved on.
        size_bytes = staged_blob_size(path) if from_index else None
        is_valid, issues = check_file_path(
            path,
            max_size_mb=args.max_size_mb,
            config=config,
            size_bytes=size_bytes,
        )
        if not is_valid:
            has_issues = True
            all_issues.extend(issues)
            if not getattr(args, "json", False):
                for issue in issues:
                    print("[commit-guard] Warning: {0}".format(issue), file=sys.stderr)

    # Content-level secret scan on staged git diff (inspired by git-secrets / gitleaks)
    secret_issues: List[str] = []
    if not getattr(args, "no_secrets", False):
        try:
            diff_text = _staged_diff_for(None if from_index else files_to_check)
        except GitError as exc:
            return _fail(
                "{0}\nCannot scan staged changes for secrets; use --no-secrets "
                "to skip the scan.".format(exc)
            )
        if diff_text:
            findings = scan_git_diff(diff_text)
            for f in findings:
                msg = f.format_message()
                secret_issues.append(msg)
                all_issues.append(msg)
                has_issues = True
                if not getattr(args, "json", False):
                    print(f"[commit-guard] Warning: {msg}", file=sys.stderr)

    if getattr(args, "json", False):
        payload = {
            "valid": not has_issues,
            "issues": all_issues,
            "secret_issues": secret_issues,
            "files_checked": len(files_to_check),
        }
        print(json.dumps(payload, indent=2))
        if has_issues and args.strict:
            return EXIT_FAIL
        return EXIT_OK

    if has_issues and args.strict:
        print(
            "\n[commit-guard] Aborting commit due to sensitive, secret or oversized "
            "files (strict mode).",
            file=sys.stderr,
        )
        return EXIT_FAIL

    if not has_issues and not args.quiet:
        if not files_to_check:
            print("[commit-guard] No staged files to check.")
        else:
            print(
                "[commit-guard] Checked {0} file(s). All clean! [OK]".format(
                    len(files_to_check)
                )
            )

    return EXIT_OK


def _staged_diff_for(paths: Optional[List[str]]) -> str:
    """
    The staged diff to scan for secrets.

    ``paths`` None means everything staged. Explicit paths (as the pre-commit
    framework passes them) restrict the diff to those files; paths outside the
    repository, or any paths when not in a repository at all, have no staged
    content and are skipped.
    """
    if paths is None:
        return staged_diff()
    if not is_git_repo():
        return ""
    root = repo_root()
    inside = [rel for rel in (_repo_relative(p, root) for p in paths) if rel]
    if not inside:
        # An empty pathspec would mean "everything", not "nothing".
        return ""
    return staged_diff(inside, cwd=root)


def _repo_relative(path: str, root: str) -> Optional[str]:
    """``path`` relative to ``root`` with forward slashes, or None if outside."""
    try:
        rel = os.path.relpath(os.path.abspath(path), root)
    except ValueError:  # another drive on Windows
        return None
    if rel == os.curdir or rel == os.pardir or rel.startswith(os.pardir + os.sep):
        return None
    return rel.replace(os.sep, "/")


def _hook_script(command: str, backup: Optional[str] = None) -> str:
    """Build a hook body that calls the interpreter commit-guard runs under."""
    # sys.executable, not a bare "python": the hook must reach the interpreter
    # commit-guard is installed into, which may not be first on PATH (or may
    # not be called "python" at all).
    interpreter = sys.executable.replace("\\", "/") or "python"
    lines = ["#!/usr/bin/env sh", HOOK_MARKER]
    if backup:
        lines.append("{0} {1}".format(BACKUP_MARKER, backup))
    lines += [
        'PYTHON="{0}"'.format(interpreter),
        'if [ -x "$PYTHON" ]; then',
        '    exec "$PYTHON" -m commit_guard.cli {0}'.format(command),
        "fi",
        "# That interpreter is gone (a deleted virtualenv, an upgraded Python):",
        "# fall back to a commit-guard on PATH.",
        "if command -v commit-guard >/dev/null 2>&1; then",
        "    exec commit-guard {0}".format(command),
        "fi",
        'echo "[commit-guard] Error: $PYTHON is missing and commit-guard is not'
        ' on PATH." >&2',
        "echo \"[commit-guard] Re-run 'commit-guard install' from the environment"
        ' commit-guard is installed in." >&2',
        "exit 1",
    ]
    return "\n".join(lines) + "\n"


def _read_hook(path: str) -> Optional[str]:
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return None


def _is_our_hook(path: str) -> bool:
    content = _read_hook(path)
    return content is not None and HOOK_MARKER in content


def _recorded_backup(path: str, name: str) -> Optional[str]:
    """The backup file name our hook at ``path`` recorded, if any."""
    content = _read_hook(path) or ""
    for line in content.splitlines():
        if line.startswith(BACKUP_MARKER):
            backup = line[len(BACKUP_MARKER) :].strip()
            # Only a sibling file named after this hook, never another path.
            if (
                backup.startswith(name + ".bak")
                and "/" not in backup
                and "\\" not in backup
            ):
                return backup
    return None


def _free_backup_name(directory: str, name: str) -> str:
    """<hook>.bak, or <hook>.bak.1, .bak.2, ... if that is already taken."""
    candidate = name + ".bak"
    counter = 0
    while os.path.lexists(os.path.join(directory, candidate)):
        counter += 1
        candidate = "{0}.bak.{1}".format(name, counter)
    return candidate


def _describe_foreign_hook(path: str) -> str:
    """Name the framework owning an existing hook, for a clearer message."""
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            content = handle.read()
    except OSError:
        return "an existing hook"
    lowered = content.lower()
    if "pre-commit" in lowered and "generated by pre-commit" in lowered:
        return "a pre-commit framework hook"
    if "husky" in lowered:
        return "a husky hook"
    if "lefthook" in lowered:
        return "a lefthook hook"
    return "an existing hook"


def _install_one(
    directory: str, name: str, command: str, force: bool, quiet: bool
) -> int:
    """Install a single hook, refusing to destroy anything already there."""
    target = os.path.join(directory, name)
    backup_name: Optional[str] = None

    if os.path.exists(target) and _is_our_hook(target):
        # Re-installing: keep the record of a backup an earlier install made.
        backup_name = _recorded_backup(target, name)
    elif os.path.exists(target):
        if not force:
            print(
                "[commit-guard] Refusing to overwrite {0} at {1}.\n"
                "  Re-run with --force to replace it (the original is kept as "
                "{2}.bak),\n"
                "  or use the pre-commit framework instead.".format(
                    _describe_foreign_hook(target), target, name
                ),
                file=sys.stderr,
            )
            return EXIT_ERROR

        # Never overwrite an existing backup: pick the next free name.
        backup_name = _free_backup_name(directory, name)
        backup = os.path.join(directory, backup_name)
        try:
            os.replace(target, backup)
        except OSError as exc:
            print(
                "[commit-guard] Error: cannot back up {0}: {1}".format(target, exc),
                file=sys.stderr,
            )
            return EXIT_ERROR
        if not quiet:
            print("[commit-guard] Existing hook backed up to {0}".format(backup))

    script = _hook_script(command, backup_name)
    try:
        with open(target, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(script)
        # Preserve any existing permission bits and add the execute bits.
        mode = os.stat(target).st_mode
        os.chmod(target, mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except OSError as exc:
        print(
            "[commit-guard] Error: cannot write {0}: {1}".format(target, exc),
            file=sys.stderr,
        )
        return EXIT_ERROR

    if not quiet:
        print("[commit-guard] Installed {0} hook: {1}".format(name, target))
    return EXIT_OK


def cmd_install(args: argparse.Namespace) -> int:
    """Install git hooks into the directory git runs hooks from."""
    if not is_git_repo():
        return _fail("not inside a git working tree")

    try:
        hooks_path = git_config("core.hooksPath")
        directory = hooks_dir()
    except GitError as exc:
        return _fail(str(exc))

    if hooks_path:
        # husky, lefthook and friends point core.hooksPath at a directory they
        # manage; a hook written to .git/hooks would never run.
        print(
            "[commit-guard] core.hooksPath is set to '{0}', so git runs the "
            "hooks in {1}\n"
            "  (usually managed by husky, lefthook or a similar tool), not "
            "those in .git/hooks.\n"
            "  commit-guard will not write into a hooks directory another tool "
            "manages. Call it from\n"
            "  that setup instead, for example:\n"
            '    commit-msg:  commit-guard check-msg "$1"\n'
            "    pre-commit:  commit-guard check-files --strict\n"
            "  or run 'git config --unset core.hooksPath' and then "
            "'commit-guard install' again.".format(hooks_path, directory),
            file=sys.stderr,
        )
        return EXIT_FAIL

    try:
        os.makedirs(directory, exist_ok=True)
    except OSError as exc:
        return _fail("cannot create {0}: {1}".format(directory, exc))

    # commit-msg validates the message; pre-commit guards the staged files.
    planned = [("commit-msg", 'check-msg "$1"')]
    if not args.msg_only:
        planned.append(("pre-commit", "check-files --strict"))

    status = EXIT_OK
    for name, command in planned:
        result = _install_one(directory, name, command, args.force, args.quiet)
        if result != EXIT_OK:
            status = result

    if status == EXIT_OK and not args.quiet:
        print("[commit-guard] Done. Run 'commit-guard uninstall' to remove.")
    return status


def _uninstall_dirs() -> List[str]:
    """Where our hooks can be: git's hooks directory, plus the git directory
    older versions wrote to, which differs in a linked worktree."""
    found: List[str] = []
    seen: Set[str] = set()
    for directory in (hooks_dir(), os.path.join(git_dir(), "hooks")):
        key = os.path.normcase(os.path.realpath(directory))
        if key not in seen:
            seen.add(key)
            found.append(directory)
    return found


def cmd_uninstall(args: argparse.Namespace) -> int:
    """Remove commit-guard hooks, restoring any backup we made."""
    if not is_git_repo():
        return _fail("not inside a git working tree")

    try:
        directories = _uninstall_dirs()
    except GitError as exc:
        return _fail(str(exc))

    removed = 0
    for directory in directories:
        for name in ("commit-msg", "pre-commit"):
            target = os.path.join(directory, name)
            if not os.path.exists(target):
                continue
            if not _is_our_hook(target):
                if not args.quiet:
                    print("[commit-guard] Leaving {0} alone (not ours).".format(target))
                continue

            recorded = _recorded_backup(target, name)
            backup = os.path.join(directory, recorded) if recorded else None
            try:
                if backup is not None and os.path.exists(backup):
                    os.replace(backup, target)
                    if not args.quiet:
                        print(
                            "[commit-guard] Restored previous hook: {0}".format(target)
                        )
                else:
                    os.remove(target)
                    if not args.quiet:
                        print("[commit-guard] Removed hook: {0}".format(target))
                removed += 1
            except OSError as exc:
                return _fail("cannot remove {0}: {1}".format(target, exc))

            stray = target + ".bak"
            if recorded is None and os.path.exists(stray) and not args.quiet:
                print(
                    "[commit-guard] Left {0} in place: this install did not "
                    "create it.".format(stray)
                )

    if removed == 0 and not args.quiet:
        print("[commit-guard] No commit-guard hooks were installed.")
    return EXIT_OK


def cmd_wizard(args: argparse.Namespace) -> int:
    """Run interactive Conventional Commit wizard."""
    try:
        config = _resolve_config(args)
    except ConfigError as exc:
        return _fail(str(exc))
    return run_wizard(config=config, dry_run=getattr(args, "dry_run", False))


def _add_config_flags(parser: argparse.ArgumentParser) -> None:
    """Flags shared by every validating subcommand."""
    parser.add_argument(
        "--config", help="Path to a configuration file (disables discovery)"
    )
    parser.add_argument(
        "--no-config",
        action="store_true",
        help="Ignore configuration files and use built-in defaults",
    )
    parser.add_argument(
        "-q", "--quiet", action="store_true", help="Suppress success messages"
    )
    parser.add_argument(
        "--json", action="store_true", help="Output machine-readable JSON results"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="commit-guard",
        description="Fast Git commit message, secret leak and staged file linter.",
    )
    parser.add_argument(
        "--version", action="version", version="%(prog)s {0}".format(__version__)
    )

    subparsers = parser.add_subparsers(dest="subcommand", help="Available subcommands")

    # commit / cz (interactive wizard)
    p_cz = subparsers.add_parser(
        "commit", aliases=["cz"], help="Interactive Conventional Commit wizard"
    )
    p_cz.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate commit message without committing",
    )
    _add_config_flags(p_cz)
    p_cz.set_defaults(func=cmd_wizard)

    # check-msg
    p_msg = subparsers.add_parser("check-msg", help="Validate commit message format")
    p_msg.add_argument(
        "file", nargs="?", help="Path to commit message file (e.g. .git/COMMIT_EDITMSG)"
    )
    p_msg.add_argument("-m", "--message", help="Commit message string directly")
    p_msg.add_argument(
        "--max-header-len",
        type=int,
        default=None,
        help="Max header length (default: 72, or the configured value)",
    )
    _add_config_flags(p_msg)
    p_msg.set_defaults(func=cmd_check_msg)

    # check-files / check-staged
    p_files = subparsers.add_parser(
        "check-files",
        aliases=["check-staged"],
        help="Validate staged files for size, sensitive patterns and secrets",
    )
    p_files.add_argument(
        "files", nargs="*", help="File paths to inspect (defaults to git staged files)"
    )
    p_files.add_argument(
        "--max-size-mb",
        type=float,
        default=None,
        help="Max allowed file size in MB (default: 10, or the configured value)",
    )
    p_files.add_argument(
        "--strict",
        action="store_true",
        help="Fail with exit 1 if sensitive files or secrets detected",
    )
    p_files.add_argument(
        "--no-secrets",
        action="store_true",
        help="Skip scanning staged diffs for hardcoded credentials",
    )
    _add_config_flags(p_files)
    p_files.set_defaults(func=cmd_check_files)

    p_inst = subparsers.add_parser(
        "install", help="Install commit-msg and pre-commit hooks into this repository"
    )
    p_inst.add_argument(
        "--force",
        action="store_true",
        help="Replace a foreign hook, keeping a .bak copy",
    )
    p_inst.add_argument(
        "--msg-only",
        action="store_true",
        help="Install only the commit-msg hook, not pre-commit",
    )
    p_inst.add_argument(
        "-q", "--quiet", action="store_true", help="Suppress success messages"
    )
    p_inst.set_defaults(func=cmd_install)

    p_uninst = subparsers.add_parser(
        "uninstall", help="Remove the hooks commit-guard installed"
    )
    p_uninst.add_argument(
        "-q", "--quiet", action="store_true", help="Suppress success messages"
    )
    p_uninst.set_defaults(func=cmd_uninstall)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.subcommand:
        parser.print_help()
        return EXIT_OK

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
