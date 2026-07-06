---
# super-harness ⇄ superpowers integration marker (parsed by SuperpowersAdapter):
change: 2026-07-06-plan-redeclare-cli-verb
stage: plan
scope:
  files:
    - docs/plans/2026-07-06-plan-redeclare-cli-verb.md
    - src/super_harness/cli/plan.py
    - tests/unit/cli/test_plan.py
    - docs/cli-reference.md
    - scripts/gen_cli_reference.py
tier_hint: Micro
---

# `plan redeclare` CLI verb Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a `super-harness plan redeclare <slug> [--reason <text>]` CLI verb that emits the already-supported `plan_redeclared` event, so a change can be rewound from any active state back to `INTENT_DECLARED` (to expand scope and re-run plan review) without the `change abandon` + new-slug + PR-marker-rewrite workaround.

**Architecture:** Pure additive CLI leaf in the existing `plan` group (`cli/plan.py`), mirroring the sibling `plan ready` emitter. The state machine already knows `plan_redeclared` (`events.py` KNOWN, `transitions.py:104` any-active-state → `INTENT_DECLARED`, `reducer.py:152` appends `{reason}` to `redeclaration_history`). The only gap is a CLI verb to emit it — no core changes. Strict emit: an illegal transition (terminal state or unknown slug) is rejected via `EmitPreconditionError` and nothing is appended.

**Tech Stack:** Python 3.10+, click, pyyaml, pytest. `EventWriter.emit` / `EmitPreconditionError` / `refresh_state_after_emit` / `derive_state` are all reused unchanged.

**Boundary (settled in brainstorming):** `redeclare` takes NO `--scope`. Scope is (re)declared on the subsequent `plan ready <slug> --scope @new`, which routes back through `AWAITING_PLAN_REVIEW` and the plan `scope-adherence` review. This deliberately keeps plan + code review re-running; we do NOT add a silent scope-amend-without-review path.

---

## Design decisions (from brainstorming, 2026-07-06)

- **Verb placement:** `plan` group (`super-harness plan redeclare`). Pairs with the sibling `plan ready` emitter (redeclare rewinds to before plan-ready; ready re-advances); the emitted event is named `plan_redeclared`; the scope-expansion workflow `plan redeclare → plan ready --scope` reads as one unit. (Accepted recommendation over `change redeclare`.)
- **`--reason` flag:** included, optional, default `""`, mirroring `change abandon --reason`. The reducer already reads `payload.reason` into `redeclaration_history` (`reducer.py:156`), so the plumbing to record it exists — `--reason` populates an audit trail explaining why a change was rewound. Empty reason → omit `reason` from the payload (keep the event minimal).

## Behavioural contract

| From state | `plan redeclare` result |
|---|---|
| `INTENT_DECLARED` | → `INTENT_DECLARED` (legal; appends redeclaration_history) |
| any active non-terminal (`AWAITING_PLAN_REVIEW`, `PLAN_APPROVED`, `IMPLEMENTATION_IN_PROGRESS`, `AWAITING_CODE_REVIEW`, `READY_TO_MERGE`, `PLAN_REJECTED`, …) | → `INTENT_DECLARED` |
| terminal (`ARCHIVED`, `ABANDONED`) | rejected, exit 2, no event |
| unknown slug (no prior state) | rejected, exit 2, no event |

Exit codes: 0 ok / 2 illegal transition / 3 no `.harness/`. (No `--scope` parse path, so no bad-scope exit.)

---

### Task 1: Failing tests for `plan redeclare`

**Files:**
- Test: `tests/unit/cli/test_plan.py` (add tests + widen module docstring to cover both verbs)

**Step 1: Write the failing tests**

Add to `tests/unit/cli/test_plan.py`. Reuse the existing `_seed` / `_state` / `_events` helpers.

```python
# --- plan redeclare (rewind any active state → INTENT_DECLARED) ---

def test_redeclare_from_ready_to_merge_rewinds_to_intent(tmp_path: Path) -> None:
    # verification_passed precedes implementation_complete: _HARD_PREREQ_EVENTS
    # (emit_validation.py) enforces it, and it is informational (stays in
    # IMPLEMENTATION_IN_PROGRESS) so the downstream transitions are unaffected.
    _seed(
        tmp_path, "c",
        "intent_declared", "plan_ready", "plan_approved",
        "implementation_started", "verification_passed", "implementation_complete",
        "code_review_passed",
    )
    assert _state(tmp_path, "c") == "READY_TO_MERGE"
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "plan", "redeclare", "c"])
    assert r.exit_code == EXIT_OK, r.output
    assert _state(tmp_path, "c") == "INTENT_DECLARED"
    assert _events(tmp_path)[-1]["type"] == "plan_redeclared"


def test_redeclare_from_plan_rejected_rewinds_to_intent(tmp_path: Path) -> None:
    # A rejection state is still "any active" — cements the transitions.py:104
    # universal branch wording.
    _seed(tmp_path, "c", "intent_declared", "plan_ready", "plan_rejected")
    assert _state(tmp_path, "c") == "PLAN_REJECTED"
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "plan", "redeclare", "c"])
    assert r.exit_code == EXIT_OK, r.output
    assert _state(tmp_path, "c") == "INTENT_DECLARED"
    assert _events(tmp_path)[-1]["type"] == "plan_redeclared"


def test_redeclare_from_intent_declared_is_legal_noop(tmp_path: Path) -> None:
    _seed(tmp_path, "c", "intent_declared")
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "plan", "redeclare", "c"])
    assert r.exit_code == EXIT_OK, r.output
    assert _state(tmp_path, "c") == "INTENT_DECLARED"
    assert _events(tmp_path)[-1]["type"] == "plan_redeclared"


def test_redeclare_records_reason_in_history(tmp_path: Path) -> None:
    _seed(tmp_path, "c", "intent_declared", "plan_ready", "plan_approved")
    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "plan", "redeclare", "c", "--reason", "expand scope"],
    )
    assert r.exit_code == EXIT_OK, r.output
    assert _events(tmp_path)[-1]["payload"].get("reason") == "expand scope"
    history = derive_state(events_path(tmp_path)).get("c").redeclaration_history
    assert history[-1]["reason"] == "expand scope"
    assert history[-1]["type"] == "plan_redeclared"


def test_redeclare_without_reason_omits_reason_from_payload(tmp_path: Path) -> None:
    _seed(tmp_path, "c", "intent_declared", "plan_ready", "plan_approved")
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "plan", "redeclare", "c"])
    assert r.exit_code == EXIT_OK, r.output
    assert "reason" not in _events(tmp_path)[-1]["payload"]


def test_redeclare_from_terminal_archived_rejected_no_event(tmp_path: Path) -> None:
    _seed(
        tmp_path, "c",
        "intent_declared", "plan_ready", "plan_approved",
        "implementation_started", "verification_passed", "implementation_complete",
        "code_review_passed", "merged",
    )
    assert _state(tmp_path, "c") == "ARCHIVED"
    before = len(_events(tmp_path))
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "plan", "redeclare", "c"])
    assert r.exit_code == EXIT_VALIDATION, r.output
    assert len(_events(tmp_path)) == before
    assert _events(tmp_path)[-1]["type"] != "plan_redeclared"


def test_redeclare_from_terminal_abandoned_rejected_no_event(tmp_path: Path) -> None:
    _seed(tmp_path, "c", "intent_declared", "intent_abandoned")
    assert _state(tmp_path, "c") == "ABANDONED"
    before = len(_events(tmp_path))
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "plan", "redeclare", "c"])
    assert r.exit_code == EXIT_VALIDATION, r.output
    assert len(_events(tmp_path)) == before
    assert _events(tmp_path)[-1]["type"] != "plan_redeclared"


def test_redeclare_unknown_slug_rejected_no_event(tmp_path: Path) -> None:
    (tmp_path / ".harness").mkdir(parents=True, exist_ok=True)
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "plan", "redeclare", "ghost"])
    assert r.exit_code == EXIT_VALIDATION, r.output
    # No plan_redeclared may be appended for an unknown slug (strict emit).
    assert not events_path(tmp_path).exists() or not any(
        e["type"] == "plan_redeclared" for e in _events(tmp_path)
    )


def test_redeclare_no_harness_exit_3(tmp_path: Path) -> None:
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "plan", "redeclare", "c"])
    assert r.exit_code == EXIT_NO_CONFIG, r.output


def test_redeclare_json_envelope(tmp_path: Path) -> None:
    _seed(tmp_path, "c", "intent_declared", "plan_ready", "plan_approved")
    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "--json", "plan", "redeclare", "c"]
    )
    assert r.exit_code == EXIT_OK, r.output
    payload = json.loads(r.stdout)
    assert payload["status"] == "pass"
    assert payload["data"]["event_emitted"] == "plan_redeclared"
    assert payload["data"]["new_state"] == "INTENT_DECLARED"
```

Also widen the module docstring's first line so it names both verbs (it currently says "for `super-harness plan ready`").

**Step 2: Run tests to verify they fail**

Run: `export PATH="$PWD/.venv/bin:$PATH" && pytest tests/unit/cli/test_plan.py -k redeclare -v`
Expected: FAIL — click reports `No such command 'redeclare'` (exit 2 from click's usage error, but the assertions on `event_emitted`/state will fail first, and the unknown-command path won't produce our JSON envelope).

**Step 3: Commit the failing tests** (optional checkpoint — or fold into Task 2 commit)

---

### Task 2: Implement `plan redeclare`

**Files:**
- Modify: `src/super_harness/cli/plan.py` (add `redeclare` command after `ready`; widen module docstring)

**Step 1: Add the command**

Append to `cli/plan.py`, after the `ready` command:

```python
@plan_group.command("redeclare")
@click.argument("slug")
@click.option(
    "--reason",
    default="",
    help="Optional human-readable reason for reopening the change (recorded in redeclaration_history).",
)
@click.pass_context
def redeclare(ctx: click.Context, slug: str, reason: str) -> None:
    """Emit `plan_redeclared` (any active state → INTENT_DECLARED).

    Rewinds a change to the first lifecycle stage so its scope can be
    (re)declared via a subsequent `plan ready <slug> --scope @new` — which
    routes back through AWAITING_PLAN_REVIEW and the plan scope-adherence
    review. Strict emit: a terminal state (ARCHIVED/ABANDONED) or unknown slug
    is an illegal transition, rejected with nothing appended.
    """
    try:
        root = find_harness_root(Path(ctx.obj.get("workspace") or "."))
    except HarnessNotInitialized as e:
        click.echo(
            format_error(subcommand="plan redeclare", message=e.message, hint=e.hint),
            err=True,
        )
        sys.exit(EXIT_NO_CONFIG)

    cs = derive_state(events_path(root)).get(slug)
    framework = cs.framework if cs is not None else "plain"  # like the sibling emitter
    payload: dict[str, object] = {}
    if reason:
        payload["reason"] = reason
    ev = Event(
        event_id=new_event_id(),
        type="plan_redeclared",
        change_id=slug,
        timestamp=utc_now_iso(),
        actor=Actor(type="human", identifier="cli"),
        framework=framework,
        payload=payload,
    )
    try:
        EventWriter(events_path(root)).emit(ev)
    except EmitPreconditionError as e:
        click.echo(
            format_error(
                subcommand="plan redeclare",
                message=str(e),
                hint="`plan_redeclared` is only legal from an active (non-terminal) state.",
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    refresh_state_after_emit(root)

    new_cs = derive_state(events_path(root)).get(slug)
    new_state = new_cs.current_state if new_cs is not None else None
    if ctx.obj.get("json"):
        click.echo(
            json_envelope(
                command="plan redeclare",
                status="pass",
                exit_code=EXIT_OK,
                data={
                    "change": slug,
                    "event_emitted": "plan_redeclared",
                    "new_state": new_state,
                },
            )
        )
    elif not ctx.obj.get("quiet"):
        click.echo(f"super-harness: emitted plan_redeclared for {slug} → {new_state}")
    sys.exit(EXIT_OK)
```

**Step 2: Widen the module docstring**

Update the `cli/plan.py` module docstring: the first line currently scopes the module to `plan ready`. Add a short paragraph documenting `plan redeclare <slug> [--reason <text>]` — emits `plan_redeclared`, any active state → INTENT_DECLARED, strict, exit 0/2/3; and note it is the late-stage scope-expansion counterpart (rewind → re-`plan ready --scope`). Mirror the reconcile note style used for `--tier-hint`: the private cli-command-surface spec should grow this verb.

**Step 3: Run tests to verify they pass**

Run: `export PATH="$PWD/.venv/bin:$PATH" && pytest tests/unit/cli/test_plan.py -v`
Expected: PASS (all `ready` + `redeclare` tests).

**Step 4: Commit**

```bash
git add src/super_harness/cli/plan.py tests/unit/cli/test_plan.py
git commit -m "feat(cli): add plan redeclare verb (emit plan_redeclared)"
```

---

### Task 3: Regenerate the derived CLI reference

**Files:**
- Modify (derived): `docs/cli-reference.md`

**Step 1: Regenerate**

Run: `export PATH="$PWD/.venv/bin:$PATH" && super-harness doc check --fix`
Expected: `docs/cli-reference.md` gains a `## super-harness plan redeclare` section with synopsis, `--reason` param, and exit codes.

**Step 2: Verify the derived doc is in sync**

Run: `export PATH="$PWD/.venv/bin:$PATH" && super-harness doc check` (no `--fix`)
Expected: exit 0 (derived doc matches generator output).

**Step 3: Commit**

```bash
git add docs/cli-reference.md
git commit -m "docs: regenerate cli-reference for plan redeclare"
```

---

### Task 3b: Document `plan redeclare` exit codes in the generator map

**Why:** `scripts/gen_cli_reference.py` holds an `_EXIT_CODES` map; commands absent
from it fall back to a bare `0 success / 1 generic error`. The sibling `plan ready`
has a full entry, so leaving `plan redeclare` on the default is a within-group
inconsistency and under-documents its real `2` (illegal transition) and `3`
(no `.harness/`) codes. (This gap was surfaced mid-implementation and the scope
was expanded to include this file by dogfooding `plan redeclare` itself.)

**Accuracy note (verified — do NOT blindly copy `plan ready`):** `plan ready`'s
entry lists `5 state.yaml lock contention`, but that is **not reachable** from the
plan-emit path — `EventWriter.emit` + `refresh_state_after_emit` take `state.yaml`
via **blocking** `fcntl.flock` (they wait, never fail-with-5). `EXIT_CONCURRENCY`
(5) is in fact not emitted by ANY CLI path today — it is a code reserved in the
command surface for `sync`/`adapter` file-locking that is not wired yet (see the
`cli/adapter.py` docstring: "No adapters.yaml file locking … so … 5 is [not
produced]"). So `plan redeclare`'s accurate set is `0/1/2/3` (NO `5`); we do not
propagate the sibling's aspirational `5`. (`plan ready`'s stale `5` is out of
scope here — logged as a follow-up.)

**Files:**
- Modify: `scripts/gen_cli_reference.py` (`_EXIT_CODES` dict — add a `"plan redeclare"` key)

**Step 1: Add the entry** (mirrors `plan ready` minus the `--scope` clause — redeclare
has no scope parse path — and minus the unreachable `5`, per the accuracy note):

```python
    "plan redeclare": [
        "`0` success",
        "`1` generic error",
        "`2` illegal lifecycle transition (terminal state / not-yet-started slug)",
        "`3` no `.harness/`",
    ],
```

**Step 2: Regenerate + verify** (`doc check --fix` then `doc check`); the redeclare
section now lists the full exit-code set. **Commit** with `cli-reference.md`.

---

### Task 4: Full suite + lint gate

**Step 1: Run the full unit suite**

Run: `export PATH="$PWD/.venv/bin:$PATH" && pytest -q`
Expected: all pass (prior count + new redeclare tests).

**Step 2: Lint (ruff import-order etc. — pothole: Codex sandbox misses this)**

Run: `export PATH="$PWD/.venv/bin:$PATH" && ruff check src/ tests/`
Expected: clean.

**Step 3: Confirm no derived-doc / AGENTS.md drift beyond cli-reference**

Run: `export PATH="$PWD/.venv/bin:$PATH" && super-harness sync --check && super-harness doc check`
Expected: both exit 0 (adding a CLI verb touches only the cli-reference derived doc, regenerated in Task 3; AGENTS.md enumerates norms/decisions, not the CLI surface, so it should not drift — this is the guard against a second derived artifact surprising the merge gate).

---

## Out of scope (explicitly NOT doing)

- **`--scope` on `redeclare`** — scope is declared on the subsequent `plan ready`.
- **scope-amend-without-review** — needs a new event type + bypasses plan scope-adherence; unsound, violates the no-laundering discipline.
- **Slug validation** — `plan ready` does not pre-validate the slug either; an unknown slug fails as an illegal transition (exit 2), consistent with the sibling emitter. (Keeping symmetry; not adding a `validate_slug` guard the sibling lacks.)
- **cli-command-surface private spec edit** — reconcile-noted in the docstring, same as how `--tier-hint` divergence is handled.

## Self-host lifecycle scope (5 files)

1. `docs/plans/2026-07-06-plan-redeclare-cli-verb.md` (this plan)
2. `src/super_harness/cli/plan.py`
3. `tests/unit/cli/test_plan.py`
4. `docs/cli-reference.md` (derived — regenerated)
5. `scripts/gen_cli_reference.py` (exit-code map entry for the new verb — added by
   dogfooding `plan redeclare` to expand scope after the gap surfaced mid-implementation)
