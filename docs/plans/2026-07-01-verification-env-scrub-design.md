# Design — Scrub `SUPER_HARNESS_*` from the verification subprocess env (HG-ENV-LEAK)

**Date:** 2026-07-01
**Scope:** `src/super_harness/sensors/verification_runner.py` + its unit tests. Housekeeping (footgun cleanup); does not advance the product thesis.

## Problem

`verification_runner._config_check_task` builds the env for each verification
subprocess (pytest, during `super-harness done`) as:

```python
merged_env = {**os.environ, **cfg.defaults.env, **spec.env}
```

The whole ambient shell environment is passed through. When a developer runs the
self-host lifecycle, they export `SUPER_HARNESS_CHANGE_ID` for other commands.
That value leaks into the pytest subprocess → into the e2e daemon tests that
spawn a real hook → `hook_entry._decide` reads the leaked change_id → the test's
ephemeral daemon has no such change → gate fails open to ALLOW → the e2e test
(which expects BLOCK) fails. Manual `python -m pytest` passes; only `done`
fails; CI is green because CI has a clean environment. This was hit repeatedly
during PR#58/#59 (had to `unset SUPER_HARNESS_CHANGE_ID` before every `done`).

This is a real harness gap (HG-ENV-LEAK): the verification subprocess should run
in a clean-room with respect to harness-control env, the same as CI does.

## Boundary decisions

### 1. Scrub the whole `SUPER_HARNESS_*` prefix, not just `CHANGE_ID`

All four known harness knobs are the same class of footgun if inherited from the
ambient shell into the verification subprocess:

- `SUPER_HARNESS_CHANGE_ID` — wrong change_id → e2e fail-open (the one that bit).
- `SUPER_HARNESS_HOOK_QUERY_TIMEOUT` — an ambient-low value reproduces the same
  fail-open symptom (hook query times out → ALLOW).
- `SUPER_HARNESS_DAEMON_START_TIMEOUT` — an ambient-short value → flaky daemon start.
- `SUPER_HARNESS_ACTOR` — poisons any test asserting on identity/actor resolution.

Whitelisting only `CHANGE_ID` leaves three live latent footguns. Prefix-scrub is
the principled fix and matches CI's clean-room.

### 2. Scrubbing does not remove an env any test genuinely needs

- **One build site on the path under change.** The `pytest` check (which runs
  the e2e suite) goes through `verification_runner._config_check_task`, whose
  `merged_env` at line 565 is the only place that hands a *constructed* base env
  to that subprocess — one point of change. `core/check_runner.py` runs the
  separate decision/doc text-lock checks and inherits ambient env too, but those
  checks neither read `SUPER_HARNESS_*` nor spawn hooks, so they don't carry this
  footgun and are intentionally out of scope.
- **No test depends on a specific ambient `SUPER_HARNESS_*` value reaching the
  subprocess.** The e2e hooks are spawned *without* `env=`, so they inherit the
  pytest process env — that inheritance is exactly the leak, and scrubbing the
  pytest process env is exactly what removes it. The values those hooks legitimately
  need are re-supplied *inside* pytest: unit tests set theirs via `monkeypatch.setenv`
  (after the outer scrub); the e2e autouse fixture `_hook_query_timeout_env`
  re-adds the timeout knobs; `CHANGE_ID`/`ACTOR` are the ones that must *not* leak.
  Integration tests that need a value pass it explicitly
  (`env={**os.environ, "SUPER_HARNESS_CHANGE_ID": "c1"}`) on their own spawned
  subprocess, which does not pass through the changed line.
- **The test pinning this merge** (`test_collect_checks_merges_env_with_os_environ`)
  asserts only that `PATH` survives and that `defaults.env`/`spec.env` layer
  correctly. `PATH` is not `SUPER_HARNESS_*`; untouched.
- **Scrub the base layer only.** The scrub applies to the `os.environ` base
  *before* `defaults.env`/`spec.env` are merged on top. If a `.harness` config
  ever explicitly declares a `SUPER_HARNESS_*` var for a check, that declared
  value is preserved. We strip leakage, never a declared value.

### 3. Regression lock: fast deterministic unit test, not pytest-in-pytest

- **Committed lock (unit):** set ambient `SUPER_HARNESS_CHANGE_ID` and
  `SUPER_HARNESS_ACTOR` via `monkeypatch`, build tasks via `collect_checks`,
  assert the resolved task env contains no `SUPER_HARNESS_*` key. Companion
  assertion: a `SUPER_HARNESS_*` var declared in `defaults.env` **is** preserved
  (locks the "scrub base, keep declared" contract).
- **Why not a full `done` e2e:** `done` runs the entire pytest suite; nesting
  that inside a test is pytest-in-pytest — slow, recursive, meta. Symptom-level
  coverage already exists (the e2e daemon tests that get poisoned today). After
  the fix they pass even under a poisoned ambient env; the unit lock proves the
  seam that makes that true.
- **Manual acceptance (run this change, recorded as bite evidence):** the
  acceptance MUST route through the runner — a bare `python -m pytest` bypasses
  `_config_check_task` and never gets the scrub. Contrast pair: with
  `SUPER_HARNESS_CHANGE_ID=fake` exported, `super-harness verify --check pytest`
  (routes through the runner) is green, while a bare `python -m pytest tests/e2e -q`
  under the same poison still fails (the pre-fix symptom, bypassing the scrub) —
  proving the poison is real and only the runner path is cleaned. Summary
  recorded here; full transcript stays in `private/`.

## Implementation shape

A single module-level helper in `verification_runner.py`, independently testable:

```python
_HARNESS_ENV_PREFIX = "SUPER_HARNESS_"

def _scrubbed_environ() -> dict[str, str]:
    """Ambient env minus harness-control knobs, so the verification subprocess
    runs in a clean-room w.r.t. SUPER_HARNESS_* (matches CI)."""
    return {k: v for k, v in os.environ.items() if not k.startswith(_HARNESS_ENV_PREFIX)}
```

`_config_check_task` line 565 becomes:

```python
merged_env = {**_scrubbed_environ(), **cfg.defaults.env, **spec.env}
```

The docstring at lines 559–562 is updated to note the base-layer scrub. `os.environ`
is never mutated — only the dict handed to the subprocess is scrubbed.

## Non-goals / lifecycle notes

- No change to `core/decisions.py` → no tier-2 `d-decision-records` reconcile tax.
- Sensor-only edit; introduces no `core → sensors` import edge, so the
  `d-core-is-base` architecture-fitness contract is unaffected.
- **Intended side effect (not a bug):** because the scrub is whole-prefix, a
  developer who exports a `SUPER_HARNESS_*` knob (e.g. `HOOK_QUERY_TIMEOUT`)
  before `super-harness done` will find it ignored by verification — the e2e
  suite re-adds its own values and unit/integration lose the override. This is
  the clean-room behavior; the supported override path is declaring the var in
  `.harness` `defaults.env`/`spec.env`.
- Small: 1 source file + tests. The design doc + plan are also committed on the
  change branch (in the harness scope), so this doc is a legitimate scoped edit
  target for the acceptance-note append.

## Manual acceptance (bite evidence, 2026-07-01)

Contrast pair, both with `SUPER_HARNESS_CHANGE_ID=fake-does-not-exist` exported:

- **Negative control (bare pytest, bypasses the runner) → FAILED as expected.**
  `python -m pytest tests/e2e -q` → `2 failed`:
  `test_pre_tool_use_blocks_then_allows` and `test_full_openspec_claude_lifecycle`
  both fail-open to ALLOW (`assert 0 == 2`, "is the daemon up?"). The poison
  really reaches the e2e-spawned hooks — HG-ENV-LEAK reproduced.
- **Fix proof (same poison, routed through the runner) → PASSED.**
  `super-harness verify --check pytest` → `verification passed (1 checks, 0 failed)`.
  The full suite ran through `_config_check_task` with the scrubbed base env, so
  the exact env that fails a bare pytest passes through the runner.
- **Retired footgun (strongest evidence).** `super-harness done` for this very
  change was run with `SUPER_HARNESS_CHANGE_ID=fake-does-not-exist` still exported
  → `implementation_complete emitted`. This is the exact scenario that forced a
  manual `unset` before every `done` in PR#58/#59; it now passes with the knob
  exported, so the lifecycle pothole is retired.
- Baseline (no poison) is subsumed by the fix proof (passing under poison implies
  passing without).

Full transcript kept out of the tracked tree (session-local).
