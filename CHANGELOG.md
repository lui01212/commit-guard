# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.0] - 2026-10-05

### Fixed

- **The `pre-commit` framework hook now scans file contents for secrets.**
  `check-files` skipped the secret scan whenever file names were passed, which
  is how the framework always runs it. Inside a git repository the staged
  changes are now always scanned, limited to the given files when there are
  any.
- **Renamed files are checked.** A staged rename (for example `settings.txt`
  to `.env`) or a type change was skipped by both the name and the content
  checks, and a commit with no added or modified files skipped the secret scan.
- **Git configuration can no longer hide secrets.** `color.ui = always`,
  `diff.external`, textconv drivers, `diff.noprefix`, `diff.relative` and
  quoted path names changed the diff output the scanner reads. These are now
  pinned. A git failure during the scan exits with code 2 instead of passing.
- **Files marked `-diff` in `.gitattributes` are scanned.** Git hid their
  content behind "Binary files differ"; real binary files are still skipped.
- Current key formats are detected: OpenAI project, service-account and admin
  keys (`sk-proj-...`), Stripe keys longer than 34 characters and restricted
  `rk_` keys, and Slack `xoxe.xoxp-...` and `xapp-...` tokens. Stripe
  publishable `pk_` keys are no longer reported, since they are public.
- Hardcoded passwords are detected in JSON (`"password": "..."`), unquoted YAML
  and `.env` values, values containing `.` or `/`, and `scheme://user:pass@host`
  URLs. References such as `${VAR}` and `os.environ[...]` are not reported.
- A key is no longer ignored because something else on the same line, such as
  an `example.com` URL or a variable name, looks like a placeholder. Only the
  value itself is checked.
- Scanning a long minified line no longer takes seconds (a 20 KB line took
  about 29 seconds).
- Findings are attributed to the right file and line for names with quotes or
  non-ASCII characters, and for added lines that start with `++`.
- **`install` puts hooks where git runs them.** In a linked worktree the hooks
  were written to a directory git never reads. When `core.hooksPath` is set
  (husky, lefthook), `install` now refuses with exit code 1 and explains how to
  call commit-guard from that setup, instead of reporting success.
- `uninstall` only restores the backup its own install made, and
  `install --force` no longer overwrites an existing `.bak`; it uses `.bak.1`,
  `.bak.2` and so on. A `.bak` without that record, such as one left by an
  earlier version, is kept in place and reported.
- The installed hook falls back to `commit-guard` on `PATH` when the recorded
  Python interpreter no longer exists, and otherwise says to re-run
  `commit-guard install`.
- `[tool.commit-guard]` in `pyproject.toml` is read on Python 3.8 to 3.10. The
  bundled TOML parser rejected inline tables and arrays of tables, so most real
  `pyproject.toml` files were skipped without a word. A `pyproject.toml` that
  cannot be parsed now produces a warning when it mentions commit-guard.
- `git commit -v`: the diff below the scissors line is no longer validated as
  part of the message.
- A header that starts with whitespace is rejected with a clear message; git
  keeps that whitespace in the commit.
- The `commit` wizard lists the configured `types`, offers the configured
  `scopes`, uses plain ASCII output (emoji crashed it on some Windows
  consoles), and aborts without committing on Ctrl-C while typing the body.

### Changed

- An invalid regular expression in `sensitive_patterns` or
  `extra_sensitive_patterns` is a configuration error (exit code 2) when the
  file is loaded, rather than a warning on every checked file.
- A `[tool.commit-guard]` table in `.commit-guard.toml` is accepted like
  `[commit-guard]`; any other table there is reported as an unknown setting
  instead of being ignored.
- Docs: the README configuration example uses regular expressions rather than
  globs (the glob example made every commit fail), the commented
  `sensitive_patterns` block in `.commit-guard.toml.example` uses literal
  strings so it is valid when uncommented, the README describes both hooks
  `install` writes, and the docs no longer promise per-package configuration in
  a monorepo: hooks run from the repository root and read the configuration
  there.

## [0.3.1] - 2026-10-05

No code changes. This release refreshes the package description on PyPI.

### Changed

- README: a "Start here" section for first-time contributors, the outdated
  Hacktoberfest 2024 badge removed, and absolute links to `CONTRIBUTING.md`
  and `LICENSE` so they resolve on PyPI.

## [0.3.0] - 2026-09-30

### Added

- `check-files` (alias `check-staged`) scans staged additions for secrets (AWS, GitHub, OpenAI,
  Anthropic, Slack and Stripe keys, PEM private keys, hardcoded database
  passwords), with `--no-secrets` and `--json`. Placeholder values and
  documentation samples are skipped.
- `commit` (alias `cz`): an interactive Conventional Commits wizard.

### Changed

- Published on PyPI as `commit-shield`. Both `commit-shield` and
  `commit-guard` are installed as CLI commands.

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

[0.4.0]: https://github.com/lui01212/commit-guard/releases/tag/v0.4.0
[0.3.1]: https://github.com/lui01212/commit-guard/releases/tag/v0.3.1
[0.3.0]: https://github.com/lui01212/commit-guard/releases/tag/v0.3.0
[0.2.0]: https://github.com/lui01212/commit-guard/releases/tag/v0.2.0
[0.1.0]: https://github.com/lui01212/commit-guard/releases/tag/v0.1.0
