# CHIO Portfolio Assistant — working rules

## Change workflow (overrides the global push-to-main preference)

- Never push directly to `main`. Every change lands through a pull request.
- Before a PR merges:
  1. The full test suites pass (`python -m pytest -q`, `npm test` in `frontend/`) — enforced by CI.
  2. An independent second-opinion review pass has run over the PR's diff
     (multiple reviewer agents, findings verified), and its outcome is recorded
     in a PR comment: findings found/fixed, or explicitly "no findings".
- A GitHub ruleset enforces the PR + passing-checks requirement on `main`;
  do not bypass or delete it, and do not use force-push.

## Frontend

- `frontend/dist` is the shipped bundle; rebuild it (`npm run build`) in the
  same PR whenever `frontend/src` changes, or the running app won't reflect
  the change.
- Pure logic goes in testable helpers (`src/lib/`, feature-local `*.ts`),
  covered by `node --test` (see `package.json`).

## Tests

- Regression tests live in `tests/test_regression_fixes.py`: one class per
  defect, a docstring naming the defect, and each test must fail against the
  pre-fix code.

## Diagnostics

- The app writes a rotating local log to `<database dir>/logs/assistant.log`
  (review creation, source state transitions, imports, bulk dismissals).
- When `[diagnostics] repo` is configured, the log tail is mirrored to that
  PRIVATE repository (`portfolio-assistant-diagnostics`) via the GitHub
  Contents API — pure httpx, no git needed on the machine. To debug a remote
  instance, read `logs/<hostname>/assistant.log` there.
- Never commit or publish the log to THIS repository — it is public and the
  log can contain project names and work data.
