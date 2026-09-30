# commit-shield 🛡️

[![PyPI version](https://img.shields.io/pypi/v/commit-shield.svg)](https://pypi.org/project/commit-shield/)
[![Python versions](https://img.shields.io/pypi/pyversions/commit-shield.svg)](https://pypi.org/project/commit-shield/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Tests](https://github.com/lui01212/commit-guard/actions/workflows/ci.yml/badge.svg)](https://github.com/lui01212/commit-guard/actions)
[![good first issues](https://img.shields.io/github/issues/lui01212/commit-guard/good%20first%20issue?label=good%20first%20issues&color=7057ff)](https://github.com/lui01212/commit-guard/issues?q=is%3Aissue+state%3Aopen+label%3A%22good+first+issue%22)
[![Hacktoberfest](https://img.shields.io/badge/Hacktoberfest-2024-ff7a59?logo=hacktoberfest)](https://hacktoberfest.com/)

**Fast, zero-dependency Git commit message and staged file linter.**

Enforces [Conventional Commits](https://www.conventionalcommits.org/) standards and guards against accidental commits of secrets (`.env`, `.pem`, `id_rsa`) or oversized files (> 10MB).

---

## ⚡ Why commit-shield?

- **Zero dependencies:** Written in pure standard Python. Instant install, lightweight, no massive node_modules or heavy binary dependencies.
- **Fast:** Runs in milliseconds during `git commit`.
- **Pre-commit ready:** Seamless drop-in integration with the popular `pre-commit` framework.
- **Dual protection:** Validates both commit message formatting AND safeguards against accidentally committed credentials/blobs.

---

## 📦 Installation

```bash
pip install commit-shield
```

Or install from source:
```bash
git clone https://github.com/lui01212/commit-guard.git
cd commit-guard
pip install -e .
```

---

## 🚀 Usage

### 1. Check Commit Messages

Validate commit message strings directly:

```bash
# Valid commit message -> exit code 0
commit-guard check-msg -m "feat(auth): add google oauth2 login provider"

# Invalid commit message -> exit code 1 with actionable errors
commit-guard check-msg -m "fixed stuff"
```

Output:
```text
[commit-guard] Commit message validation failed:
  - Header does not follow Conventional Commits format: '<type>(<scope>): <description>'.
    Received: 'fixed stuff'
    Allowed types: build, chore, ci, docs, feat, fix, perf, refactor, revert, style, test
```

### 2. Check Staged Files (Secrets & Large Blobs)

```bash
# Checks all currently staged files in git
commit-guard check-files --strict
```

### 3. One-Click Git Hook Setup (No pre-commit framework needed)

Install the hook directly into your local `.git/hooks/commit-msg`:

```bash
commit-guard install
```

---

## 🔧 Integration with `pre-commit`

Add this to your `.pre-commit-config.yaml`:

```yaml
repos:
  - repo: https://github.com/lui01212/commit-guard
    rev: v0.2.0
    hooks:
      - id: commit-guard-msg
      - id: commit-guard-files
```

---

## ⚙️ Configuration

`commit-guard` works zero-config out of the box, but can be fully customized via `.commit-guard.toml` or `[tool.commit-guard]` in `pyproject.toml`:

```toml
# .commit-guard.toml
max_header_len = 72
require_scope = false
skip_merge_commits = true
max_size_mb = 10.0

# Add extra sensitive patterns to protect
extra_sensitive_patterns = ["*.secret", "*_token.json"]

# Allowlist false-positives
allowlist = ["*.example", "*.sample", "*.template"]
```

See [.commit-guard.toml.example](.commit-guard.toml.example) for all available options.

---

## 📋 Allowed Commit Types

| Type | Purpose |
| :--- | :--- |
| `feat` | A new feature |
| `fix` | A bug fix |
| `docs` | Documentation only changes |
| `style` | Formatting, missing semi-colons, white-space changes |
| `refactor` | Code restructuring without fixing bugs or adding features |
| `perf` | Performance improvement |
| `test` | Adding missing tests or correcting existing tests |
| `build` | Changes that affect the build system or dependencies |
| `ci` | Changes to CI configuration files and scripts |
| `chore` | Maintenance tasks, tooling updates |
| `revert` | Reverting a previous commit |

---

## 🤝 Contributing

Contributions are warmly welcomed! We have plenty of beginner-friendly tasks:
- Adding custom pattern checks.
- Expanding sensitive file extension detections.
- Adding localized error messages (Vietnamese, Spanish, etc.).

Please see [CONTRIBUTING.md](CONTRIBUTING.md) for details on how to get started.

---

## 📄 License

[MIT License](LICENSE) © 2026 lui01212
