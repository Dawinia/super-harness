---
# super-harness ⇄ superpowers integration marker (parsed by SuperpowersAdapter):
change: 2026-07-06-remove-custom-plugin-loading
stage: plan
scope:
  files:
    - src/super_harness/adapters/registry.py
    - src/super_harness/core/_registry.py
    - src/super_harness/core/_plugin_loader.py
    - src/super_harness/core/paths.py
    - src/super_harness/sensors/registry.py
    - src/super_harness/gates/registry.py
    - src/super_harness/cli/sensor.py
    - src/super_harness/cli/gate.py
    - src/super_harness/cli/adapter.py
    - src/super_harness/cli/sync.py
    - src/super_harness/daemon/framework_observer.py
    - src/super_harness/engineering/agents_md_render.py
    - tests/unit/adapters/test_registry.py
    - tests/unit/sensors/test_registry.py
    - tests/unit/gates/test_registry.py
    - tests/unit/cli/test_sensor.py
    - tests/unit/cli/test_gate.py
    - tests/integration/cli/test_adapter.py
    - tests/unit/core/test_no_plugin_exec.py
    - docs/limitations.md
    - docs/cli-reference.md
    - docs/plans/2026-07-06-plugin-loading-removal-design.md
    - docs/plans/2026-07-06-plugin-loading-removal-implementation.md
---

# Remove custom plugin loading (v0.1 builtin-only) — Implementation Plan

> **For agentic workers:** This project is self-hosted under super-harness. Execution
> runs through the self-host lifecycle (`change start` → plan review → `implementation` →
> `done` → code review → `attest` → PR), NOT subagent-driven-development. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Delete the arbitrary-code-execution primitive (`exec_module` on a yaml-supplied
path) from v0.1 so no live path can execute user-supplied plugin code; collapse the three
yaml loaders (adapters / sensors / gates) to builtin-only.

**Architecture:** Remove `core/_plugin_loader.py` outright; strip the `builtin: false`
(adapters) and dict-form (sensors/gates) plugin branches; a non-builtin entry now raises a
clear `ValueError`. The three runtime callers already wrap load in `try/except` (fail-safe
advisory-skip), so no crash. Full removal of the v0.2 extension seam — v0.2 rebuilds it
with a sandbox. See `docs/plans/2026-07-06-plugin-loading-removal-design.md`.

**Tech Stack:** Python 3.10+, pytest, click, PyYAML.

---

## File Structure

**Source (modify):**
- `src/super_harness/adapters/registry.py` — drop `_resolve_custom` + `load_class_from_path` import; builtin-only, non-builtin entry raises.
- `src/super_harness/core/_registry.py` — drop `_load_plugin`, `read_plugin_paths`, `builtin_only`; dict entry raises.
- `src/super_harness/sensors/registry.py` — drop `builtin_only` param.
- `src/super_harness/gates/registry.py` — drop `builtin_only` param.
- `src/super_harness/cli/sensor.py` — drop plugin row branch + `read_plugin_paths` + `builtin_only`.
- `src/super_harness/cli/gate.py` — same as sensor.

**Source (delete):**
- `src/super_harness/core/_plugin_loader.py`

**Tests (modify/prune):**
- `tests/unit/adapters/test_registry.py`, `tests/unit/sensors/test_registry.py`,
  `tests/unit/gates/test_registry.py`, `tests/unit/cli/test_sensor.py`,
  `tests/unit/cli/test_gate.py`

**Docs:**
- `docs/limitations.md`, `docs/cli-reference.md`, touched-file docstrings,
  `private/specs/2026-05-26-sensor-gate-architecture.md`,
  `private/specs/2026-05-27-adapter-architecture.md`, `AGENTS.md` (regen).

---

## Task 1: Security RED test — a `builtin: false` adapter entry must NOT execute

**Files:**
- Test: `tests/unit/adapters/test_registry.py`

- [ ] **Step 1: Write the failing security regression test (parameterized)**

Parameterize over BOTH exec branches (`type: framework` at `registry.py:244` and
`type: agent` at `registry.py:250`) and over every non-builtin shape — `builtin: false`,
the `builtin` key omitted (the realistic hand-edit; `entry.get("builtin", False)` defaults
to reject), AND `builtin: false, enabled: false` (proves the reject fires BEFORE the
`enabled` skip — see Codex blocker / Task 2). Add to `tests/unit/adapters/test_registry.py`:

```python
import pytest


def _evil_module(sentinel: Path) -> str:
    # Import side effect = writing the sentinel. If the module is ever exec'd,
    # the sentinel appears — the assertion that it does NOT is the RCE guard.
    return (
        "from pathlib import Path\n"
        f"Path({str(sentinel)!r}).write_text('pwned')\n"
        "class Evil:\n    pass\n"
    )


@pytest.mark.parametrize("kind", ["framework", "agent"])
@pytest.mark.parametrize(
    "extra",
    [
        {"builtin": False},                    # explicit non-builtin
        {},                                    # builtin key omitted
        {"builtin": False, "enabled": False},  # disabled non-builtin — must STILL raise
    ],
)
def test_non_builtin_adapter_is_rejected_without_executing(
    tmp_path: Path, kind: str, extra: dict
) -> None:
    sentinel = tmp_path / "EXECUTED"
    mod = tmp_path / "evil.py"
    mod.write_text(_evil_module(sentinel), encoding="utf-8")
    entry = {"name": "evil", "type": kind, "path": str(mod), "class": "Evil", **extra}
    yml = tmp_path / "adapters.yaml"
    yml.write_text(yaml.safe_dump({"adapters": [entry]}), encoding="utf-8")

    with pytest.raises(ValueError, match="not supported in v0.1"):
        load_adapters(yml)
    assert not sentinel.exists(), "plugin module was executed — RCE surface still open"
```

(`tests/unit/adapters/test_registry.py` already imports both `pytest` and `yaml` at the top
— do NOT re-import. Match the neighboring tests' `yaml.safe_dump` style.)

- [ ] **Step 2: Run test to verify it fails (RED)**

Run: `pytest "tests/unit/adapters/test_registry.py::test_non_builtin_adapter_is_rejected_without_executing" -v`
Expected: FAIL — currently `load_adapters` executes `evil.py` for the enabled cases
(sentinel appears, no raise), and the `enabled: false` case is silently skipped (no raise).

## Task 2: Collapse the adapters loader to builtin-only

**Files:**
- Modify: `src/super_harness/adapters/registry.py`

- [ ] **Step 1: Remove the custom-loading import**

Delete line: `from super_harness.core._plugin_loader import load_class_from_path`
Delete the `_FW_BASE` / `_AG_BASE` bindings (only `_resolve_custom` used them). Keep the
`_BuiltinAdapter` type alias and everything else.

- [ ] **Step 2: Reject non-builtin entries BEFORE the enabled check, delete `_resolve_custom`**

In `load_adapters`, the current order is: name/dup validation → `if not entry.get("enabled", True): continue` → builtin/custom branch. The reject MUST fire before the `enabled` skip so a
`builtin: false, enabled: false` entry still raises (Codex blocker — the contract is "any
non-builtin is rejected, loudly, regardless of enabled"). Insert the reject immediately
after the `seen_names.add(raw_name)` line and BEFORE the `enabled` check:

```python
        # v0.1 is builtin-only. Reject any non-builtin entry loudly, BEFORE the
        # enabled check, so a disabled non-builtin can never slip through silently
        # and no path can import a user-supplied module. (`builtin: true` is the
        # only accepted value; false / omitted / truthy-non-bool all reject.)
        if entry.get("builtin", False) is not True:
            raise ValueError(
                f"{yaml_path}: adapter {raw_name!r} is not a built-in "
                f"(builtin must be true); custom plugins are not supported in "
                f"v0.1 (builtin-only). See docs/limitations.md."
            )

        if not entry.get("enabled", True):
            continue

        _resolve_builtin(entry, raw_name, yaml_path, frameworks, agents)
```

(Remove the old `if entry.get("builtin", False): _resolve_builtin(...) else: _resolve_custom(...)`
block.) Delete the entire `_resolve_custom` function.

- [ ] **Step 3: Update the module + `load_adapters` docstrings**

Remove the "custom (`builtin: false`) entries are dynamically imported" sentence and the
"**v0.1 plugin scope:** custom adapters execute arbitrary code" note from the module
docstring. In `load_adapters`'s Raises section, drop the
`FileNotFoundError / ImportError / AttributeError / TypeError` custom-loader line; keep
`yaml.YAMLError` + `ValueError` (now also covering the builtin:false rejection).

- [ ] **Step 4: Run the security test — now GREEN**

Run: `pytest "tests/unit/adapters/test_registry.py::test_non_builtin_adapter_is_rejected_without_executing" -v`
Expected: PASS for all 6 parametrizations (raises ValueError, no sentinel).

## Task 3: Prune / convert the adapters custom-loading tests

**Files:**
- Modify: `tests/unit/adapters/test_registry.py`

- [ ] **Step 1: Delete tests that assert custom loading works or exercise the deleted primitive**

Delete these (they test removed behavior):
- `test_load_custom_framework_via_path_class`
- `test_custom_missing_path_raises`
- `test_custom_missing_class_raises`
- `test_custom_nonexistent_path_raises`
- `test_custom_named_like_builtin_raises`
- `test_load_class_from_path_success`
- `test_load_class_from_path_missing_attribute`
- `test_load_class_from_path_wrong_base`

Remove the `from super_harness.core._plugin_loader import load_class_from_path` import at
the top. Keep `test_duplicate_yaml_name_raises` (dup-name detection runs before the builtin
check — still valid). For `test_disabled_entry_skipped`: after the Task 2 reorder, a
disabled entry only skips when it is a **builtin** (a disabled non-builtin now RAISES).
Retarget this test to a `builtin: true, enabled: false` entry (e.g. `plain` / `superpowers`
disabled) and assert it is skipped (empty result). If the test currently used
`builtin: false, enabled: false`, that case is now covered by the Task 1 parametrization
(it must raise), so do not leave it asserting a silent skip.

- [ ] **Step 2: Run the adapters registry suite**

Run: `pytest tests/unit/adapters/test_registry.py -v`
Expected: PASS (all remaining + the new Task 1 test).

## Task 4: RED tests — sensors/gates dict entries must NOT execute

**Files:**
- Modify: `tests/unit/sensors/test_registry.py`, `tests/unit/gates/test_registry.py`

- [ ] **Step 1: Add the no-exec rejection test to each file**

For sensors (`tests/unit/sensors/test_registry.py`), add:

```python
def test_plugin_entry_is_rejected_without_executing(tmp_path: Path) -> None:
    sentinel = tmp_path / "EXECUTED"
    mod = tmp_path / "evil_sensor.py"
    mod.write_text(
        "from pathlib import Path\n"
        f"Path({str(sentinel)!r}).write_text('pwned')\n",
        encoding="utf-8",
    )
    yml = tmp_path / "sensors.yaml"
    yml.write_text(
        "sensors:\n"
        "  - my-custom:\n"
        f"      path: {mod}\n"
        "      class: Evil\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="not supported in v0.1"):
        load_sensors(yml)
    assert not sentinel.exists()
```

Mirror it in `tests/unit/gates/test_registry.py` (top key `gates:`, call `load_gates`).

- [ ] **Step 2: Run to verify RED**

Run: `pytest tests/unit/sensors/test_registry.py::test_plugin_entry_is_rejected_without_executing tests/unit/gates/test_registry.py::test_plugin_entry_is_rejected_without_executing -v`
Expected: FAIL — currently the dict entry is exec'd (sentinel appears), no raise.

## Task 5: Collapse `core/_registry` to builtin-only

**Files:**
- Modify: `src/super_harness/core/_registry.py`

- [ ] **Step 1: Remove the plugin import + `builtin_only` param + dict-plugin branch**

- Delete `from super_harness.core._plugin_loader import load_class_from_path`.
- In `load_components`, remove the `builtin_only: bool = False` parameter. Replace the
  per-entry dispatch:

```python
    for entry in entries:
        if isinstance(entry, str):
            _load_builtin(entry, builtin, components)
        elif isinstance(entry, dict):
            raise ValueError(
                f"{yaml_path}: {yaml_top_key} entry {list(entry.keys())!r} is a "
                f"plugin (path + class); custom plugins are not supported in v0.1 "
                f"(builtin-only). See docs/limitations.md."
            )
        else:
            raise ValueError(
                f"{yaml_path}: each entry under {yaml_top_key!r} must be a string "
                f"(built-in name); got {type(entry).__name__}"
            )
```

- Delete the `_load_plugin` function and the `read_plugin_paths` function.
- Remove `read_plugin_paths` from `__all__`.
- Rewrite the module docstring: drop the plugin schema block + the "**v0.1 plugin scope**"
  paragraph; state that only built-in names (strings) are supported and dict/plugin entries
  are rejected in v0.1.

- [ ] **Step 2: Run the no-exec tests — expect an import error first**

The sensors/gates registry wrappers still pass `builtin_only=` (Task 6 fixes them) and the
CLIs still import `read_plugin_paths` (Task 7). Do not run the full suite yet; proceed to
Task 6.

## Task 6: Drop `builtin_only` from the sensors/gates registry wrappers

**Files:**
- Modify: `src/super_harness/sensors/registry.py`, `src/super_harness/gates/registry.py`

- [ ] **Step 1: Simplify `load_sensors`**

```python
def load_sensors(yaml_path: Path) -> list[Sensor]:
    """Load built-in sensors from `yaml_path` (typically `.harness/sensors.yaml`).

    Only built-in names (strings) are supported in v0.1; a plugin (path + class)
    entry raises ValueError. Returns `[]` if the file is absent.
    """
    return load_components(
        yaml_path,
        yaml_top_key="sensors",
        base_class=_BASE,
        builtin=_BUILTIN,
    )
```

Also update the module docstring: drop the "Plugin via dynamic import" block and the
"**v0.1 plugin scope**" paragraph; drop the "Do NOT call it from a plugin module …"
paragraph in `register_builtin` (no plugin modules exist now).

- [ ] **Step 2: Mirror in `load_gates`** (top key `gates`, same docstring edits).

- [ ] **Step 3: Convert the registry tests**

In `tests/unit/sensors/test_registry.py` and `tests/unit/gates/test_registry.py`:
- Remove every `builtin_only=True` / `builtin_only=False` argument (call `load_*(yml)`).
- Delete tests that assert plugin loading succeeds or exercise plugin-only schema errors
  routed through the now-deleted `_load_plugin`: `test_load_custom_plugin`,
  `test_load_rejects_non_gate_plugin_class` / `test_load_rejects_non_sensor_plugin_class`,
  `test_load_skips_disabled_plugin`, `test_load_skips_all_plugins_when_builtin_only`,
  `test_load_rejects_plugin_with_missing_path`, `test_load_rejects_plugin_with_missing_class_key`,
  `test_load_rejects_plugin_with_nonexistent_path`, `test_load_rejects_plugin_with_class_not_in_module`,
  `test_load_rejects_plugin_with_multiple_keys`.
- Keep and adjust: `test_load_returns_empty_when_yaml_missing`, `test_load_builtin_by_name`
  / `test_register_builtin_then_load` (drop `builtin_only`), `test_load_skips_unknown_builtin_with_warning`,
  `test_load_rejects_non_list_entries`, `test_load_handles_null_top_key`,
  `test_load_handles_empty_yaml_file`, `test_get_builtin_*`, `test_list_builtins_*`, plus
  the new no-exec test from Task 4.

- [ ] **Step 4: Run both registry suites**

Run: `pytest tests/unit/sensors/test_registry.py tests/unit/gates/test_registry.py -v`
Expected: PASS.

## Task 7: Simplify the CLI `sensor list` / `gate list`

**Files:**
- Modify: `src/super_harness/cli/sensor.py`, `src/super_harness/cli/gate.py`

- [ ] **Step 1: Drop the plugin row branch + `read_plugin_paths` + `builtin_only`**

In `_collect_sensor_rows` (sensor.py) keep the strict `load_sensors(yaml_path)` call for
its error-surfacing side effect (dict entry → ValueError → EXIT_VALIDATION), but the
returned instances are all built-ins, so drop `read_plugin_paths`, drop the `builtin_only`
arg, and drop the `"source": "plugin"` row append. Simplify to:

```python
    if yaml_path.exists():
        # Strict load surfaces yaml-shape / plugin-rejection errors to the caller.
        # v0.1 is builtin-only, so every loaded instance is already a built-in row.
        load_sensors(yaml_path)
    return rows
```

Update the `_collect_sensor_rows` docstring (drop the `read_plugin_paths` / plugin-path
mention). Remove the now-unused `read_plugin_paths` import. In `_render_human_table`, drop
the `if r["source"] == "plugin"` branch (rows are always built-in now); the `path` key can
stay `None` for JSON stability.

Also edit the two remaining stale-prose surfaces in `cli/sensor.py` (both in scope):
- The `sensor list` COMMAND docstring `"""List built-in + plugin sensors visible to the
  dispatcher."""` → `"""List built-in sensors visible to the dispatcher."""` (this string is
  the ground truth `doc check` renders into `docs/cli-reference.md`).
- The MODULE docstring (`cli/sensor.py:1-26`) — remove the "distinguishing built-in
  registrations from `.harness/sensors.yaml` plugin entries", "Plugin rows annotate the
  source with the yaml-declared path", and "plugin (always a string) rows" sentences; state
  that v0.1 is builtin-only and the strict loader still runs to surface yaml-shape errors.

- [ ] **Step 2: Mirror ALL of Step 1 in `gate.py`** (command help docstring, module
  docstring, `_collect_gate_rows`, `_render_human_table`, `read_plugin_paths` import).

- [ ] **Step 3: Convert the CLI list tests**

In `tests/unit/cli/test_sensor.py` and `tests/unit/cli/test_gate.py`:
- Delete plugin-output tests: `test_list_with_plugin_yaml`, `test_list_marks_builtin_vs_plugin`,
  and the plugin-branch of `test_list_json_output` (retarget it to a builtin-only yaml, or
  delete if fully plugin-specific).
- Convert `test_list_reports_yaml_validation_errors` to feed a dict/plugin entry and assert
  the command exits non-zero (EXIT_VALIDATION) with the "not supported in v0.1" message.
- Remove `_write_plugin_module` helper if it becomes unused.

- [ ] **Step 4: Run the CLI suites**

Run: `pytest tests/unit/cli/test_sensor.py tests/unit/cli/test_gate.py -v`
Expected: PASS.

## Task 8: Delete `_plugin_loader.py` + add the structural no-exec guard

**Files:**
- Delete: `src/super_harness/core/_plugin_loader.py`
- Test: `tests/unit/core/test_no_plugin_exec.py` (create)

- [ ] **Step 1: Confirm no importers remain**

Run: `grep -rn "_plugin_loader\|load_class_from_path" src/ tests/`
Expected: no matches (all removed in Tasks 2/5/3/6). If any remain, remove them.

- [ ] **Step 2: Delete the file**

Run: `git rm src/super_harness/core/_plugin_loader.py`

- [ ] **Step 3: Add a structural guard test (module-gone AND no exec primitive anywhere)**

The design requires "no `exec_module` call remains in the package" — a module-import check
alone is too weak (both reviewers flagged this). Scan the whole `src/super_harness` tree for
the load-from-path primitives, so a reintroduction ELSEWHERE is caught too. Create
`tests/unit/core/test_no_plugin_exec.py`:

```python
"""Guard: the arbitrary-code-execution plugin primitive stays deleted (F12).

v0.1 is builtin-only. If v0.2 reintroduces plugin loading it must ship with a
sandbox and this guard must be updated deliberately — not silently regressed.
"""
import importlib
from pathlib import Path

import super_harness

# Import-from-path / dynamic-exec primitives that would reopen the RCE surface.
_FORBIDDEN = (
    "exec_module(",
    "spec_from_file_location(",
    "load_class_from_path",
    "_plugin_loader",
)


def test_plugin_loader_module_is_gone() -> None:
    try:
        importlib.import_module("super_harness.core._plugin_loader")
    except ModuleNotFoundError:
        return
    raise AssertionError("core._plugin_loader was reintroduced without review")


def test_no_load_from_path_primitive_in_package() -> None:
    pkg_root = Path(super_harness.__file__).parent
    offenders: list[str] = []
    for py in pkg_root.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        for needle in _FORBIDDEN:
            if needle in text:
                offenders.append(f"{py.relative_to(pkg_root)}: {needle}")
    assert not offenders, (
        "arbitrary-code-execution plugin primitive reappeared in the package "
        f"(F12 regression): {offenders}"
    )
```

- [ ] **Step 4: Run it**

Run: `pytest tests/unit/core/test_no_plugin_exec.py -v`
Expected: PASS (only after Tasks 2/3/5/6/7/8 have removed every occurrence — run last).

## Task 9: Docs — limitations + CLI command help

**Files:**
- Modify: `docs/limitations.md`
- (CLI docstrings edited in Task 7; `docs/cli-reference.md` is REGENERATED in Task 10.)

Note: `private/` is gitignored, so the historical architecture specs
(`private/specs/*`) are out of PR scope — no edit (they do not ship). `docs/cli-reference.md`
is a generated doc (`super-harness doc check`), so it is NOT hand-edited; the "List built-in
gates/sensors" source is the `gate list` / `sensor list` command docstrings in
`cli/gate.py` / `cli/sensor.py`, edited in Task 7 Step 1.

- [ ] **Step 1: `docs/limitations.md`** — under "What v0.1 does NOT ship yet", add:

```markdown
**Custom plugins (v0.2+):**
- Custom sensors / gates / framework adapters loaded from `.harness/*.yaml`
  (`path` + `class`). v0.1 is builtin-only: loading contributor Python in-process
  needs a trust/sandbox model, which lands with the plugins themselves in v0.2. A
  non-builtin entry is rejected (it is not executed).
```

- [ ] **Step 2: `core/paths.py` — drop stale "enumerate plugin entries" wording**

Two docstrings still describe a plugin surface. In `sensors_yaml_path`, change
`"... reads this to enumerate plugin entries."` → `"... reads this to list the built-in
sensors named in the file (v0.1 is builtin-only)."`; mirror in `gates_yaml_path`.

- [ ] **Step 3: `cli/adapter.py` — relabel the dead "custom" adapter source**

`_collect_adapter_rows`'s display maps `"source": "built-in" if r["builtin"] else "custom"`
(cli/adapter.py ~line 751). Since a non-builtin adapter row is now rejected by every loader
(it can never activate), "custom" misrepresents it as a supported extension. Change the
else-label from `"custom"` to `"unsupported"` so `adapter list` is honest that a hand-edited
`builtin: false` row is not a live seam. (This command reads raw yaml and never imports the
path — not an RCE surface — but the relabel completes "delete the seam entirely".) Update the
corresponding assertion in `tests/integration/cli/test_adapter.py` (grep for `"custom"`), and
if a test fabricates a `builtin: false` row expecting a working "custom" adapter, retarget it
to assert the `"unsupported"` label.

Note: the `gate list` / `sensor list` command help docstrings are edited in Task 7 Step 1/2
(they are the source `doc check` renders into `docs/cli-reference.md`).

## Task 10: Regenerate AGENTS.md + full green

- [ ] **Step 1: Regenerate managed docs**

Run: `export PATH="$PWD/.venv/bin:$PATH" && super-harness doc check --fix && super-harness sync --agents-md && super-harness doc check && super-harness sync --check`
Expected: exit 0. `doc check --fix` regenerates `docs/cli-reference.md` from the edited
command docstrings (the "List built-in gates/sensors" lines lose "+ plugin"). AGENTS.md is
regenerated if any adapter subsection changed (likely no diff — this change touches no
adapter subsection text, but run to be safe). If AGENTS.md does diff, add it to the
lifecycle scope before `attest write`.

- [ ] **Step 2: Run the full suite**

Run: `export PATH="$PWD/.venv/bin:$PATH" && pytest -q`
Expected: PASS (the ~1628 baseline minus the pruned plugin tests plus the new no-exec tests).

- [ ] **Step 3: Lint / type**

Run: `export PATH="$PWD/.venv/bin:$PATH" && ruff check src tests && mypy src`
Expected: clean. (Watch for now-unused imports flagged by ruff — remove them.)

## Task 11: Stale exception-family comments in the runtime callers (scope expansion)

Codex code-review flagged that the three runtime `load_adapters` callers still describe
its OLD exception surface (the "six-family error set" incl. `ImportError`/`AttributeError`/
`TypeError` from the deleted plugin-exec path). Builtin-only `load_adapters` now raises only
`yaml.YAMLError` / `ValueError` / `OSError`.

**Files:** `src/super_harness/cli/sync.py`, `src/super_harness/daemon/framework_observer.py`,
`src/super_harness/engineering/agents_md_render.py`.

- [ ] Update the comment/docstring in each to the current three-family surface.
- [ ] Narrow the over-broad `except` tuples in `cli/sync.py` and `agents_md_render.py` to
  `(yaml.YAMLError, ValueError, OSError)` (drop the now-unreachable exec families).
  `framework_observer` uses a bare `except Exception` — docstring only.

No behavior change (the dropped exceptions can no longer be raised); existing fail-safe tests
cover these paths. These three files were out of the original `plan_ready` scope, so this
change is re-declared under a wider scope (slug `2026-07-06-remove-custom-plugin-loading`) —
the harness has no verb to expand scope from `READY_TO_MERGE`.

---

## Self-Review

- **Spec coverage:** Design §"Scope of removal" → Tasks 2/5/6/7/8. §"Behavior contract"
  (raise, not skip) → Tasks 2/5. §"Testing" (no-exec RED for all three) → Tasks 1/4 +
  structural guard Task 8. §"Docs" → Task 9 + docstring edits folded into Tasks 2/5/6/7.
  §"AGENTS.md regen" → Task 10.
- **Fail-safe claim:** the three runtime callers (`cli/sync.py`,
  `daemon/framework_observer.build_manager_failsafe`, `engineering/agents_md_render`)
  already `try/except Exception` around `load_adapters`, so the new ValueError degrades to
  advisory-skip / no-watchers — no crash. No code change needed there; do NOT add guards
  (would be the patch-style fix the design rejects).
- **Type consistency:** `load_sensors(yaml_path)` / `load_gates(yaml_path)` /
  `load_components(...)` all lose `builtin_only` together (Tasks 5/6); every caller updated
  (Task 7 CLIs, Task 3/6 tests). `read_plugin_paths` deleted (Task 5) and its sole importers
  (Task 7 CLIs) updated.
- **Blast-radius / self-host:** no decision anchors any touched file → no tier-2 reconcile.
  Scope is carried by the plan doc's `scope.files` frontmatter (SuperpowersAdapter emits
  `plan_ready` with it); it lists every tracked Source/Test/Docs file plus this plan + the
  design doc. `private/` is gitignored → the historical specs are out of PR scope. `AGENTS.md`
  is added to scope only if `sync --agents-md` produces a diff.
