# Tier-2 Reviewable Anchors Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Build the middle rung of the decision-conformance strength ladder — a decision that carries a body-hash-locked acceptance criterion but no runnable check binds by routing its anchored code's changes into a recorded, attributable re-review, mechanically forced at the merge boundary.

**Architecture:** A new ` ```review ` block in the decision body (locked for free by `ratified_text_hash`) declares tier-2. Anchored files are content-fingerprinted (sha256) at `decision reconcile`, stored in the decision's frontmatter (`reconciled_anchors`). A standing invariant in `run_check` (pure layer: filesystem reads but no subprocess/git) flips a decision `suspect` when any anchored file's fingerprint diverges. `decision check` warns on suspect (routing, §12.4-safe because mechanical); `decision check --gate-reconcile` (CI merge boundary only) exits 2 on suspect (teeth — forces the re-review to *happen*, never judges correctness). `decision reconcile`/`decision betray` record the attributable verdict.

**Tech Stack:** Python 3.10+, click, PyYAML, pytest. No new dependencies.

**Design SSOT:** `docs/plans/2026-06-20-tier-2-reviewable-anchors-design.md` (read §2–§8 before starting). Umbrella SSOT: `docs/plans/2026-06-05-decision-conformance-harness-design.md` §7.2/§12.3/§12.4/§6.

**Verification discipline (every task):** run tests with the project venv on PATH —
`PATH="$(pwd)/.venv/bin:$PATH" pytest ...`. Bare `pytest` is missing dev deps. NEVER
`uv run` inside the repo (re-points `.venv`, drops dev deps). One commit per task.

**Files in play:**
- `src/super_harness/core/decisions.py` — Decision model, parse/serialize (Tasks 1–2)
- `src/super_harness/core/decision_check.py` — `run_check` pure layer + suspect (Task 3)
- `src/super_harness/cli/decision.py` — check output/flag + reconcile/betray verbs (Tasks 4–7)
- `scripts/gen_cli_reference.py` + `docs/cli-reference.md` — derived doc (Task 8)
- Tests: `tests/unit/core/test_decisions.py`, `tests/unit/core/test_decision_check.py`,
  `tests/unit/cli/test_decision.py`

---

## Test harness contract (READ — the task stubs depend on this)

**Real CLI invocation pattern** (NOT `obj={...}` — that placeholder is wrong). The
workspace is threaded via the **global `--workspace` flag**, and `--json` is a **global
flag that goes BEFORE the subcommand**, both on the root `main` group:

```python
from click.testing import CliRunner
from super_harness.cli import main

# plain:
CliRunner().invoke(main, ["--workspace", str(root), "decision", "check", "--gate-reconcile"])
# json:
CliRunner().invoke(main, ["--workspace", str(root), "--json", "decision", "check"])
```

**Shared seed helpers** (the `reconciled_anchors=` kwarg requires Task 2's Decision field +
serializer — these helpers are first used in Task 3, so do not paste them into a Task-1
test. Put in the relevant test modules; Task 4/5 fixtures seed the
baseline by writing the decision file DIRECTLY via `write_decision` — they must NOT depend
on the `decision reconcile` verb, which only exists from Task 6, to keep every task's tests
fail-then-pass with no forward dependency):

```python
from super_harness.core.decisions import (
    Decision, write_decision, parse_decision_file, decisions_dir,
)
from super_harness.core.decision_check import fingerprint_file  # available from Task 3

def _seed_tier2(root, *, baseline: str = "none", changed: bool = False):
    """baseline: 'none' (unreconciled) | 'match' (reconciled-clean) | 'stale' (suspect).
    Writes a ratified tier-2 decision d-t2 + an anchored src file, seeding
    reconciled_anchors directly in frontmatter (no reconcile-verb dependency)."""
    (root / ".harness").mkdir(parents=True, exist_ok=True)
    (root / "docs" / "decisions").mkdir(parents=True, exist_ok=True)
    (root / "src").mkdir(parents=True, exist_ok=True)
    src = root / "src" / "x.py"
    src.write_text("v = 1  # @decision:d-t2\n", encoding="utf-8")
    anchors = None
    if baseline == "match":
        anchors = {"src/x.py": fingerprint_file(root, "src/x.py")}
    elif baseline == "stale":
        anchors = {"src/x.py": "sha256:" + "0" * 64}  # well-formed but != current → suspect
    d = Decision(
        id="d-t2", status="ratified", ratified_by="seed@x", ratified_at="2026-06-20T00:00:00Z",
        body="Body.\n\n```review\ncrit\n```",
        path=decisions_dir(root) / "d-t2.md", reconciled_anchors=anchors,
    )
    # lock the body hash so the record is a well-formed ratified decision
    from super_harness.core.decisions import compute_body_hash
    d.ratified_text_hash = compute_body_hash(d.body)
    write_decision(d)
    if changed:
        src.write_text("v = 2  # @decision:d-t2\n", encoding="utf-8")
    return root
```

(`baseline="stale"` or `changed=True` ⇒ suspect; `"match"` ⇒ clean; `"none"` ⇒
unreconciled. A `.harness/` dir alone is enough for `find_harness_root`; the decision/anchor
tests don't need full `init`.) For a **tier-1** seed (reconcile/betray rejection tests),
write a decision whose body has a ` ```check ` block instead of ` ```review `. For a
**tier-3** seed, a ratified decision with neither block.

---

## Task 1: `parse_review` + `acceptance` field + tier classification

**Files:**
- Modify: `src/super_harness/core/decisions.py`
- Test: `tests/unit/core/test_decisions.py`

**Step 1: Write failing tests**

```python
# tests/unit/core/test_decisions.py
from super_harness.core.decisions import parse_review, decision_tier, Decision

def test_parse_review_extracts_criterion():
    body = "Prose.\n\n```review\nError responses must not leak stack traces.\n```\n"
    assert parse_review(body) == "Error responses must not leak stack traces."

def test_parse_review_absent_returns_none():
    assert parse_review("just prose, no block") is None

def test_parse_review_empty_block_returns_none():
    assert parse_review("```review\n\n```") is None

def test_parse_review_rejects_two_blocks():
    import pytest
    body = "```review\na\n```\n```review\nb\n```"
    with pytest.raises(ValueError):
        parse_review(body)

def test_tier1_when_check_present_even_with_review():
    d = Decision(id="d", status="ratified", check="echo ok", acceptance="crit")
    assert decision_tier(d) == 1

def test_tier2_when_review_only():
    d = Decision(id="d", status="ratified", acceptance="crit")
    assert decision_tier(d) == 2

def test_tier3_when_neither():
    d = Decision(id="d", status="ratified")
    assert decision_tier(d) == 3
```

**Step 2: Run to verify fail**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/core/test_decisions.py -k "review or tier" -v`
Expected: FAIL (ImportError: cannot import `parse_review` / `decision_tier`).

**Step 3: Implement**

In `decisions.py`, after `parse_counterexample`, add:

```python
def parse_review(body: str) -> str | None:
    ms = _blocks(body, "review")
    if not ms:
        return None
    if len(ms) > 1:
        raise ValueError("at most one ```review block per decision")
    stripped = ms[0].group("inner").strip()
    return stripped or None
```

Add `acceptance: str | None = None` to the `Decision` dataclass (after `counterexample`).
In `parse_decision_file`'s `Decision(...)` constructor add `acceptance=parse_review(body),`.

Add a module-level classifier (after the dataclass / helpers):

```python
def decision_tier(d: Decision) -> int:
    """1 = executable check (hard); 2 = reviewable acceptance criterion; 3 = context."""
    if d.check is not None:
        return 1
    if d.acceptance is not None:
        return 2
    return 3
```

**Step 4: Run to verify pass**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/core/test_decisions.py -k "review or tier" -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add src/super_harness/core/decisions.py tests/unit/core/test_decisions.py
git commit -m "feat(decisions): parse ```review block + tier classification (tier-2 birth)"
```

---

## Task 2: Reconcile/betray frontmatter fields + serialize/parse round-trip

**Files:**
- Modify: `src/super_harness/core/decisions.py`
- Test: `tests/unit/core/test_decisions.py`

**Step 1: Write failing tests**

```python
from super_harness.core.decisions import (
    Decision, serialize_decision, parse_decision_file, compute_body_hash, write_decision,
)

def test_reconcile_fields_roundtrip(tmp_path):
    p = tmp_path / "d-x.md"
    d = Decision(
        id="d-x", status="ratified", body="Body.\n\n```review\ncrit\n```",
        path=p,
        reconciled_anchors={"src/a.py": "sha256:aaa", "src/b.py": "sha256:bbb"},
        last_reconciled_by="alice@example.com",
        last_reconciled_at="2026-06-20T00:00:00Z",
        last_reconcile_kind="self",
    )
    write_decision(d)
    back = parse_decision_file(p)
    assert back.reconciled_anchors == {"src/a.py": "sha256:aaa", "src/b.py": "sha256:bbb"}
    assert back.last_reconciled_by == "alice@example.com"
    assert back.last_reconcile_kind == "self"

def test_betray_fields_roundtrip(tmp_path):
    p = tmp_path / "d-y.md"
    d = Decision(id="d-y", status="ratified", body="b", path=p,
                 last_betrayed_by="bob@x.com", last_betrayed_at="2026-06-20T00:00:00Z",
                 last_betray_justification="no longer masks 500s")
    write_decision(d)
    back = parse_decision_file(p)
    assert back.last_betray_justification == "no longer masks 500s"

def test_frontmatter_additions_do_not_change_body_hash():
    body = "Body.\n\n```review\ncrit\n```"
    h1 = compute_body_hash(body)
    d = Decision(id="d", status="ratified", body=body,
                 reconciled_anchors={"src/a.py": "sha256:aaa"})
    # serialize then re-parse body; hash must be stable (frontmatter excluded)
    assert compute_body_hash(body) == h1

def test_malformed_reconciled_anchors_rejected(tmp_path):
    import pytest
    p = tmp_path / "d-z.md"
    p.write_text("---\nid: d-z\nstatus: ratified\nreconciled_anchors: not-a-map\n---\nbody\n")
    with pytest.raises(ValueError):
        parse_decision_file(p)
```

**Step 2: Run to verify fail**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/core/test_decisions.py -k "roundtrip or hash or malformed" -v`
Expected: FAIL (unexpected keyword arg `reconciled_anchors` / fields dropped).

**Step 3: Implement**

Add to the `Decision` dataclass (after `acceptance`):

```python
    reconciled_anchors: dict[str, str] | None = None
    last_reconciled_by: str | None = None
    last_reconciled_at: str | None = None
    last_reconcile_kind: str | None = None
    last_betrayed_by: str | None = None
    last_betrayed_at: str | None = None
    last_betray_justification: str | None = None
```

Rewrite `serialize_decision` (widen type, add scalar keys, assign the dict as a dict):

```python
def serialize_decision(decision: Decision) -> str:
    fm: dict[str, object] = {"id": decision.id, "status": decision.status}
    for key in ("ratified_by", "ratified_at", "supersedes", "superseded_by",
                "ratified_text_hash", "last_reconciled_by", "last_reconciled_at",
                "last_reconcile_kind", "last_betrayed_by", "last_betrayed_at",
                "last_betray_justification"):
        val = getattr(decision, key)
        if val:
            fm[key] = val
    if decision.reconciled_anchors:
        fm["reconciled_anchors"] = dict(decision.reconciled_anchors)
    fm_text = yaml.safe_dump(fm, sort_keys=False).strip()
    return f"---\n{fm_text}\n---\n{decision.body}\n"
```

In `parse_decision_file`, before the `return Decision(...)`, validate the mapping:

```python
    raw_anchors = data.get("reconciled_anchors")
    if raw_anchors is not None and not isinstance(raw_anchors, dict):
        raise ValueError("reconciled_anchors must be a mapping of file -> fingerprint")
```

Add to the `Decision(...)` constructor:

```python
        reconciled_anchors=raw_anchors,
        last_reconciled_by=data.get("last_reconciled_by"),
        last_reconciled_at=data.get("last_reconciled_at"),
        last_reconcile_kind=data.get("last_reconcile_kind"),
        last_betrayed_by=data.get("last_betrayed_by"),
        last_betrayed_at=data.get("last_betrayed_at"),
        last_betray_justification=data.get("last_betray_justification"),
```

**Step 4: Run to verify pass**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/core/test_decisions.py -v`
Expected: PASS (all decisions tests, incl. existing ones).

**Step 5: Commit**

```bash
git add src/super_harness/core/decisions.py tests/unit/core/test_decisions.py
git commit -m "feat(decisions): persist reconcile/betray stamps in frontmatter (outside body-hash lock)"
```

---

## Task 3: Fingerprint helper + suspect computation in `run_check` (pure)

**Files:**
- Modify: `src/super_harness/core/decision_check.py`
- Test: `tests/unit/core/test_decision_check.py`

**Step 1: Write failing tests.** `ws` below is a seeded workspace root — use the
`_seed_tier2(tmp_path, baseline=..., changed=...)` helper from the Test harness contract
section (it returns the root) for the tier-2 cases, and the existing
`test_decision_check.py` scaffolding (build `docs/decisions/*.md` + anchored source) for
the tier-1/tier-3/retired control cases. These call `run_check(ws)` directly (core layer),
no CLI.

```python
# Helper in the test: write a ratified tier-2 decision + anchor a source file, then
# fingerprint it into reconciled_anchors to simulate a prior reconcile.
from super_harness.core.decision_check import run_check, fingerprint_file

def test_tier2_unreconciled_when_anchored_but_no_baseline(ws):
    # ratified tier-2 decision d-t2 (```review block, no check), no reconciled_anchors
    # source file src/x.py contains "# @decision:d-t2"
    res = run_check(ws)
    assert "d-t2" in res.unreconciled_tier2
    assert not res.suspect_tier2

def test_tier2_clean_when_baseline_matches(ws):
    # reconciled_anchors = {"src/x.py": fingerprint_file(ws, "src/x.py")}
    res = run_check(ws)
    assert not res.suspect_tier2 and not res.unreconciled_tier2

def test_tier2_suspect_when_anchored_file_changes(ws):
    # baseline stored, then src/x.py edited (append a line)
    res = run_check(ws)
    assert [s.id for s in res.suspect_tier2] == ["d-t2"]
    assert "src/x.py" in res.suspect_tier2[0].changed_files

def test_tier2_suspect_on_new_anchor_not_in_baseline(ws):
    # baseline covers src/x.py; a second file src/y.py also anchors d-t2 but absent from baseline
    res = run_check(ws)
    assert "src/y.py" in res.suspect_tier2[0].changed_files

def test_superseded_or_retired_tier2_never_suspect(ws):
    # d-t2 status flipped to retired, anchored + changed
    res = run_check(ws)
    assert not res.suspect_tier2 and not res.unreconciled_tier2

def test_tier1_and_tier3_untouched_by_suspect_logic(ws):
    res = run_check(ws)
    assert not res.suspect_tier2 and not res.unreconciled_tier2
```

**Step 2: Run to verify fail**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/core/test_decision_check.py -k "tier2 or suspect or retired" -v`
Expected: FAIL (no `fingerprint_file`; `CheckResult` has no `suspect_tier2`).

**Step 3: Implement**

In `decision_check.py`:

```python
import hashlib  # at top with other stdlib imports

from super_harness.core.decisions import (  # extend existing import
    RecordError, compute_body_hash, decision_tier, load_decisions,
)

@dataclass
class SuspectDecision:
    id: str
    changed_files: list[str]


def fingerprint_file(workspace_root: Path, rel: str) -> str:
    """sha256 of raw file bytes (byte-exact, binary-safe, subprocess/git-free).
    Deliberately NOT normalized — unlike `compute_body_hash` (which CRLF/whitespace-
    normalizes prose), any byte change to anchored code (incl. whitespace) should
    re-route the review (design §3/§4: coarse by construction, false-positives accepted).
    Shares only the `sha256:` prefix convention, not the normalization."""
    digest = hashlib.sha256((workspace_root / rel).read_bytes()).hexdigest()
    return f"sha256:{digest}"
```

Invariant note for the suspect loop: every path in `locations` was successfully
`read_text`'d by the scanner moments earlier (anchor_scanner.py skips unreadable files via
`continue`), so `read_bytes` here will not hit a missing/unreadable file in practice. Keep
the call direct (no defensive try/except) to match the codebase's fail-loud-on-surprise
style; the deleted-but-tracked corruption edge is the §9 defer, not handled here.

Add fields to `CheckResult`:

```python
    suspect_tier2: list[SuspectDecision] = field(default_factory=list)
    unreconciled_tier2: list[str] = field(default_factory=list)
```

In `run_check`, after `locations` / `effective_ratified` are computed (locations maps
`id -> [(rel_file, line)]`), add the tier-2 pass:

```python
    suspect_tier2: list[SuspectDecision] = []
    unreconciled_tier2: list[str] = []
    by_id = {d.id: d for d in decisions}
    for did in sorted(effective_ratified):
        d = by_id[did]
        if decision_tier(d) != 2:
            continue
        anchored = sorted({f for f, _ln in locations.get(did, [])})
        if not anchored:
            continue  # dangling-down (already warned); nothing to reconcile
        baseline = d.reconciled_anchors or {}
        if not baseline:
            unreconciled_tier2.append(did)
            continue
        changed = [f for f in anchored if fingerprint_file(workspace_root, f) != baseline.get(f)]
        if changed:
            suspect_tier2.append(SuspectDecision(id=did, changed_files=changed))
```

Note: only `effective_ratified` decisions are visited → superseded/retired/integrity-violated
are excluded by construction (satisfies the §4 lifecycle rule). Thread both new lists into
the `CheckResult(...)` return. Keep `.ok` UNCHANGED — tier-2 must not affect the pure
`ok` property (routing, not a gate; the gate decision lives in the CLI, Task 5).

**Step 4: Run to verify pass**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/core/test_decision_check.py -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add src/super_harness/core/decision_check.py tests/unit/core/test_decision_check.py
git commit -m "feat(decision-check): standing tier-2 suspect invariant (content fingerprint vs baseline)"
```

---

## Task 4: `decision check` default warn output + JSON threading

**Files:**
- Modify: `src/super_harness/cli/decision.py` (`check_cmd`)
- Test: `tests/unit/cli/test_decision.py`

**Step 1: Write failing tests** (use the Test harness contract above — `main`,
`--workspace`, `_seed_tier2` seeding the baseline directly, NO reconcile-verb dependency).

```python
import json
from click.testing import CliRunner
from super_harness.cli import main

def test_check_warns_on_suspect_tier2_exit0(tmp_path):
    root = _seed_tier2(tmp_path, baseline="match", changed=True)  # suspect
    result = CliRunner().invoke(main, ["--workspace", str(root), "decision", "check"])
    assert result.exit_code == 0
    assert "REVIEW-NEEDED" in result.output and "d-t2" in result.output

def test_check_json_exposes_tier2(tmp_path):
    root = _seed_tier2(tmp_path, baseline="match", changed=True)
    result = CliRunner().invoke(main, ["--workspace", str(root), "--json", "decision", "check"])
    data = json.loads(result.output)["data"]
    assert data["suspect_tier2"] and data["suspect_tier2"][0]["id"] == "d-t2"
    assert "unreconciled_tier2" in data
```

**Step 2: Run to verify fail**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/cli/test_decision.py -k "suspect or tier2" -v`
Expected: FAIL (no marker in output; JSON lacks keys).

**Step 3: Implement**

In `check_cmd`, the status/exit ladder currently treats `dangling_down`/`unhashed_ratified`
as the `warning` branch. Extend that branch to also fire on tier-2:

```python
    elif (result.dangling_down or result.unhashed_ratified
          or result.suspect_tier2 or result.unreconciled_tier2):
        exit_code, status = EXIT_OK, "warning"
```

Add text output (in the non-JSON branch, alongside the other warnings):

```python
        for did in result.unreconciled_tier2:
            click.echo(f"REVIEW-NEEDED {did} (tier-2, never reconciled — run "
                       f"`decision reconcile {did}`)")
        for s in result.suspect_tier2:
            files = ", ".join(s.changed_files)
            click.echo(f"REVIEW-NEEDED {s.id} (tier-2, anchored code changed: {files} — "
                       f"re-review then `decision reconcile {s.id}` / `decision betray {s.id}`)")
```

Add to the `--json` envelope `data` dict:

```python
                    "suspect_tier2": [
                        {"id": s.id, "changed_files": s.changed_files}
                        for s in result.suspect_tier2
                    ],
                    "unreconciled_tier2": list(result.unreconciled_tier2),
```

**Step 4: Run to verify pass**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/cli/test_decision.py -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add src/super_harness/cli/decision.py tests/unit/cli/test_decision.py
git commit -m "feat(decision-check): surface tier-2 suspect as non-blocking REVIEW-NEEDED warning (routing)"
```

---

## Task 5: `--gate-reconcile` flag (merge-boundary teeth)

**Files:**
- Modify: `src/super_harness/cli/decision.py` (`check_cmd`)
- Test: `tests/unit/cli/test_decision.py`

**Step 1: Write failing tests**

```python
def test_gate_reconcile_blocks_suspect_tier2(tmp_path):
    root = _seed_tier2(tmp_path, baseline="match", changed=True)
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "check", "--gate-reconcile"])
    assert r.exit_code == 2

def test_gate_reconcile_blocks_unreconciled_tier2(tmp_path):
    root = _seed_tier2(tmp_path, baseline="none")
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "check", "--gate-reconcile"])
    assert r.exit_code == 2

def test_gate_reconcile_passes_clean_tree(tmp_path):
    root = _seed_tier2(tmp_path, baseline="match")
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "check", "--gate-reconcile"])
    assert r.exit_code == 0

def test_default_check_still_exit0_on_same_suspect_tree(tmp_path):
    root = _seed_tier2(tmp_path, baseline="match", changed=True)
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "check"])
    assert r.exit_code == 0  # routing, not gate
```

**Step 2: Run to verify fail**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/cli/test_decision.py -k "gate_reconcile" -v`
Expected: FAIL (no such option).

**Step 3: Implement**

Add the flag to `check_cmd`:

```python
@click.option("--gate-reconcile", is_flag=True,
              help="Merge-boundary teeth: exit 2 on any suspect/unreconciled tier-2 "
                   "decision (default mode only warns).")
```

(thread `gate_reconcile: bool` through the signature). In the exit ladder, BEFORE the
`warning` branch, add a gate branch that only fires when the flag is set:

```python
    elif gate_reconcile and (result.suspect_tier2 or result.unreconciled_tier2):
        exit_code, status = EXIT_VALIDATION, "fail"
```

Keep ordering so tier-1 violations (integrity / check-fail / dangling-up) still take
precedence as exit 2. When `--gate-reconcile` fails, also print a one-line reason to
stderr so CI logs are legible:

```python
        for did in result.unreconciled_tier2:
            click.echo(f"GATE-RECONCILE {did}: tier-2 never reconciled", err=True)
        for s in result.suspect_tier2:
            click.echo(f"GATE-RECONCILE {s.id}: tier-2 anchored code changed, no reconcile",
                       err=True)
```

**Step 4: Run to verify pass**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/cli/test_decision.py -k "gate_reconcile or check" -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add src/super_harness/cli/decision.py tests/unit/cli/test_decision.py
git commit -m "feat(decision-check): --gate-reconcile merge-boundary teeth (block suspect tier-2)"
```

---

## Task 6: `decision reconcile` verb

**Files:**
- Modify: `src/super_harness/cli/decision.py`
- Test: `tests/unit/cli/test_decision.py`

**Step 1: Write failing tests**

```python
def test_reconcile_sets_baseline_and_clears_suspect(tmp_path):
    root = _seed_tier2(tmp_path, baseline="match", changed=True)  # suspect
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "reconcile", "d-t2"])
    assert r.exit_code == 0
    chk = CliRunner().invoke(main, ["--workspace", str(root), "decision", "check", "--gate-reconcile"])
    assert chk.exit_code == 0

def test_reconcile_first_time_on_unreconciled(tmp_path):
    root = _seed_tier2(tmp_path, baseline="none")
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "reconcile", "d-t2",
                                  "--kind", "independent"])
    assert r.exit_code == 0
    d = parse_decision_file(root / "docs/decisions/d-t2.md")
    assert d.reconciled_anchors and d.last_reconcile_kind == "independent"

def test_reconcile_rejects_non_tier2(tmp_path):
    # tier-1 seed: body carries a ```check block instead of ```review
    root = _seed_tier1(tmp_path, "d-tier1")
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "reconcile", "d-tier1"])
    assert r.exit_code == 2

def test_reconcile_rejects_tier3(tmp_path):
    root = _seed_tier3(tmp_path, "d-ctx")  # ratified, no check/review block
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "reconcile", "d-ctx"])
    assert r.exit_code == 2

def test_reconcile_clears_betray_stamps(tmp_path):
    root = _seed_tier2(tmp_path, baseline="match", changed=True)
    CliRunner().invoke(main, ["--workspace", str(root), "decision", "betray", "d-t2",
                              "--justification", "x"])
    CliRunner().invoke(main, ["--workspace", str(root), "decision", "reconcile", "d-t2"])
    d = parse_decision_file(root / "docs/decisions/d-t2.md")
    assert d.last_betrayed_by is None
```

(`_seed_tier1`/`_seed_tier3` are trivial variants of `_seed_tier2`: same ratified scaffold
but the body carries a ` ```check ` block / no block. The tier-1 seed needs no anchor.)

**Step 2: Run to verify fail** — `pytest ... -k reconcile`, expect "No such command 'reconcile'".

**Step 3: Implement**

```python
@decision_group.command("reconcile")
@click.argument("decision_id")
@click.option("--justification", default="", help="Why the code still satisfies the criterion.")
@click.option("--kind", type=click.Choice(["self", "independent"]), default="self",
              help="Disclosure: self-review (same actor as the change) or independent reviewer.")
@click.pass_context
def reconcile_cmd(ctx: click.Context, decision_id: str, justification: str, kind: str) -> None:
    """Record a tier-2 re-review verdict (code still satisfies D); re-stamp the baseline."""
    root = _resolve(ctx, "decision reconcile")
    d = _load_one(root, "decision reconcile", decision_id)
    if d.status != "ratified" or decision_tier(d) != 2:
        click.echo(format_error(subcommand="decision reconcile",
                   message=f"{decision_id!r} is not a ratified tier-2 (reviewable) decision",
                   hint="reconcile applies only to a ratified decision with a ```review block."),
                   err=True)
        sys.exit(EXIT_VALIDATION)
    include, exclude = load_source_scope(root)
    locs = scan_sentinel_locations(root, file_globs=include, keyword=ANCHOR_KEYWORD,
                                   exclude_globs=exclude + ALWAYS_EXCLUDE).get(decision_id, [])
    anchored = sorted({f for f, _ln in locs})
    if not anchored:
        click.echo(format_error(subcommand="decision reconcile",
                   message=f"{decision_id!r} has no code anchors to reconcile",
                   hint="Anchor the code with `# @decision:%s` first." % decision_id), err=True)
        sys.exit(EXIT_VALIDATION)
    d.reconciled_anchors = {f: fingerprint_file(root, f) for f in anchored}
    d.last_reconciled_by = resolve_identity(root)
    d.last_reconciled_at = utc_now_iso()
    d.last_reconcile_kind = kind
    d.last_betrayed_by = d.last_betrayed_at = d.last_betray_justification = None
    write_decision(d)
    click.echo(f"reconciled {decision_id} ({len(anchored)} file(s), kind={kind}, "
               f"by {d.last_reconciled_by})")
    sys.exit(EXIT_OK)
```

Add imports at top of `cli/decision.py`: `fingerprint_file` from `core.decision_check`,
`decision_tier` from `core.decisions`.

**Step 4: Run to verify pass** — `pytest tests/unit/cli/test_decision.py -k reconcile -v` → PASS.

**Step 5: Commit**

```bash
git add src/super_harness/cli/decision.py tests/unit/cli/test_decision.py
git commit -m "feat(decision): add `decision reconcile` verb (record tier-2 verdict, re-stamp baseline)"
```

---

## Task 7: `decision betray` verb

**Files:**
- Modify: `src/super_harness/cli/decision.py`
- Test: `tests/unit/cli/test_decision.py`

**Step 1: Write failing tests**

```python
def test_betray_stamps_fields_and_stays_suspect(tmp_path):
    root = _seed_tier2(tmp_path, baseline="match", changed=True)
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "betray", "d-t2",
                                  "--justification", "no longer masks 500s"])
    assert r.exit_code == 0
    d = parse_decision_file(root / "docs/decisions/d-t2.md")
    assert d.last_betray_justification == "no longer masks 500s"
    # baseline NOT advanced → still blocks the gate
    chk = CliRunner().invoke(main, ["--workspace", str(root), "decision", "check", "--gate-reconcile"])
    assert chk.exit_code == 2

def test_betray_requires_justification(tmp_path):
    root = _seed_tier2(tmp_path, baseline="match", changed=True)
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "betray", "d-t2"])
    assert r.exit_code == 2  # click missing required option

def test_betray_rejects_non_tier2(tmp_path):
    root = _seed_tier1(tmp_path, "d-tier1")
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "betray", "d-tier1",
                                  "--justification", "x"])
    assert r.exit_code == 2
```

**Step 2: Run to verify fail** — expect "No such command 'betray'".

**Step 3: Implement**

```python
@decision_group.command("betray")
@click.argument("decision_id")
@click.option("--justification", required=True,
              help="Why the changed code no longer satisfies the criterion.")
@click.pass_context
def betray_cmd(ctx: click.Context, decision_id: str, justification: str) -> None:
    """Record that the anchored code no longer satisfies D. Does NOT advance the
    baseline (D stays suspect); resolution is human-only (re-ratify or fix the code)."""
    root = _resolve(ctx, "decision betray")
    d = _load_one(root, "decision betray", decision_id)
    if d.status != "ratified" or decision_tier(d) != 2:
        click.echo(format_error(subcommand="decision betray",
                   message=f"{decision_id!r} is not a ratified tier-2 (reviewable) decision",
                   hint="betray applies only to a ratified decision with a ```review block."),
                   err=True)
        sys.exit(EXIT_VALIDATION)
    d.last_betrayed_by = resolve_identity(root)
    d.last_betrayed_at = utc_now_iso()
    d.last_betray_justification = justification
    write_decision(d)
    click.echo(f"betrayed {decision_id} (by {d.last_betrayed_by}) — stays suspect until a "
               f"human re-ratifies an updated decision or the code is fixed + reconciled")
    sys.exit(EXIT_OK)
```

**Step 4: Run to verify pass** — `pytest tests/unit/cli/test_decision.py -k betray -v` → PASS.

**Step 5: Commit**

```bash
git add src/super_harness/cli/decision.py tests/unit/cli/test_decision.py
git commit -m "feat(decision): add `decision betray` verb (record tier-2 escalation, baseline held)"
```

---

## Task 8: `_EXIT_CODES` entries + regenerate `docs/cli-reference.md`

**Files:**
- Modify: `scripts/gen_cli_reference.py` (`_EXIT_CODES`)
- Regenerate: `docs/cli-reference.md`
- Test: `tests/unit/scripts/test_gen_cli_reference.py` (only if a leaf-coverage test exists)

**Step 1: Add `_EXIT_CODES` entries** for the two new leaves + refresh `decision check`:

```python
    "decision reconcile": [
        "`0` success",
        "`2` not a ratified tier-2 decision, or no code anchors",
        "`3` no `.harness/`",
    ],
    "decision betray": [
        "`0` success",
        "`2` not a ratified tier-2 decision (or missing --justification)",
        "`3` no `.harness/`",
    ],
```

Update the `decision check` entry to note the gate mode:

```python
    "decision check": [
        "`0` clean, or only warnings (dangling-down / tier-2 review-needed)",
        "`2` dangling-up / integrity / tier-1 check failure; or (with --gate-reconcile) "
        "a suspect/unreconciled tier-2 decision",
        "`3` record/config error or no `.harness/`",
    ],
```

**Step 2: Regenerate the derived doc via the sanctioned in-repo command**

Run: `PATH="$(pwd)/.venv/bin:$PATH" super-harness doc check --fix`
(Do NOT model `python -m scripts.gen_cli_reference --emit > docs/cli-reference.md` — the
generator's own header notice asserts the regen command is `doc check --fix`, and
`test_gen_cli_reference.py` forbids the raw module path appearing in the notice. `--fix`
runs the registered generators from `.harness/derived-docs.yaml` and writes the docs.)

**Step 3: Verify no drift via the gate**

Run: `PATH="$(pwd)/.venv/bin:$PATH" super-harness doc check`
Expected: exit 0 (all derived docs in sync). Also run the generator test:
`PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/scripts/test_gen_cli_reference.py -v` → PASS.
(Note: no test enforces leaf-command coverage in `_EXIT_CODES` — an omitted verb silently
gets a generic `0/1` block. Correctness is enforced only by `test_real_cli_reference_is_in_sync`
byte-matching the generator output, so the `_EXIT_CODES` entries you add must match what you
actually ship.)

**Step 4: Commit**

```bash
git add scripts/gen_cli_reference.py docs/cli-reference.md
git commit -m "docs(cli-reference): regen for decision reconcile/betray + --gate-reconcile"
```

---

## Task 9: Lifecycle-interaction tests + full suite + dogfood

**Files:**
- Test: `tests/unit/core/test_decision_check.py`, `tests/unit/cli/test_decision.py`

**Step 1: Add lifecycle tests**

```python
def test_ratify_does_not_auto_reconcile(ws):
    # ratify a tier-2 decision that already has a code anchor → must be UNRECONCILED,
    # not silently baselined (first re-review must still be forced).
    # (run `decision ratify` then run_check; assert id in unreconciled_tier2)

def test_tier_flip_check_to_review_requires_reratify_then_unreconciled(ws):
    # a ratified tier-1 decision; edit body to drop ```check, add ```review →
    # decision check reports integrity violation (exit 2). After re-ratify it is tier-2
    # with no baseline → unreconciled until first reconcile.

def test_reconcile_then_unchanged_is_idempotent_noop(ws):
    # reconcile twice with no code change → second is a clean no-op, still exit 0,
    # fingerprints identical.
```

**Step 2: Run the FULL suite + linters + gates** (the real green bar):

```bash
PATH="$(pwd)/.venv/bin:$PATH" pytest -q
PATH="$(pwd)/.venv/bin:$PATH" ruff check src tests scripts
PATH="$(pwd)/.venv/bin:$PATH" mypy src
PATH="$(pwd)/.venv/bin:$PATH" super-harness decision check
PATH="$(pwd)/.venv/bin:$PATH" super-harness doc check
```
Expected: all green; full test count strictly greater than the pre-task baseline.

**Step 3: Dogfood the tier-2 lifecycle on a transient decision** (prove it bites end-to-end,
then remove the scratch decision/anchor so the repo stays clean — do NOT commit the scratch):

```bash
PATH="$(pwd)/.venv/bin:$PATH" bash -c '
set -e
cat > docs/decisions/d-scratch-t2.md <<EOF
---
id: d-scratch-t2
status: proposed
---
Scratch tier-2 for dogfood.

\`\`\`review
The scratch sentinel comment must stay present.
\`\`\`
EOF
# anchor a scratch source line
echo "# @decision:d-scratch-t2" >> src/super_harness/__init__.py
super-harness decision ratify d-scratch-t2
super-harness decision check --gate-reconcile; echo "expected exit 2 (unreconciled): $?"
super-harness decision reconcile d-scratch-t2
super-harness decision check --gate-reconcile; echo "expected exit 0 (reconciled): $?"
echo "# touch" >> src/super_harness/__init__.py
super-harness decision check --gate-reconcile; echo "expected exit 2 (suspect): $?"
super-harness decision check; echo "expected exit 0 (default warns): $?"
super-harness decision betray d-scratch-t2 --justification "dogfood"
super-harness decision check --gate-reconcile; echo "expected exit 2 (betray held): $?"
'
# CLEANUP — restore the scratch anchor + remove scratch decision
git checkout src/super_harness/__init__.py
rm docs/decisions/d-scratch-t2.md
```

Confirm the printed exit codes match the expectations in the echoes. Capture the transcript
in the task report.

**Step 4: Commit the lifecycle tests** (NOT the scratch artifacts):

```bash
git add tests/unit/core/test_decision_check.py tests/unit/cli/test_decision.py
git commit -m "test(tier-2): lifecycle interactions (no auto-reconcile, tier flip, idempotent reconcile)"
```

---

## After all tasks: self-host merge gate (separate task #5 in the session task list)

Per `[[project-self-host-pr-attest-scope]]`: `change start` → `plan ready --scope` covering
ALL changed files **including `docs/cli-reference.md`** (derived doc — the known scope trap)
→ `review approve --reviewer plan-reviewer` → implement-gate open → `review approve
--reviewer code-reviewer` → `implementation start` → `done` → `attest write` → commit the
attestation → `attest verify --base main --head HEAD` (local dry-run) → push → `gh pr create`
(body correct on first try; local token lacks read:org so PR title/body can't be edited after).

If a scope file was missed post-approval, re-root via the validated EventWriter
`plan_redeclared` event (no CLI verb exposes it yet — known harness gap), then re-`plan ready`
+ re-approve.
