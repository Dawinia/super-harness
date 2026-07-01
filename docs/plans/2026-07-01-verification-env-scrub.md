# Verification Env Scrub (HG-ENV-LEAK) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the verification subprocess from inheriting ambient `SUPER_HARNESS_*` env, so an exported `SUPER_HARNESS_CHANGE_ID` (or any harness knob) can no longer poison the e2e tests run by `super-harness done`.

**Architecture:** Add one module-level helper `_scrubbed_environ()` in `sensors/verification_runner.py` that returns `os.environ` minus every `SUPER_HARNESS_*` key, and use it as the base layer when building each check's `merged_env`. The scrub applies only to the ambient base — `defaults.env`/`spec.env` still layer on top, so an explicitly-declared harness var is preserved.

**Tech Stack:** Python 3.10+, pytest. TDD, one source file + its unit tests.

---

## File Structure

- Modify: `src/super_harness/sensors/verification_runner.py`
  - Add `_HARNESS_ENV_PREFIX` constant + `_scrubbed_environ()` helper.
  - Change `_config_check_task` (line ~565) to build `merged_env` from the scrubbed base.
  - Update the `_config_check_task` docstring (lines ~559–562) to note the base-layer scrub.
- Modify: `tests/unit/sensors/test_verification_runner.py`
  - Import `_scrubbed_environ`.
  - Add a helper unit test (prefix stripped, non-harness kept).
  - Add a `collect_checks` behavioral regression (ambient poison scrubbed, declared var preserved).
- Also in the change scope (committed on the branch, no code): the two docs —
  `docs/plans/2026-07-01-verification-env-scrub.md` (this plan) and
  `docs/plans/2026-07-01-verification-env-scrub-design.md` (the design; Task 3
  appends its acceptance note here). Both are declared in `plan ready --scope`
  so editing the design doc in Task 3 is not scope drift.

No `core/decisions.py` change → no tier-2 reconcile. Sensor-only edit → no new `core → sensors` import edge.

---

### Task 1: `_scrubbed_environ()` helper

**Files:**
- Modify: `src/super_harness/sensors/verification_runner.py`
- Test: `tests/unit/sensors/test_verification_runner.py`

- [ ] **Step 1: Add the import for the helper under test**

In the existing `from super_harness.sensors.verification_runner import (...)` block in the test file, add `_scrubbed_environ,` (keep the list alphabetical-ish; placing it after `_result`/near the other underscore imports is fine — the block already imports private names like `_all_pass_must`).

- [ ] **Step 2: Write the failing test**

Add to `tests/unit/sensors/test_verification_runner.py`:

```python
def test_scrubbed_environ_strips_harness_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Every SUPER_HARNESS_* knob is dropped from the ambient base; unrelated
    # vars (PATH, and any non-harness name) survive.
    monkeypatch.setenv("SUPER_HARNESS_CHANGE_ID", "leaked")
    monkeypatch.setenv("SUPER_HARNESS_ACTOR", "leaked@x")
    monkeypatch.setenv("NOT_HARNESS", "kept")

    scrubbed = _scrubbed_environ()

    assert not any(k.startswith("SUPER_HARNESS_") for k in scrubbed)
    assert scrubbed["NOT_HARNESS"] == "kept"
    assert "PATH" in scrubbed  # unrelated ambient vars pass through
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/unit/sensors/test_verification_runner.py::test_scrubbed_environ_strips_harness_prefix -v`
Expected: FAIL with `ImportError` / `cannot import name '_scrubbed_environ'`.

- [ ] **Step 4: Write the helper**

In `src/super_harness/sensors/verification_runner.py`, add near the top-level module constants/helpers (above `_config_check_task`):

```python
_HARNESS_ENV_PREFIX = "SUPER_HARNESS_"


def _scrubbed_environ() -> dict[str, str]:
    """Ambient `os.environ` minus every ``SUPER_HARNESS_*`` knob.

    The verification subprocess must run in a clean-room with respect to
    harness-control env (as CI does). Otherwise an exported knob — e.g.
    ``SUPER_HARNESS_CHANGE_ID`` set for the self-host lifecycle — leaks into the
    pytest subprocess and its spawned hooks, changing gate behaviour and causing
    false failures. Scrubs the ambient base only; `os.environ` is never mutated.
    """
    return {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(_HARNESS_ENV_PREFIX)
    }
```

(`os` is already imported at the top of the module.)

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/unit/sensors/test_verification_runner.py::test_scrubbed_environ_strips_harness_prefix -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/super_harness/sensors/verification_runner.py tests/unit/sensors/test_verification_runner.py
git commit -m "feat(sensors): add _scrubbed_environ helper stripping SUPER_HARNESS_* from ambient env"
```

---

### Task 2: Wire the scrub into the check env + behavioral regression lock

**Files:**
- Modify: `src/super_harness/sensors/verification_runner.py:565` (and docstring ~559–562)
- Test: `tests/unit/sensors/test_verification_runner.py`

- [ ] **Step 1: Write the failing regression test**

Add to `tests/unit/sensors/test_verification_runner.py` (mirrors the existing
`test_collect_checks_merges_env_with_os_environ` shell-echo pattern):

```python
def test_collect_checks_scrubs_ambient_harness_env_but_keeps_declared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Ambient SUPER_HARNESS_CHANGE_ID must NOT reach the check subprocess (it
    # would poison e2e hooks). A SUPER_HARNESS_* var DECLARED in defaults.env is
    # a deliberate config layer and MUST survive. Baseline disabled so tasks[0]
    # is the check under test.
    monkeypatch.setenv("SUPER_HARNESS_CHANGE_ID", "leaked")
    archive = tmp_path / "arch"
    cfg = _config(
        layers=Layers(baseline=False),
        checks=[
            _spec(
                check_id="envc",
                command="echo [$SUPER_HARNESS_CHANGE_ID][$SUPER_HARNESS_KEEP]",
                capture="stdout",
            )
        ],
        defaults=Defaults(env={"SUPER_HARNESS_KEEP": "declared"}),
    )
    tasks = collect_checks(cfg, context=_ctx(tmp_path), archive=archive, variables={})
    tasks[0].run()
    assert (archive / "envc.stdout").read_text() == "[][declared]\n"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/sensors/test_verification_runner.py::test_collect_checks_scrubs_ambient_harness_env_but_keeps_declared -v`
Expected: FAIL — actual stdout is `[leaked][declared]\n` because line 565 still spreads raw `os.environ`.

- [ ] **Step 3: Wire the scrub into `merged_env`**

In `src/super_harness/sensors/verification_runner.py`, change the `_config_check_task` body:

```python
    merged_env = {**_scrubbed_environ(), **cfg.defaults.env, **spec.env}
```

(was `merged_env = {**os.environ, **cfg.defaults.env, **spec.env}`)

And update the docstring paragraph (~559–562) to:

```python
    """Wrap one config `CheckSpec` as a `CheckTask` bound to a `run_check` call.

    Resolves the per-check workdir + merges the three env layers at BIND time
    (scrubbed `os.environ` < `defaults.env` < `spec.env`) so the closure captures
    concrete values, then binds them via default args to dodge Python's
    late-binding-in-loops trap (the `spec=spec, ...` defaults snapshot per-
    iteration values). The base layer is `_scrubbed_environ()` (ambient minus
    `SUPER_HARNESS_*`) so an exported harness knob cannot leak into the check
    subprocess; a knob explicitly declared in `defaults.env`/`spec.env` still
    layers on top and is preserved.
    """
```

- [ ] **Step 4: Run the new regression + the pre-existing env test to verify both pass**

Run: `python -m pytest tests/unit/sensors/test_verification_runner.py::test_collect_checks_scrubs_ambient_harness_env_but_keeps_declared tests/unit/sensors/test_verification_runner.py::test_collect_checks_merges_env_with_os_environ -v`
Expected: both PASS (the new scrub regression, and the unchanged PATH/layering test).

- [ ] **Step 5: Run the full verification_runner unit file**

Run: `python -m pytest tests/unit/sensors/test_verification_runner.py -q`
Expected: all PASS — no regression in the env/merge/late-binding tests.

- [ ] **Step 6: Commit**

```bash
git add src/super_harness/sensors/verification_runner.py tests/unit/sensors/test_verification_runner.py
git commit -m "fix(sensors): scrub SUPER_HARNESS_* from verification subprocess env (HG-ENV-LEAK)"
```

---

### Task 3: Manual acceptance (bite evidence) — the leak no longer bites the runner

**Files:** `docs/plans/2026-07-01-verification-env-scrub-design.md` (append a short
acceptance note; verbose output stays in `private/`).

> **Why not a bare `python -m pytest`:** the scrub lives in
> `_config_check_task` (the runner). A bare `python -m pytest` bypasses the
> runner entirely and inherits the exported env straight from the shell, so it
> would fail *both* before and after the fix. Acceptance MUST route through
> `super-harness verify`/`done`. The bare pytest is used here only as the
> *negative control* that proves the poison is real.

- [ ] **Step 1: Negative control — prove the ambient poison is real (runner bypassed)**

Run (from repo root, venv on PATH):

```bash
export SUPER_HARNESS_CHANGE_ID=fake-does-not-exist
python -m pytest tests/e2e -q
```

Expected: FAIL — the e2e daemon gate tests fail-open to ALLOW because the bare
pytest process inherits `SUPER_HARNESS_CHANGE_ID=fake` and its spawned hooks
(called without `env=`) read it. This is the pre-fix symptom and confirms the
poison actually reaches the hooks. (It is unaffected by the scrub because it
never touches the runner.)

- [ ] **Step 2: Fix proof — same poison, routed through the runner, is clean**

Run (same shell, `SUPER_HARNESS_CHANGE_ID=fake-does-not-exist` still exported):

```bash
super-harness verify --check pytest
```

Expected: PASS. `verify --check pytest` runs `python -m pytest -q` through
`_config_check_task`, whose `merged_env` is now scrubbed — the pytest subprocess
starts without `SUPER_HARNESS_*`, so the e2e hooks no longer see the poison.
This is the fix's bite evidence: the exact env that fails a bare pytest passes
through the runner.

- [ ] **Step 3: Baseline sanity (no poison)**

```bash
unset SUPER_HARNESS_CHANGE_ID
super-harness verify --check pytest
```

Expected: PASS (unchanged baseline).

- [ ] **Step 4: Record the result**

Append a short "Manual acceptance" note (the three commands + pass/fail) to
`docs/plans/2026-07-01-verification-env-scrub-design.md`; keep any verbose output
in `private/`.

---

## Self-Review

**Spec coverage:**
- Boundary 1 (scrub whole prefix) → Task 1 helper uses `startswith(_HARNESS_ENV_PREFIX)`; Task 1 test asserts `ACTOR` + `CHANGE_ID` both dropped. ✓
- Boundary 2 (no needed env removed; scrub base only, keep declared) → Task 2 regression asserts declared `SUPER_HARNESS_KEEP` survives; existing PATH/layering test re-run in Step 4–5. ✓
- Boundary 3 (fast unit lock, not pytest-in-pytest; manual acceptance) → Task 2 unit regression is the committed lock; Task 3 is the manual bite check, routed through the runner (`verify --check pytest`) with a bare-pytest negative control — never a bare pytest as the fix proof. ✓
- One build site on the changed path / no core→sensors edge / no decisions.py → stated in File Structure (check_runner inherits ambient too but is benign and out of scope); no task touches those. ✓

**Placeholder scan:** No TBD/TODO; every code step shows concrete code and exact commands. ✓

**Type consistency:** `_scrubbed_environ` (Task 1) is the exact name imported and called in Task 2; `_HARNESS_ENV_PREFIX` used consistently. Test helpers (`_config`, `_ctx`, `_spec`, `Defaults`, `Layers`) match the existing test file's definitions. ✓
