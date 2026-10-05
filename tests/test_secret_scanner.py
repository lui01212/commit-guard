"""
Unit tests for secret_scanner and wizard in commit-shield.
Uses standard library unittest (zero external dependencies).
"""

import contextlib
import io
import time
import unittest
from unittest import mock

from commit_guard.config import Config
from commit_guard.secret_scanner import _mask_secret, scan_git_diff, scan_text
from commit_guard.wizard import compose_commit_message, run_wizard


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


# Fake credentials are assembled at runtime from repeated characters, so the
# repository never contains a string shaped exactly like a live key.
FAKE_AWS = "AKIA" + "Q" * 16


def _rules(text):
    return [finding.rule_name for finding in scan_text(text)]


class TestKeyFormats(unittest.TestCase):
    def test_openai_project_and_service_account_keys(self):
        for prefix in ("sk-", "sk-proj-", "sk-svcacct-", "sk-admin-"):
            key = prefix + "Ab1_" * 10 + "-T3"
            with self.subTest(prefix=prefix):
                self.assertEqual(_rules("OPENAI_API_KEY=" + key), ["OpenAI API Key"])

    def test_anthropic_key_is_not_also_reported_as_openai(self):
        key = "sk-ant-api03-" + "b" * 40 + "-AA"
        findings = scan_text("ANTHROPIC_API_KEY=" + key)
        self.assertEqual([f.rule_name for f in findings], ["Anthropic API Key"])

    def test_stripe_current_length_and_restricted_keys(self):
        self.assertEqual(
            _rules("STRIPE=sk_live_" + "a1" * 50), ["Stripe Secret / Live Key"]
        )
        self.assertEqual(
            _rules("STRIPE=rk_test_" + "b" * 24), ["Stripe Secret / Live Key"]
        )

    def test_stripe_publishable_key_is_public(self):
        self.assertEqual(_rules("const pk = 'pk_live_" + "c" * 24 + "'"), [])

    def test_slack_token_shapes(self):
        tokens = [
            "xoxb-" + "1" * 12 + "-" + "2" * 13 + "-" + "a" * 24,
            "xoxe.xoxp-1-" + "M" * 60,
            "xapp-1-A" + "0" * 10 + "-" + "1" * 13 + "-" + "f" * 64,
            "xoxe-1-" + "R" * 40,
        ]
        for token in tokens:
            with self.subTest(token=token[:12]):
                self.assertEqual(_rules("SLACK=" + token), ["Slack Token"])


class TestPlaceholderScope(unittest.TestCase):
    def test_example_elsewhere_on_the_line_does_not_hide_a_key(self):
        line = (
            'boto3.client("s3", aws_access_key_id="{0}", '
            'endpoint_url="https://s3.example.com")'.format(FAKE_AWS)
        )
        self.assertEqual(_rules(line), ["AWS Access Key ID"])

    def test_placeholder_variable_name_does_not_hide_a_key(self):
        token = "ghp_" + "d" * 36
        self.assertEqual(
            _rules('test_fake_token = "{0}"'.format(token)), ["GitHub Token"]
        )

    def test_placeholder_values_are_still_skipped(self):
        for line in (
            'aws_access_key_id = "AKIAIOSFODNN7EXAMPLE"',
            "api_key = 'YOUR_KEY_HERE'",
            "api_key = 'sk-" + "e" * 20 + "_EXAMPLE'",
            'password: "********"',
        ):
            with self.subTest(line=line):
                self.assertEqual(_rules(line), [])


class TestGenericPassword(unittest.TestCase):
    def test_detected_forms(self):
        for line in (
            '"password": "hunter2hunter2"',
            "password: hunter2hunter2",
            'db_password = "hunter2.hunter/2"',
            "DB_PASSWORD=hunter2hunter2",
            "'password' => 'hunter2hunter2',",
            'password := "hunter2hunter2"',
        ):
            with self.subTest(line=line):
                self.assertEqual(_rules(line), ["Generic Hardcoded Password / Secret"])

    def test_references_and_code_are_not_secrets(self):
        for line in (
            "password: ${DB_PASSWORD}",
            'password = os.environ["DB_PASSWORD"]',
            'password = os.getenv("DB_PASSWORD")',
            "password = settings.DB_PASSWORD",
            "password = user_password",
            'password = ""',
            "password: {{ vault_db_password }}",
            "password: *db_password_anchor",
        ):
            with self.subTest(line=line):
                self.assertEqual(_rules(line), [])

    def test_credentials_in_a_connection_url(self):
        self.assertEqual(
            _rules('DATABASE_URL = "postgres://admin:hunter2hunter2@db:5432/app"'),
            ["Password in URL"],
        )
        self.assertEqual(
            _rules("BROKER=amqp://svc:s3cr3t-Value@mq.internal//"), ["Password in URL"]
        )

    def test_urls_without_a_real_password(self):
        for line in (
            "url = https://github.com/lui01212/commit-guard",
            "DATABASE_URL=postgres://user:password@localhost/app",
            "DATABASE_URL=postgres://user:${DB_PASS}@localhost/app",
            "see http://localhost:8080/path for details",
        ):
            with self.subTest(line=line):
                self.assertEqual(_rules(line), [])


class TestScanTiming(unittest.TestCase):
    """Long single lines (minified or generated files) must scan quickly."""

    LIMIT_SECONDS = 2.0
    SIZE = 100_000

    def test_long_lines_scan_in_linear_time(self):
        shapes = {
            "word characters": "a" * self.SIZE,
            "dotted identifier": "a." * (self.SIZE // 2),
            "repeated sk- prefix": "sk-" * (self.SIZE // 3),
            "repeated slack prefix": "xoxb-" * (self.SIZE // 5),
            "repeated keyword": "password=" * (self.SIZE // 9),
            "keyword then spaces": "password" + " " * self.SIZE,
            "url-ish": "a://" * (self.SIZE // 4),
            "colons": "a:" * (self.SIZE // 2),
        }
        for label, line in shapes.items():
            with self.subTest(shape=label):
                start = time.perf_counter()
                scan_git_diff("+++ b/app.min.js\n@@ -0,0 +1 @@\n+" + line + "\n")
                elapsed = time.perf_counter() - start
                self.assertLess(elapsed, self.LIMIT_SECONDS, label)


class TestDiffParsing(unittest.TestCase):
    def diff(self, path_header, *added):
        lines = [
            "diff --git a/x b/x",
            "new file mode 100644",
            "--- /dev/null",
            "+++ " + path_header,
            "@@ -0,0 +1,{0} @@".format(len(added)),
        ]
        lines += ["+" + line for line in added]
        return "\n".join(lines) + "\n"

    def test_added_line_starting_with_plus_plus_is_scanned(self):
        findings = scan_git_diff(self.diff("b/notes.md", "++ token=" + FAKE_AWS))
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].file_path, "notes.md")

    def test_added_line_that_looks_like_a_file_header_is_content(self):
        diff = self.diff("b/real.py", "++ b/fake.py", "key = " + FAKE_AWS)
        findings = scan_git_diff(diff)
        self.assertEqual(
            [(f.file_path, f.line_number) for f in findings], [("real.py", 2)]
        )

    def test_c_quoted_path_is_unquoted(self):
        header = '"b/we\\"ird\\tname\\303\\251.py"'
        findings = scan_git_diff(self.diff(header, "key = " + FAKE_AWS))
        self.assertEqual(findings[0].file_path, 'we"ird\tname\u00e9.py')

    def test_path_with_space_and_trailing_tab(self):
        findings = scan_git_diff(self.diff("b/my file.py\t", "key = " + FAKE_AWS))
        self.assertEqual(findings[0].file_path, "my file.py")

    def test_line_numbers_across_hunks_and_no_newline_marker(self):
        diff = (
            "diff --git a/app.py b/app.py\n"
            "--- a/app.py\n"
            "+++ b/app.py\n"
            "@@ -3 +3 @@\n"
            "-old\n"
            "+new\n"
            "\\ No newline at end of file\n"
            "@@ -10,0 +11,2 @@\n"
            "+x = 1\n"
            "+key = " + FAKE_AWS + "\n"
        )
        findings = scan_git_diff(diff)
        self.assertEqual([f.line_number for f in findings], [12])

    def test_carriage_return_in_content_does_not_break_parsing(self):
        diff = self.diff("b/a.txt", "one\rtwo", "key = " + FAKE_AWS)
        self.assertEqual([f.line_number for f in scan_git_diff(diff)], [2])

    def test_binary_content_is_skipped(self):
        diff = self.diff("b/blob.bin", "\0\0" + FAKE_AWS)
        self.assertEqual(scan_git_diff(diff), [])

    def test_files_are_attributed_separately(self):
        diff = self.diff("b/one.py", "x = 1") + self.diff("b/two.py", "k = " + FAKE_AWS)
        self.assertEqual([f.file_path for f in scan_git_diff(diff)], ["two.py"])


class TestWizardFlow(unittest.TestCase):
    """Drive run_wizard with scripted answers."""

    def run_wizard(self, answers, config=None, dry_run=True):
        replies = iter(answers)

        def fake_input(prompt=""):
            print(prompt, end="")
            value = next(replies)
            if isinstance(value, BaseException):
                raise value
            return value

        out = io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch("builtins.input", fake_input))
            git_mock = stack.enter_context(mock.patch("commit_guard.wizard.run_git"))
            stack.enter_context(contextlib.redirect_stdout(out))
            stack.enter_context(contextlib.redirect_stderr(out))
            try:
                code = run_wizard(config=config, dry_run=dry_run)
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), git_mock

    def test_menu_lists_configured_types(self):
        config = Config(types=["feature", "bugfix"])
        code, out, _ = self.run_wizard(["2", "", "add thing", "", "", ""], config)
        self.assertEqual(code, 0)
        self.assertIn("[ 1] bugfix", out)
        self.assertIn("[ 2] feature", out)
        self.assertIn("Select type (1-2)", out)
        self.assertIn("feature: add thing", out)
        self.assertNotIn("Warning", out)

    def test_configured_scopes_are_offered(self):
        config = Config(scopes=["api", "cli"])
        code, out, _ = self.run_wizard(["1", "2", "add thing", "", "", ""], config)
        self.assertEqual(code, 0)
        self.assertIn("Select scope", out)
        self.assertIn("feat(cli): add thing", out)

    def test_output_is_plain_ascii(self):
        _, out, _ = self.run_wizard(["1", "", "add thing", "", "", ""])
        out.encode("ascii")  # raises if an emoji slipped back in

    def test_ctrl_c_in_body_aborts_without_committing(self):
        code, out, git_mock = self.run_wizard(
            ["1", "", "add thing", "first body line", KeyboardInterrupt()],
            dry_run=False,
        )
        self.assertNotEqual(code, 0)
        self.assertIn("Aborted", out)
        git_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
