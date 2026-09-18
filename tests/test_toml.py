"""
Tests for commit_guard._toml.

The fallback parser only has to agree with tomllib on the subset of TOML that
configuration files actually use, so every case here is checked against both
code paths when tomllib is available.
"""

import unittest

from commit_guard import _toml
from commit_guard._toml import TomlError, loads


def _fallback(text):
    return _toml._fallback_loads(text)


def _both(text):
    """Return (stdlib_result, fallback_result); stdlib is None on Python < 3.11."""
    stdlib = None
    if _toml._tomllib is not None:
        stdlib = _toml._tomllib.loads(text)
    return stdlib, _fallback(text)


class TestParserAgreement(unittest.TestCase):
    def assert_agree(self, text):
        stdlib, fallback = _both(text)
        if stdlib is not None:
            self.assertEqual(stdlib, fallback)
        return fallback

    def test_scalars(self):
        result = self.assert_agree(
            'name = "commit-guard"\n'
            "count = 42\n"
            "ratio = 1.5\n"
            "enabled = true\n"
            "disabled = false\n"
        )
        self.assertEqual(result["name"], "commit-guard")
        self.assertEqual(result["count"], 42)
        self.assertEqual(result["ratio"], 1.5)
        self.assertIs(result["enabled"], True)
        self.assertIs(result["disabled"], False)

    def test_tables_and_dotted_keys(self):
        result = self.assert_agree("[tool.commit-guard]\n" "max_header_len = 80\n")
        self.assertEqual(result["tool"]["commit-guard"]["max_header_len"], 80)

    def test_arrays(self):
        result = self.assert_agree(
            'types = ["feat", "fix", "chore"]\n' "limits = [1, 2, 3]\n" "empty = []\n"
        )
        self.assertEqual(result["types"], ["feat", "fix", "chore"])
        self.assertEqual(result["limits"], [1, 2, 3])
        self.assertEqual(result["empty"], [])

    def test_multi_line_array(self):
        result = self.assert_agree(
            "types = [\n" '    "feat",\n' '    "fix",\n' '    "docs",\n' "]\n"
        )
        self.assertEqual(result["types"], ["feat", "fix", "docs"])

    def test_comments_are_stripped(self):
        result = self.assert_agree(
            "# leading comment\n"
            "max_header_len = 80  # trailing comment\n"
            "\n"
            'name = "x"\n'
        )
        self.assertEqual(result["max_header_len"], 80)
        self.assertEqual(result["name"], "x")

    def test_hash_inside_string_is_not_a_comment(self):
        result = self.assert_agree('pattern = "value # not-a-comment"\n')
        self.assertEqual(result["pattern"], "value # not-a-comment")

    def test_single_quoted_literal_string(self):
        result = self.assert_agree("pattern = '(^|/)\\.env$'\n")
        # Literal strings do not process escapes.
        self.assertEqual(result["pattern"], r"(^|/)\.env$")

    def test_escapes_in_basic_string(self):
        result = self.assert_agree(r'text = "a\tb\nc\\d\"e"' + "\n")
        self.assertEqual(result["text"], 'a\tb\nc\\d"e')

    def test_blank_and_whitespace_lines(self):
        result = self.assert_agree("\n   \n" "key = 1\n" "\n")
        self.assertEqual(result, {"key": 1})


class TestFallbackErrors(unittest.TestCase):
    """The fallback parser must reject bad input rather than guess."""

    def test_unterminated_table_header(self):
        with self.assertRaises(TomlError):
            _fallback("[tool.commit-guard\nkey = 1\n")

    def test_line_without_equals(self):
        with self.assertRaises(TomlError):
            _fallback("this is not toml\n")

    def test_unterminated_string(self):
        with self.assertRaises(TomlError):
            _fallback('key = "unterminated\n')

    def test_unterminated_array(self):
        with self.assertRaises(TomlError):
            _fallback('key = ["a", "b"\n')

    def test_loads_wraps_parse_errors_as_tomlerror(self):
        with self.assertRaises(TomlError):
            loads("[[[not valid\n")


if __name__ == "__main__":
    unittest.main()
