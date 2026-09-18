"""
Tests for commit_guard.cli.

These drive main(argv) directly rather than spawning subprocesses, so exit
codes and stderr text are asserted without depending on how the package was
installed. Tests that touch git build a throwaway repository instead of using
the one this suite runs inside.
"""

import contextlib
import io
import os
import shutil
import subprocess
import tempfile
import unittest

from commit_guard.cli import (
    EXIT_ERROR,
    EXIT_FAIL,
    EXIT_OK,
    HOOK_MARKER,
    build_parser,
    main,
)

GIT = shutil.which("git")


@contextlib.contextmanager
def captured():
    """Capture stdout and stderr, yielding both buffers."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        yield out, err


@contextlib.contextmanager
def chdir(path):
    previous = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def run(argv):
    """Run main(argv), returning (exit_code, stdout, stderr)."""
    with captured() as (out, err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


class TestParser(unittest.TestCase):
    def test_no_subcommand_prints_help_and_succeeds(self):
        code, out, _ = run([])
        self.assertEqual(code, EXIT_OK)
        self.assertIn("usage", out.lower())

    def test_version_exits_zero(self):
        from commit_guard import __version__

        with self.assertRaises(SystemExit) as ctx, captured() as (out, _):
            main(["--version"])
        self.assertEqual(ctx.exception.code, 0)
        self.assertIn(__version__, out.getvalue())

    def test_length_flags_default_to_none_so_config_can_win(self):
        parser = build_parser()
        msg_args = parser.parse_args(["check-msg", "-m", "feat: x"])
        self.assertIsNone(msg_args.max_header_len)
        file_args = parser.parse_args(["check-files", "a.txt"])
        self.assertIsNone(file_args.max_size_mb)

    def test_unknown_subcommand_is_rejected(self):
        with self.assertRaises(SystemExit), captured():
            main(["not-a-command"])


class TestCheckMsg(unittest.TestCase):
    def test_valid_message_exits_ok(self):
        code, out, _ = run(["check-msg", "-m", "feat(auth): add oauth2 login"])
        self.assertEqual(code, EXIT_OK)
        self.assertIn("valid", out.lower())

    def test_invalid_message_exits_fail(self):
        code, _, err = run(["check-msg", "-m", "fixed stuff"])
        self.assertEqual(code, EXIT_FAIL)
        self.assertIn("validation failed", err.lower())

    def test_merge_commit_is_skipped(self):
        code, _, _ = run(["check-msg", "-m", "Merge branch 'main' into feature/x"])
        self.assertEqual(code, EXIT_OK)

    def test_quiet_suppresses_success_output(self):
        code, out, _ = run(["check-msg", "-m", "feat: add thing", "--quiet"])
        self.assertEqual(code, EXIT_OK)
        self.assertEqual(out, "")

    def test_quiet_still_reports_failures(self):
        code, _, err = run(["check-msg", "-m", "nope", "--quiet"])
        self.assertEqual(code, EXIT_FAIL)
        self.assertNotEqual(err, "")

    def test_missing_file_is_a_tool_error_not_a_validation_failure(self):
        code, _, err = run(["check-msg", os.path.join("no", "such", "file")])
        self.assertEqual(code, EXIT_ERROR)
        self.assertIn("not found", err.lower())


class TestCheckFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="cg-files-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def make(self, name, size=16):
        path = os.path.join(self.tmp, name)
        with open(path, "wb") as handle:
            handle.write(b"x" * size)
        return path

    def test_clean_explicit_files_exit_ok(self):
        path = self.make("main.py")
        code, out, _ = run(["check-files", path])
        self.assertEqual(code, EXIT_OK)
        self.assertIn("clean", out.lower())

    def test_sensitive_file_warns_but_exits_ok_without_strict(self):
        path = self.make(".env")
        code, _, err = run(["check-files", path])
        self.assertEqual(code, EXIT_OK)
        self.assertIn("warning", err.lower())

    def test_sensitive_file_fails_with_strict(self):
        path = self.make(".env")
        code, _, err = run(["check-files", path, "--strict"])
        self.assertEqual(code, EXIT_FAIL)
        self.assertIn("strict", err.lower())

    def test_allowlisted_example_file_passes_strict(self):
        path = self.make(".env.example")
        code, _, _ = run(["check-files", path, "--strict"])
        self.assertEqual(code, EXIT_OK)

    def test_oversized_file_fails_with_strict(self):
        path = self.make("big.bin", size=4096)
        code, _, err = run(["check-files", path, "--strict", "--max-size-mb", "0.001"])
        self.assertEqual(code, EXIT_FAIL)
        self.assertIn("exceeds max allowed size", err.lower())

    def test_missing_explicit_config_is_a_tool_error(self):
        path = self.make("main.py")
        code, _, err = run(
            ["check-files", path, "--config", os.path.join(self.tmp, "nope.toml")]
        )
        self.assertEqual(code, EXIT_ERROR)
        self.assertIn("not found", err.lower())

    def test_config_file_is_honoured(self):
        path = self.make("internal-notes.txt")
        cfg = os.path.join(self.tmp, "cg.toml")
        with open(cfg, "w", encoding="utf-8") as handle:
            handle.write('extra_sensitive_patterns = ["internal-notes"]\n')
        code, _, err = run(["check-files", path, "--strict", "--config", cfg])
        self.assertEqual(code, EXIT_FAIL)
        self.assertIn("internal-notes", err)

    def test_no_config_ignores_discovered_files(self):
        path = self.make("internal-notes.txt")
        cfg = os.path.join(self.tmp, "cg.toml")
        with open(cfg, "w", encoding="utf-8") as handle:
            handle.write('extra_sensitive_patterns = ["internal-notes"]\n')
        # --no-config wins over --config, so the custom pattern is dropped.
        code, _, _ = run(
            ["check-files", path, "--strict", "--config", cfg, "--no-config"]
        )
        self.assertEqual(code, EXIT_OK)


@unittest.skipIf(GIT is None, "git is not installed")
class TestHookLifecycle(unittest.TestCase):
    """install/uninstall against a real, throwaway git repository."""

    def setUp(self):
        self.repo = os.path.realpath(tempfile.mkdtemp(prefix="cg-repo-"))
        self.addCleanup(shutil.rmtree, self.repo, True)
        subprocess.run(
            [GIT, "init", "-q"],
            cwd=self.repo,
            check=True,
            capture_output=True,
        )
        self.hooks = os.path.join(self.repo, ".git", "hooks")

    def hook(self, name):
        return os.path.join(self.hooks, name)

    def read(self, path):
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    def test_install_writes_both_hooks(self):
        with chdir(self.repo):
            code, out, _ = run(["install"])
        self.assertEqual(code, EXIT_OK)
        for name in ("commit-msg", "pre-commit"):
            path = self.hook(name)
            self.assertTrue(os.path.isfile(path), "{0} not installed".format(name))
            body = self.read(path)
            self.assertIn(HOOK_MARKER, body)
            self.assertIn("commit_guard.cli", body)
        self.assertIn('check-msg "$1"', self.read(self.hook("commit-msg")))
        self.assertIn("check-files --strict", self.read(self.hook("pre-commit")))

    def test_installed_hook_uses_a_real_interpreter_path(self):
        # A bare "python" would break where the interpreter is not on PATH.
        with chdir(self.repo):
            run(["install", "--quiet"])
        body = self.read(self.hook("commit-msg"))
        self.assertNotIn('"python" -m', body)

    def test_install_msg_only_skips_pre_commit(self):
        with chdir(self.repo):
            code, _, _ = run(["install", "--msg-only", "--quiet"])
        self.assertEqual(code, EXIT_OK)
        self.assertTrue(os.path.isfile(self.hook("commit-msg")))
        self.assertFalse(os.path.exists(self.hook("pre-commit")))

    def test_install_is_idempotent(self):
        with chdir(self.repo):
            run(["install", "--quiet"])
            code, _, _ = run(["install", "--quiet"])
        self.assertEqual(code, EXIT_OK)
        # Re-installing over our own hook must not create a backup.
        self.assertFalse(os.path.exists(self.hook("commit-msg") + ".bak"))

    def write_foreign(self, name, body="#!/bin/sh\necho someone elses hook\n"):
        path = self.hook(name)
        if not os.path.isdir(self.hooks):
            os.makedirs(self.hooks)
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(body)
        return path

    def test_install_refuses_to_clobber_a_foreign_hook(self):
        path = self.write_foreign("commit-msg")
        original = self.read(path)
        with chdir(self.repo):
            code, _, err = run(["install"])
        self.assertEqual(code, EXIT_ERROR)
        self.assertIn("refusing to overwrite", err.lower())
        # The foreign hook must be untouched and no backup invented.
        self.assertEqual(self.read(path), original)
        self.assertFalse(os.path.exists(path + ".bak"))

    def test_force_replaces_foreign_hook_and_keeps_backup(self):
        path = self.write_foreign("commit-msg")
        original = self.read(path)
        with chdir(self.repo):
            code, _, _ = run(["install", "--force", "--quiet"])
        self.assertEqual(code, EXIT_OK)
        self.assertIn(HOOK_MARKER, self.read(path))
        self.assertEqual(self.read(path + ".bak"), original)

    def test_foreign_framework_is_named_in_the_refusal(self):
        self.write_foreign(
            "commit-msg", "#!/bin/sh\n# File generated by pre-commit: ...\n"
        )
        with chdir(self.repo):
            _, _, err = run(["install"])
        self.assertIn("pre-commit framework hook", err.lower())

    def test_uninstall_removes_our_hooks(self):
        with chdir(self.repo):
            run(["install", "--quiet"])
            code, _, _ = run(["uninstall", "--quiet"])
        self.assertEqual(code, EXIT_OK)
        self.assertFalse(os.path.exists(self.hook("commit-msg")))
        self.assertFalse(os.path.exists(self.hook("pre-commit")))

    def test_uninstall_restores_a_backed_up_hook(self):
        path = self.write_foreign("commit-msg")
        original = self.read(path)
        with chdir(self.repo):
            run(["install", "--force", "--quiet"])
            code, _, _ = run(["uninstall", "--quiet"])
        self.assertEqual(code, EXIT_OK)
        # Uninstall must put the user's own hook back, not leave a .bak behind.
        self.assertEqual(self.read(path), original)
        self.assertFalse(os.path.exists(path + ".bak"))

    def test_uninstall_leaves_foreign_hooks_alone(self):
        path = self.write_foreign("pre-commit")
        original = self.read(path)
        with chdir(self.repo):
            code, out, _ = run(["uninstall"])
        self.assertEqual(code, EXIT_OK)
        self.assertEqual(self.read(path), original)
        self.assertIn("not ours", out.lower())

    def test_uninstall_without_hooks_is_not_an_error(self):
        with chdir(self.repo):
            code, out, _ = run(["uninstall"])
        self.assertEqual(code, EXIT_OK)
        self.assertIn("no commit-guard hooks", out.lower())
