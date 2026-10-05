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
import sys
import tempfile
import unittest
from unittest import mock

from commit_guard.cli import (
    BACKUP_MARKER,
    EXIT_ERROR,
    EXIT_FAIL,
    EXIT_OK,
    HOOK_MARKER,
    _hook_script,
    build_parser,
    main,
)
from commit_guard.gitutil import GitError

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


def git(repo, *args):
    """Run git in ``repo`` without hooks, signing or a global identity."""
    return subprocess.run(
        [
            GIT,
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "-c",
            "commit.gpgsign=false",
        ]
        + list(args),
        cwd=repo,
        check=True,
        capture_output=True,
    )


# Assembled at runtime so no live-looking key is stored in the repository.
FAKE_AWS = "AKIA" + "Q" * 16


@unittest.skipIf(GIT is None, "git is not installed")
class TestStagedSecretScan(unittest.TestCase):
    """check-files against the index of a real, throwaway repository."""

    def setUp(self):
        self.repo = os.path.realpath(tempfile.mkdtemp(prefix="cg-scan-"))
        self.addCleanup(shutil.rmtree, self.repo, True)
        git(self.repo, "init", "-q")
        git(self.repo, "commit", "-q", "--allow-empty", "--no-verify", "-m", "init")

    def write(self, name, text, mode="w"):
        path = os.path.join(self.repo, name)
        parent = os.path.dirname(path)
        if not os.path.isdir(parent):
            os.makedirs(parent)
        with open(
            path, mode, **({} if "b" in mode else {"encoding": "utf-8"})
        ) as handle:
            handle.write(text)
        return path

    def check(self, *argv):
        with chdir(self.repo):
            return run(["check-files", "--strict", "--no-config"] + list(argv))

    def test_filenames_from_pre_commit_still_get_a_content_scan(self):
        self.write("app.py", "aws_key = {0}\n".format(FAKE_AWS))
        git(self.repo, "add", "app.py")
        code, _, err = self.check("app.py")
        self.assertEqual(code, EXIT_FAIL)
        self.assertIn("[SECRET LEAK] app.py:1", err)

    def test_content_scan_is_limited_to_the_given_files(self):
        self.write("clean.py", "x = 1\n")
        self.write("leak.py", "aws_key = {0}\n".format(FAKE_AWS))
        git(self.repo, "add", "clean.py", "leak.py")
        code, _, err = self.check("clean.py")
        self.assertEqual(code, EXIT_OK, err)

    def test_glob_characters_in_a_filename_are_literal(self):
        self.write("leak.py", "aws_key = {0}\n".format(FAKE_AWS))
        git(self.repo, "add", "leak.py")
        code, _, err = self.check("*.py")
        self.assertEqual(code, EXIT_OK, err)

    def test_rename_with_added_secret_is_scanned(self):
        self.write("lib.py", "".join("x{0} = {0}\n".format(i) for i in range(30)))
        git(self.repo, "add", "lib.py")
        git(self.repo, "commit", "-q", "--no-verify", "-m", "add lib")
        git(self.repo, "mv", "lib.py", "core.py")
        self.write("core.py", "aws_key = {0}\n".format(FAKE_AWS), mode="a")
        git(self.repo, "add", "core.py")
        code, _, err = self.check()
        self.assertEqual(code, EXIT_FAIL)
        self.assertIn("[SECRET LEAK] core.py:31", err)

    def test_rename_into_a_sensitive_name_is_flagged(self):
        self.write("settings.txt", "debug = true\n")
        git(self.repo, "add", "settings.txt")
        git(self.repo, "commit", "-q", "--no-verify", "-m", "add settings")
        git(self.repo, "mv", "settings.txt", ".env")
        code, _, err = self.check()
        self.assertEqual(code, EXIT_FAIL)
        self.assertIn("'.env'", err)

    def test_deletion_only_commit_reports_nothing_staged(self):
        self.write("gone.py", "x = 1\n")
        git(self.repo, "add", "gone.py")
        git(self.repo, "commit", "-q", "--no-verify", "-m", "add gone")
        git(self.repo, "rm", "-q", "gone.py")
        code, out, _ = self.check()
        self.assertEqual(code, EXIT_OK)
        self.assertIn("no staged files", out.lower())

    def test_user_diff_settings_do_not_hide_secrets(self):
        self.write("sub/app.py", "aws_key = {0}\n".format(FAKE_AWS))
        git(self.repo, "add", "sub/app.py")
        for key, value in (
            ("color.ui", "always"),
            ("diff.noprefix", "true"),
            ("diff.mnemonicPrefix", "true"),
            ("diff.external", "false"),
            ("diff.relative", "true"),
            ("core.quotePath", "true"),
        ):
            git(self.repo, "config", key, value)
        # Run from elsewhere in the tree: diff.relative would hide sub/.
        os.makedirs(os.path.join(self.repo, "other"))
        with chdir(os.path.join(self.repo, "other")):
            code, _, err = run(["check-files", "--strict", "--no-config"])
        self.assertEqual(code, EXIT_FAIL)
        self.assertIn("[SECRET LEAK] sub/app.py:1", err)

    def test_non_ascii_filename_is_attributed(self):
        name = "r\u00e9sum\u00e9.py"
        self.write(name, "aws_key = {0}\n".format(FAKE_AWS))
        git(self.repo, "add", name)
        code, _, err = self.check()
        self.assertEqual(code, EXIT_FAIL)
        self.assertIn("[SECRET LEAK] {0}:1".format(name), err)

    def test_file_marked_minus_diff_is_still_scanned(self):
        self.write(".gitattributes", "creds.cfg -diff\n")
        self.write("creds.cfg", "aws_key = {0}\n".format(FAKE_AWS))
        git(self.repo, "add", ".gitattributes", "creds.cfg")
        code, _, err = self.check()
        self.assertEqual(code, EXIT_FAIL)
        self.assertIn("[SECRET LEAK] creds.cfg:1", err)

    def test_real_binary_file_is_not_scanned(self):
        self.write("blob.bin", b"\0\x01\x02" + FAKE_AWS.encode() + b"\n", mode="wb")
        git(self.repo, "add", "blob.bin")
        code, _, err = self.check()
        self.assertEqual(code, EXIT_OK, err)

    def test_git_failure_during_the_scan_is_a_tool_error(self):
        self.write("app.py", "x = 1\n")
        git(self.repo, "add", "app.py")
        with mock.patch(
            "commit_guard.cli.staged_diff", side_effect=GitError("git diff exploded")
        ):
            code, _, err = self.check()
        self.assertEqual(code, EXIT_ERROR)
        self.assertIn("git diff exploded", err)

    def test_explicit_paths_outside_the_repository_are_not_diffed(self):
        outside = os.path.realpath(tempfile.mkdtemp(prefix="cg-outside-"))
        self.addCleanup(shutil.rmtree, outside, True)
        path = os.path.join(outside, "main.py")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("x = 1\n")
        code, _, err = self.check(path)
        self.assertEqual(code, EXIT_OK, err)


@unittest.skipIf(GIT is None, "git is not installed")
class TestHookLocation(unittest.TestCase):
    """Hooks must land where git will actually run them."""

    def setUp(self):
        self.repo = os.path.realpath(tempfile.mkdtemp(prefix="cg-loc-"))
        self.addCleanup(shutil.rmtree, self.repo, True)
        git(self.repo, "init", "-q")
        git(self.repo, "commit", "-q", "--allow-empty", "--no-verify", "-m", "init")
        self.hooks = os.path.join(self.repo, ".git", "hooks")

    def test_install_refuses_when_core_hooks_path_is_set(self):
        git(self.repo, "config", "core.hooksPath", ".husky")
        with chdir(self.repo):
            code, _, err = run(["install"])
        self.assertEqual(code, EXIT_FAIL)
        self.assertIn("core.hooksPath", err)
        self.assertIn('commit-guard check-msg "$1"', err)
        self.assertFalse(os.path.exists(os.path.join(self.hooks, "commit-msg")))
        self.assertFalse(
            os.path.exists(os.path.join(self.repo, ".husky", "commit-msg"))
        )

    def test_linked_worktree_installs_into_the_shared_hooks(self):
        worktree = os.path.realpath(tempfile.mkdtemp(prefix="cg-wt-"))
        self.addCleanup(shutil.rmtree, worktree, True)
        os.rmdir(worktree)
        git(self.repo, "worktree", "add", "-q", worktree, "-b", "side")
        with chdir(worktree):
            code, _, _ = run(["install", "--quiet"])
        self.assertEqual(code, EXIT_OK)
        self.assertTrue(os.path.isfile(os.path.join(self.hooks, "commit-msg")))
        with chdir(worktree):
            code, _, _ = run(["uninstall", "--quiet"])
        self.assertEqual(code, EXIT_OK)
        self.assertFalse(os.path.exists(os.path.join(self.hooks, "commit-msg")))

    def test_install_from_a_subdirectory(self):
        sub = os.path.join(self.repo, "src", "pkg")
        os.makedirs(sub)
        with chdir(sub):
            code, _, _ = run(["install", "--quiet"])
        self.assertEqual(code, EXIT_OK)
        self.assertTrue(os.path.isfile(os.path.join(self.hooks, "pre-commit")))


@unittest.skipIf(GIT is None, "git is not installed")
class TestHookBackups(unittest.TestCase):
    """Only the backup an install made is ever restored."""

    def setUp(self):
        self.repo = os.path.realpath(tempfile.mkdtemp(prefix="cg-bak-"))
        self.addCleanup(shutil.rmtree, self.repo, True)
        git(self.repo, "init", "-q")
        self.hooks = os.path.join(self.repo, ".git", "hooks")
        if not os.path.isdir(self.hooks):
            os.makedirs(self.hooks)

    def put(self, name, body):
        with open(os.path.join(self.hooks, name), "w", encoding="utf-8") as handle:
            handle.write(body)

    def read(self, name):
        with open(os.path.join(self.hooks, name), encoding="utf-8") as handle:
            return handle.read()

    def cg(self, *argv):
        with chdir(self.repo):
            return run(list(argv))

    def test_uninstall_does_not_restore_a_backup_it_never_made(self):
        self.put("pre-commit.bak", "#!/bin/sh\necho stale\n")
        self.cg("install", "--quiet")
        code, out, _ = self.cg("uninstall")
        self.assertEqual(code, EXIT_OK)
        self.assertFalse(os.path.exists(os.path.join(self.hooks, "pre-commit")))
        self.assertEqual(self.read("pre-commit.bak"), "#!/bin/sh\necho stale\n")
        self.assertIn("pre-commit.bak in place", out)

    def test_force_keeps_an_existing_backup_and_uses_the_next_name(self):
        self.put("commit-msg.bak", "older backup\n")
        self.put("commit-msg", "#!/bin/sh\necho mine\n")
        code, _, _ = self.cg("install", "--force", "--quiet", "--msg-only")
        self.assertEqual(code, EXIT_OK)
        self.assertEqual(self.read("commit-msg.bak"), "older backup\n")
        self.assertEqual(self.read("commit-msg.bak.1"), "#!/bin/sh\necho mine\n")
        self.assertIn(BACKUP_MARKER + " commit-msg.bak.1", self.read("commit-msg"))

        # Re-installing must keep the record, so uninstall still restores it.
        self.cg("install", "--quiet", "--msg-only")
        self.cg("uninstall", "--quiet")
        self.assertEqual(self.read("commit-msg"), "#!/bin/sh\necho mine\n")
        self.assertEqual(self.read("commit-msg.bak"), "older backup\n")
        self.assertFalse(os.path.exists(os.path.join(self.hooks, "commit-msg.bak.1")))


class TestHookScript(unittest.TestCase):
    """The installed hook copes with the recorded interpreter going away."""

    def test_script_records_backup_and_falls_back_to_path(self):
        body = _hook_script("check-files --strict", "pre-commit.bak")
        self.assertIn(HOOK_MARKER, body)
        self.assertIn(BACKUP_MARKER + " pre-commit.bak", body)
        self.assertIn("command -v commit-guard", body)
        self.assertIn("exec commit-guard check-files --strict", body)

    @unittest.skipIf(os.name == "nt" or shutil.which("sh") is None, "needs POSIX sh")
    def test_missing_interpreter_falls_back_or_explains(self):
        tmp = os.path.realpath(tempfile.mkdtemp(prefix="cg-hook-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        hook = os.path.join(tmp, "pre-commit")
        with mock.patch.object(sys, "executable", os.path.join(tmp, "gone", "python")):
            body = _hook_script("check-files --strict")
        with open(hook, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(body)
        sh = shutil.which("sh")

        empty = os.path.join(tmp, "empty")
        os.mkdir(empty)
        missing = subprocess.run(
            [sh, hook], env={"PATH": empty}, capture_output=True, text=True
        )
        self.assertEqual(missing.returncode, 1)
        self.assertIn("commit-guard install", missing.stderr)

        fake_bin = os.path.join(tmp, "bin")
        os.mkdir(fake_bin)
        fake = os.path.join(fake_bin, "commit-guard")
        with open(fake, "w", encoding="utf-8", newline="\n") as handle:
            handle.write('#!/bin/sh\necho "fallback $*"\n')
        os.chmod(fake, 0o755)
        found = subprocess.run(
            [sh, hook],
            env={"PATH": fake_bin + os.pathsep + os.path.dirname(sh)},
            capture_output=True,
            text=True,
        )
        self.assertEqual(found.returncode, 0, found.stderr)
        self.assertIn("fallback check-files --strict", found.stdout)
