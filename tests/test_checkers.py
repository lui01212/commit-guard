"""
Unit tests for commit_guard.checkers.
Uses Python's standard library unittest (zero dependencies).
"""

import unittest

from commit_guard.checkers import check_commit_message, check_file_path
from commit_guard.config import Config


class TestCommitMessageChecker(unittest.TestCase):
    def test_valid_conventional_commits(self):
        valid_samples = [
            "feat: add google oauth2 login",
            "fix(auth): resolve token refresh race condition",
            "docs: update installation instructions in readme",
            "chore(deps): bump urllib3 from 2.0 to 2.1",
            "refactor(core): simplify data parsing pipeline",
            "feat(api)!: breaking change to v2 endpoints",
            "test: add unit tests for user service",
            "ci: add github actions workflow",
        ]
        for msg in valid_samples:
            with self.subTest(msg=msg):
                is_valid, errors = check_commit_message(msg)
                self.assertTrue(is_valid, f"Expected valid, got errors: {errors}")
                self.assertEqual(len(errors), 0)

    def test_invalid_commit_type(self):
        is_valid, errors = check_commit_message("randomtype: something done")
        self.assertFalse(is_valid)
        self.assertTrue(
            any("Unknown commit type" in e or "does not follow" in e for e in errors)
        )

    def test_invalid_format(self):
        invalid_samples = [
            "",
            "Fixed a bug in login",
            "feat",
            "feat:",
            "feat: a.",  # Ends with period
        ]
        for msg in invalid_samples:
            with self.subTest(msg=msg):
                is_valid, errors = check_commit_message(msg)
                self.assertFalse(is_valid, f"Expected invalid for '{msg}'")

    def test_header_too_long(self):
        long_subject = "feat: " + "a" * 80
        is_valid, errors = check_commit_message(long_subject, max_header_len=72)
        self.assertFalse(is_valid)
        self.assertTrue(any("too long" in e for e in errors))


class TestFilePathChecker(unittest.TestCase):
    def test_sensitive_files(self):
        sensitive = [
            ".env",
            "config/.env.local",
            "server.key",
            "certificates/cert.pem",
            "id_rsa",
            "secrets.yaml",
        ]
        for path in sensitive:
            with self.subTest(path=path):
                is_valid, issues = check_file_path(path)
                self.assertFalse(
                    is_valid, f"Expected warning for sensitive file: {path}"
                )

    def test_safe_files(self):
        safe = [
            "README.md",
            "src/main.py",
            "package.json",
            "docs/guide.html",
        ]
        for path in safe:
            with self.subTest(path=path):
                is_valid, issues = check_file_path(path)
                self.assertTrue(is_valid, f"Expected safe for: {path}")


class TestVerboseCommitAndWhitespace(unittest.TestCase):
    SCISSORS = "# ------------------------ >8 ------------------------\n"

    def test_text_below_scissors_line_is_ignored(self):
        msg = (
            "feat: add long module\n"
            "\n"
            "# Please enter the commit message for your changes.\n"
            + self.SCISSORS
            + "# Do not modify or remove the line above.\n"
            "diff --git a/long.py b/long.py\n"
            "+x = '" + "a" * 100 + "'\n"
        )
        config = Config(max_body_line_len=72)
        is_valid, errors = check_commit_message(msg, config=config)
        self.assertTrue(is_valid, errors)

    def test_body_above_scissors_line_is_still_checked(self):
        msg = "feat: add thing\n\n" + "b" * 100 + "\n" + self.SCISSORS + "diff\n"
        is_valid, errors = check_commit_message(
            msg, config=Config(max_body_line_len=72)
        )
        self.assertFalse(is_valid)
        self.assertTrue(any("Body line" in e for e in errors))

    def test_header_with_leading_whitespace_is_rejected(self):
        is_valid, errors = check_commit_message("   feat: indented header")
        self.assertFalse(is_valid)
        self.assertTrue(any("whitespace" in e for e in errors), errors)

    def test_trailing_whitespace_is_still_tolerated(self):
        is_valid, errors = check_commit_message("feat: add thing   \n")
        self.assertTrue(is_valid, errors)


if __name__ == "__main__":
    unittest.main()
