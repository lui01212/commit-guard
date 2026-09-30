"""
Unit tests for secret_scanner and wizard in commit-shield.
Uses standard library unittest (zero external dependencies).
"""

import unittest

from commit_guard.secret_scanner import _mask_secret, scan_git_diff, scan_text
from commit_guard.wizard import compose_commit_message


class TestSecretScanner(unittest.TestCase):
    def test_mask_secret(self):
        self.assertEqual(_mask_secret("1234"), "***")
        masked = _mask_secret("sk-ant-1234567890abcdef")
        self.assertTrue(masked.startswith("sk-a"))
        self.assertTrue(masked.endswith("cdef"))
        self.assertIn("...", masked)

    def test_detect_aws_key(self):
        text = "aws_key = 'AKIA1234567890ABCDEF'"
        findings = scan_text(text)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].rule_name, "AWS Access Key ID")

    def test_detect_github_token(self):
        # Construct exact 36 char token for matching
        full_token = "ghp_" + ("a" * 36)
        findings = scan_text(f"GH_TOKEN='{full_token}'")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].rule_name, "GitHub Token")

    def test_detect_openai_and_anthropic(self):
        token_oa = "sk-" + ("a" * 25)
        token_ant = "sk-ant-" + ("b" * 25)
        findings = scan_text(f"OA = '{token_oa}'\nANT = '{token_ant}'")
        self.assertEqual(len(findings), 2)
        rule_names = {f.rule_name for f in findings}
        self.assertIn("OpenAI API Key", rule_names)
        self.assertIn("Anthropic API Key", rule_names)

    def test_detect_pem_private_key(self):
        pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----"
        findings = scan_text(pem)
        self.assertGreaterEqual(len(findings), 1)
        self.assertEqual(findings[0].rule_name, "PEM Private Key Header")

    def test_detect_hardcoded_password(self):
        text = "database_password = 'MySecretP@ssw0rd!123'"
        findings = scan_text(text)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].rule_name, "Generic Hardcoded Password / Secret")

    def test_false_positive_filtering(self):
        text = (
            "api_key = '<YOUR_API_KEY_HERE>'\n"
            "password = 'example_dummy_password'\n"
            "aws = 'AKIAIOSFODNN7EXAMPLE'"  # Public doc example
        )
        findings = scan_text(text)
        self.assertEqual(len(findings), 0)

    def test_scan_git_diff_hunk(self):
        fake_diff = (
            "diff --git a/app.py b/app.py\n"
            "--- a/app.py\n"
            "+++ b/app.py\n"
            "@@ -10,3 +10,4 @@\n"
            " import os\n"
            "+STRIPE_KEY = 'sk_live_' + '123456789012345678901234'\n"
            "+# normal line\n"
            "-old_line\n"
        )
        stripe_live = "sk_live_" + ("1" * 24)
        sample_diff = fake_diff.replace(
            "'sk_live_' + '123456789012345678901234'", f"'{stripe_live}'"
        )
        findings = scan_git_diff(sample_diff)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].file_path, "app.py")
        self.assertEqual(findings[0].rule_name, "Stripe Secret / Live Key")


class TestWizard(unittest.TestCase):
    def test_compose_standard_message(self):
        msg = compose_commit_message(
            commit_type="feat",
            scope="auth",
            subject="add oauth2 login provider",
            body="Implements Google OAuth2 provider using standard libraries.",
            is_breaking=False,
            issues_closed="#42",
        )
        expected = (
            "feat(auth): add oauth2 login provider\n\n"
            "Implements Google OAuth2 provider using standard libraries.\n\n"
            "Closes #42"
        )
        self.assertEqual(msg, expected)

    def test_compose_breaking_change_message(self):
        msg = compose_commit_message(
            commit_type="refactor",
            scope="api",
            subject="drop legacy v1 endpoints",
            is_breaking=True,
            breaking_description="v1 API endpoints have been removed in favor of v2.",
        )
        self.assertIn("refactor(api)!: drop legacy v1 endpoints", msg)
        self.assertIn("BREAKING CHANGE: v1 API endpoints have been removed", msg)

    def test_cli_json_check_msg_valid(self):
        import io
        import json
        from contextlib import redirect_stdout

        from commit_guard.cli import main

        buf = io.StringIO()
        with redirect_stdout(buf):
            ret = main(["check-msg", "-m", "feat(cli): add json flag", "--json"])
        self.assertEqual(ret, 0)
        data = json.loads(buf.getvalue())
        self.assertTrue(data["valid"])
        self.assertEqual(data["errors"], [])

    def test_cli_json_check_msg_invalid(self):
        import io
        import json
        from contextlib import redirect_stdout

        from commit_guard.cli import main

        buf = io.StringIO()
        with redirect_stdout(buf):
            ret = main(["check-msg", "-m", "bad commit message", "--json"])
        self.assertEqual(ret, 1)
        data = json.loads(buf.getvalue())
        self.assertFalse(data["valid"])
        self.assertGreater(len(data["errors"]), 0)

    def test_cli_check_staged_alias(self):
        from commit_guard.cli import build_parser

        parser = build_parser()
        # Parse arguments without --help to avoid SystemExit
        args = parser.parse_args(
            ["check-staged", "file1.py", "--no-config", "--no-secrets"]
        )
        self.assertEqual(args.subcommand, "check-staged")
        self.assertEqual(args.files, ["file1.py"])

    def test_wizard_dry_run_cli(self):
        from commit_guard.wizard import check_commit_message, compose_commit_message

        msg = compose_commit_message("fix", "core", "resolve memory leak")
        valid, errors = check_commit_message(msg)
        self.assertTrue(valid)
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
