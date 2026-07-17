---
change: init-interactive-wizard
stage: plan
tier_hint: Large
scope:
  files:
    - pyproject.toml
    - .github/workflows/test.yml
    - src/super_harness/cli/__init__.py
    - src/super_harness/cli/lazy_group.py
    - src/super_harness/adapters/install.py
    - src/super_harness/cli/adapter.py
    - src/super_harness/cli/init.py
    - src/super_harness/cli/init_plan.py
    - src/super_harness/cli/init_ui.py
    - src/super_harness/cli/init_executor.py
    - src/super_harness/cli/init_github.py
    - tests/unit/cli/test_lazy_group.py
    - tests/unit/cli/test_entrypoint.py
    - tests/unit/adapters/test_install.py
    - tests/integration/cli/test_adapter.py
    - tests/integration/cli/test_adapter_install.py
    - tests/unit/cli/test_init_plan.py
    - tests/unit/cli/test_init_ui.py
    - tests/unit/cli/test_init_executor.py
    - tests/integration/cli/test_init.py
    - tests/integration/cli/test_init_setup_github.py
    - tests/integration/cli/test_init_windows_entrypoint.py
    - tests/unit/scripts/test_gen_cli_reference.py
    - scripts/gen_cli_reference.py
    - docs/cli-reference.md
    - docs/getting-started.md
    - docs/plans/2026-07-17-init-interactive-wizard-design.md
    - docs/plans/2026-07-17-init-interactive-wizard-implementation.md
---

# Native Cross-platform `init` Wizard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the comma-driven `super-harness init` interaction with the approved five-stage Questionary/Rich guided wizard while preserving non-interactive behavior and making the installed `init` entrypoint work on native Windows.

**Architecture:** Keep Click as the public command surface, but load top-level commands lazily so `init` does not import unrelated POSIX-only modules. Split initialization into immutable read-only planning, three interaction/rendering backends, a prompt-free executor, and prompt-free GitHub operations. Existing scaffold and marker-bounded write behavior remains the source of truth; the new layers control sequencing, confirmation, and truthful step reporting.

**Tech Stack:** Python 3.10+, Click, Questionary 2.x, Rich 14.x, PyYAML, pytest, mypy, Ruff, GitHub Actions.

---

## File structure

### New production modules

- `src/super_harness/cli/lazy_group.py` — declarative top-level command registry and Click lazy-loading group.
- `src/super_harness/adapters/install.py` — platform-neutral adapter config persistence and installation service shared by `init` and `adapter install`.
- `src/super_harness/cli/init_plan.py` — immutable request/preflight/choice/plan types plus read-only validation and plan construction.
- `src/super_harness/cli/init_ui.py` — capability selection, Questionary/Rich wizard, deterministic line prompts, and non-interactive renderer.
- `src/super_harness/cli/init_executor.py` — prompt-free operation runner and process-local step events.
- `src/super_harness/cli/init_github.py` — read-only GitHub conflict planning and prompt-free application.

### Modified production modules

- `src/super_harness/cli/__init__.py` — define the root group without eager command imports and register stable lazy command specifications.
- `src/super_harness/cli/adapter.py` — consume the shared adapter installation service; keep command-only lifecycle imports local to the commands that need them.
- `src/super_harness/cli/init.py` — retain Click options and orchestration only; add `--yes` and delegate planning/UI/execution.
- `pyproject.toml` — add bounded Questionary and Rich runtime dependencies without adding a package-wide Windows classifier.

### Tests and delivery files

- `tests/unit/cli/test_lazy_group.py` and `tests/unit/cli/test_entrypoint.py` — lazy dispatch, stable help, and forbidden-import isolation.
- `tests/unit/adapters/test_install.py`, `tests/integration/cli/test_adapter.py`, and `tests/integration/cli/test_adapter_install.py` — extraction parity for adapter persistence and installation.
- `tests/unit/cli/test_init_plan.py` — mode precedence, force-review matrix, validation, and planned actions.
- `tests/unit/cli/test_init_ui.py` — capability matrix, line prompts, guided wizard decisions, glyphs, cancellation, and rendering.
- `tests/unit/cli/test_init_executor.py` — operation order, step ledger, failures, and interruption.
- `tests/integration/cli/test_init.py` and `tests/integration/cli/test_init_setup_github.py` — end-to-end compatibility and confirmation/write boundaries.
- `tests/integration/cli/test_init_windows_entrypoint.py` — installed-entrypoint-safe import contract and Windows path/CRLF cases.
- `.github/workflows/test.yml` — focused native Windows wheel job; retain the existing full Ubuntu/macOS matrix.
- `scripts/gen_cli_reference.py`, `tests/unit/scripts/test_gen_cli_reference.py`, and `docs/cli-reference.md` — prove lazy introspection and publish `--yes`.
- `docs/getting-started.md` — document the guided, line, and non-interactive paths with a representative rail capture.

## Task 1: Introduce lazy top-level command dispatch

**Files:**

- Create: `src/super_harness/cli/lazy_group.py`
- Modify: `src/super_harness/cli/__init__.py`
- Create: `tests/unit/cli/test_lazy_group.py`
- Modify: `tests/unit/cli/test_entrypoint.py`
- Modify: `tests/unit/scripts/test_gen_cli_reference.py`

- [ ] **Step 1: Write failing lazy-registry tests**

Add tests that construct a two-command registry with temporary importable modules and assert:

```python
def test_list_commands_does_not_import_registered_modules(monkeypatch):
    group = LazyGroup(command_specs={"alpha": "pkg.alpha:alpha_cmd"})
    assert group.list_commands(click.Context(group)) == ["alpha"]
    assert "pkg.alpha" not in sys.modules


def test_get_command_imports_only_requested_module(monkeypatch):
    group = LazyGroup(
        command_specs={
            "alpha": "pkg.alpha:alpha_cmd",
            "beta": "pkg.beta:beta_cmd",
        }
    )
    command = group.get_command(click.Context(group), "alpha")
    assert command is not None
    assert "pkg.alpha" in sys.modules
    assert "pkg.beta" not in sys.modules
```

Also assert a real leaf (`init`) resolves as `GroupAwareCommand`, a real subgroup and every descendant resolve as `GroupAwareGroup` / `GroupAwareCommand`, root command order is stable, `--help` still lists every command, and the CLI reference generator can intentionally traverse the complete lazy tree. The real-leaf assertion is the regression guard against passing a leaf to `rewrap_subtree`.

- [ ] **Step 2: Run the focused tests and confirm RED**

Run:

```bash
pytest -q tests/unit/cli/test_lazy_group.py tests/unit/cli/test_entrypoint.py tests/unit/scripts/test_gen_cli_reference.py
```

Expected: import error for `super_harness.cli.lazy_group` or assertions showing the root still imports every command eagerly.

- [ ] **Step 3: Implement `CommandSpec` and `LazyGroup`**

Use a frozen specification and `module:attribute` targets:

```python
@dataclass(frozen=True)
class CommandSpec:
    target: str
    help: str


class LazyGroup(GroupAwareGroup):
    def __init__(self, *args: object, command_specs: Mapping[str, CommandSpec], **kwargs: object):
        super().__init__(*args, **kwargs)
        self._command_specs = dict(command_specs)

    def list_commands(self, ctx: click.Context) -> list[str]:
        return list(self._command_specs)

    def get_command(self, ctx: click.Context, name: str) -> click.Command | None:
        spec = self._command_specs.get(name)
        if spec is None:
            return None
        module_name, attribute = spec.target.split(":", 1)
        command = getattr(importlib.import_module(module_name), attribute)
        if isinstance(command, click.Group):
            command.__class__ = GroupAwareGroup
            rewrap_subtree(command)
        else:
            command.__class__ = GroupAwareCommand
        return command
```

`rewrap_subtree` accepts a `click.Group` and rewrites only its descendants, so the loader must wrap the loaded root node explicitly and call the helper only for a group. Override help formatting only as needed to use `CommandSpec.help` without importing command modules. Keep Click's unknown-command behavior and global-option redirection behavior unchanged.

- [ ] **Step 4: Replace eager root imports with the stable registry**

In `src/super_harness/cli/__init__.py`, retain root options and `main`, remove command-module imports and `add_command` calls, and declare every current top-level command with its exact public name, import target, and one-line help. Ensure `init` points to `super_harness.cli.init:init_cmd`.

- [ ] **Step 5: Run focused tests and command-surface checks**

Run:

```bash
pytest -q tests/unit/cli/test_lazy_group.py tests/unit/cli/test_entrypoint.py tests/unit/cli/test_group_options.py tests/unit/scripts/test_gen_cli_reference.py
python -m super_harness.cli --help
python -m scripts.gen_cli_reference --emit >/dev/null
```

Expected: all tests pass, root help lists the same commands in the same order, and reference generation exits 0.

- [ ] **Step 6: Check decisions and commit**

Run:

```bash
super-harness decision check --changed
git add src/super_harness/cli/__init__.py src/super_harness/cli/lazy_group.py tests/unit/cli/test_lazy_group.py tests/unit/cli/test_entrypoint.py tests/unit/scripts/test_gen_cli_reference.py
git commit -m "refactor(cli): load top-level commands lazily"
```

## Task 2: Extract platform-neutral adapter installation

**Files:**

- Create: `src/super_harness/adapters/install.py`
- Modify: `src/super_harness/cli/adapter.py`
- Modify: `src/super_harness/cli/init.py`
- Create: `tests/unit/adapters/test_install.py`
- Modify: `tests/integration/cli/test_adapter.py`
- Modify: `tests/integration/cli/test_adapter_install.py`
- Modify: `tests/unit/cli/test_entrypoint.py`

- [ ] **Step 1: Write failing parity and import-isolation tests**

Move the adapter config round-trip cases into a service-level test and add a subprocess test that installs a meta-path blocker before resolving `init`:

```python
FORBIDDEN_INIT_IMPORTS = {
    "fcntl",
    "super_harness.core.writer",
    "super_harness.core.post_emit",
    "super_harness.daemon.server",
    "super_harness.daemon.supervisor",
}


def test_resolving_init_does_not_import_posix_lifecycle_modules():
    code = """
import importlib.abc
import sys
import click

blocked = {
    "fcntl",
    "super_harness.core.writer",
    "super_harness.core.post_emit",
    "super_harness.daemon.server",
    "super_harness.daemon.supervisor",
}
class Blocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in blocked:
            raise AssertionError(f"forbidden import: {fullname}")
        return None

sys.meta_path.insert(0, Blocker())
from super_harness.cli import main
assert main.get_command(click.Context(main), "init") is not None
"""
    subprocess.run([sys.executable, "-c", code], check=True)
```

The service tests must cover header creation, top-level-key preservation, idempotent install, removal, corrupt YAML, and agent hook/config installation.

- [ ] **Step 2: Run the focused tests and confirm RED**

Run:

```bash
pytest -q tests/unit/adapters/test_install.py tests/unit/cli/test_entrypoint.py tests/integration/cli/test_adapter.py tests/integration/cli/test_adapter_install.py
```

Expected: service import is missing and resolving `init` reaches a forbidden POSIX module.

- [ ] **Step 3: Move shared helpers without changing behavior**

Move `_ADAPTERS_YAML_HEADER`, adapter config read/write functions, entry persistence/removal, and `install_agent_integration` into `super_harness.adapters.install`. Keep exception types, YAML shape, comments, marker replacement, and atomic-write behavior byte-compatible.

`cli.adapter` should import those helpers. Lifecycle-only symbols used by `scan-once` or other commands must be imported inside those command functions so importing the module's install surface does not pull `core.writer` or `core.post_emit`.

- [ ] **Step 4: Point `init.py` at the platform-neutral service**

Replace the import from `super_harness.cli.adapter` with:

```python
from super_harness.adapters.install import install_agent_integration
```

Do not otherwise restructure `init.py` in this task.

- [ ] **Step 5: Run parity and import-isolation tests**

Run:

```bash
pytest -q tests/unit/adapters/test_install.py tests/unit/cli/test_entrypoint.py tests/integration/cli/test_adapter.py tests/integration/cli/test_adapter_install.py tests/integration/cli/test_init.py
```

Expected: all pass; resolving the lazy `init` command imports none of the forbidden modules.

- [ ] **Step 6: Check decisions and commit**

Run:

```bash
super-harness decision check --changed
git add src/super_harness/adapters/install.py src/super_harness/cli/adapter.py src/super_harness/cli/init.py tests/unit/adapters/test_install.py tests/unit/cli/test_entrypoint.py tests/integration/cli/test_adapter.py tests/integration/cli/test_adapter_install.py
git commit -m "refactor(init): isolate adapter installation from POSIX lifecycle"
```

## Task 3: Build the immutable preflight and plan model

**Files:**

- Create: `src/super_harness/cli/init_plan.py`
- Create: `tests/unit/cli/test_init_plan.py`

- [ ] **Step 1: Write the mode and review-precedence matrix as tests**

Define table-driven cases for:

- non-TTY fresh init with explicit flags;
- non-TTY `--force` with existing review files and no review flags preserving raw bytes without parsing;
- non-TTY reconfiguration rejecting producer-only, model-only, mismatched, and incomplete pairs;
- non-TTY complete explicit reconfiguration ignoring persisted values;
- interactive force edit using valid persisted values as editable defaults;
- interactive malformed/unsupported persisted review config requiring explicit reset;
- unavailable coding integration accepted but not preselected;
- unavailable review producer disabled interactively and rejected when explicit;
- GitHub file decisions appearing in planned actions before apply.

Use explicit factories rather than Click contexts:

```python
request = InitRequest(
    workspace=tmp_path,
    interactive=False,
    force=True,
    integrations=(),
    review_producers=(),
    review_models=(),
    setup_github=False,
    assume_yes=False,
    quiet=False,
)
preflight = inspect_workspace(request, executable_lookup=fake_which)
plan = build_init_plan(request, preflight, choices=None)
assert plan.review_write is ReviewWrite.PRESERVE
assert plan.file_actions[".harness/review-governance.yaml"] is FileAction.PRESERVE
```

- [ ] **Step 2: Run the unit tests and confirm RED**

Run:

```bash
pytest -q tests/unit/cli/test_init_plan.py
```

Expected: import error for `super_harness.cli.init_plan`.

- [ ] **Step 3: Implement frozen domain types**

Create frozen dataclasses/enums with no Questionary, Rich, or Click imports:

```python
class InteractionMode(Enum):
    NON_INTERACTIVE = "non_interactive"
    LINE = "line"
    GUIDED = "guided"


class ReviewWrite(Enum):
    PRESERVE = "preserve"
    UPDATE = "update"
    RESET = "reset"


class FileAction(Enum):
    CREATE = "create"
    UPDATE = "update"
    PRESERVE = "preserve"
    SKIP = "skip"


@dataclass(frozen=True)
class InitRequest:
    workspace: Path
    interaction_mode: InteractionMode
    force: bool
    framework: str | None
    integrations: tuple[str, ...]
    review_producers: tuple[str, ...]
    review_models: tuple[tuple[str, str], ...]
    review_flags_explicit: bool
    no_agent: bool
    setup_github: bool
    assume_yes: bool
    quiet: bool
    json_output: bool


@dataclass(frozen=True)
class InitPreflight:
    workspace: Path
    harness_state: HarnessState
    executable_availability: Mapping[str, bool]
    review_governance_bytes: bytes | None
    review_profile_bytes: bytes | None
    persisted_review_models: tuple[tuple[str, str], ...]
    review_config_error: str | None
    existing_files: Mapping[str, ExistingFileState]


@dataclass(frozen=True)
class InitChoices:
    integrations: tuple[str, ...]
    review_models: tuple[tuple[str, str], ...]
    setup_github: bool
    github_file_decisions: Mapping[str, GithubFileDecision]
    review_write: ReviewWrite


@dataclass(frozen=True)
class InitPlan:
    workspace: Path
    force: bool
    quiet: bool
    integrations: tuple[str, ...]
    review_models: tuple[tuple[str, str], ...]
    review_write: ReviewWrite
    setup_github: bool
    github_file_decisions: Mapping[str, GithubFileDecision]
    file_actions: tuple[PlannedFileAction, ...]
```

Define `HarnessState`, `ExistingFileState`, `GithubFileDecision`, and `PlannedFileAction` as closed enums or frozen value objects in the same module. Keep the listed fields synchronized with the existing Click surface if implementation discovery finds an already-supported input that must cross the planning boundary.

- [ ] **Step 4: Implement read-only inspection and pure plan construction**

`inspect_workspace` may read paths, raw bytes, executable availability, and existing marker state but must not create directories or normalize files in place. `build_init_plan` must return a new immutable plan or a typed validation error; it must not prompt or write.

Keep non-interactive opaque preservation on a separate path that never calls `yaml.safe_load`. Parse existing review config only for interactive edit/reset decisions.

- [ ] **Step 5: Run tests, type checking, and commit**

Run:

```bash
pytest -q tests/unit/cli/test_init_plan.py
mypy src/super_harness/cli/init_plan.py
ruff check src/super_harness/cli/init_plan.py tests/unit/cli/test_init_plan.py
super-harness decision check --changed
git add src/super_harness/cli/init_plan.py tests/unit/cli/test_init_plan.py
git commit -m "feat(init): add immutable preflight and plan model"
```

## Task 4: Add capability selection and plain interaction backends

**Files:**

- Create: `src/super_harness/cli/init_ui.py`
- Create: `tests/unit/cli/test_init_ui.py`

- [ ] **Step 1: Write the capability matrix tests**

Cover stdin TTY, stdout TTY, `TERM=dumb`, redirected output, `NO_COLOR`, unsafe encodings, and narrow widths independently:

```python
@pytest.mark.parametrize(
    ("stdin_tty", "stdout_tty", "term", "expected"),
    [
        (False, True, "xterm-256color", InteractionMode.NON_INTERACTIVE),
        (True, True, "xterm-256color", InteractionMode.GUIDED),
        (True, False, "xterm-256color", InteractionMode.LINE),
        (True, True, "dumb", InteractionMode.LINE),
    ],
)
def test_choose_interaction_mode(stdin_tty, stdout_tty, term, expected):
    capabilities = detect_terminal_capabilities(
        stdin_tty=stdin_tty,
        stdout_tty=stdout_tty,
        term=term,
        no_color=False,
        encoding="utf-8",
        width=80,
    )
    assert capabilities.mode is expected
```

Add tests proving line mode asks one yes/no question per integration/producer, never accepts or renders a comma parser, validates models inline, supports back/cancel, and emits no ANSI cursor sequences.

- [ ] **Step 2: Run the focused tests and confirm RED**

Run:

```bash
pytest -q tests/unit/cli/test_init_ui.py -k 'capability or line or non_interactive'
```

Expected: missing UI module/classes.

- [ ] **Step 3: Implement capability and glyph selection**

Return an explicit value object instead of reading global terminal state throughout the UI:

```python
@dataclass(frozen=True)
class TerminalCapabilities:
    mode: InteractionMode
    color: bool
    unicode: bool
    width: int
```

`NO_COLOR` changes `color` only. Encoding safety changes `unicode` only. A TTY stdin with redirected/limited output uses line input and plain rendering.

- [ ] **Step 4: Implement `LineInitUI` and `NonInteractiveInitUI`**

Inject text input/output callables. Line selection must iterate stable options and ask `Select Codex integration? [Y/n]`, never ask for a combined numeric string. `NonInteractiveInitUI.collect` derives no new choices and never calls input. Both render the same immutable plan and step events as plain, deterministic lines.

- [ ] **Step 5: Run tests, static checks, and commit**

Run:

```bash
pytest -q tests/unit/cli/test_init_ui.py -k 'capability or line or non_interactive'
mypy src/super_harness/cli/init_ui.py
ruff check src/super_harness/cli/init_ui.py tests/unit/cli/test_init_ui.py
super-harness decision check --changed
git add src/super_harness/cli/init_ui.py tests/unit/cli/test_init_ui.py
git commit -m "feat(init): add terminal capability and plain UI backends"
```

## Task 5: Implement the Questionary/Rich guided rail

**Files:**

- Modify: `pyproject.toml`
- Modify: `src/super_harness/cli/init_ui.py`
- Modify: `tests/unit/cli/test_init_ui.py`

- [ ] **Step 1: Declare bounded runtime dependencies and install the editable package**

Add:

```toml
"questionary>=2.1,<3",
"rich>=14,<15",
```

Run:

```bash
pip install -e ".[dev]"
```

Expected: clean dependency resolution on Python 3.10+.

- [ ] **Step 2: Write failing guided-flow tests with injected prompt answers**

Test the complete state machine:

- detected choices are preselected and labeled;
- checkbox selection uses a filled indicator plus green foreground when color
  is available, while unselected options use an empty indicator plus normal
  foreground and neither state inherits prompt_toolkit's reverse-video
  selected-row background;
- `NO_COLOR` and other color-disabled capability paths retain the filled/empty
  indicator distinction without emitting selection color;
- unavailable producers are disabled;
- unavailable integrations remain selectable;
- a model prompt stays active until non-empty;
- review returns `BACK`, `CONFIRM`, or `CANCEL`;
- `--yes` bypasses only review confirmation;
- Questionary `None` becomes explicit cancel and `KeyboardInterrupt` remains interruption;
- unsafe encoding uses `+`, `|`, `*`, and `x` ASCII glyphs;
- narrow output omits secondary hints before wrapping primary values;
- no Rich live display is active during a Questionary call.

- [ ] **Step 3: Run guided tests and confirm RED**

Run:

```bash
pytest -q tests/unit/cli/test_init_ui.py -k guided
```

Expected: guided backend is missing or does not satisfy the five-stage transitions.

- [ ] **Step 4: Implement `InteractiveInitUI`**

Use Questionary only through an injected prompt adapter and Rich only through an injected console. Render completed rail rows before and after each prompt; use `Console.status` only during executor operations, never while Questionary owns stdin. Pass terminal color capability into the prompt adapter. Its explicit checkbox style must neutralize prompt_toolkit's inherited `selected: reverse` rule and apply a portable ANSI green foreground to selected options only when color is enabled. The built-in filled/empty indicator remains the color-independent selection cue, and the pointer remains the independent focus cue.

The configuration loop must be explicit:

```python
while True:
    choices = self.collect_configuration(request, preflight, defaults=choices)
    plan = build_init_plan(request, preflight, choices)
    decision = ReviewDecision.CONFIRM if request.assume_yes else self.review(plan)
    if decision is ReviewDecision.BACK:
        continue
    if decision is ReviewDecision.CANCEL:
        return WizardResult.cancelled()
    return WizardResult.confirmed(plan)
```

Render the approved `preflight -> configuration -> review -> apply -> outcome` rail, color-independent labels, path wrapping, and a single next/recovery command.

- [ ] **Step 5: Run all UI tests and static checks**

Run:

```bash
pytest -q tests/unit/cli/test_init_ui.py
mypy src/super_harness/cli/init_ui.py
ruff check src/super_harness/cli/init_ui.py tests/unit/cli/test_init_ui.py
```

Expected: all pass.

- [ ] **Step 6: Check decisions and commit**

Run:

```bash
super-harness decision check --changed
git add pyproject.toml src/super_harness/cli/init_ui.py tests/unit/cli/test_init_ui.py
git commit -m "feat(init): add Questionary and Rich guided wizard"
```

## Task 6: Resolve GitHub choices before apply

**Files:**

- Create: `src/super_harness/cli/init_github.py`
- Modify: `src/super_harness/cli/init_plan.py`
- Modify: `src/super_harness/cli/init.py`
- Modify: `tests/unit/cli/test_init_plan.py`
- Modify: `tests/integration/cli/test_init_setup_github.py`

- [ ] **Step 1: Add failing tests for prompt-free GitHub planning**

Cover fresh files, identical files, one valid metadata block, duplicate blocks, non-UTF-8 content, TTY append/overwrite/keep decisions, non-TTY skip without `--quiet`, `--quiet` append/overwrite, explicit cancel, and Ctrl+C.

Add a hard executor-boundary assertion:

```python
def test_apply_github_plan_never_calls_input(monkeypatch, github_plan):
    monkeypatch.setattr("builtins.input", lambda *_: pytest.fail("apply prompted"))
    apply_github_plan(github_plan)
```

- [ ] **Step 2: Run tests and confirm RED**

Run:

```bash
pytest -q tests/unit/cli/test_init_plan.py tests/integration/cli/test_init_setup_github.py
```

Expected: GitHub conflict prompts still occur during apply or the new module is missing.

- [ ] **Step 3: Extract read-only inspection and prompt-free application**

Represent each file decision in the plan:

```python
class GithubFileDecision(Enum):
    CREATE = "create"
    KEEP = "keep"
    APPEND = "append"
    OVERWRITE = "overwrite"
```

`inspect_github_files` reads and validates existing content. The UI resolves any interactive ambiguity before review. `apply_github_plan` consumes only resolved decisions and retains current `gh` exit code 4, settings advisories, log naming, bundled-template bytes, and duplicate-block validation.

- [ ] **Step 4: Remove all apply-time prompts from `init.py`**

The old helpers may remain temporarily as thin delegates, but no function reachable after final confirmation may call `click.confirm`, `input`, or Questionary.

- [ ] **Step 5: Run GitHub compatibility tests and commit**

Run:

```bash
pytest -q tests/integration/cli/test_init_setup_github.py tests/unit/cli/test_init_plan.py
ruff check src/super_harness/cli/init_github.py src/super_harness/cli/init_plan.py tests/integration/cli/test_init_setup_github.py
super-harness decision check --changed
git add src/super_harness/cli/init_github.py src/super_harness/cli/init_plan.py src/super_harness/cli/init.py tests/unit/cli/test_init_plan.py tests/integration/cli/test_init_setup_github.py
git commit -m "refactor(init): resolve GitHub conflicts before apply"
```

## Task 7: Add the prompt-free executor and step ledger

**Files:**

- Create: `src/super_harness/cli/init_executor.py`
- Create: `tests/unit/cli/test_init_executor.py`

- [ ] **Step 1: Write failing orchestration tests**

Use injected operations to assert the stable sequence:

```python
EXPECTED_STEPS = [
    "scaffold",
    "skeleton_config",
    "review_config",
    "agent_integrations",
    "agents_md",
    "gitignore",
    "github",
]
```

Test success, warning, typed domain failure, unexpected failure, and `KeyboardInterrupt` during the fourth step. Assert completed events remain, the failed/interrupted step is named once, later steps do not run, and no rollback callback is invoked.

- [ ] **Step 2: Run tests and confirm RED**

Run:

```bash
pytest -q tests/unit/cli/test_init_executor.py
```

Expected: missing executor module.

- [ ] **Step 3: Implement closed step events and execution result**

Use frozen events with stable identifiers:

```python
class StepState(Enum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    WARNED = "warned"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


@dataclass(frozen=True)
class InitStepEvent:
    step_id: str
    state: StepState
    detail: str
```

The executor accepts an event sink and operation bundle, performs no prompt or rendering call, retains existing domain exit codes/`format_error` details, and returns the completed ledger with the final result.

- [ ] **Step 4: Run unit tests, static checks, and commit**

Run:

```bash
pytest -q tests/unit/cli/test_init_executor.py
mypy src/super_harness/cli/init_executor.py
ruff check src/super_harness/cli/init_executor.py tests/unit/cli/test_init_executor.py
super-harness decision check --changed
git add src/super_harness/cli/init_executor.py tests/unit/cli/test_init_executor.py
git commit -m "feat(init): add prompt-free executor and step ledger"
```

## Task 8: Rewire the public `init` command and preserve compatibility

**Files:**

- Modify: `src/super_harness/cli/init.py`
- Modify: `src/super_harness/cli/init_plan.py`
- Modify: `src/super_harness/cli/init_ui.py`
- Modify: `src/super_harness/cli/init_executor.py`
- Modify: `tests/integration/cli/test_init.py`
- Modify: `tests/integration/cli/test_init_setup_github.py`

- [ ] **Step 1: Add failing public-command tests**

Add `--yes` help coverage and integration scenarios for:

- guided confirm writes only after review;
- guided back edits and then applies the revised plan;
- explicit cancel exits 0 with `Setup cancelled` and changes no scoped files;
- pre-apply Ctrl+C exits 1 and changes no scoped files;
- during-apply Ctrl+C exits 1 and prints the completed/interrupted ledger;
- non-TTY with and without `--yes` applies immediately;
- partial explicit flags prompt only for unresolved interactive values;
- TTY stdin plus redirected stdout and `TERM=dumb` use line yes/no prompts;
- force review preservation/reconfiguration/reset matrix;
- old comma-entry input is absent from the interaction path;
- adapter, AGENTS.md, `.gitignore`, and GitHub failure exit codes remain unchanged.

Capture the pre-confirm file snapshot explicitly:

```python
def assert_init_owned_paths_absent(root: Path) -> None:
    assert not (root / ".harness").exists()
    assert not (root / "AGENTS.md").exists()
    assert not (root / ".gitignore").exists()
    assert not (root / ".github").exists()
```

- [ ] **Step 2: Run integration tests and confirm RED**

Run:

```bash
pytest -q tests/integration/cli/test_init.py tests/integration/cli/test_init_setup_github.py
```

Expected: `--yes` is unknown and the current command cannot satisfy confirmation/cancel/line-mode boundaries.

- [ ] **Step 3: Reduce `init_cmd` to orchestration**

Keep all existing options unchanged and add:

```python
@click.option(
    "--yes",
    "assume_yes",
    is_flag=True,
    help="Skip the final confirmation in interactive mode.",
)
```

The orchestration order is fixed:

```python
capabilities = detect_terminal_capabilities(stdin, stdout, environ)
request = request_from_click(
    workspace=workspace,
    interaction_mode=capabilities.mode,
    force=force,
    framework=framework,
    integrations=integrations,
    review_producers=review_producers,
    review_models=review_models,
    no_agent=no_agent,
    setup_github=setup_github,
    assume_yes=assume_yes,
    quiet=quiet,
    json_output=json_output,
)
preflight = inspect_workspace(request)
ui = create_init_ui(capabilities, quiet=request.quiet)
wizard_result = ui.prepare_plan(request, preflight)
if wizard_result.cancelled:
    ui.render_cancelled()
    return
operations = build_init_operations(workspace=request.workspace)
result = InitExecutor(operations=operations).apply(wizard_result.plan, ui.on_step)
ui.render_outcome(result)
```

Translate explicit cancel, Questionary sentinel, pre-apply interruption, and during-apply interruption only at their owning boundary. Preserve the current JSON caveat and quiet behavior.

- [ ] **Step 4: Delete superseded comma-prompt and mixed orchestration helpers**

Remove `_prompt_multi_select` and any prompt-bearing apply helper. Keep reusable low-level scaffold/write functions only where the executor calls them without UI knowledge. Update monkeypatch targets in integration tests to the module that now owns each operation.

- [ ] **Step 5: Run the complete init/adapter regression slice**

Run:

```bash
pytest -q tests/unit/cli/test_init_plan.py tests/unit/cli/test_init_ui.py tests/unit/cli/test_init_executor.py tests/integration/cli/test_init.py tests/integration/cli/test_init_setup_github.py tests/integration/cli/test_adapter.py tests/integration/cli/test_adapter_install.py
mypy src/super_harness/cli/init.py src/super_harness/cli/init_plan.py src/super_harness/cli/init_ui.py src/super_harness/cli/init_executor.py src/super_harness/cli/init_github.py
ruff check src/super_harness/cli/init.py src/super_harness/cli/init_plan.py src/super_harness/cli/init_ui.py src/super_harness/cli/init_executor.py src/super_harness/cli/init_github.py tests/unit/cli/test_init_plan.py tests/unit/cli/test_init_ui.py tests/unit/cli/test_init_executor.py tests/integration/cli/test_init.py tests/integration/cli/test_init_setup_github.py
```

Expected: all pass.

- [ ] **Step 6: Check decisions and commit**

Run:

```bash
super-harness decision check --changed
git add src/super_harness/cli/init.py src/super_harness/cli/init_plan.py src/super_harness/cli/init_ui.py src/super_harness/cli/init_executor.py tests/integration/cli/test_init.py tests/integration/cli/test_init_setup_github.py
git commit -m "feat(init): orchestrate the complete guided setup flow"
```

## Task 9: Prove native Windows installed-wheel reachability

**Files:**

- Create: `tests/integration/cli/test_init_windows_entrypoint.py`
- Modify: `.github/workflows/test.yml`

- [ ] **Step 1: Add cross-platform entrypoint contract tests**

Test that resolving/running `init` does not load the forbidden POSIX modules, paths containing spaces are preserved, Windows-style CRLF user files retain their non-marker content, and non-TTY initialization works through a subprocess.

Mark only genuinely Windows-specific assertions with `skipif(sys.platform != "win32")`; the forbidden-import and path-with-spaces tests must run on every OS.

- [ ] **Step 2: Run the tests locally**

Run:

```bash
pytest -q tests/integration/cli/test_init_windows_entrypoint.py
```

Expected: all portable cases pass; Windows-only cases skip outside Windows.

- [ ] **Step 3: Add a focused Windows wheel job**

Append a separate `windows-init` job without widening the full suite:

```yaml
windows-init:
  runs-on: windows-latest
  steps:
    - uses: actions/checkout@v4
    - uses: actions/setup-python@v5
      with:
        python-version: "3.12"
    - name: Build wheel
      run: |
        python -m pip install build
        python -m build --wheel
    - name: Install wheel and test dependencies
      shell: pwsh
      run: |
        $wheel = Get-ChildItem dist\*.whl | Select-Object -First 1
        python -m pip install $wheel.FullName pytest
    - name: Exercise installed init entrypoint
      shell: pwsh
      run: |
        super-harness init --help
        $workspace = Join-Path $env:RUNNER_TEMP "super harness init smoke"
        New-Item -ItemType Directory -Force -Path $workspace | Out-Null
        super-harness --workspace $workspace init --no-agent
        if (-not (Test-Path (Join-Path $workspace ".harness"))) { exit 1 }
    - name: Run focused init tests
      run: pytest -q tests/integration/cli/test_init_windows_entrypoint.py tests/unit/cli/test_init_plan.py tests/unit/cli/test_init_ui.py tests/unit/cli/test_init_executor.py
```

Because the wheel excludes tests, keep the checkout as the test working directory while importing the installed wheel; verify with `python -c "import super_harness; print(super_harness.__file__)"` that the import resolves from site-packages, not `src/`.

- [ ] **Step 4: Lint workflow-sensitive code and commit**

Run:

```bash
ruff check tests/integration/cli/test_init_windows_entrypoint.py
super-harness decision check --changed
git add .github/workflows/test.yml tests/integration/cli/test_init_windows_entrypoint.py
git commit -m "ci(init): verify the installed wizard on Windows"
```

## Task 10: Update generated and onboarding documentation

**Files:**

- Modify: `scripts/gen_cli_reference.py`
- Modify: `tests/unit/scripts/test_gen_cli_reference.py`
- Modify: `docs/cli-reference.md`
- Modify: `docs/getting-started.md`

- [ ] **Step 1: Add documentation assertions**

Assert the generated `init` section includes `--yes` with the exact interactive-only meaning and that generation still traverses all lazy commands. Add a getting-started check only if an existing docs test harness supports it; do not add a new parser solely for prose.

- [ ] **Step 2: Update getting-started guidance**

Document:

- the five stages and review-before-write guarantee;
- keys: arrows move, Space toggles, Enter accepts, Back returns, Ctrl+C interrupts;
- `--yes` skips only the interactive final confirmation;
- non-TTY scripts apply immediately and do not need `--yes`;
- limited terminals receive one yes/no prompt per option;
- native Windows support in this change is scoped to `init`, not every lifecycle command.

Include a narrow, copyable terminal block showing the approved guided rail; do not commit the brainstorming HTML or `.superpowers/` artifacts.

- [ ] **Step 3: Regenerate the CLI reference**

Run:

```bash
super-harness doc check --fix
super-harness doc check
pytest -q tests/unit/scripts/test_gen_cli_reference.py
```

Expected: generated reference is in sync and includes `--yes`.

- [ ] **Step 4: Check docs and commit**

Run:

```bash
super-harness decision check --changed
git add scripts/gen_cli_reference.py tests/unit/scripts/test_gen_cli_reference.py docs/cli-reference.md docs/getting-started.md
git commit -m "docs(init): document the guided cross-platform workflow"
```

## Task 11: Verify the complete change and advance the lifecycle

**Files:**

- Modify only if verification exposes an in-scope defect: files already listed in this plan's `scope.files`.

- [ ] **Step 1: Run formatting and static analysis**

Run:

```bash
ruff format --check src/super_harness tests scripts
ruff check src/super_harness tests scripts
mypy src/super_harness
```

Expected: all exit 0.

- [ ] **Step 2: Run the full test suite**

Run:

```bash
pytest -v -m "not e2e"
```

Expected: all non-E2E tests pass.

- [ ] **Step 3: Run documentation, decision, and harness gates**

Run:

```bash
super-harness doc check
super-harness decision check --changed
super-harness verify
```

Expected: all exit 0. Do not dismiss the known old-event warnings as test failures; only new non-zero results block completion.

- [ ] **Step 4: Perform manual terminal smoke checks**

In a disposable workspace on macOS/Linux, run the guided TTY flow through selection, Back, confirmation, and cancel. Verify ASCII fallback with a safe limited-terminal invocation and non-TTY behavior with redirected stdin. When native Windows Terminal/PowerShell is available, run the installed wheel and manually exercise arrow, Space, Enter, Back, cancel, ASCII fallback, and a workspace path containing spaces.

If performed, record the native Windows manual evidence in the change handoff, including Windows version, terminal/shell, installed wheel version or commit, tested path, and observed key/fallback outcomes. This manual smoke is recommended but non-blocking and may be completed after authoring. Its absence must be disclosed in the handoff; it does not block `super-harness done`. The focused Windows CI job remains a mandatory merge gate and cannot be replaced by mocked prompts or a non-Windows terminal.

- [ ] **Step 5: Inspect the exact diff and commits**

Run:

```bash
git status --short
git diff --check
git log --oneline --decorate -12
git diff main...HEAD --stat
git diff main...HEAD
```

Expected: only declared-scope files are changed, `.codegraph/` and `.superpowers/` remain untracked and unstaged, commit messages are English, and there are no unfinished markers or unrelated edits.

- [ ] **Step 6: Commit verification-only fixes separately, if any**

If an in-scope verification fix was required, rerun its failing command plus Steps 1–3, then commit with an English message describing the actual fix. If no files changed, do not create an empty commit.

- [ ] **Step 7: Complete authoring lifecycle after local automated and terminal evidence is green**

Run:

```bash
super-harness done init-interactive-wizard
```

Precondition: local automated checks and available manual terminal checks from Step 4 pass, with any missing native Windows manual smoke disclosed in the handoff. Expected: verification passes and the lifecycle records implementation completion. The `windows-init` CI job must pass before merge. Do not push, open a PR, start external review, override a gate, or merge without separate user approval.

## Plan self-review checklist

- [ ] Every acceptance criterion in the approved design maps to at least one task and test above.
- [ ] Windows acceptance uses the installed console script, not only `CliRunner` or mocked UI.
- [ ] Native Windows arrow/Space/Enter/Back/cancel/ASCII manual evidence is recommended but non-blocking; the installed-wheel Windows CI job is mandatory before merge.
- [ ] Non-interactive force preservation never parses or rewrites existing review bytes.
- [ ] Interactive GitHub conflicts are resolved before final review; the executor has no prompt path.
- [ ] `--yes` is interactive-confirmation-only and never supplies selections, models, or conflict decisions.
- [ ] Explicit cancel and both interruption boundaries have asserted exit codes and write boundaries.
- [ ] Rich and Questionary never own live terminal rendering simultaneously.
- [ ] Checkbox selection uses filled-plus-green for selected options and empty-plus-normal foreground for unselected options when color is enabled, retains the indicator distinction when color is disabled, and never uses a reverse-video row background.
- [ ] No package-wide Windows classifier or claim is added.
- [ ] All code, tests, docs, plan text, and commit messages are English.
- [ ] `.codegraph/`, `.superpowers/`, and unrelated user files remain unstaged.
