# `sync` refreshes the managed `.gitignore` block — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> to implement this plan task-by-task (fresh subagent per task + code review).

**Goal:** Give an already-initialized repo a non-destructive way to refresh the
marker-bounded `.gitignore` block when `_CANONICAL_PATHS` changes, and add a
dogfood drift-guard that the committed block stays in sync with the injector.

**Architecture:** `super-harness sync` currently re-renders only the AGENTS.md
section. Add a second leg that calls the *existing*
`engineering.gitignore_injector.inject_gitignore_block` (already marker-bounded,
non-destructive, no-op-when-current, fail-loud on duplicate/unbalanced/non-UTF-8
markers). New command surface: no flag = refresh **both** AGENTS.md + gitignore;
`--agents-md` = AGENTS.md only (tightened from its v0.1 "== no-arg" placeholder);
`--gitignore` = gitignore only (new); `--adapter X` = adapter subsection only
(unchanged, still narrowest scope). Design:
`docs/plans/2026-06-03-sync-gitignore-refresh-design.md`.

**Tech Stack:** Python 3.10+, click, pytest. No new dependencies; the injector is
reused unchanged.

**Run tests with the repo venv on PATH** (daemon/hook tests are skipped, and the
console scripts are not found, otherwise):
`PATH="$(pwd)/.venv/bin:$PATH" python -m pytest …`

**Lifecycle note:** This change runs under the live PreToolUse hard gate. Drive
the lifecycle by hand via the `super-harness` CLI verbs (Bash, never gated):
`change start` → `plan ready --scope` → reviewer subagent → `review approve` →
implement → code review → `done` → PR. If the gate ever wedges, `touch
.harness/gate-disabled` to disable and `rm` it to re-enable.

---

### Task 1: Dogfood drift-guard test (committed block == injector output)

Independent, lowest-risk, currently passes — lock the invariant first.

**Files:**
- Test: `tests/unit/engineering/test_gitignore_injector.py` (append one test)

**Step 1: Write the test**

Append to `tests/unit/engineering/test_gitignore_injector.py`:

```python
def test_committed_repo_gitignore_block_matches_injector() -> None:
    """Dogfood drift-guard: this repo's committed root `.gitignore` super-harness
    block is byte-identical to what `inject_gitignore_block` would render today.

    Guards against `_CANONICAL_PATHS` drifting from the committed block (the gap
    that masked PR #34 review I-1). If this fails, run
    `super-harness sync --gitignore` and commit the updated `.gitignore`.
    """
    from super_harness.engineering.gitignore_injector import (
        GITIGNORE_BEGIN_MARKER,
        GITIGNORE_END_MARKER,
        _render_block,
    )

    repo_root = Path(__file__).resolve().parents[3]
    gitignore = repo_root / ".gitignore"
    assert gitignore.exists(), f"{gitignore} missing"

    text = gitignore.read_text(encoding="utf-8")
    assert text.count(GITIGNORE_BEGIN_MARKER) == 1, "expected exactly one block"
    assert text.count(GITIGNORE_END_MARKER) == 1, "expected exactly one block"

    begin = text.index(GITIGNORE_BEGIN_MARKER)
    end = text.index(GITIGNORE_END_MARKER) + len(GITIGNORE_END_MARKER)
    committed_block = text[begin:end]

    # `_render_block()` ends with a trailing LF; the in-file block does not carry
    # its own trailing LF inside the [begin, end] slice — strip for comparison.
    assert committed_block == _render_block().rstrip("\n"), (
        "Committed .gitignore super-harness block has drifted from "
        "_CANONICAL_PATHS. Run `super-harness sync --gitignore` and commit."
    )
```

Confirm `from pathlib import Path` is already imported at the top of the file; if
not, add it.

**Step 2: Run it — expect PASS** (the block was regenerated in commit `bb13ecc`,
so it currently matches):

```bash
PATH="$(pwd)/.venv/bin:$PATH" python -m pytest \
  tests/unit/engineering/test_gitignore_injector.py::test_committed_repo_gitignore_block_matches_injector -v
```
Expected: PASS. (If it FAILS, the committed block is already drifted — stop and
report; do not "fix" by editing the test.)

**Step 3: Commit**

```bash
git add tests/unit/engineering/test_gitignore_injector.py
git commit -m "test(sync): dogfood drift-guard — committed .gitignore block == injector output"
```

---

### Task 2: `--gitignore` scope leg (new flag, default behavior unchanged)

Add the gitignore re-render as a `--gitignore`-scoped leg. Default `sync` and
`--agents-md` are NOT changed in this task (that is Task 3) — this leaves a green
intermediate state.

**Files:**
- Modify: `src/super_harness/cli/sync.py`
- Test: `tests/integration/cli/test_sync.py`, `tests/unit/cli/test_sync.py`

**Step 1: Write failing integration tests**

Append to `tests/integration/cli/test_sync.py` (reuse the existing `_init` /
`_agents_md` helpers; add a `_gitignore` helper near `_agents_md`):

```python
def _gitignore(ws: Path) -> Path:
    return ws / ".gitignore"


def test_sync_gitignore_refreshes_block_in_place(tmp_path: Path) -> None:
    """`sync --gitignore` re-renders the managed block when it has drifted,
    preserving user content outside the markers and leaving AGENTS.md untouched."""
    from super_harness.engineering.gitignore_injector import _render_block

    runner = CliRunner()
    assert _init(runner, tmp_path).exit_code == 0
    # Simulate drift: replace the canonical block body with a stale single line,
    # keeping the markers; add user content outside the markers.
    gi = _gitignore(tmp_path)
    gi.write_text(
        "# >>> super-harness gitignore (do not edit between markers)\n"
        ".harness/state.yaml\n"
        "# <<< super-harness gitignore\n"
        "my-own-ignore/\n"
    )
    agents_before = _agents_md(tmp_path).read_text()

    r = runner.invoke(
        main, ["--workspace", str(tmp_path), "--quiet", "sync", "--gitignore"]
    )
    assert r.exit_code == 0, r.output

    after = gi.read_text()
    # Block now matches the full canonical render…
    assert _render_block().rstrip("\n") in after
    # …user content preserved…
    assert "my-own-ignore/" in after
    # …AGENTS.md untouched by a gitignore-scoped sync.
    assert _agents_md(tmp_path).read_text() == agents_before


def test_sync_gitignore_creates_block_when_absent(tmp_path: Path) -> None:
    """`sync --gitignore` writes the block when `.gitignore` has none yet."""
    runner = CliRunner()
    assert _init(runner, tmp_path).exit_code == 0
    _gitignore(tmp_path).unlink()  # init wrote one; remove to test the absent path

    r = runner.invoke(
        main, ["--workspace", str(tmp_path), "--quiet", "sync", "--gitignore"]
    )
    assert r.exit_code == 0, r.output
    text = _gitignore(tmp_path).read_text()
    assert "# >>> super-harness gitignore" in text
    assert ".claude/settings.local.json" in text


def test_sync_gitignore_idempotent(tmp_path: Path) -> None:
    """A second `sync --gitignore` on canonical state is a byte-identical no-op."""
    runner = CliRunner()
    assert _init(runner, tmp_path).exit_code == 0
    runner.invoke(main, ["--workspace", str(tmp_path), "--quiet", "sync", "--gitignore"])
    before = _gitignore(tmp_path).read_text()
    runner.invoke(main, ["--workspace", str(tmp_path), "--quiet", "sync", "--gitignore"])
    assert _gitignore(tmp_path).read_text() == before
```

Append a failure-envelope unit test to `tests/unit/cli/test_sync.py`:

```python
def test_sync_gitignore_write_failure_exits_generic(tmp_path: Path) -> None:
    """If the `.gitignore` write fails, `sync --gitignore` surfaces a clean
    format_error (exit 1, no traceback). Force a portable OSError by placing a
    DIRECTORY at the `.gitignore` path: the injector's read raises
    IsADirectoryError (an OSError subclass) on every platform."""
    (tmp_path / ".harness").mkdir()
    (tmp_path / ".gitignore").mkdir()

    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "--quiet", "sync", "--gitignore"]
    )
    assert r.exit_code == 1, r.output
    assert "Traceback" not in r.stderr
    assert "super-harness sync:" in r.stderr
    assert "failed to update .gitignore" in r.stderr
    assert "Hint:" in r.stderr
```

**Step 2: Run them — expect FAIL** (`--gitignore` is not a known option yet):

```bash
PATH="$(pwd)/.venv/bin:$PATH" python -m pytest \
  tests/integration/cli/test_sync.py -k gitignore \
  tests/unit/cli/test_sync.py::test_sync_gitignore_write_failure_exits_generic -v
```
Expected: FAIL (Click: "no such option: --gitignore").

**Step 3: Implement**

In `src/super_harness/cli/sync.py`:

1. Add imports near the existing `engineering.agents_md` import:

```python
from super_harness.engineering.gitignore_injector import (
    GitignoreInjectionError,
    inject_gitignore_block,
)
```

2. Add a hint constant near `_AGENTS_MD_WRITE_HINT`:

```python
_GITIGNORE_WRITE_HINT = (
    "Fix .gitignore (permissions / duplicate super-harness markers) and "
    "re-run `sync --gitignore`."
)
```

3. Add the `--gitignore` option to `sync_cmd` (after `--adapter`):

```python
@click.option(
    "--gitignore",
    "gitignore",
    is_flag=True,
    help="Re-render ONLY the managed .gitignore block (no AGENTS.md change). "
    "Picks up `_CANONICAL_PATHS` additions from a super-harness upgrade "
    "without re-running init.",
)
```

Add `gitignore: bool` to the `sync_cmd` signature (after `adapter_name`), and
update the dispatch so `--adapter` still wins, then `--gitignore` only:

```python
def sync_cmd(
    ctx: click.Context,
    agents_md: bool,
    adapter_name: str | None,
    gitignore: bool,
    assume_yes: bool,
) -> None:
    ...
    root = _resolve_root(ctx, "sync")
    agents_path = root / "AGENTS.md"
    quiet = bool(ctx.obj.get("quiet"))

    if adapter_name is not None:
        _sync_adapter(root, agents_path, adapter_name, quiet=quiet, assume_yes=assume_yes)
    elif gitignore and not agents_md:
        _sync_gitignore(root, quiet=quiet)
    else:
        _sync_full(root, agents_path, quiet=quiet, assume_yes=assume_yes)
```

(Do not change `_sync_full` yet — Task 3 makes it write gitignore too. For now
`--gitignore` alone hits the new leg; no-arg and `--agents-md` still hit the
existing AGENTS.md-only `_sync_full`. The `and not agents_md` guard is forward
prep for Task 3's combined default.)

4. Add the new leg helper:

```python
def _sync_gitignore(root: Path, *, quiet: bool) -> None:
    """Re-render ONLY the managed `.gitignore` block (init + sync SSOT).

    Reuses `inject_gitignore_block` (marker-bounded, non-destructive, no-op when
    current, fail-loud on duplicate/unbalanced/non-UTF-8 markers). No confirm
    prompt: the block is purely our canonical path list — there is no user
    content between the markers to lose. Mirrors init's error envelope.
    """
    try:
        inject_gitignore_block(root / ".gitignore")
    except (OSError, GitignoreInjectionError) as e:
        click.echo(
            format_error(
                subcommand="sync",
                message=f"failed to update .gitignore: {e}",
                hint=_GITIGNORE_WRITE_HINT,
            ),
            err=True,
        )
        sys.exit(EXIT_GENERIC)

    if not quiet:
        click.echo("Synced .gitignore super-harness block.")
    sys.exit(EXIT_OK)
```

**Step 4: Run the new tests — expect PASS:**

```bash
PATH="$(pwd)/.venv/bin:$PATH" python -m pytest \
  tests/integration/cli/test_sync.py -k gitignore \
  tests/unit/cli/test_sync.py::test_sync_gitignore_write_failure_exits_generic -v
```
Expected: PASS.

**Step 5: Run the full sync suites — expect still green** (default/`--agents-md`
unchanged this task):

```bash
PATH="$(pwd)/.venv/bin:$PATH" python -m pytest \
  tests/integration/cli/test_sync.py tests/unit/cli/test_sync.py -v
```

**Step 6: Commit**

```bash
git add src/super_harness/cli/sync.py tests/integration/cli/test_sync.py tests/unit/cli/test_sync.py
git commit -m "feat(sync): add --gitignore scope to re-render the managed .gitignore block"
```

---

### Task 3: Default `sync` refreshes both + narrow `--agents-md`

Make no-arg `sync` refresh AGENTS.md **and** the gitignore block; tighten
`--agents-md` to AGENTS.md-only. Update the two existing tests whose intent
changes.

**Files:**
- Modify: `src/super_harness/cli/sync.py`
- Test: `tests/integration/cli/test_sync.py`

**Step 1: Update + add tests**

Replace `test_sync_agents_md_flag_identical_to_no_arg` with a test that locks the
NEW scoping (AGENTS.md identical, but gitignore differs):

```python
def test_sync_agents_md_is_agents_only_no_arg_also_does_gitignore(
    tmp_path: Path,
) -> None:
    """`--agents-md` re-renders AGENTS.md but does NOT touch the gitignore block;
    no-arg `sync` refreshes BOTH. (v0.1 placeholder `--agents-md == no-arg`
    tightened to a real scope.)"""
    from super_harness.engineering.gitignore_injector import _render_block

    runner = CliRunner()
    assert _init(runner, tmp_path).exit_code == 0
    agents_after_init = _agents_md(tmp_path).read_text()

    # Drift the gitignore block (stale body).
    gi = _gitignore(tmp_path)
    gi.write_text(
        "# >>> super-harness gitignore (do not edit between markers)\n"
        ".harness/state.yaml\n"
        "# <<< super-harness gitignore\n"
    )

    # --agents-md leaves the (drifted) gitignore block alone.
    r1 = runner.invoke(
        main, ["--workspace", str(tmp_path), "--quiet", "sync", "--agents-md"]
    )
    assert r1.exit_code == 0, r1.output
    assert _agents_md(tmp_path).read_text() == agents_after_init
    assert _render_block().rstrip("\n") not in gi.read_text()  # still drifted

    # no-arg sync refreshes the gitignore block too.
    r2 = runner.invoke(main, ["--workspace", str(tmp_path), "--quiet", "sync"])
    assert r2.exit_code == 0, r2.output
    assert _render_block().rstrip("\n") in gi.read_text()  # now refreshed
```

Add a combined success-message test (non-quiet):

```python
def test_sync_no_arg_success_message_mentions_both(tmp_path: Path) -> None:
    runner = CliRunner()
    assert _init(runner, tmp_path).exit_code == 0
    r = runner.invoke(
        main, ["--workspace", str(tmp_path), "sync"], input="y\n"
    )
    assert r.exit_code == 0, r.output
    assert "AGENTS.md" in r.output
    assert ".gitignore" in r.output
```

**Step 2: Run — expect FAIL** (`--agents-md` currently mutates nothing extra, but
no-arg does NOT yet refresh gitignore; the combined message is absent):

```bash
PATH="$(pwd)/.venv/bin:$PATH" python -m pytest \
  tests/integration/cli/test_sync.py -k "agents_md_is_agents_only or success_message_mentions_both" -v
```
Expected: FAIL.

**Step 3: Implement** — make `_sync_full` write the gitignore block after the
AGENTS.md leg. In `sync.py`, change `_sync_full`:

```python
def _sync_full(
    root: Path, agents_path: Path, *, quiet: bool, assume_yes: bool
) -> None:
    """Full re-render: AGENTS.md section (version bump + all adapters) AND the
    managed `.gitignore` block. The AGENTS.md leg owns the overwrite-confirm; the
    `.gitignore` leg has no user content between its markers, so it piggybacks
    silently after a confirmed AGENTS.md render."""
    try:
        _confirm_overwrite_if_present(agents_path, quiet=quiet, assume_yes=assume_yes)
        render_super_harness_section(root, agents_path, __version__)
    except (OSError, AgentsMdInjectionError) as e:
        click.echo(
            format_error(
                subcommand="sync",
                message=f"failed to update AGENTS.md: {e}",
                hint=_AGENTS_MD_WRITE_HINT,
            ),
            err=True,
        )
        sys.exit(EXIT_GENERIC)

    try:
        inject_gitignore_block(root / ".gitignore")
    except (OSError, GitignoreInjectionError) as e:
        click.echo(
            format_error(
                subcommand="sync",
                message=f"failed to update .gitignore: {e}",
                hint=_GITIGNORE_WRITE_HINT,
            ),
            err=True,
        )
        sys.exit(EXIT_GENERIC)

    if not quiet:
        click.echo(
            f"Synced AGENTS.md super-harness section (v{__version__}) "
            f"and .gitignore block."
        )
    sys.exit(EXIT_OK)
```

Then make `--agents-md` AGENTS.md-only. The dispatch already routes `--gitignore
and not agents_md` to `_sync_gitignore`; now add an `--agents-md`-only leg so it
does NOT fall through to `_sync_full` (which now also writes gitignore). Add a
small helper and update dispatch:

```python
def _sync_agents_md_only(
    root: Path, agents_path: Path, *, quiet: bool, assume_yes: bool
) -> None:
    """Re-render ONLY the AGENTS.md section (no gitignore leg)."""
    try:
        _confirm_overwrite_if_present(agents_path, quiet=quiet, assume_yes=assume_yes)
        render_super_harness_section(root, agents_path, __version__)
    except (OSError, AgentsMdInjectionError) as e:
        click.echo(
            format_error(
                subcommand="sync",
                message=f"failed to update AGENTS.md: {e}",
                hint=_AGENTS_MD_WRITE_HINT,
            ),
            err=True,
        )
        sys.exit(EXIT_GENERIC)
    if not quiet:
        click.echo(f"Synced AGENTS.md super-harness section (v{__version__}).")
    sys.exit(EXIT_OK)
```

Update the dispatch in `sync_cmd`:

```python
    if adapter_name is not None:
        _sync_adapter(root, agents_path, adapter_name, quiet=quiet, assume_yes=assume_yes)
    elif gitignore and not agents_md:
        _sync_gitignore(root, quiet=quiet)
    elif agents_md and not gitignore:
        _sync_agents_md_only(root, agents_path, quiet=quiet, assume_yes=assume_yes)
    else:
        # no flag, or both --agents-md and --gitignore → full (both artifacts)
        _sync_full(root, agents_path, quiet=quiet, assume_yes=assume_yes)
```

> Note: this introduces light duplication between `_sync_agents_md_only` and the
> AGENTS.md leg of `_sync_full`. Keep it — extracting a shared inner helper that
> calls `sys.exit` is more tangled than the two readable legs. (DRY judgment:
> the duplicated block is 9 lines of error-envelope boilerplate already mirrored
> across `_sync_adapter`; a future refactor can unify all three.)

**Step 4: Run the changed tests — expect PASS:**

```bash
PATH="$(pwd)/.venv/bin:$PATH" python -m pytest \
  tests/integration/cli/test_sync.py -k "agents_md_is_agents_only or success_message_mentions_both" -v
```

**Step 5: Run the full sync suites + verify no other test asserted old behavior:**

```bash
PATH="$(pwd)/.venv/bin:$PATH" python -m pytest \
  tests/integration/cli/test_sync.py tests/unit/cli/test_sync.py -v
```
The pre-existing `test_sync_full_agents_md_write_failure_exits_generic` (unit)
still passes: AGENTS.md fails first (directory at its path), so `_sync_full`
exits before the gitignore leg.

**Step 6: Commit**

```bash
git add src/super_harness/cli/sync.py tests/integration/cli/test_sync.py
git commit -m "feat(sync): default sync refreshes both AGENTS.md + .gitignore; --agents-md now AGENTS.md-only"
```

---

### Task 4: Docs — help text, module docstring, cli-reference, surface spec

**Files:**
- Modify: `src/super_harness/cli/sync.py` (module docstring + `--agents-md` help)
- Modify: `docs/cli-reference.md` (regenerated)
- Test: `tests/integration/cli/test_sync.py` (help test)

**Step 1: Update the `--agents-md` help + module docstring.** The current
`--agents-md` help says "identical to no-arg". Rewrite it to the tightened scope,
keeping the genuine v0.1 adapter-checks no-op caveat (it still applies):

```python
@click.option(
    "--agents-md",
    "agents_md",
    is_flag=True,
    help=(
        "Re-render ONLY the AGENTS.md super-harness section (no .gitignore "
        "change). v0.1: built-in adapters contribute no verification.yaml checks "
        "yet, so the adapter-checks sync leg is a no-op (v0.2 adds it)."
    ),
)
```

Update the module docstring "Modes:" block (top of `sync.py`) to describe the
four scopes (no-flag = both; `--agents-md`; `--gitignore`; `--adapter`) and the
precedence (`--adapter` narrowest; `--agents-md` + `--gitignore` together = both).

**Step 2: Update the help test.** `test_sync_help_agents_md_advertises_v01_noop`
asserts `"v0.1"` and `"no-op"` are in `sync --help` — both survive the rewrite,
so the test still passes. Add one assertion that `--gitignore` is advertised:

```python
def test_sync_help_lists_gitignore_scope(tmp_path: Path) -> None:
    """`sync --help` documents the new --gitignore scope."""
    r = CliRunner().invoke(main, ["sync", "--help"])
    assert r.exit_code == 0
    assert "--gitignore" in r.output
```

**Step 3: Regenerate the CLI reference:**

```bash
PATH="$(pwd)/.venv/bin:$PATH" python -m scripts.gen_cli_reference
```
Then verify the in-sync guard passes:

```bash
PATH="$(pwd)/.venv/bin:$PATH" python -m pytest \
  tests/unit/scripts/test_gen_cli_reference.py::test_real_cli_reference_is_in_sync -v
```

**Step 4: Run the help test:**

```bash
PATH="$(pwd)/.venv/bin:$PATH" python -m pytest \
  tests/integration/cli/test_sync.py -k help -v
```

**Step 5: Commit**

```bash
git add src/super_harness/cli/sync.py docs/cli-reference.md tests/integration/cli/test_sync.py
git commit -m "docs(sync): document --gitignore scope + regenerate cli-reference"
```

---

### Task 5: Full-suite green + self-host gate verification

**Step 1: Full suite (repo venv on PATH), 2× for the known daemon-start flake:**

```bash
PATH="$(pwd)/.venv/bin:$PATH" python -m pytest -q
PATH="$(pwd)/.venv/bin:$PATH" python -m pytest -q
```
Expected: all green both runs.

**Step 2: Lint + types:**

```bash
PATH="$(pwd)/.venv/bin:$PATH" ruff check src tests
PATH="$(pwd)/.venv/bin:$PATH" mypy src
```

**Step 3: Manual dogfood — `sync --gitignore` is a no-op on this repo NOW**
(committed block already canonical), proving the non-destructive refresh path:

```bash
PATH="$(pwd)/.venv/bin:$PATH" super-harness sync --gitignore
git diff --exit-code .gitignore   # expect no diff (byte-identical no-op)
```

**Step 4: No commit** (verification only). Proceed to `super-harness done` +
code review + PR per the lifecycle.

---

## Out of scope (YAGNI)

- No change to `_CANONICAL_PATHS` or the injector's marker grammar.
- No `init` change (the `.harness/.state.lock` omission is a separate OPEN-ITEM).
- No gitignore confirm prompt / `--force` (the block is always safe to regen).
