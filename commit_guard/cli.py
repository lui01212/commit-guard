"""
CLI entry-point for commit-guard.
Supports standalone usage, git hook usage, and pre-commit framework integration.
"""

import argparse
import os
import sys
import subprocess
from typing import List

from commit_guard import __version__
from commit_guard.checkers import check_commit_message, check_file_path


def cmd_check_msg(args: argparse.Namespace) -> int:
    """Validate a commit message file or text argument."""
    msg = ""
    if args.file:
        if not os.path.exists(args.file):
            print(f"Error: Commit message file not found: {args.file}", file=sys.stderr)
            return 1
        with open(args.file, "r", encoding="utf-8", errors="replace") as f:
            msg = f.read()
    elif args.message:
        msg = args.message
    else:
        # Read from stdin
        msg = sys.stdin.read()
        
    is_valid, errors = check_commit_message(msg, max_header_len=args.max_header_len)
    
    if is_valid:
        if not args.quiet:
            print("[commit-guard] Commit message valid. [OK]")
        return 0
    else:
        print("\n[commit-guard] Commit message validation failed:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        print("\nGuidelines: Format must be '<type>(<optional-scope>): <description>'", file=sys.stderr)
        print("Example: feat(auth): add google oauth2 login provider\n", file=sys.stderr)
        return 1


def cmd_check_files(args: argparse.Namespace) -> int:
    """Validate files for size and sensitive patterns."""
    files_to_check: List[str] = []
    
    if args.files:
        files_to_check = args.files
    else:
        # If no files specified, query staged files via git
        try:
            result = subprocess.run(
                ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
                capture_output=True,
                text=True,
                check=True
            )
            files_to_check = [f.strip() for f in result.stdout.splitlines() if f.strip()]
        except Exception:
            files_to_check = []
            
    if not files_to_check:
        if not args.quiet:
            print("[commit-guard] No staged files to check.")
        return 0
        
    has_issues = False
    for path in files_to_check:
        is_valid, issues = check_file_path(path, max_size_mb=args.max_size_mb)
        if not is_valid:
            has_issues = True
            for issue in issues:
                print(f"[commit-guard] Warning: {issue}", file=sys.stderr)
                
    if has_issues and args.strict:
        print("\n[commit-guard] Aborting commit due to sensitive or oversized files (strict mode).", file=sys.stderr)
        return 1
        
    if not has_issues and not args.quiet:
        print(f"[commit-guard] Checked {len(files_to_check)} file(s). All clean! [OK]")
        
    return 0


def cmd_install(args: argparse.Namespace) -> int:
    """Install git hooks directly into .git/hooks/."""
    git_dir = os.path.join(os.getcwd(), ".git")
    if not os.path.isdir(git_dir):
        print("Error: No .git directory found in current path.", file=sys.stderr)
        return 1
        
    hooks_dir = os.path.join(git_dir, "hooks")
    os.makedirs(hooks_dir, exist_ok=True)
    
    # Install commit-msg hook
    commit_msg_hook = os.path.join(hooks_dir, "commit-msg")
    hook_script = (
        "#!/usr/bin/env sh\n"
        "# commit-guard automatic hook\n"
        "python -m commit_guard.cli check-msg \"$1\"\n"
    )
    
    with open(commit_msg_hook, "w", encoding="utf-8", newline="\n") as f:
        f.write(hook_script)
        
    # Make executable on unix
    try:
        os.chmod(commit_msg_hook, 0o755)
    except Exception:
        pass
        
    print(f"[commit-guard] Successfully installed commit-msg hook into {commit_msg_hook}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="commit-guard",
        description="Fast, zero-dependency Git commit message and staged file linter."
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    
    subparsers = parser.add_subparsers(dest="subcommand", help="Available subcommands")
    
    # check-msg
    p_msg = subparsers.add_parser("check-msg", help="Validate commit message format")
    p_msg.add_argument("file", nargs="?", help="Path to commit message file (e.g. .git/COMMIT_EDITMSG)")
    p_msg.add_argument("-m", "--message", help="Commit message string directly")
    p_msg.add_argument("--max-header-len", type=int, default=72, help="Max header length (default: 72)")
    p_msg.add_argument("-q", "--quiet", action="store_true", help="Suppress success messages")
    p_msg.set_defaults(func=cmd_check_msg)
    
    # check-files
    p_files = subparsers.add_parser("check-files", help="Validate staged files for size and secrets")
    p_files.add_argument("files", nargs="*", help="File paths to inspect (defaults to git staged files)")
    p_files.add_argument("--max-size-mb", type=float, default=10.0, help="Max allowed file size in MB (default: 10)")
    p_files.add_argument("--strict", action="store_true", help="Fail with exit 1 if sensitive files detected")
    p_files.add_argument("-q", "--quiet", action="store_true", help="Suppress success messages")
    p_files.set_defaults(func=cmd_check_files)
    
    # install
    p_inst = subparsers.add_parser("install", help="Install hook script into local .git/hooks/")
    p_inst.set_defaults(func=cmd_install)
    
    args = parser.parse_args(argv)
    
    if not args.subcommand:
        parser.print_help()
        return 0
        
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
