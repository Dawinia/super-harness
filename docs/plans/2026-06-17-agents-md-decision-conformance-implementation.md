# AGENTS.md Decision-Conformance + sync Drift Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the §4.1 "agent self-check (portable)" sensor into the generated AGENTS.md by adding a `### Decision conformance` section to the outer framework template, and add a shippable `sync --check` drift gate so a stale generated AGENTS.md (or `.gitignore` block) fails CI.

**Architecture:** Half 1 adds static guidance to the outer-section template (`agents_md_render.py`) — reaches every super-harness user via `init`/`sync`, agent-agnostic. Half 2 adds `super-harness sync --check`: a dry-run that renders the would-be content into a throwaway temp copy (reusing the exact init/sync render path — no second template), diffs it against the on-disk file, and exits non-zero on drift. CI runs `sync --check` as a gate.

**Tech Stack:** Python 3.10+, Click CLI, pytest. Verify with the project venv: prefix test/CLI commands with `PATH="$(pwd)/.venv/bin:$PATH"`.

---

## File Structure

- **Modify** `src/super_harness/engineering/agents_md_render.py` — add `### Decision conformance` to `_AGENTS_MD_SECTION_TEMPLATE` (Task 1).
- **Regenerate** `AGENTS.md` (tracked) via `sync --agents-md` (Task 1).
- **Create** `src/super_harness/core/sync_check.py` — `run_sync_check` temp-copy render + diff (Tasks 2-3).
- **Create** `tests/unit/core/test_sync_check.py` — core drift tests (Tasks 2-3).
- **Modify** `src/super_harness/cli/sync.py` — add `--check` flag + dispatch (Task 4).
- **Modify** `tests/unit/cli/test_sync.py` — CLI `--check` tests (Task 4).
- **Modify** `tests/unit/engineering/test_agents_md_render.py` — Half 1 render test (Task 1).
- **Modify** `.github/workflows/doc-check.yml` — add a `sync --check` step (Task 5).
- **Modify** `private/OPEN-ITEMS.md` — mark the NEW(2026-06-17) item done (Task 5).

---

## Task 1: Add `### Decision conformance` to the outer template

**Files:**
- Test: `tests/unit/engineering/test_agents_md_render.py`
- Modify: `src/super_harness/engineering/agents_md_render.py` (`_AGENTS_MD_SECTION_TEMPLATE`)
- Regenerate: `AGENTS.md`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/engineering/test_agents_md_render.py`:

```python
def test_outer_section_has_decision_conformance(tmp_path: Path) -> None:
    """The outer framework section teaches the portable §4.1 local sensor: run
    `decision check` at checkpoints, self-test a check with `ratify --dry-run`,
    keep `decision check` / `doc check` green locally. Lives in the OUTER section
    (not a CC-specific adapter block) because these are plain CLI commands any
    agent runs."""
    agents = tmp_path / "AGENTS.md"

    render_super_harness_section(tmp_path, agents, "0.1.0")

    text = agents.read_text()
    assert "### Decision conformance" in text
    assert "super-harness decision check --changed" in text
    assert "super-harness decision ratify <id> --dry-run" in text
    assert "super-harness doc check" in text
    # The section sits inside the managed outer block (before the end marker).
    assert text.index("### Decision conformance") < text.index(
        "<!-- super-harness section end -->"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/engineering/test_agents_md_render.py::test_outer_section_has_decision_conformance -v`
Expected: FAIL — `assert "### Decision conformance" in text` (section not yet in template).

- [ ] **Step 3: Add the section to the template**

In `src/super_harness/engineering/agents_md_render.py`, inside `_AGENTS_MD_SECTION_TEMPLATE`, insert the new section between the `### File scope` block and the `<!-- super-harness section end -->` marker. Find:

```python
### File scope

When implementing a change, edit only files in the declared `scope.files`
(see the plan artifact). Edits outside scope trigger drift warnings.

<!-- super-harness section end -->"""
```

Replace with:

```python
### File scope

When implementing a change, edit only files in the declared `scope.files`
(see the plan artifact). Edits outside scope trigger drift warnings.

### Decision conformance

Ratified decisions under `docs/decisions/` are binding: super-harness
hash-locks each decision's text and, where configured, attaches an executable
check. Treat `super-harness decision check` as a LOCAL SENSOR you consult while
you work — CI runs it too as the un-bypassable floor, so keep it green locally.

- **At natural checkpoints** (a chunk done, before you commit) run
  `super-harness decision check --changed`. A non-zero exit means you violated a
  ratified decision or edited a ratified decision's body text — fix it before
  continuing; don't push the drift downstream to CI.
- **Don't hand-edit the body of a ratified decision.** Its text is hash-locked;
  re-ratifying (`super-harness decision ratify <id>`) is the only unlock, and is
  a deliberate, recorded act.
- **Attaching an executable check to a decision?** Before you propose it, run
  `super-harness decision ratify <id> --dry-run` to confirm the check actually
  bites (runs the bite-test without ratifying).
- `super-harness decision check` (full) and `super-harness doc check` are also
  CI gates — keep both green locally so a push never bounces.

<!-- super-harness section end -->"""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/engineering/test_agents_md_render.py -v`
Expected: PASS (all tests in the file, including the new one).

- [ ] **Step 5: Regenerate the tracked AGENTS.md**

Run: `PATH="$(pwd)/.venv/bin:$PATH" super-harness sync --agents-md -y`
Expected: `Synced AGENTS.md super-harness section (v0.1.0).` Then confirm the section landed:

Run: `grep -n "### Decision conformance" AGENTS.md`
Expected: one match inside the managed block.

- [ ] **Step 6: Commit**

```bash
git add tests/unit/engineering/test_agents_md_render.py \
        src/super_harness/engineering/agents_md_render.py AGENTS.md
git commit -m "feat(agents-md): add Decision conformance section to outer template"
```

---

## Task 2: Core `run_sync_check` — AGENTS.md drift via temp-copy render

**Files:**
- Create: `src/super_harness/core/sync_check.py`
- Test: `tests/unit/core/test_sync_check.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/core/test_sync_check.py`:

```python
"""Unit tests for `run_sync_check` — the `sync --check` drift engine.

It renders what `sync` WOULD write into a throwaway temp copy (reusing the exact
init/sync render path) and diffs against the on-disk file, reporting drift without
writing anything.
"""

from __future__ import annotations

from pathlib import Path

from super_harness.core.sync_check import run_sync_check
from super_harness.engineering.agents_md_render import render_super_harness_section


def test_freshly_rendered_agents_md_is_in_sync(tmp_path: Path) -> None:
    """A repo whose AGENTS.md was just rendered shows NO AGENTS.md drift."""
    agents = tmp_path / "AGENTS.md"
    render_super_harness_section(tmp_path, agents, "0.1.0")

    result = run_sync_check(
        tmp_path, "0.1.0", check_agents=True, check_gitignore=False
    )

    assert result.in_sync
    assert result.drift == []


def test_hand_mutated_agents_md_section_is_drift(tmp_path: Path) -> None:
    """Editing inside the managed section (the DO NOT EDIT block) is reported as
    drift, with a diff, and the file on disk is NOT modified by the check."""
    agents = tmp_path / "AGENTS.md"
    render_super_harness_section(tmp_path, agents, "0.1.0")
    original = agents.read_text()
    agents.write_text(original.replace("### Branch naming", "### Branch naming EDITED"))
    mutated = agents.read_text()

    result = run_sync_check(
        tmp_path, "0.1.0", check_agents=True, check_gitignore=False
    )

    assert not result.in_sync
    assert len(result.drift) == 1
    assert result.drift[0].name == "AGENTS.md"
    assert "Branch naming" in result.drift[0].diff
    # --check never writes.
    assert agents.read_text() == mutated


def test_stale_version_stamp_is_drift(tmp_path: Path) -> None:
    """If AGENTS.md was rendered at an OLD version, checking at a NEW version
    reports drift (the begin-marker version stamp differs)."""
    agents = tmp_path / "AGENTS.md"
    render_super_harness_section(tmp_path, agents, "0.0.9")

    result = run_sync_check(
        tmp_path, "0.1.0", check_agents=True, check_gitignore=False
    )

    assert not result.in_sync
    assert result.drift[0].name == "AGENTS.md"


def test_content_outside_markers_is_not_drift(tmp_path: Path) -> None:
    """The managed-only guarantee (§7): user content OUTSIDE the super-harness
    markers is never inspected, so adding it does not register as drift."""
    agents = tmp_path / "AGENTS.md"
    render_super_harness_section(tmp_path, agents, "0.1.0")
    agents.write_text("# My project notes\n\n" + agents.read_text() + "\nFooter.\n")

    result = run_sync_check(
        tmp_path, "0.1.0", check_agents=True, check_gitignore=False
    )

    assert result.in_sync


def test_absent_agents_md_is_drift(tmp_path: Path) -> None:
    """A repo with NO AGENTS.md is drifted from what `sync` would write (it would
    create the section), so --check reports drift rather than silently passing."""
    result = run_sync_check(
        tmp_path, "0.1.0", check_agents=True, check_gitignore=False
    )

    assert not result.in_sync
    assert result.drift[0].name == "AGENTS.md"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/core/test_sync_check.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'super_harness.core.sync_check'`.

- [ ] **Step 3: Write the minimal implementation**

Create `src/super_harness/core/sync_check.py`:

```python
"""Drift check for the managed `sync` artifacts (AGENTS.md section + .gitignore).

`super-harness sync --check` is a dry-run: it renders what `sync` WOULD write into
a throwaway temp copy of each managed artifact — reusing the EXACT init/sync render
path, so there is no second template — diffs that canonical result against the
on-disk file, and reports drift WITHOUT writing. Exit semantics live in the CLI.

API stability: **experimental** (v0.1).
"""
from __future__ import annotations

import difflib
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from super_harness.engineering.agents_md_render import render_super_harness_section
from super_harness.engineering.gitignore_injector import inject_gitignore_block

# Bound a runaway diff (mirrors core/doc_check's _DIFF_MAX_LINES).
_DIFF_MAX_LINES = 40


@dataclass(frozen=True)
class ArtifactDrift:
    name: str   # "AGENTS.md" | ".gitignore"
    diff: str   # bounded unified diff (on-disk vs would-be-regenerated)


@dataclass
class SyncCheckResult:
    drift: list[ArtifactDrift] = field(default_factory=list)

    @property
    def in_sync(self) -> bool:
        return not self.drift


def _normalize(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _read(path: Path) -> str:
    """Normalized text of ``path``; "" if absent / unreadable / non-UTF-8."""
    try:
        return _normalize(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return ""


def _bounded_diff(on_disk: str, canonical: str, name: str) -> str:
    diff = "".join(
        difflib.unified_diff(
            on_disk.splitlines(keepends=True),
            canonical.splitlines(keepends=True),
            fromfile=name,
            tofile=f"{name} (would be regenerated by `sync`)",
        )
    )
    lines = diff.splitlines(keepends=True)
    if len(lines) <= _DIFF_MAX_LINES:
        return diff
    extra = len(lines) - _DIFF_MAX_LINES
    return "".join(lines[:_DIFF_MAX_LINES]) + f"... ({extra} more lines)\n"


def _check_agents_md(root: Path, version: str, tmp: Path) -> ArtifactDrift | None:
    """Render the canonical AGENTS.md into a temp copy and diff vs on-disk."""
    agents = root / "AGENTS.md"
    tmp_agents = tmp / "AGENTS.md"
    if agents.exists():
        shutil.copyfile(agents, tmp_agents)
    # Reads adapters from the REAL root; writes the canonical section into the copy.
    render_super_harness_section(root, tmp_agents, version)
    canonical = _read(tmp_agents)
    on_disk = _read(agents)
    if canonical == on_disk:
        return None
    return ArtifactDrift(name="AGENTS.md", diff=_bounded_diff(on_disk, canonical, "AGENTS.md"))


def run_sync_check(
    root: Path,
    version: str,
    *,
    check_agents: bool,
    check_gitignore: bool,
) -> SyncCheckResult:
    """Render in-scope managed artifacts into a temp dir and report drift.

    ``check_agents`` / ``check_gitignore`` select which managed artifacts to
    inspect (the CLI maps the ``--agents-md`` / ``--gitignore`` scope flags). No
    file under ``root`` is ever written.
    """
    result = SyncCheckResult()
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if check_agents:
            drift = _check_agents_md(root, version, tmp)
            if drift is not None:
                result.drift.append(drift)
        # .gitignore leg added in Task 3.
    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/core/test_sync_check.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/super_harness/core/sync_check.py tests/unit/core/test_sync_check.py
git commit -m "feat(sync): core run_sync_check drift engine (AGENTS.md leg)"
```

---

## Task 3: Add the `.gitignore` leg to `run_sync_check`

**Files:**
- Modify: `src/super_harness/core/sync_check.py`
- Test: `tests/unit/core/test_sync_check.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/core/test_sync_check.py`:

```python
from super_harness.engineering.gitignore_injector import inject_gitignore_block


def test_freshly_injected_gitignore_is_in_sync(tmp_path: Path) -> None:
    """A .gitignore whose block was just injected shows NO .gitignore drift."""
    inject_gitignore_block(tmp_path / ".gitignore")

    result = run_sync_check(
        tmp_path, "0.1.0", check_agents=False, check_gitignore=True
    )

    assert result.in_sync


def test_mutated_gitignore_block_is_drift(tmp_path: Path) -> None:
    """Editing inside the managed .gitignore block is reported as drift; the file
    on disk is NOT modified by the check."""
    gi = tmp_path / ".gitignore"
    inject_gitignore_block(gi)
    original = gi.read_text()
    # Drop a real canonical path line from inside the managed block.
    # `.harness/state.yaml` IS in `_CANONICAL_PATHS` (verified); the socket path
    # is NOT, so do not use it here.
    mutated = "\n".join(
        line for line in original.splitlines() if line != ".harness/state.yaml"
    ) + "\n"
    assert mutated != original, "test bug: nothing removed — path not in block"
    gi.write_text(mutated)

    result = run_sync_check(
        tmp_path, "0.1.0", check_agents=False, check_gitignore=True
    )

    assert not result.in_sync
    assert result.drift[0].name == ".gitignore"
    assert gi.read_text() == mutated


def test_both_artifacts_checked_together(tmp_path: Path) -> None:
    """With both legs enabled and both freshly rendered, the repo is in sync."""
    render_super_harness_section(tmp_path, tmp_path / "AGENTS.md", "0.1.0")
    inject_gitignore_block(tmp_path / ".gitignore")

    result = run_sync_check(
        tmp_path, "0.1.0", check_agents=True, check_gitignore=True
    )

    assert result.in_sync
```

Note: the test deletes the literal `.harness/state.yaml`, which IS in
`_CANONICAL_PATHS`. The inline `assert mutated != original` guards against a future
rename of that constant. To re-confirm the tuple if needed:

Run: `PATH="$(pwd)/.venv/bin:$PATH" python -c "from super_harness.engineering.gitignore_injector import _CANONICAL_PATHS; print(_CANONICAL_PATHS)"`

- [ ] **Step 2: Run test to verify it fails**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/core/test_sync_check.py -k gitignore -v`
Expected: FAIL — the `.gitignore` legs return `in_sync` (no `.gitignore` checking yet), so `assert not result.in_sync` fails on the mutated test.

- [ ] **Step 3: Add the `.gitignore` leg**

In `src/super_harness/core/sync_check.py`, add a checker function above `run_sync_check`:

```python
def _check_gitignore(root: Path, tmp: Path) -> ArtifactDrift | None:
    """Inject the canonical block into a temp copy of .gitignore and diff."""
    gi = root / ".gitignore"
    tmp_gi = tmp / ".gitignore"
    if gi.exists():
        shutil.copyfile(gi, tmp_gi)
    inject_gitignore_block(tmp_gi)
    canonical = _read(tmp_gi)
    on_disk = _read(gi)
    if canonical == on_disk:
        return None
    return ArtifactDrift(name=".gitignore", diff=_bounded_diff(on_disk, canonical, ".gitignore"))
```

Then replace the `# .gitignore leg added in Task 3.` comment inside `run_sync_check` with:

```python
        if check_gitignore:
            drift = _check_gitignore(root, tmp)
            if drift is not None:
                result.drift.append(drift)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/core/test_sync_check.py -v`
Expected: PASS (all tests).

- [ ] **Step 5: Commit**

```bash
git add src/super_harness/core/sync_check.py tests/unit/core/test_sync_check.py
git commit -m "feat(sync): add .gitignore leg to run_sync_check"
```

---

## Task 4: Wire `--check` into the `sync` CLI

**Files:**
- Modify: `src/super_harness/cli/sync.py`
- Test: `tests/unit/cli/test_sync.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/cli/test_sync.py`:

```python
from super_harness.engineering.agents_md_render import render_super_harness_section
from super_harness.engineering.gitignore_injector import inject_gitignore_block
from super_harness.version import __version__


def _init_harness(root: Path) -> None:
    (root / ".harness").mkdir()


def test_sync_check_clean_repo_exits_ok(tmp_path: Path) -> None:
    """`sync --check` on a freshly-rendered repo → exit 0, no diff written."""
    _init_harness(tmp_path)
    render_super_harness_section(tmp_path, tmp_path / "AGENTS.md", __version__)
    inject_gitignore_block(tmp_path / ".gitignore")

    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "sync", "--check"])
    assert r.exit_code == 0, r.output


def test_sync_check_drifted_agents_md_exits_validation(tmp_path: Path) -> None:
    """A hand-mutated AGENTS.md managed section → exit 2 (EXIT_VALIDATION) + diff,
    file unchanged."""
    _init_harness(tmp_path)
    agents = tmp_path / "AGENTS.md"
    render_super_harness_section(tmp_path, agents, __version__)
    mutated = agents.read_text().replace("### File scope", "### File scope EDITED")
    agents.write_text(mutated)

    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "sync", "--check"])
    assert r.exit_code == 2, r.output
    assert "AGENTS.md" in r.stderr
    assert agents.read_text() == mutated  # never written


def test_sync_check_with_adapter_is_rejected(tmp_path: Path) -> None:
    """`--adapter` + `--check` is rejected (the --agents-md check already covers
    adapter subsections); exit is NOT 2 (that means drift), so use EXIT_GENERIC."""
    _init_harness(tmp_path)

    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "sync", "--check", "--adapter", "claude-code"],
    )
    assert r.exit_code == 1, r.output
    assert "does not support `--adapter`" in r.stderr
    assert "Traceback" not in r.stderr


def test_sync_check_agents_only_scope(tmp_path: Path) -> None:
    """`sync --agents-md --check` checks ONLY AGENTS.md: a drifted .gitignore does
    not fail the agents-only check."""
    _init_harness(tmp_path)
    render_super_harness_section(tmp_path, tmp_path / "AGENTS.md", __version__)
    # .gitignore intentionally absent → would drift if checked, but scope excludes it.

    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "sync", "--agents-md", "--check"]
    )
    assert r.exit_code == 0, r.output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/cli/test_sync.py -k check -v`
Expected: FAIL — `--check` is not a known option (`Error: No such option: --check`), so exit code is 2 from Click's usage error on the clean-repo test path, and the reject/scope assertions fail.

- [ ] **Step 3: Add the `--check` flag + dispatch**

In `src/super_harness/cli/sync.py`:

(a) Add the import near the other core imports (after the `gitignore_injector` import block):

```python
from super_harness.core.sync_check import run_sync_check
```

(b) Add `EXIT_VALIDATION` to the existing exit-code import:

```python
from super_harness.exit_codes import (
    EXIT_GENERIC,
    EXIT_NO_CONFIG,
    EXIT_OK,
    EXIT_VALIDATION,
)
```

(c) Add the `--check` option to `sync_cmd` (after the `--yes` option, before `@click.pass_context`):

```python
@click.option(
    "--check",
    "check",
    is_flag=True,
    help="Dry-run: report drift between the managed artifacts and the "
    "super-harness template WITHOUT writing. Exit 2 on drift. Composes with "
    "--agents-md / --gitignore; not supported with --adapter.",
)
```

(d) Add `check: bool` to the `sync_cmd` signature (after `assume_yes: bool`).

(e) At the TOP of `sync_cmd`'s body, right after `quiet = bool(ctx.obj.get("quiet"))`, insert the check dispatch (it exits, so it precedes the write dispatch):

```python
    if check:
        if adapter_name is not None:
            click.echo(
                format_error(
                    subcommand="sync",
                    message="`sync --check` does not support `--adapter`",
                    hint="Use `sync --agents-md --check` to verify the whole "
                    "AGENTS.md section (it already covers every adapter subsection).",
                ),
                err=True,
            )
            sys.exit(EXIT_GENERIC)
        # No scope flag → check both; a single scope flag narrows.
        check_agents = agents_md or not gitignore
        check_gitignore = gitignore or not agents_md
        _sync_check(
            root, check_agents=check_agents, check_gitignore=check_gitignore, quiet=quiet
        )
```

(f) Add the `_sync_check` helper (place it after `_sync_full`, before `_sync_agents_md_only`):

```python
def _sync_check(
    root: Path, *, check_agents: bool, check_gitignore: bool, quiet: bool
) -> None:
    """Report drift for the in-scope managed artifacts; never writes.

    Exit: drift → EXIT_VALIDATION (2, matches `doc check`); clean → EXIT_OK;
    a render/IO failure → EXIT_GENERIC via the shared AGENTS.md error envelope.
    """
    try:
        result = run_sync_check(
            root, __version__, check_agents=check_agents, check_gitignore=check_gitignore
        )
    except (OSError, AgentsMdInjectionError, GitignoreInjectionError) as e:
        click.echo(
            format_error(
                subcommand="sync",
                message=f"failed to compute drift: {e}",
                hint=_AGENTS_MD_WRITE_HINT,
            ),
            err=True,
        )
        sys.exit(EXIT_GENERIC)

    if result.drift:
        for artifact in result.drift:
            click.echo(artifact.diff, err=True)
        names = ", ".join(artifact.name for artifact in result.drift)
        click.echo(
            format_error(
                subcommand="sync",
                message=f"{names} out of sync with the super-harness template",
                hint="Run `super-harness sync` to regenerate, then commit the result.",
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)

    if not quiet:
        checked = []
        if check_agents:
            checked.append("AGENTS.md")
        if check_gitignore:
            checked.append(".gitignore")
        click.echo(
            f"{' and '.join(checked)} in sync with the super-harness template."
        )
    sys.exit(EXIT_OK)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/cli/test_sync.py -v`
Expected: PASS (existing + new `--check` tests).

- [ ] **Step 5: Run the full sync/render/check test scope + lint/type**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/cli/test_sync.py tests/unit/core/test_sync_check.py tests/unit/engineering/test_agents_md_render.py tests/integration/cli/test_sync.py -q`
Expected: PASS.

Run: `PATH="$(pwd)/.venv/bin:$PATH" ruff check src/super_harness/cli/sync.py src/super_harness/core/sync_check.py && PATH="$(pwd)/.venv/bin:$PATH" mypy src/super_harness/cli/sync.py src/super_harness/core/sync_check.py`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add src/super_harness/cli/sync.py tests/unit/cli/test_sync.py
git commit -m "feat(sync): add --check drift gate (composes with scope flags)"
```

---

## Task 5: CI gate step + OPEN-ITEMS update

**Files:**
- Modify: `.github/workflows/doc-check.yml`
- Modify: `private/OPEN-ITEMS.md`

- [ ] **Step 1: Add the `sync --check` CI step**

In `.github/workflows/doc-check.yml`, after the `Derivable-doc conformance (regen-and-diff)` step, append:

```yaml
      - name: Managed-artifact drift (sync --check)
        run: super-harness sync --check
```

- [ ] **Step 2: Verify the gate passes locally against this repo**

Run: `PATH="$(pwd)/.venv/bin:$PATH" super-harness sync --check; echo "exit=$?"`
Expected: `AGENTS.md and .gitignore are in sync with the super-harness template.` and `exit=0`.

(If exit is 2, the tracked AGENTS.md / .gitignore drifted from the template — run `super-harness sync` and re-commit before proceeding.)

- [ ] **Step 3: Mark the OPEN-ITEMS entry done**

In `private/OPEN-ITEMS.md`, under the `NEW(2026-06-17, ...)` block in the SLICE-4 section, append a resolution line:

```markdown
**RESOLVED(2026-06-17, this slice):** Half 1 — `### Decision conformance` added to
the OUTER framework template (`agents_md_render.py`), not the claude-code adapter
subsection (portable per design §4.1: plain CLI commands any agent runs). Half 2 —
shippable `sync --check` drift gate (temp-copy render + diff, exit 2 on drift),
wired into `doc-check.yml`; serves both downstream self-check and this repo's
self-hosting drift gate, subsuming the earlier derived-doc-registration option.
DEFER (registered): a user-facing PostToolUse hook auto-fire of `decision check`
remains the deferred §4.1 layer (Claude-Code-only, fail-open).
```

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/doc-check.yml private/OPEN-ITEMS.md
git commit -m "ci(sync): gate AGENTS.md/.gitignore drift with sync --check; close OPEN-ITEM"
```

---

## Task 6: Full verification before PR

- [ ] **Step 1: Run the whole unit + integration suite**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest -q`
Expected: all green (the 8 e2e/daemon "failures" under a bare `pytest` are a known PATH gap; the `PATH=` prefix avoids them).

- [ ] **Step 2: Lint + type the whole change**

Run: `PATH="$(pwd)/.venv/bin:$PATH" ruff check src tests && PATH="$(pwd)/.venv/bin:$PATH" mypy src`
Expected: clean.

- [ ] **Step 3: Confirm both gates are green locally (dogfood)**

Run: `PATH="$(pwd)/.venv/bin:$PATH" super-harness decision check; echo "decision=$?"; PATH="$(pwd)/.venv/bin:$PATH" super-harness doc check; echo "doc=$?"; PATH="$(pwd)/.venv/bin:$PATH" super-harness sync --check; echo "sync=$?"`
Expected: `decision=0` (or the 9 lazy-warn lines, still exit 0), `doc=0`, `sync=0`.

---

## Self-Review notes (author)

- **Spec coverage:** §3.1 placement → Task 1 (outer template); §3.2 content → Task 1 verbatim + test; §3.3 regeneration → Task 1 Step 5; §4.2 semantics (exit 2, scope flags, `--adapter` rejection, no writes) → Task 4; §4.3 temp-copy render → Tasks 2-3; §4.4 CI wiring → Task 5; §5 tests → Tasks 1-4; §6 files → all tasks; §7 limits (PostToolUse stays deferred) → Task 5 Step 3.
- **`--json`:** not added (design: not honored in v0.1, consistent with `sync` today).
- **Type consistency:** `run_sync_check(root, version, *, check_agents, check_gitignore) -> SyncCheckResult`; `SyncCheckResult.drift: list[ArtifactDrift]`, `.in_sync: bool`; `ArtifactDrift(name, diff)` — used identically in Tasks 2/3/4.
