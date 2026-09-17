# Contributing to commit-guard

Welcome! We are excited to have you contribute to **commit-guard**.

---

## 🌟 Good First Issues

Looking for your first contribution? Check our [open issues with label `good first issue`](https://github.com/lui01212/commit-guard/issues?q=is%3Aissue+state%3Aopen+label%3A%22good+first+issue%22). Common beginner tasks include:
- Adding new sensitive file patterns to `commit_guard/checkers.py`.
- Adding unit test cases for edge-case commit messages.
- Improving documentation or translation of error messages.

---

## 🛠️ Development Setup

1. **Fork and clone:**
   ```bash
   git clone https://github.com/<your-username>/commit-guard.git
   cd commit-guard
   ```

2. **Zero-dependency philosophy:**
   This project relies exclusively on Python standard libraries! No heavy third-party packages are required.

3. **Running tests:**
   Run the test suite using Python's built-in `unittest`:
   ```bash
   python -m unittest discover tests
   ```

4. **Making changes:**
   - Create a branch: `git checkout -b feature/your-feature-name`
   - Write tests for your changes in `tests/test_checkers.py`.
   - Run tests to ensure everything passes: `python -m unittest`
   - Commit with Conventional Commits: `git commit -m "feat(checker): add regex check for api keys"`
   - Push and open a Pull Request!
