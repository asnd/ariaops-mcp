---
description: Verify CI gates locally, sync tests/docs with the change, then commit and push
allowed-tools: Bash(uv run:*), Bash(git:*), Bash(gh:*)
---

Ship the current working-tree changes. Work through every step; stop and report
instead of pushing if any gate fails.

## 1. Run the same gates CI runs (.github/workflows/ci.yml)

```bash
uv run --frozen ruff check src/ tests/
uv run --frozen pyright src/
uv run --frozen pytest -ra --tb=short --maxfail=1 --cov=src/ariaops_mcp --cov-report=term-missing
```

Fix any failure before continuing.

## 2. Check test coverage of the change

Look at the coverage report for the files touched in this change (`git diff
--name-only` / `git status`). Any new module or new branch with no covering
test gets one added under `tests/` following the existing patterns
(pytest + pytest-asyncio, Starlette TestClient, respx for httpx mocking).

## 3. Check CI and docs are in sync with the change

- New runtime/dev dependency in `pyproject.toml`? → `uv lock` so
  `uv sync --frozen` in CI keeps working; commit `uv.lock`.
- New env var, tool, or user-visible behavior? → update `README.md`,
  `.env.example`, and `TOOLS.md` accordingly.
- Anything CI itself must learn (new Python version, new job)? → update
  `.github/workflows/ci.yml`.

## 4. Commit and push

- On `main`: create a descriptive feature branch first (this repo merges via
  PRs), commit, push with `-u origin <branch>`, then open a PR with
  `gh pr create` summarizing the change. On an existing feature branch: commit
  and push to it.
- Write a conventional, descriptive commit message (`feat:`/`fix:`/`docs:` …)
  covering all logical changes; split into multiple commits if the diff
  contains clearly independent changes.
- Never `push --force`, never skip hooks.

Finish by reporting: gates run and their results, what was committed, the
branch/PR URL.
