# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-09-18

The theme of this release is correctness and configurability. v0.1.0 validated
the happy path; this release fixes the cases where it was wrong, and lets you
change the rules without editing the source.

### Added

- **Configuration files.** Settings are read from `[tool.commit-guard]` in
  `pyproject.toml` or from a standalone `.commit-guard.toml`. Discovery walks
  upward from the current directory to the repository root, so a monorepo can
  override rules per package. Precedence is: built-in defaults, then
  `pyproject.toml`, then `.commit-guard.toml`, then CLI flags.
- `--config PATH` to name a configuration file explicitly (disables discovery),
  and `--no-config` to ignore configuration files entirely.
- `-q` / `--quiet` on the validating subcommands, to suppress success output
  while still reporting failures.
- **`uninstall` subcommand**, which removes only hooks commit-guard installed
  and restores any hook it previously backed up.
- `install --force` to replace a hook owned by another tool, keeping the
  original as `<hook>.bak`, and `install --msg-only` to skip the `pre-commit`
  hook.
- `install` now installs a `pre-commit` hook (`check-files --strict`) in
  addition to `commit-msg`.
- An allowlist for paths that look sensitive but are not: `*.example`,
  `*.sample`, `*.template`, `*.dist` and friends are no longer flagged.
- Configurable `scopes` with `require_scope`, `subject_min_len`,
  `allow_trailing_period`, and an optional `max_body_line_len` body check that
  exempts URLs, git trailers and indented or quoted lines.
- An example `.commit-guard.toml.example` documenting every setting.

### Fixed

- **Merge, revert, fixup and squash commits no longer fail validation.** Git
  generates these messages itself, so rejecting them made `git merge` and
  interactive rebase unusable. Controlled by `skip_merge_commits` and
  `skip_prefixes`.
- **`install` no longer destroys an existing hook.** It now refuses to
  overwrite a hook it does not own, names the framework that owns it
  (pre-commit, husky, lefthook) in the message, and only replaces it with
  `--force`, keeping a `.bak` copy.
- **Git failures are no longer treated as "nothing to check".** v0.1.0 caught
  every exception around git and returned success, so a broken repository or a
  missing `git` silently passed the commit. Git errors now exit with code 2.
- **File size is read from the staged blob, not the working tree.** Checking
  the file on disk reported the wrong size whenever the working copy had moved
  on from what was staged.
- **Installed hooks call `sys.executable`, not a bare `python`.** The old hook
  broke wherever the interpreter commit-guard was installed into was not first
  on `PATH`, or was not named `python`.
- `__version__` and the packaging metadata can no longer drift: the version is
  single-sourced from `commit_guard/__init__.py` via Hatch.

### Changed

- **Exit code 2 now means "commit-guard could not do its job"**, as distinct
  from exit code 1, "the commit is invalid". Exit code 0 still means success.
  Scripts that only branch on zero/non-zero are unaffected.
- Unknown keys in a configuration file are an error rather than a silent
  no-op, so a typo such as `max_header_length` is reported instead of ignored.
- An uppercase type (`Feat: ...`) now reports "type must be lowercase" instead
  of the generic format error.

### Compatibility

- Public API from v0.1.0 is unchanged: `check_commit_message(msg,
  max_header_len=...)` and `check_file_path(path, max_size_mb=...)` keep their
  signatures, and `CONVENTIONAL_TYPES` is still exported. Both functions take
  a new optional `config=` argument.
- Still zero runtime dependencies, still `requires-python >= 3.8`. TOML is read
  with `tomllib` on 3.11+ and with a small bundled parser below that.

## [0.1.0] - 2026-09-17

### Added

- Initial release: Conventional Commits validation, detection of sensitive
  staged files and oversized blobs, a `commit-msg` hook installer, and
  `pre-commit` framework integration.

[0.2.0]: https://github.com/lui01212/commit-guard/releases/tag/v0.2.0
[0.1.0]: https://github.com/lui01212/commit-guard/releases/tag/v0.1.0
