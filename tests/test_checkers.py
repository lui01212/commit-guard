"""
Unit tests for commit_guard.checkers.
Uses Python's standard library unittest (zero dependencies).
"""

import unittest

from commit_guard.checkers import check_commit_message, check_file_path


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


if __name__ == "__main__":
    unittest.main()
