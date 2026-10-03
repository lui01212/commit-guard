# Contributing to commit-guard

Thanks for taking a look! Small, focused contributions are welcome.

## Start with one small issue

[Our featured beginner issue](https://github.com/lui01212/commit-guard/issues/8): Add regression tests for multiline commit messages.
It lists the exact files, expected output or test cases, and completion criteria.

Comment if you would like to work on it and wait for assignment before starting.
Respect existing assignments. Ask questions before expanding the scope; a draft PR
is welcome for early feedback. Assignment and passing tests do not guarantee a merge.

## Local setup

Use Python 3.8 or newer. Fork the repository, then clone your fork:

```sh
git clone https://github.com/YOUR-USERNAME/commit-guard.git
cd commit-guard
git switch -c docs/your-change
python -m unittest discover -s tests -v
```

The runtime uses the Python standard library. Tests can run from the source checkout
without installing the package. Packaging, lint, and CI tools have their own dependencies.
For installed CLI commands, use a virtual environment and `python -m pip install -e .`;
review the packaging configuration before installing.

Repository: `commit-guard`; PyPI distribution: `commit-shield`; Python import: `commit_guard`.
Both commit-shield and commit-guard are installed CLI aliases.

Review code before running it. Use synthetic data in examples. Avoid credentials,
private files, hook installation, or AI client configuration changes unless required
by the task.

## Submit a focused PR

- Link the issue and explain the change.
- Keep unrelated cleanup out of the diff.
- Add or update tests when behavior changes; include commands and outcomes.
- For documentation, run the snippets and check the links.
- Use a Conventional Commit message, for example `docs: clarify first local example`.
- Push your branch to your fork and open a PR, or a draft PR for early feedback.

Maintainers review scope, correctness, validation, and privacy before merging.
Respond to review comments in the PR; there is no guaranteed review time.
Contributions are credited through GitHub PR authorship, commit history, and the
[contributors page](https://github.com/lui01212/commit-guard/graphs/contributors).

## Larger contributions

Issues labelled `help wanted` can need design discussion and more testing.
Discuss the scope before starting; documentation and test-only issues are the
recommended starting point.
