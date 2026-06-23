# Check-drafting assist + decision-birth prompt — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Transfer the craft of writing a check that bites (a guidance recipe in
AGENTS.md) and stop decisions from landing toothless by accident (two-directional
birth-prompt advisories), without adding any CLI command, gate, or hook.

**Architecture:** Two surfaces only. (1) Extend the existing `### Decision
conformance` block in the AGENTS.md SSOT renderer (`agents_md_render.py`) with an
"Arming a decision" recipe, then regenerate the committed `AGENTS.md` via
`super-harness sync`. (2) Add advisory output to `decision new` (stderr, pure
pointer) and the tier-3 `decision ratify --dry-run` path (a separate stderr line
after the existing stdout line). No new command, no exit-code change, no PreToolUse
surface. The `decision scaffold` command considered in design was dropped as
gilding (see design §2).

**Tech Stack:** Python 3.10+, Click 8.4.1 (its `CliRunner` `Result` exposes
separate `.stdout` / `.stderr`), pytest. Verify with the project venv:
`PATH="$(pwd)/.venv/bin:$PATH" pytest ...`.

**Design doc:** `docs/plans/2026-06-23-check-drafting-assist-design.md` (converged
through 2 adversarial review rounds).

**Cross-cutting rules (read before starting):**
- All `pytest` / `super-harness` invocations go through the project venv:
  `PATH="$(pwd)/.venv/bin:$PATH" ...`. Never `uv run` inside the project.
- New-stream assertions use `r.stdout` / `r.stderr` — **never `r.output`** (the
  combined stream passes regardless of routing and would not guard the split).
- The AGENTS section version stamp is **NOT bumped**; the recipe's text change
  alone drives the `sync --check` drift guard.

---

### Task 1: `decision new` birth advisory (stderr, pure pointer)

**Files:**
- Modify: `src/super_harness/cli/decision.py` (`new_cmd`, around line 95)
- Test: `tests/unit/cli/test_decision.py`

**Step 1: Write the failing tests**

```python
def test_new_prints_birth_advisory_on_stderr(tmp_path):
    root = _init(tmp_path)
    r = CliRunner().invoke(
        main, ["--workspace", str(root), "decision", "new", "d-a", "--text", "x"])
    assert r.exit_code == 0, r.output
    # created-path stays machine-readable on stdout
    assert "created docs/decisions/d-a.md (proposed)" in r.stdout
    # advisory is on stderr only, leads with the "valid outcome" framing,
    # and carries NO arming command (nothing to --dry-run yet at birth)
    assert "context-only" in r.stderr
    assert "Arming a decision" in r.stderr
    assert "--dry-run" not in r.stderr
    assert "Arming a decision" not in r.stdout
    # lead-with the valid-outcome framing (design §3.2): position, not just presence
    assert r.stderr.index("context-only") < r.stderr.index("Arming a decision")
```

**Step 2: Run test to verify it fails**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/cli/test_decision.py::test_new_prints_birth_advisory_on_stderr -v`
Expected: FAIL (`context-only` not in stderr — no advisory yet).

**Step 3: Write minimal implementation**

In `new_cmd`, after the existing `click.echo(f"created ...")` and before
`sys.exit(EXIT_OK)`:

```python
    click.echo(f"created {path.relative_to(root)} (proposed)")
    click.echo(
        'Note: most decisions stay context-only — that is the norm. If this one '
        'states a brittle mechanical invariant, the "Arming a decision" recipe in '
        "AGENTS.md shows how to add an executable check.",
        err=True,
    )
    sys.exit(EXIT_OK)
```

**Step 4: Run test to verify it passes**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/cli/test_decision.py::test_new_prints_birth_advisory_on_stderr -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/super_harness/cli/decision.py tests/unit/cli/test_decision.py
git commit -m "feat(decision): birth advisory on decision new (stderr, pure pointer)"
```

---

### Task 2: tier-3 `ratify --dry-run` pointer + committing-ratify stays silent

**Files:**
- Modify: `src/super_harness/cli/decision.py` (`ratify_cmd`, the
  `elif dry_run:` branch around line 159)
- Test: `tests/unit/cli/test_decision.py`

**Step 1: Write the failing tests**

```python
def test_dry_run_tier3_keeps_stdout_line_and_adds_stderr_pointer(tmp_path):
    root = _init(tmp_path)
    CliRunner().invoke(
        main, ["--workspace", str(root), "decision", "new", "d-c", "--text", "ctx"])
    r = CliRunner().invoke(
        main, ["--workspace", str(root), "decision", "ratify", "d-c", "--dry-run"])
    assert r.exit_code == 0, r.output
    # existing contract: the tier-3 line stays on STDOUT (callers may grep it)
    assert "no check block (tier-3 context) - nothing to bite-test" in r.stdout
    # the new pointer is a SEPARATE line on stderr, leads with "valid outcome"
    assert "valid outcome" in r.stderr
    assert "Arming a decision" in r.stderr
    # lead-with the valid-outcome framing (design §3.2): position, not just presence
    assert r.stderr.index("valid outcome") < r.stderr.index("Arming a decision")
    # the pointer did not leak onto stdout
    assert "Arming a decision" not in r.stdout


def test_committing_ratify_of_tier3_prints_no_advisory(tmp_path):
    root = _init(tmp_path)
    CliRunner().invoke(
        main, ["--workspace", str(root), "decision", "new", "d-c", "--text", "ctx"])
    r = CliRunner().invoke(
        main, ["--workspace", str(root), "decision", "ratify", "d-c"])
    assert r.exit_code == 0, r.output
    assert "ratified d-c" in r.stdout
    # deliberate tier-3 ratification must NOT be nagged on either stream
    assert "Arming a decision" not in r.stdout
    assert "Arming a decision" not in r.stderr
```

**Step 2: Run tests to verify they fail**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/cli/test_decision.py -k "dry_run_tier3 or committing_ratify_of_tier3" -v`
Expected: the dry-run test FAILS (`valid outcome` not in stderr); the
committing-ratify test PASSES already (no advisory there by construction — this
test pins that property so a later change can't regress it).

**Step 3: Write minimal implementation**

Replace the `elif dry_run:` branch (currently a single `click.echo(...)` +
`sys.exit`) with:

```python
    elif dry_run:
        click.echo("no check block (tier-3 context) - nothing to bite-test")
        click.echo(
            '(context-only is a valid outcome; if this states a mechanical '
            'invariant, see the "Arming a decision" recipe in AGENTS.md, then '
            "re-run --dry-run to confirm the check bites.)",
            err=True,
        )
        sys.exit(EXIT_OK)
```

Do NOT touch the committing path (no `dry_run`, no check) — it already prints only
`ratified <id> (by ...)` and must stay silent.

**Step 4: Run tests to verify they pass**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/cli/test_decision.py -k "dry_run_tier3 or committing_ratify_of_tier3" -v`
Expected: both PASS

**Step 5: Commit**

```bash
git add src/super_harness/cli/decision.py tests/unit/cli/test_decision.py
git commit -m "feat(decision): tier-3 dry-run pointer (stderr); committing ratify stays silent"
```

---

### Task 3: "Arming a decision" recipe in the AGENTS.md renderer

**Files:**
- Modify: `src/super_harness/engineering/agents_md_render.py`
  (`_AGENTS_MD_SECTION_TEMPLATE`, inside the `### Decision conformance` block,
  before `<!-- super-harness section end -->`)
- Test: `tests/unit/engineering/test_agents_md_render.py`
  (alongside `test_outer_section_has_decision_conformance`, line 117)

**Hygiene constraint:** the recipe's example tokens must NOT collide with any
ratified check's scan target. Use the generic `requests` / `^import requests`
example (no `api.github.com`, the token `d-gh-cli-not-rest` scans for). Checks are
path-scoped to `src/`, so AGENTS.md is not scanned anyway, but keep examples
generic as defense-in-depth.

**`.format()` constraint (build-breaker if violated):** the template is rendered
via `_AGENTS_MD_SECTION_TEMPLATE.format(version=version)`
(`agents_md_render.py:135`). The recipe markdown must contain **no literal `{` or
`}`** — a stray brace raises `KeyError`/`ValueError` at render time and bounces
both the render test and `sync --check`. The recipe block below is brace-free;
keep it that way (if you ever need a literal brace, double it: `{{` / `}}`).

**Step 1: Write the failing test**

```python
def test_decision_conformance_has_arming_recipe(tmp_path: Path) -> None:
    agents = tmp_path / "AGENTS.md"
    render_super_harness_section(tmp_path, agents, "0.1.0")
    text = agents.read_text(encoding="utf-8")
    assert "Arming a decision" in text
    # the craft rungs that close friction D/E/G
    assert "brittle one-token signature" in text
    assert "context-only (tier-3)" in text  # the do-NOT-arm rung
    assert "```check" in text                # the block format shown inline
    assert "```counterexample" in text
```

(Match the existing test's import of `render_super_harness_section`.)

**Step 2: Run test to verify it fails**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/engineering/test_agents_md_render.py::test_decision_conformance_has_arming_recipe -v`
Expected: FAIL (`Arming a decision` not in rendered text).

**Step 3: Write minimal implementation**

In `_AGENTS_MD_SECTION_TEMPLATE`, append this sub-block to the `### Decision
conformance` section (after the existing bullets, before
`<!-- super-harness section end -->`):

```markdown
**Arming a decision with a check (the craft).** A check is a shell snippet that
exits nonzero when a decision is violated; `ratify` bite-tests it so it can't be
hollow. Writing one that catches violations without false positives is judgment —
yours, not the tool's — and the recipe is:

- Pick the **brittle one-token signature** of a violation, not a broad word
  (`^import requests`, not `requests`, which also hits prose / yaml).
- Prefer import/access patterns over bare substrings to dodge prose false positives.
- The check runs through the host's `/bin/sh` and `grep`, so prefer portable
  POSIX BRE/ERE; it **must exit nonzero on violation** (`! grep ...` inverts
  grep's exit).
- A denylist is coarse by construction (`^import` misses `as` / `from` forms);
  widen deliberately and record the ceiling in the decision body.
- **Scope the grep to source paths (e.g. `src/`), never `.`** — at ratify the
  check runs over the whole tree, so a bare `.` scans the decision file itself
  (which holds the counterexample) and reports "check fails on current code".
- Add a check + a minimal counterexample, then
  `super-harness decision ratify <id> --dry-run` until it reports `bites`:

  ```check
  ! grep -rn '<brittle pattern>' <scoped paths>
  ```

  ```counterexample path=<relative/path>
  <one minimal violating line the check above must catch>
  ```

- **If there is no brittle signature, leave it context-only (tier-3)** — do not
  invent a hollow check just to have one.
```

**Step 4: Run test to verify it passes**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/engineering/test_agents_md_render.py::test_decision_conformance_has_arming_recipe -v`
Expected: PASS

**Step 5: Regenerate the committed AGENTS.md (two-file atomic commit)**

The committed `AGENTS.md` must match the renderer or the self-host `sync --check`
gate bounces (exact text diff). Regenerate and stage BOTH files together:

```bash
PATH="$(pwd)/.venv/bin:$PATH" super-harness sync --agents-md
git diff --stat AGENTS.md   # confirm the recipe sub-block landed
```

**Step 6: Update the ARCHITECTURE.md §7 mirror (hand edit, reviewer-checked)**

`docs/ARCHITECTURE.md §7` ("Decision conformance") is a hand-maintained prose
mirror with NO drift gate. Add a one-line pointer to the new authoring recipe (or
a short "Arming a decision" note) so §7 does not silently drift. Keep it light —
§7 is architecture narrative, the recipe is how-to; a cross-reference is enough.
The code reviewer must eyeball §7 against the recipe.

**Step 7: Verify the drift guard is green**

Run: `PATH="$(pwd)/.venv/bin:$PATH" super-harness sync --check`
Expected: clean (committed AGENTS.md == rendered). Then run the render-test file:
`PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/engineering/test_agents_md_render.py -v`
Expected: all PASS (including the existing idempotency + drift-guard tests).

**Step 8: Commit**

```bash
git add src/super_harness/engineering/agents_md_render.py \
        tests/unit/engineering/test_agents_md_render.py AGENTS.md docs/ARCHITECTURE.md
git commit -m "feat(agents-md): arming-a-decision recipe in Decision conformance section"
```

---

### Task 4: Full-suite green + self-host merge-gate dry-run

**Step 1: Run the whole suite + lint + types**

```bash
PATH="$(pwd)/.venv/bin:$PATH" pytest -q
PATH="$(pwd)/.venv/bin:$PATH" ruff check .
PATH="$(pwd)/.venv/bin:$PATH" mypy src
```

Expected: all green (no new failures vs main; record the baseline count).

**Step 2: Decision/doc gates green locally**

```bash
PATH="$(pwd)/.venv/bin:$PATH" super-harness decision check
PATH="$(pwd)/.venv/bin:$PATH" super-harness doc check
PATH="$(pwd)/.venv/bin:$PATH" super-harness sync --check
```

Expected: `decision check` clean (the recipe text must not trip any ratified
check — verify the hygiene constraint in Task 3 held); `doc check` and
`sync --check` clean.

**Step 3: Self-host lifecycle (see design §6 for the full sequence)**

This task is the merge-gate paperwork, run once at the end, mirroring PR #45:
`change start` → `plan ready --scope '[...]'` (every touched file:
`src/super_harness/cli/decision.py`,
`src/super_harness/engineering/agents_md_render.py`, `AGENTS.md`,
`docs/ARCHITECTURE.md`, `tests/unit/cli/test_decision.py`,
`tests/unit/engineering/test_agents_md_render.py`, both plan docs) →
`review approve --reviewer plan-reviewer` → `implementation start` →
(Tasks 1–3 happen here) → `done` → `review approve --reviewer code-reviewer` →
`attest write` + commit the attestation → `attest verify --base main --head HEAD`
→ push → `gh pr create` → after merge: `on-merge --commit <sha> --change <slug>`.

**Note:** the `--json` flag is GLOBAL — place it before `decision`
(`super-harness --json decision check`), never after. Append to `private/` files
with Bash (`>>`), not the Edit tool.

---

## Close-out checklist (after merge)

- `private/CAPABILITY-CONVERGENCE-LEDGER.md`: add a row (capability delta + type +
  which intent it advances + re-score the intent×built matrix + hard:context +
  armed count). Record the **scaffold-dropped-as-gilding finding** and the
  before/after on arming-the-next-decision cost.
- `private/OPEN-ITEMS.md`: update the SLICE arm-decision-teeth deferral — close
  check-drafting (recipe shipped) + decision-birth-prompt; note edit-time
  PreToolUse reminder + `check try` remain deferred.
- Memory: update `project-harness-dogfood-ledger` + `project-phase-status`.
