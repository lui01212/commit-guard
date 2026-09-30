"""
Interactive commit wizard for writing Conventional Commits.
Inspired by Commitizen (cz commit) with zero external dependencies.
"""

import sys
from typing import List, Optional, Tuple

from commit_guard.checkers import check_commit_message
from commit_guard.config import Config
from commit_guard.gitutil import run_git

COMMIT_TYPES: List[Tuple[str, str]] = [
    ("feat", "A new feature for the user or system"),
    ("fix", "A bug fix"),
    ("docs", "Documentation only changes"),
    ("style", "Code style & formatting changes (no logic changes)"),
    ("refactor", "Code refactoring (neither fixes a bug nor adds a feature)"),
    ("perf", "A code change that improves performance"),
    ("test", "Adding or updating unit/integration tests"),
    ("build", "Changes that affect the build system or packaging"),
    ("ci", "Changes to CI/CD configuration files and scripts"),
    ("chore", "Routine maintenance tasks, dependencies, tooling"),
    ("revert", "Reverts a previous commit"),
]


def prompt_choice(prompt: str, choices: List[Tuple[str, str]]) -> str:
    """Display a numbered menu and prompt the user to select one."""
    print(f"\n{prompt}\n")
    for idx, (code, desc) in enumerate(choices, start=1):
        print(f"  [{idx:2d}] {code:10s} - {desc}")
    print()

    while True:
        try:
            raw = input("Select type (1-11): ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            sys.exit(1)

        if raw.isdigit():
            val = int(raw)
            if 1 <= val <= len(choices):
                return choices[val - 1][0]
        else:
            # User typed the code name directly
            matching = [c for c, _ in choices if c == raw.lower()]
            if matching:
                return matching[0]

        print(f"Invalid selection. Please enter a number between 1 and {len(choices)}.")


def prompt_text(prompt: str, required: bool = False, default: str = "") -> str:
    """Prompt the user for a text input."""
    suffix = f" (default: {default})" if default else ""
    suffix += " [required]" if required else " [optional, Enter to skip]"
    
    while True:
        try:
            val = input(f"{prompt}{suffix}: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            sys.exit(1)

        if not val and default:
            return default
        if not val and required:
            print("This field is required. Please provide a description.")
            continue
        return val


def prompt_yes_no(prompt: str, default: bool = False) -> bool:
    """Prompt for a yes/no question."""
    hint = "[y/N]" if not default else "[Y/n]"
    while True:
        try:
            ans = input(f"{prompt} {hint}: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            sys.exit(1)

        if not ans:
            return default
        if ans in ("y", "yes"):
            return True
        if ans in ("n", "no"):
            return False
        print("Please answer 'y' or 'n'.")


def compose_commit_message(
    commit_type: str,
    scope: str,
    subject: str,
    body: str = "",
    is_breaking: bool = False,
    breaking_description: str = "",
    issues_closed: str = "",
) -> str:
    """Format components into a standard Conventional Commit string."""
    breaking_mark = "!" if is_breaking else ""
    scope_part = f"({scope})" if scope else ""
    
    # Strip any trailing period from subject
    subject_clean = subject.rstrip(".")
    
    header = f"{commit_type}{scope_part}{breaking_mark}: {subject_clean}"
    sections = [header]

    if body.strip():
        sections.append(body.strip())

    footer_items = []
    if is_breaking and breaking_description.strip():
        footer_items.append(f"BREAKING CHANGE: {breaking_description.strip()}")

    if issues_closed.strip():
        for item in issues_closed.split(","):
            clean_item = item.strip()
            if clean_item:
                if not clean_item.lower().startswith(("closes", "fixes", "resolves")):
                    clean_item = f"Closes {clean_item}"
                footer_items.append(clean_item)

    if footer_items:
        sections.append("\n".join(footer_items))

    return "\n\n".join(sections)


def run_wizard(config: Optional[Config] = None, dry_run: bool = False) -> int:
    """
    Run interactive wizard in terminal to construct and execute a Conventional Commit.
    """
    print("\n" + "=" * 60)
    print("🛡️  commit-shield: Interactive Conventional Commit Wizard")
    print("=" * 60)

    # 1. Type
    commit_type = prompt_choice("Select the type of change you are committing:", COMMIT_TYPES)

    # 2. Scope
    scope = prompt_text("Scope of this change (e.g. auth, api, cli, config)")

    # 3. Subject
    subject = prompt_text("Short imperative description (e.g. add google login)", required=True)

    # 4. Long Body
    print("\nDetailed body description (optional). Press Enter twice to finish:")
    body_lines = []
    while True:
        try:
            line = input()
        except (EOFError, KeyboardInterrupt):
            break
        if not line and (not body_lines or not body_lines[-1]):
            break
        body_lines.append(line)
    body = "\n".join(body_lines).strip()

    # 5. Breaking change
    is_breaking = prompt_yes_no("Are there any BREAKING CHANGES in this commit?", default=False)
    breaking_desc = ""
    if is_breaking:
        breaking_desc = prompt_text("Describe the breaking changes", required=True)

    # 6. Issue reference
    issues = prompt_text("Issues closed by this commit (e.g. #123, PROJ-45)")

    # Compose
    commit_msg = compose_commit_message(
        commit_type=commit_type,
        scope=scope,
        subject=subject,
        body=body,
        is_breaking=is_breaking,
        breaking_description=breaking_desc,
        issues_closed=issues,
    )

    # Validate composed message
    valid, errors = check_commit_message(commit_msg, config=config)
    print("\n" + "-" * 60)
    print("Generated Commit Message:")
    print("-" * 60)
    print(commit_msg)
    print("-" * 60)

    if not valid:
        print("\n❌ Warning: Composed message failed validation:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        print()

    if dry_run:
        print("[Dry Run] Commit message generated. Not committing.")
        return 0

    # Confirmation
    confirm = prompt_yes_no("Proceed to commit staged changes with this message?", default=True)
    if not confirm:
        print("Commit aborted.")
        return 0

    try:
        run_git(["commit", "-m", commit_msg])
        print("✅ Commit created successfully!")
        return 0
    except Exception as e:
        print(f"❌ Git commit failed: {e}", file=sys.stderr)
        return 1
