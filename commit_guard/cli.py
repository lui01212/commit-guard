"""
CLI entry-point for commit-guard.
Supports standalone usage, git hook usage, and pre-commit framework integration.
"""

import argparse
import os
import stat
import sys
from typing import List, Optional

from commit_guard import __version__
from commit_guard.checkers import check_commit_message, check_file_path
from commit_guard.config import Config, ConfigError, load_config
from commit_guard.gitutil import (
    GitError,
    git_dir,
    is_git_repo,
    staged_blob_size,
    staged_files,
)

EXIT_OK = 0
EXIT_FAIL = 1
# Distinct from a validation failure so scripts can tell "the commit is bad"
# apart from "commit-guard could not do its job".
EXIT_ERROR = 2

HOOK_MARKER = "# commit-guard automatic hook"


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

    if not files_to_check:
        if not args.quiet:
            print("[commit-guard] No staged files to check.")
        return EXIT_OK

    has_issues = False
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
            for issue in issues:
                print("[commit-guard] Warning: {0}".format(issue), file=sys.stderr)

    if has_issues and args.strict:
        print(
            "\n[commit-guard] Aborting commit due to sensitive or oversized "
            "files (strict mode).",
            file=sys.stderr,
        )
        return EXIT_FAIL

    if not has_issues and not args.quiet:
        print(
            "[commit-guard] Checked {0} file(s). All clean! [OK]".format(
                len(files_to_check)
            )
        )

    return EXIT_OK


def _hook_script(command: str) -> str:
    """Build a hook body that calls the interpreter commit-guard runs under."""
    # sys.executable, not a bare "python": the hook must reach the interpreter
    # commit-guard is installed into, which may not be first on PATH (or may
    # not be called "python" at all).
    interpreter = sys.executable.replace("\\", "/") or "python"
    return (
        "#!/usr/bin/env sh\n" "{marker}\n" '"{python}" -m commit_guard.cli {command}\n'
    ).format(marker=HOOK_MARKER, python=interpreter, command=command)


def _is_our_hook(path: str) -> bool:
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return HOOK_MARKER in handle.read()
    except OSError:
        return False


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
    hooks_dir: str, name: str, command: str, force: bool, quiet: bool
) -> int:
    """Install a single hook, refusing to destroy anything already there."""
    target = os.path.join(hooks_dir, name)
    script = _hook_script(command)

    if os.path.exists(target) and not _is_our_hook(target):
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

        backup = target + ".bak"
        try:
            # Replace an older backup rather than failing on Windows, where
            # os.replace onto an existing path is the only atomic option.
            os.replace(target, backup)
        except OSError as exc:
            print(
                "[commit-guard] Error: cannot back up {0}: {1}".format(target, exc),
                file=sys.stderr,
            )
            return EXIT_ERROR
        if not quiet:
            print("[commit-guard] Existing hook backed up to {0}".format(backup))

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
    """Install git hooks directly into .git/hooks/."""
    if not is_git_repo():
        return _fail("not inside a git working tree")

    try:
        hooks_dir = os.path.join(git_dir(), "hooks")
    except GitError as exc:
        return _fail(str(exc))

    try:
        os.makedirs(hooks_dir, exist_ok=True)
    except OSError as exc:
        return _fail("cannot create {0}: {1}".format(hooks_dir, exc))

    # commit-msg validates the message; pre-commit guards the staged files.
    planned = [("commit-msg", 'check-msg "$1"')]
    if not args.msg_only:
        planned.append(("pre-commit", "check-files --strict"))

    status = EXIT_OK
    for name, command in planned:
        result = _install_one(hooks_dir, name, command, args.force, args.quiet)
        if result != EXIT_OK:
            status = result

    if status == EXIT_OK and not args.quiet:
        print("[commit-guard] Done. Run 'commit-guard uninstall' to remove.")
    return status


def cmd_uninstall(args: argparse.Namespace) -> int:
    """Remove commit-guard hooks, restoring any backup we made."""
    if not is_git_repo():
        return _fail("not inside a git working tree")

    try:
        hooks_dir = os.path.join(git_dir(), "hooks")
    except GitError as exc:
        return _fail(str(exc))

    removed = 0
    for name in ("commit-msg", "pre-commit"):
        target = os.path.join(hooks_dir, name)
        if not os.path.exists(target):
            continue
        if not _is_our_hook(target):
            if not args.quiet:
                print("[commit-guard] Leaving {0} alone (not ours).".format(target))
            continue

        backup = target + ".bak"
        try:
            if os.path.exists(backup):
                os.replace(backup, target)
                if not args.quiet:
                    print("[commit-guard] Restored previous hook: {0}".format(target))
            else:
                os.remove(target)
                if not args.quiet:
                    print("[commit-guard] Removed hook: {0}".format(target))
            removed += 1
        except OSError as exc:
            return _fail("cannot remove {0}: {1}".format(target, exc))

    if removed == 0 and not args.quiet:
        print("[commit-guard] No commit-guard hooks were installed.")
    return EXIT_OK


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="commit-guard",
        description="Fast, zero-dependency Git commit message and staged file linter.",
    )
    parser.add_argument(
        "--version", action="version", version="%(prog)s {0}".format(__version__)
    )

    subparsers = parser.add_subparsers(dest="subcommand", help="Available subcommands")

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

    p_files = subparsers.add_parser(
        "check-files", help="Validate staged files for size and secrets"
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
        help="Fail with exit 1 if sensitive files detected",
    )
    _add_config_flags(p_files)
    p_files.set_defaults(func=cmd_check_files)

    p_inst = subparsers.add_parser(
        "install", help="Install hook scripts into local .git/hooks/"
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
        "uninstall", help="Remove commit-guard hooks from local .git/hooks/"
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
