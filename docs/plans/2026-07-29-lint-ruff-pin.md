---
change: 2026-07-29-lint-ruff-pin
stage: plan
---

# CI lint broken by an unpinned ruff — fix plan (Micro)

**Goal:** Get `lint` green again and stop an upstream ruff release from turning
`main` and every in-flight PR red without a local change.

**Tier:** Micro — six type-annotation reorderings plus one dependency bound.

## The defect

`pyproject.toml:55` declares `ruff>=0.4` — a lower bound only. CI runs
`pip install -e ".[dev]"`, so it installs whatever ruff released most recently,
while a contributor's existing virtualenv keeps an older pin. When ruff stabilises
a new rule, CI goes red on code nobody touched.

That happened today: RUF036 (`None` must come last in a type union) fires on six
pre-existing sites. Locally `ruff 0.15.14` reports `All checks passed!`; CI's
newer build reports 6 errors. PR #87 — which does not touch any of these files —
was blocked by it.

## Fix

Two parts, both required. Reordering alone leaves the next release free to do the
same thing again; pinning alone hides a real style violation.

1. **Reorder six unions** so `None` is last (pure annotation order, zero runtime
   effect — `X | None | Y` and `X | Y | None` are the same type):
   - `src/super_harness/cli/init_ui.py:957`, `:978`
   - `tests/integration/cli/test_init.py:135`
   - `tests/unit/cli/test_init_ui.py:1014`, `:1015`, `:1016`

2. **Bound the dev pin** to `ruff>=0.15,<0.16` so CI and a fresh local install
   agree, and a new minor cannot introduce rules mid-flight. Upgrading becomes a
   deliberate, reviewable change rather than an ambient breakage.

## Verification

```bash
.venv/bin/ruff check src tests     # expect: All checks passed!
.venv/bin/pytest -q                # expect: 2102 passed (unchanged)
.venv/bin/mypy                     # expect: clean
```

CI `lint` must go green, and PR #87 must go green after rebasing onto this.

## Scope

- `src/super_harness/cli/init_ui.py`
- `tests/integration/cli/test_init.py`
- `tests/unit/cli/test_init_ui.py`
- `pyproject.toml`
- `docs/plans/2026-07-29-lint-ruff-pin.md`

## Not in scope

- Upgrading ruff to the version that introduced the rule (deliberate, separate).
- Any behavioural change. Every edit here is annotation order or a version bound.
