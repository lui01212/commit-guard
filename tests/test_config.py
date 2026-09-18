"""
Tests for commit_guard.config: discovery, precedence, coercion and errors.

Every test builds a throwaway directory tree so nothing depends on the
repository this suite happens to run inside.
"""

import os
import shutil
import tempfile
import unittest

from commit_guard.config import (
    DEFAULT_ALLOWLIST,
    DEFAULT_SENSITIVE_PATTERNS,
    DEFAULT_TYPES,
    ConfigError,
    find_config_files,
    load_config,
)


class _TempTree(unittest.TestCase):
    """Base class providing a temporary directory that looks like a repo."""

    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="cg-config-"))
        # find_config_files stops at the directory holding .git, so fake one.
        os.mkdir(os.path.join(self.root, ".git"))
        self.addCleanup(shutil.rmtree, self.root, True)

    def write(self, relpath, text):
        path = os.path.join(self.root, relpath)
        parent = os.path.dirname(path)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path


class TestDefaults(unittest.TestCase):
    def test_no_files_yields_builtin_defaults(self):
        empty = os.path.realpath(tempfile.mkdtemp(prefix="cg-empty-"))
        self.addCleanup(shutil.rmtree, empty, True)
        config = load_config(start_dir=empty)
        self.assertEqual(config.max_header_len, 72)
        self.assertEqual(config.max_size_mb, 10.0)
        self.assertEqual(config.subject_min_len, 3)
        self.assertEqual(config.max_body_line_len, 0)
        self.assertTrue(config.skip_merge_commits)
        self.assertFalse(config.require_scope)
        self.assertFalse(config.allow_trailing_period)
        self.assertEqual(config.types, set(DEFAULT_TYPES))
        self.assertEqual(config.allowlist, list(DEFAULT_ALLOWLIST))
        self.assertEqual(config.sources, [])

    def test_no_config_flag_skips_files_entirely(self):
        tree = os.path.realpath(tempfile.mkdtemp(prefix="cg-skip-"))
        self.addCleanup(shutil.rmtree, tree, True)
        with open(os.path.join(tree, ".commit-guard.toml"), "w", encoding="utf-8") as h:
            h.write("max_header_len = 200\n")
        config = load_config(start_dir=tree, use_files=False)
        self.assertEqual(config.max_header_len, 72)
        self.assertEqual(config.sources, [])


class TestDiscovery(_TempTree):
    def test_finds_pyproject_and_standalone_nearest_last(self):
        self.write("pyproject.toml", "[tool.commit-guard]\nmax_header_len = 80\n")
        self.write(".commit-guard.toml", "max_header_len = 90\n")
        found = find_config_files(self.root)
        self.assertEqual(len(found), 2)
        # Nearest-last ordering means the standalone file is applied after
        # pyproject.toml, so it wins.
        self.assertTrue(found[-1].endswith(".commit-guard.toml"))

    def test_walks_upward_from_subdirectory(self):
        self.write(".commit-guard.toml", "max_header_len = 88\n")
        sub = os.path.join(self.root, "src", "deep")
        os.makedirs(sub)
        config = load_config(start_dir=sub)
        self.assertEqual(config.max_header_len, 88)

    def test_stops_at_repo_root(self):
        # A config above the .git directory must not be picked up.
        outer = os.path.dirname(self.root)
        stray = os.path.join(outer, ".commit-guard.toml")
        if os.path.exists(stray):
            self.skipTest("unexpected config file in temp parent")
        found = find_config_files(self.root)
        self.assertEqual(found, [])


class TestPrecedence(_TempTree):
    def test_standalone_overrides_pyproject(self):
        self.write("pyproject.toml", "[tool.commit-guard]\nmax_header_len = 80\n")
        self.write(".commit-guard.toml", "max_header_len = 100\n")
        config = load_config(start_dir=self.root)
        self.assertEqual(config.max_header_len, 100)
        self.assertEqual(len(config.sources), 2)

    def test_nearer_file_overrides_farther(self):
        self.write(".commit-guard.toml", "max_header_len = 80\n")
        sub = os.path.join(self.root, "packages", "api")
        os.makedirs(sub)
        self.write(
            os.path.join("packages", "api", ".commit-guard.toml"),
            "max_header_len = 120\n",
        )
        config = load_config(start_dir=sub)
        self.assertEqual(config.max_header_len, 120)

    def test_unset_keys_keep_earlier_values(self):
        self.write(
            "pyproject.toml",
            "[tool.commit-guard]\nmax_header_len = 80\nsubject_min_len = 5\n",
        )
        self.write(".commit-guard.toml", "max_header_len = 100\n")
        config = load_config(start_dir=self.root)
        self.assertEqual(config.max_header_len, 100)
        # Not mentioned in the nearer file, so the pyproject value survives.
        self.assertEqual(config.subject_min_len, 5)

    def test_explicit_path_disables_discovery(self):
        self.write(".commit-guard.toml", "max_header_len = 100\n")
        named = self.write("custom.toml", "max_header_len = 55\n")
        config = load_config(start_dir=self.root, config_path=named)
        self.assertEqual(config.max_header_len, 55)
        self.assertEqual(len(config.sources), 1)


class TestTables(_TempTree):
    def test_pyproject_requires_tool_table(self):
        # A bare [commit-guard] table in pyproject.toml is not the documented
        # location, so it must be ignored rather than half-honoured.
        self.write("pyproject.toml", "[commit-guard]\nmax_header_len = 200\n")
        config = load_config(start_dir=self.root)
        self.assertEqual(config.max_header_len, 72)

    def test_standalone_accepts_top_level_keys(self):
        self.write(".commit-guard.toml", "max_header_len = 95\n")
        config = load_config(start_dir=self.root)
        self.assertEqual(config.max_header_len, 95)

    def test_standalone_accepts_commit_guard_table(self):
        self.write(".commit-guard.toml", "[commit-guard]\nmax_header_len = 96\n")
        config = load_config(start_dir=self.root)
        self.assertEqual(config.max_header_len, 96)


class TestCoercion(_TempTree):
    def test_lists_and_bools_and_floats(self):
        self.write(
            ".commit-guard.toml",
            'types = ["feat", "fix"]\n'
            'scopes = ["api", "cli"]\n'
            "require_scope = true\n"
            "allow_trailing_period = true\n"
            "max_size_mb = 2.5\n"
            "max_body_line_len = 100\n",
        )
        config = load_config(start_dir=self.root)
        self.assertEqual(config.types, {"feat", "fix"})
        self.assertEqual(config.scopes, {"api", "cli"})
        self.assertTrue(config.require_scope)
        self.assertTrue(config.allow_trailing_period)
        self.assertEqual(config.max_size_mb, 2.5)
        self.assertEqual(config.max_body_line_len, 100)

    def test_integer_accepted_for_float_key(self):
        self.write(".commit-guard.toml", "max_size_mb = 5\n")
        config = load_config(start_dir=self.root)
        self.assertEqual(config.max_size_mb, 5.0)

    def test_unknown_key_is_an_error(self):
        # Typos must not silently do nothing.
        self.write(".commit-guard.toml", "max_header_length = 80\n")
        with self.assertRaises(ConfigError) as ctx:
            load_config(start_dir=self.root)
        self.assertIn("max_header_length", str(ctx.exception))

    def test_bool_rejected_for_int_key(self):
        self.write(".commit-guard.toml", "max_header_len = true\n")
        with self.assertRaises(ConfigError):
            load_config(start_dir=self.root)

    def test_wrong_type_for_list_key(self):
        self.write(".commit-guard.toml", 'types = "feat"\n')
        with self.assertRaises(ConfigError):
            load_config(start_dir=self.root)

    def test_wrong_type_for_bool_key(self):
        self.write(".commit-guard.toml", "require_scope = 1\n")
        with self.assertRaises(ConfigError):
            load_config(start_dir=self.root)


class TestPatternMerging(_TempTree):
    def test_sensitive_patterns_replaces(self):
        self.write(".commit-guard.toml", 'sensitive_patterns = ["^only-this$"]\n')
        config = load_config(start_dir=self.root)
        self.assertEqual(config.sensitive_patterns, ["^only-this$"])

    def test_extra_sensitive_patterns_appends(self):
        self.write(".commit-guard.toml", 'extra_sensitive_patterns = ["^custom$"]\n')
        config = load_config(start_dir=self.root)
        self.assertEqual(
            config.sensitive_patterns, list(DEFAULT_SENSITIVE_PATTERNS) + ["^custom$"]
        )

    def test_replace_then_append_across_files(self):
        self.write(
            "pyproject.toml", '[tool.commit-guard]\nsensitive_patterns = ["^base$"]\n'
        )
        self.write(".commit-guard.toml", 'extra_sensitive_patterns = ["^added$"]\n')
        config = load_config(start_dir=self.root)
        self.assertEqual(config.sensitive_patterns, ["^base$", "^added$"])

    def test_allowlist_replaces(self):
        self.write(".commit-guard.toml", 'allowlist = ["*.fixture"]\n')
        config = load_config(start_dir=self.root)
        self.assertEqual(config.allowlist, ["*.fixture"])


class TestSemanticErrors(_TempTree):
    def test_require_scope_without_scopes_is_rejected(self):
        # Requiring a scope while allowing none would reject every commit.
        self.write(".commit-guard.toml", "require_scope = true\n")
        with self.assertRaises(ConfigError):
            load_config(start_dir=self.root)

    def test_require_scope_with_scopes_is_accepted(self):
        self.write(".commit-guard.toml", 'require_scope = true\nscopes = ["api"]\n')
        config = load_config(start_dir=self.root)
        self.assertTrue(config.require_scope)

    def test_missing_explicit_config_file_is_an_error(self):
        with self.assertRaises(ConfigError):
            load_config(
                start_dir=self.root, config_path=os.path.join(self.root, "nope.toml")
            )

    def test_broken_pyproject_is_skipped_during_discovery(self):
        # Somebody else's malformed pyproject.toml must not break commit-guard.
        self.write("pyproject.toml", "[[[ not valid toml\n")
        self.write(".commit-guard.toml", "max_header_len = 77\n")
        config = load_config(start_dir=self.root)
        self.assertEqual(config.max_header_len, 77)

    def test_broken_file_named_explicitly_is_an_error(self):
        broken = self.write("pyproject.toml", "[[[ not valid toml\n")
        with self.assertRaises(ConfigError):
            load_config(start_dir=self.root, config_path=broken)

    def test_broken_standalone_config_is_an_error(self):
        self.write(".commit-guard.toml", "not toml at all\n")
        with self.assertRaises(ConfigError):
            load_config(start_dir=self.root)


if __name__ == "__main__":
    unittest.main()
