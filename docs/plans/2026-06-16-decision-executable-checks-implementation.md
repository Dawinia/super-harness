# Decision Executable Checks (Tool B) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** A ratified decision can carry a runnable check + a counterexample; `decision
ratify` proves the check "bites" (passes on real code, fails on the counterexample) before
accepting it, and `decision check` runs every tier-1 check so code that violates a decision
is blocked (exit 2) — locally for the agent (`--changed`) and in CI (full).

**Architecture (design §4.2, resolved 2026-06-16):** Keep the pure layer pure. The check +
counterexample live **inline in the decision `.md` body** as two fenced blocks (` ```check `
and ` ```counterexample path=<rel> `), so Tool A's body hash locks them for free.
`core/decisions.py` parses the blocks into `Decision.check` / `Decision.counterexample`.
A **new** `core/check_runner.py` owns all impurity: the **two-sided bite-test** (pass side
runs the check against the real tree read-only; bite side runs it in a temp **sandbox** with
the counterexample injected) and the full/`--changed` run. `core/decision_check.py::run_check`
stays untouched (pure). `cli/decision.py` composes both into one `decision check`, runs the
bite-test inside `ratify` (+ a `--dry-run` agent self-test), and reports the hard:context
ratio. Exit semantics: non-zero check (incl. timeout/broken) is **fail-closed → exit 2**.

**Tech Stack:** Python 3.10+, click, PyYAML, pytest. `subprocess` (`sh -c`, timeout),
`shutil` (sandbox copy), `tempfile`. Reuses `core/anchor_scanner._list_files` (git ls-files
+ walk fallback) and `core/source_scope.load_source_scope`.

**Source map (verified against current `main` + the Tool A branch):**
- `src/super_harness/core/decisions.py` — `Decision` dataclass already carries
  `ratified_text_hash`; `parse_decision_file` populates fields; `normalize_body` /
  `compute_body_hash` exist. **Body is the SSOT** the blocks live in.
- `src/super_harness/core/decision_check.py` — `run_check` (pure: dangling +
  `integrity_violations` + `unhashed_ratified`). **Do not add subprocess here.**
- `src/super_harness/core/anchor_scanner.py` — `scan_sentinel_locations(root, file_globs,
  *, keyword, exclude_globs) -> dict[id, [(rel_file, line)]]`; private `_list_files(root)`
  (git ls-files, walk fallback) reusable for the sandbox copy + `--changed`.
- `src/super_harness/core/source_scope.py` — `load_source_scope(root) -> (include, exclude)`
  (defaults `["**/*"]` / `["docs/**"]`).
- `src/super_harness/cli/decision.py` — `ratify_cmd`, `check_cmd`; `ALWAYS_EXCLUDE =
  ["docs/decisions/**"]`, `ANCHOR_KEYWORD`. Exit codes from `super_harness.exit_codes`
  (`EXIT_OK=0`, `EXIT_VALIDATION=2`, `EXIT_NO_CONFIG=3`).
- `src/super_harness/cli/output.py` — `json_envelope(command, status, exit_code, data,
  errors)`, `Status = Literal["pass","fail","warning"]`.
- Tests: `tests/unit/core/`, `tests/unit/cli/`. `test_decision.py` has `_init(tmp_path)`
  (makes `.harness/`) and uses `CliRunner().invoke(main, ["--workspace", str(root), ...])`.

**Design ref:** `docs/plans/2026-06-12-decision-text-lock-design.md` §4, §4.2, §6.

**Scope boundary:** Tier-1 hard checks + the tier-3 "context, never gates" classification +
the ratio report (design §4 this-slice rungs). **Deferred (not here):** fixture-spill
counterexamples (+ their digest lock), tier-2 reviewable anchors, the change→route-to-review
trigger, per-check timeout override, `--changed-since <ref>`, PostToolUse hook auto-fire.

---

## Task 1: Parse the two body blocks → `Decision.check` + `Decision.counterexample`

The check + counterexample are fenced blocks in the body. Parsing is pure and lives in
`decisions.py`. `serialize_decision` is **unchanged** — the blocks are already part of
`body`, so they round-trip for free (and stay under the body hash).

**Files:**
- Modify: `src/super_harness/core/decisions.py`
- Test: `tests/unit/core/test_decisions.py`

**Step 1: Write the failing test**

```python
from super_harness.core.decisions import Counterexample, parse_check, parse_counterexample

BODY = (
    "Passwords must be stored with bcrypt - never MD5.\n\n"
    "```check\n! grep -rIn \"md5(.*password\" src/\n```\n\n"
    "```counterexample path=src/auth/legacy.py\npw = md5(user.password)\n```\n"
)


def test_parse_check_extracts_command():
    assert parse_check(BODY) == '! grep -rIn "md5(.*password" src/'


def test_parse_counterexample_extracts_path_and_content():
    ce = parse_counterexample(BODY)
    assert ce == Counterexample(path="src/auth/legacy.py", content="pw = md5(user.password)")


def test_no_blocks_returns_none():
    assert parse_check("just prose, tier-3 context.") is None
    assert parse_counterexample("just prose.") is None


def test_more_than_one_check_block_raises():
    import pytest
    two = "```check\na\n```\n```check\nb\n```\n"
    with pytest.raises(ValueError, match="at most one"):
        parse_check(two)


def test_counterexample_requires_path():
    import pytest
    with pytest.raises(ValueError, match="path="):
        parse_counterexample("```counterexample\npw = bad\n```\n")
```

Also assert the parsed fields land on the `Decision` via `parse_decision_file`:

```python
def test_decision_file_carries_parsed_check(tmp_path):
    from super_harness.core.decisions import parse_decision_file
    p = _write(tmp_path / "docs/decisions/d-pw.md",
               f"---\nid: d-pw\nstatus: proposed\n---\n{BODY}")
    d = parse_decision_file(p)
    assert d.check == '! grep -rIn "md5(.*password" src/'
    assert d.counterexample.path == "src/auth/legacy.py"
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/unit/core/test_decisions.py -k "parse_check or counterexample or carries_parsed" -v`
Expected: FAIL — `parse_check` / `Counterexample` not defined.

**Step 3: Write minimal implementation**

In `decisions.py` add a `Counterexample` dataclass and two pure parsers, then populate the
`Decision` in `parse_decision_file`. Use a fence regex (info string after ` ``` `):

```python
import re  # already imported

@dataclass
class Counterexample:
    path: str
    content: str


# A fenced block: ```<info>\n<body>\n```  (info = first line after the fence).
_FENCE_RE = re.compile(r"^```(?P<info>[^\n]*)\n(?P<inner>.*?)\n```", re.DOTALL | re.MULTILINE)


def _blocks(body: str, kind: str) -> list[re.Match[str]]:
    return [m for m in _FENCE_RE.finditer(body) if m.group("info").split()[:1] == [kind]]


def parse_check(body: str) -> str | None:
    ms = _blocks(body, "check")
    if not ms:
        return None
    if len(ms) > 1:
        raise ValueError("at most one ```check block per decision")
    return ms[0].group("inner").strip()


def parse_counterexample(body: str) -> Counterexample | None:
    ms = _blocks(body, "counterexample")
    if not ms:
        return None
    if len(ms) > 1:
        raise ValueError("at most one ```counterexample block per decision")
    info = ms[0].group("info")
    m = re.search(r"\bpath=(\S+)", info)
    if not m:
        raise ValueError("```counterexample block needs path=<relative-path>")
    return Counterexample(path=m.group(1), content=ms[0].group("inner").strip())
```

Add the fields to the dataclass (after `body`, since they are *derived* from it — keep them
out of `serialize_decision`'s frontmatter loop):

```python
    body: str = ""
    path: Path | None = None
    check: str | None = None
    counterexample: Counterexample | None = None
```

In `parse_decision_file`, after computing `body`, parse and pass them:

```python
    return Decision(
        ...,
        body=body,
        path=path,
        check=parse_check(body),
        counterexample=parse_counterexample(body),
    )
```

> Note: `serialize_decision` iterates a fixed frontmatter tuple and then writes `body`
> verbatim — leave it untouched so `check`/`counterexample` are never double-written.

> **Fence must be column-0 (`_FENCE_RE` is `^```` -anchored).** CommonMark allows up to 3
> spaces of fence indent; this parser does not. An indented ` ```check ` block parses as
> *no check* → tier-3 → ratifies with **no bite-test**, silently bypassing anti-hollow.
> This is acceptable (authors write column-0 fences) but **must be stated** so it isn't a
> hidden hole. Add a test `test_indented_fence_is_not_a_check` asserting an indented block
> yields `parse_check(...) is None`, making the limitation explicit and intentional.

> **Reason-string coupling:** the CLI tests in Task 5 assert substrings (`"did not bite"`,
> `"counterexample"`, `"current code"`) that are hard-coded in `bite_test`'s `reason` /
> `ratify_cmd`'s error text. Keep the wording stable, or these distant tests break on a
> reword. (Acceptable for this slice; flagged so it's a conscious coupling.)

**Step 4: Run test to verify it passes**

Run: `pytest tests/unit/core/test_decisions.py -v`
Expected: PASS (new + all existing).

**Step 5: Commit**

```bash
git add src/super_harness/core/decisions.py tests/unit/core/test_decisions.py
git commit -m "feat(decisions): parse inline check + counterexample body blocks"
```

---

## Task 2: `run_one_check` — execute one check command against a tree

The single impure primitive: `sh -c <cmd>` with a timeout, in a given cwd, fail-closed.
Lives in the **new** `core/check_runner.py`. Exit 0 = satisfied; anything else (incl.
timeout / command-not-found / broken check) = not satisfied.

**Files:**
- Create: `src/super_harness/core/check_runner.py`
- Test: `tests/unit/core/test_check_runner.py`

**Step 1: Write the failing test**

```python
from super_harness.core.check_runner import CheckRun, run_one_check


def test_zero_exit_is_satisfied(tmp_path):
    r = run_one_check("true", cwd=tmp_path)
    assert r.satisfied is True and r.exit_code == 0


def test_nonzero_exit_is_not_satisfied(tmp_path):
    r = run_one_check("false", cwd=tmp_path)
    assert r.satisfied is False and r.exit_code != 0


def test_grep_runs_in_given_cwd(tmp_path):
    (tmp_path / "f.py").write_text("pw = md5(user.password)\n")
    # the decision's check: "no md5 on passwords" → grep MUST find it here → ! grep => fail
    r = run_one_check('! grep -rIn "md5(.*password" .', cwd=tmp_path)
    assert r.satisfied is False


def test_timeout_is_not_satisfied(tmp_path):
    r = run_one_check("sleep 5", cwd=tmp_path, timeout=1)
    assert r.satisfied is False and "timeout" in r.detail.lower()
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/unit/core/test_check_runner.py -v`
Expected: FAIL — module missing.

**Step 3: Write minimal implementation**

```python
"""Executable-check runner (design §4.2) — the impure half of Tool B.

`run_check` in decision_check.py stays pure; ALL subprocess / sandbox / git-diff
machinery lives here so the structural-integrity layer never imports subprocess.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

DEFAULT_TIMEOUT = 30  # seconds (per-check override deferred, design §4.2)


@dataclass
class CheckRun:
    satisfied: bool       # True iff the command exited 0
    exit_code: int        # -1 sentinel for timeout / spawn failure
    detail: str           # short human reason (stderr tail / "timeout" / "...")


def run_one_check(command: str, *, cwd: Path, timeout: int = DEFAULT_TIMEOUT) -> CheckRun:
    try:
        proc = subprocess.run(
            command, shell=True, cwd=str(cwd),
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return CheckRun(False, -1, f"timeout after {timeout}s")
    except OSError as e:  # shell missing etc.
        return CheckRun(False, -1, f"could not run: {e}")
    detail = (proc.stderr or proc.stdout or "").strip().splitlines()
    return CheckRun(proc.returncode == 0, proc.returncode, detail[-1] if detail else "")
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/unit/core/test_check_runner.py -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add src/super_harness/core/check_runner.py tests/unit/core/test_check_runner.py
git commit -m "feat(check-runner): run_one_check (sh -c, timeout, fail-closed)"
```

---

## Task 3: `build_sandbox` — copy in-scope tree + inject the counterexample

The bite side needs the check to "see" the counterexample without touching the real tree.
Copy the in-scope working tree into a tempdir and create the counterexample file there.

**Files:**
- Modify: `src/super_harness/core/check_runner.py`
- Test: `tests/unit/core/test_check_runner.py`

**Step 1: Write the failing test**

```python
from super_harness.core.check_runner import build_sandbox
from super_harness.core.decisions import Counterexample


def test_sandbox_copies_inscope_and_injects(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/app.py").write_text("clean = True\n")
    ce = Counterexample(path="src/auth/legacy.py", content="pw = md5(user.password)")
    with build_sandbox(tmp_path, ce) as sb:
        assert (sb / "src/app.py").read_text() == "clean = True\n"   # copied
        assert (sb / "src/auth/legacy.py").read_text() == "pw = md5(user.password)\n"
    assert not sb.exists()        # cleaned up on context exit


def test_sandbox_excludes_dot_dirs(tmp_path):
    (tmp_path / ".venv").mkdir(); (tmp_path / ".venv/x.py").write_text("junk\n")
    (tmp_path / "src").mkdir(); (tmp_path / "src/app.py").write_text("ok\n")
    ce = Counterexample(path="src/bad.py", content="bad")
    with build_sandbox(tmp_path, ce) as sb:
        assert not (sb / ".venv").exists()
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/unit/core/test_check_runner.py -k sandbox -v`
Expected: FAIL — `build_sandbox` not defined.

**Step 3: Write minimal implementation**

Reuse `anchor_scanner._list_files` (git ls-files; dot-segment-skipping walk fallback) +
`source_scope` so the sandbox matches what checks actually scan. `copy2` is COW on APFS.

```python
import shutil
import tempfile
from contextlib import contextmanager
from collections.abc import Iterator

from super_harness.core.anchor_scanner import _list_files, _matches_any
from super_harness.core.decisions import Counterexample
from super_harness.core.source_scope import load_source_scope


@contextmanager
def build_sandbox(workspace_root: Path, counterexample: Counterexample) -> Iterator[Path]:
    include, exclude = load_source_scope(workspace_root)
    tmp = Path(tempfile.mkdtemp(prefix="sh-bite-"))
    try:
        for f in _list_files(workspace_root):
            rel = f.relative_to(workspace_root)
            if not _matches_any(rel, include) or _matches_any(rel, exclude):
                continue
            dest = tmp / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dest)          # fcopyfile/COW on APFS, plain copy elsewhere
        ce_path = tmp / counterexample.path
        ce_path.parent.mkdir(parents=True, exist_ok=True)
        ce_path.write_text(counterexample.content + "\n", encoding="utf-8")
        yield tmp
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
```

> Known limitation (document, don't fix here): `_list_files` lists *tracked* files (git
> ls-files), so a brand-new **untracked** source file the agent just created is not copied
> into the sandbox. The bite side still works (the counterexample is injected explicitly);
> the pass side runs on the real tree anyway. CI runs against the committed PR tree.

**Step 4: Run test to verify it passes**

Run: `pytest tests/unit/core/test_check_runner.py -k sandbox -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add src/super_harness/core/check_runner.py tests/unit/core/test_check_runner.py
git commit -m "feat(check-runner): build_sandbox copies in-scope tree + injects counterexample"
```

---

## Task 4: `bite_test` — pass on real tree + bite in sandbox (with self-detection)

Compose Tasks 2-3 into the anti-hollow proof. Returns a structured verdict the CLI turns
into accept/reject. **Pass side = real tree (read-only); bite side = sandbox.**

**Files:**
- Modify: `src/super_harness/core/check_runner.py`
- Test: `tests/unit/core/test_check_runner.py`

**Step 1: Write the failing test**

```python
from super_harness.core.check_runner import bite_test
from super_harness.core.decisions import Counterexample

CHECK = '! grep -rIn "md5(.*password" src/'
CE = Counterexample(path="src/auth/legacy.py", content="pw = md5(user.password)")


def _clean_repo(root):
    (root / "src").mkdir(); (root / "src/app.py").write_text("clean = True\n")


def test_bite_test_accepts_a_real_check(tmp_path):
    _clean_repo(tmp_path)
    v = bite_test(tmp_path, CHECK, CE)
    assert v.ok is True
    assert v.pass_side.satisfied is True      # clean code passes
    assert v.bite_side.satisfied is False     # counterexample makes it fail


def test_bite_test_rejects_hollow_check(tmp_path):
    _clean_repo(tmp_path)
    v = bite_test(tmp_path, "true", CE)       # always-passing → never bites
    assert v.ok is False and "did not bite" in v.reason


def test_bite_test_rejects_check_failing_on_clean_code(tmp_path):
    # check is broken / too strict: fails even on clean code → pass side fails
    (tmp_path / "src").mkdir(); (tmp_path / "src/app.py").write_text("ok\n")
    v = bite_test(tmp_path, "false", CE)
    assert v.ok is False and "current code" in v.reason


def test_bite_test_detects_pollution(tmp_path):
    # an over-wide check that scans the decision .md (which holds the inline counterexample)
    _clean_repo(tmp_path)
    (tmp_path / "docs/decisions").mkdir(parents=True)
    (tmp_path / "docs/decisions/d-pw.md").write_text("```counterexample\npw = md5(user.password)\n```\n")
    wide = '! grep -rIn "md5(.*password" .'   # scans docs/ too
    v = bite_test(tmp_path, wide, CE)
    assert v.ok is False and "current code" in v.reason   # pass side fails on real tree
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/unit/core/test_check_runner.py -k bite_test -v`
Expected: FAIL — `bite_test` not defined.

**Step 3: Write minimal implementation**

```python
@dataclass
class BiteVerdict:
    ok: bool
    reason: str
    pass_side: CheckRun
    bite_side: CheckRun


def bite_test(
    workspace_root: Path, command: str, counterexample: Counterexample,
    *, timeout: int = DEFAULT_TIMEOUT,
) -> BiteVerdict:
    # Pass side: run the RAW command on the UNFILTERED real tree, read-only (cwd=repo_root,
    # no source_scope). This is deliberate — it is the ONLY reason pollution self-detection
    # works: an over-wide check (e.g. `grep . `) scans the inline counterexample sitting in
    # docs/decisions/<id>.md and fails here. Do NOT add source_scope filtering to the pass
    # side. (Same run a normal `decision check` does — the runner is shared.)
    p = run_one_check(command, cwd=workspace_root, timeout=timeout)
    if not p.satisfied:
        return BiteVerdict(False, f"check fails on current code ({p.detail}) - fix the "
                                  f"code, or scope the check away from the counterexample",
                           p, CheckRun(False, p.exit_code, ""))
    # Bite side: sandbox with the counterexample injected.
    with build_sandbox(workspace_root, counterexample) as sb:
        b = run_one_check(command, cwd=sb, timeout=timeout)
    if b.satisfied:
        return BiteVerdict(False, "check did not bite the counterexample "
                                  "(it passed with the bad snippet present)", p, b)
    return BiteVerdict(True, "bites", p, b)
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/unit/core/test_check_runner.py -k bite_test -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add src/super_harness/core/check_runner.py tests/unit/core/test_check_runner.py
git commit -m "feat(check-runner): two-sided bite_test (pass on real tree, bite in sandbox)"
```

---

## Task 5: `decision ratify` runs the bite-test (+ `--dry-run` self-test)

A tier-1 decision (has a `check` block) must pass the bite-test before ratify accepts it.
Anti-hollow: `check` present but no `counterexample` → reject. `--dry-run` runs the same
gate **without** writing status/hash — the agent's pre-proposal self-check.

**Files:**
- Modify: `src/super_harness/cli/decision.py` (`ratify_cmd`)
- Test: `tests/unit/cli/test_decision.py`

**Step 1: Write the failing test**

```python
TIER1 = (
    "Passwords never stored with MD5.\n\n"
    "```check\n! grep -rIn \"md5(.*password\" src/\n```\n\n"
    "```counterexample path=src/legacy.py\npw = md5(user.password)\n```\n"
)


def _seed_clean_src(root):
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "src/app.py").write_text("clean = True\n", encoding="utf-8")


def test_ratify_accepts_when_check_bites(tmp_path):
    root = _init(tmp_path); _seed_clean_src(root)
    _w(root / "docs/decisions/d-pw.md", f"---\nid: d-pw\nstatus: proposed\n---\n{TIER1}")
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "ratify", "d-pw"])
    assert r.exit_code == 0, r.output
    assert parse_decision_file(root / "docs/decisions/d-pw.md").status == "ratified"


def test_ratify_rejects_hollow_check(tmp_path):
    root = _init(tmp_path); _seed_clean_src(root)
    body = "Be safe.\n\n```check\ntrue\n```\n\n```counterexample path=src/x.py\nbad\n```\n"
    _w(root / "docs/decisions/d-h.md", f"---\nid: d-h\nstatus: proposed\n---\n{body}")
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "ratify", "d-h"])
    assert r.exit_code == 2
    assert "did not bite" in r.output
    assert parse_decision_file(root / "docs/decisions/d-h.md").status == "proposed"  # unchanged


def test_ratify_rejects_check_without_counterexample(tmp_path):
    root = _init(tmp_path); _seed_clean_src(root)
    body = "No md5.\n\n```check\n! grep -rIn md5 src/\n```\n"
    _w(root / "docs/decisions/d-n.md", f"---\nid: d-n\nstatus: proposed\n---\n{body}")
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "ratify", "d-n"])
    assert r.exit_code == 2 and "counterexample" in r.output


def test_dry_run_does_not_change_status(tmp_path):
    root = _init(tmp_path); _seed_clean_src(root)
    _w(root / "docs/decisions/d-pw.md", f"---\nid: d-pw\nstatus: proposed\n---\n{TIER1}")
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "ratify",
                                  "d-pw", "--dry-run"])
    assert r.exit_code == 0 and "bites" in r.output
    assert parse_decision_file(root / "docs/decisions/d-pw.md").status == "proposed"


def test_tier3_decision_ratifies_without_bite_test(tmp_path):
    # no check block → tier-3 context → ratify as before (Tool A only)
    root = _init(tmp_path)
    CliRunner().invoke(main, ["--workspace", str(root), "decision", "new", "d-c",
                              "--text", "Code should be elegant."])
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "ratify", "d-c"])
    assert r.exit_code == 0
```

Add the `_w` helper if not already present (write a file, making parents). Reuse the one in
the Tool A integrity tests if it exists.

**Step 2: Run test to verify it fails**

Run: `pytest tests/unit/cli/test_decision.py -k "bites or hollow or dry_run or without_counter or tier3" -v`
Expected: FAIL — no `--dry-run` option / no bite-test wiring.

**Step 3: Write minimal implementation**

Add the option + bite-test to `ratify_cmd`, before the status flip:

```python
from super_harness.core.check_runner import bite_test

@decision_group.command("ratify")
@click.argument("decision_id")
@click.option("--dry-run", is_flag=True, help="Run the bite-test only; do not ratify.")
@click.pass_context
def ratify_cmd(ctx: click.Context, decision_id: str, dry_run: bool) -> None:
    root = _resolve(ctx, "decision ratify")
    d = _load_one(root, "decision ratify", decision_id)
    if d.status not in ("proposed", "ratified"):
        ...  # unchanged guard

    if d.check is not None:                       # tier-1 → must prove it bites
        if d.counterexample is None:
            click.echo(format_error(subcommand="decision ratify",
                       message=f"{decision_id!r} has a check but no counterexample",
                       hint="Add a ```counterexample path=<rel> block, or remove the check."),
                       err=True)
            sys.exit(EXIT_VALIDATION)
        verdict = bite_test(root, d.check, d.counterexample)
        if not verdict.ok:
            click.echo(f"BITE-TEST FAILED: {verdict.reason}", err=True)
            sys.exit(EXIT_VALIDATION)
        click.echo(f"bite-test: {verdict.reason}")
        if dry_run:
            sys.exit(EXIT_OK)
    elif dry_run:
        click.echo("no check block (tier-3 context) - nothing to bite-test")
        sys.exit(EXIT_OK)

    d.status = "ratified"
    d.ratified_by = resolve_identity(root)
    d.ratified_at = utc_now_iso()
    d.ratified_text_hash = compute_body_hash(d.body)
    write_decision(d)
    click.echo(f"ratified {decision_id} (by {d.ratified_by})")
    sys.exit(EXIT_OK)
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/unit/cli/test_decision.py -v`
Expected: PASS (new + all existing).

**Step 5: Commit**

```bash
git add src/super_harness/cli/decision.py tests/unit/cli/test_decision.py
git commit -m "feat(decision): ratify proves the check bites (+ --dry-run self-test)"
```

---

## Task 6: `run_executable_checks` — full + `--changed` over all tier-1 decisions

Run every ratified tier-1 decision's check against the **real tree** (read-only). `--changed`
narrows to checks whose anchored files moved. Split the git-diff extraction (impure) from the
selection logic (pure) so the selection is unit-testable without a git repo.

**Files:**
- Modify: `src/super_harness/core/check_runner.py`
- Test: `tests/unit/core/test_check_runner.py`

**Step 1: Write the failing test**

```python
from super_harness.core.check_runner import (
    CheckFailure, run_executable_checks, select_changed,
)
from super_harness.core.decisions import Decision, Counterexample


def _ratified(did, check):
    return Decision(id=did, status="ratified", check=check,
                    counterexample=Counterexample(path="src/x.py", content="bad"))


def test_select_changed_keeps_only_touched_anchors():
    decisions = [_ratified("d-a", "true"), _ratified("d-b", "true")]
    anchor_map = {"d-a": [("src/a.py", 1)], "d-b": [("src/b.py", 1)]}
    changed = {"src/a.py"}
    ids = {d.id for d in select_changed(decisions, anchor_map, changed)}
    assert ids == {"d-a"}


def test_run_executable_checks_flags_violation(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/bad.py").write_text("pw = md5(user.password)\n")
    decisions = [_ratified("d-pw", '! grep -rIn "md5(.*password" src/')]
    failures = run_executable_checks(tmp_path, decisions)
    assert [f.id for f in failures] == ["d-pw"]
    assert isinstance(failures[0], CheckFailure)


def test_run_executable_checks_clean_is_empty(tmp_path):
    (tmp_path / "src").mkdir(); (tmp_path / "src/ok.py").write_text("clean = True\n")
    decisions = [_ratified("d-pw", '! grep -rIn "md5(.*password" src/')]
    assert run_executable_checks(tmp_path, decisions) == []


def test_only_ratified_tier1_run(tmp_path):
    proposed = Decision(id="d-p", status="proposed", check="false",
                        counterexample=Counterexample("src/x.py", "b"))
    tier3 = Decision(id="d-c", status="ratified", check=None)
    assert run_executable_checks(tmp_path, [proposed, tier3]) == []
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/unit/core/test_check_runner.py -k "select_changed or executable_checks or tier1" -v`
Expected: FAIL — symbols not defined.

**Step 3: Write minimal implementation**

```python
@dataclass
class CheckFailure:
    id: str
    exit_code: int
    detail: str


def select_changed(decisions, anchor_map, changed_files):
    out = []
    for d in decisions:
        files = {f for f, _ln in anchor_map.get(d.id, [])}
        if files & changed_files:
            out.append(d)
    return out


def run_executable_checks(workspace_root, decisions, *, timeout=DEFAULT_TIMEOUT):
    failures: list[CheckFailure] = []
    for d in decisions:
        if d.status != "ratified" or d.check is None:
            continue
        run = run_one_check(d.check, cwd=workspace_root, timeout=timeout)
        if not run.satisfied:
            failures.append(CheckFailure(id=d.id, exit_code=run.exit_code, detail=run.detail))
    failures.sort(key=lambda f: f.id)
    return failures


def changed_files(workspace_root: Path) -> set[str] | None:
    """Working-tree changes vs HEAD plus untracked-not-ignored (design §4.2).

    Returns None if not a git repo / git unavailable → caller falls back to FULL
    (never silently under-run; full is the safe direction)."""
    try:
        diff = subprocess.run(["git", "diff", "--name-only", "HEAD"], cwd=str(workspace_root),
                              capture_output=True, text=True, check=True)
        others = subprocess.run(["git", "ls-files", "--others", "--exclude-standard"],
                                cwd=str(workspace_root), capture_output=True, text=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return {ln for ln in (diff.stdout + others.stdout).splitlines() if ln.strip()}
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/unit/core/test_check_runner.py -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add src/super_harness/core/check_runner.py tests/unit/core/test_check_runner.py
git commit -m "feat(check-runner): run_executable_checks + select_changed + changed_files"
```

---

## Task 7: `decision check` composes everything (+ `--changed`, ratio, JSON, exit)

One command for agent / human / CI. Pure `run_check` first (referential + integrity); then
executable checks (full or `--changed`); then the hard:context ratio. Exit priority: record
errors (3) > integrity violations (2) > check failures (2) > dangling-up (2) > warnings (0).

**Files:**
- Modify: `src/super_harness/cli/decision.py` (`check_cmd`)
- Test: `tests/unit/cli/test_decision.py`

**Step 1: Write the failing test**

```python
def _ratify_tier1(root, did="d-pw"):
    _seed_clean_src(root)
    _w(root / f"docs/decisions/{did}.md", f"---\nid: {did}\nstatus: proposed\n---\n{TIER1}")
    CliRunner().invoke(main, ["--workspace", str(root), "decision", "ratify", did])


def test_check_blocks_when_code_violates_decision(tmp_path):
    root = _init(tmp_path); _ratify_tier1(root)
    (root / "src/bad.py").write_text("pw = md5(user.password)\n")   # now violate it
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "check"])
    assert r.exit_code == 2
    assert "CHECK-FAILED" in r.output and "d-pw" in r.output


def test_check_green_when_code_honors_decision(tmp_path):
    root = _init(tmp_path); _ratify_tier1(root)
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "check"])
    assert r.exit_code == 0


def test_check_json_has_failures_and_ratio(tmp_path):
    root = _init(tmp_path); _ratify_tier1(root)
    (root / "src/bad.py").write_text("pw = md5(user.password)\n")
    r = CliRunner().invoke(main, ["--workspace", str(root), "--json", "decision", "check"])
    payload = json.loads(r.output)
    assert payload["data"]["check_failures"][0]["id"] == "d-pw"
    assert payload["data"]["hard_context"] == {"hard": 1, "context": 0}
    assert payload["status"] == "fail"


def test_changed_nongit_falls_back_to_full(tmp_path):
    # not a git repo → changed_files None → falls back to FULL (still blocks; never under-run)
    root = _init(tmp_path); _ratify_tier1(root)
    (root / "src/bad.py").write_text("pw = md5(user.password)\n")
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "check", "--changed"])
    assert r.exit_code == 2   # fallback-to-full caught it


def test_changed_runs_touched_anchor_and_skips_untouched(tmp_path):
    """The real git --changed path: a per-file check anchored in a-file runs only when
    a-file moved; a violation in an UNtouched, unanchored-by-the-changed-set file is
    skipped (the documented honest miss, design §4.2)."""
    import subprocess
    root = _init(tmp_path)
    (root / "src").mkdir()
    # two decisions, each a per-file check, each anchored in its own file
    def mk(did, fname):
        body = (f"No BAD in {fname}.\n\n```check\n! grep -rIn BAD src/{fname}\n```\n\n"
                f"```counterexample path=src/{fname}\nBAD\n```\n")
        (root / f"src/{fname}").write_text(f"# @decision:{did}\nclean = True\n")
        _w(root / f"docs/decisions/{did}.md", f"---\nid: {did}\nstatus: proposed\n---\n{body}")
    mk("d-a", "a.py"); mk("d-b", "b.py")
    # make it a git repo and commit the clean state (HEAD baseline)
    sh = lambda *a: subprocess.run(["git", *a], cwd=root, capture_output=True, check=True)
    sh("init"); sh("config", "user.email", "t@t"); sh("config", "user.name", "t")
    sh("add", "-A"); sh("commit", "-m", "clean")
    inv = lambda *a: CliRunner().invoke(main, ["--workspace", str(root), "decision", *a])
    inv("ratify", "d-a"); inv("ratify", "d-b")
    # introduce BAD into BOTH files, but only a.py is "touched" relative to HEAD... so touch both,
    # commit b.py's change so it is NOT in `git diff HEAD`, leave a.py uncommitted (changed)
    (root / "src/b.py").write_text("# @decision:d-b\nBAD\n"); sh("add", "src/b.py"); sh("commit", "-m", "b")
    (root / "src/a.py").write_text("# @decision:d-a\nBAD\n")          # uncommitted → in diff HEAD
    r = inv("check", "--changed")
    assert r.exit_code == 2 and "d-a" in r.output      # touched anchor's check ran + caught
    assert "d-b" not in r.output                        # committed (untouched-vs-HEAD) → skipped (honest miss)
    # sanity: full run catches both
    assert "d-b" in inv("check").output
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/unit/cli/test_decision.py -k "violates or honors or failures_and_ratio or changed_skips" -v`
Expected: FAIL — no check execution / `--changed` / ratio.

**Step 3: Write minimal implementation**

In `check_cmd`, add `--changed`, compute the executable failures + ratio, fold into status:

```python
from super_harness.core.check_runner import (
    changed_files, run_executable_checks, select_changed,
)
from super_harness.core.decisions import load_decisions

@decision_group.command("check")
@click.option("--changed", is_flag=True, help="Only run checks whose anchored files moved.")
@click.pass_context
def check_cmd(ctx: click.Context, changed: bool) -> None:
    root = _resolve(ctx, "decision check")
    result = run_check(root)                       # pure layer, unchanged

    decisions, _ = load_decisions(root)
    ratified_tier1 = [d for d in decisions if d.status == "ratified" and d.check]
    to_run = ratified_tier1
    if changed:
        cf = changed_files(root)
        if cf is not None:                        # None → not a git repo → run FULL
            include, exclude = load_source_scope(root)
            amap = scan_sentinel_locations(root, file_globs=include, keyword=ANCHOR_KEYWORD,
                                           exclude_globs=exclude + ALWAYS_EXCLUDE)
            to_run = select_changed(ratified_tier1, amap, cf)
    check_failures = run_executable_checks(root, to_run)

    hard = len(ratified_tier1)
    context = sum(1 for d in decisions if d.status == "ratified" and not d.check)

    if result.errors:
        exit_code, status = EXIT_NO_CONFIG, "fail"
    elif result.integrity_violations or check_failures or result.dangling_up:
        exit_code, status = EXIT_VALIDATION, "fail"
    elif result.dangling_down or result.unhashed_ratified:
        exit_code, status = EXIT_OK, "warning"
    else:
        exit_code, status = EXIT_OK, "pass"
```

**The two output branches are explicit (this is the most-tested integration point — do not
guess which branch).** The existing `check_cmd` has `if ctx.obj.get("json"): ... else: ...`.

**(a) JSON branch** — add the two keys **inside the existing `data={...}` dict** (alongside
`dangling_up`/`dangling_down`/`integrity_violations`/`unhashed_ratified`), nothing else
moves:

```python
                data={
                    # ... existing four keys unchanged ...
                    "check_failures": [
                        {"id": f.id, "exit_code": f.exit_code, "detail": f.detail}
                        for f in check_failures
                    ],
                    "hard_context": {"hard": hard, "context": context},
                },
```

**(b) text branch (the `else:`)** — add the `CHECK-FAILED` loop **right after** the existing
`integrity_violations` loop (so order is: errors → integrity → check-failures → dangling-up),
and the ratio line **just before** the final `if status == "pass": click.echo(...)`:

```python
        for f in check_failures:
            click.echo(f"CHECK-FAILED @decision:{f.id} (exit {f.exit_code}: {f.detail})",
                       err=True)
        # ... existing dangling-up / dangling-down / unhashed loops ...
        ratio = f" ({round(100 * hard / (hard + context))}% hard)" if hard + context else ""
        click.echo(f"hard:context = {hard}:{context}{ratio}")
        if status == "pass":
            click.echo("decision check: clean")
```

> `test_check_json_envelope` asserts sub-keys **by presence, not exclusivity**, so the two
> additive `data` keys do not break it (confirmed against the current test).

**Step 4: Run test to verify it passes**

Run: `pytest tests/unit/cli/test_decision.py -v`
Expected: PASS (new + all existing — confirm `test_check_json_envelope` still green; new keys
are additive inside `data`).

**Step 5: Commit**

```bash
git add src/super_harness/cli/decision.py tests/unit/cli/test_decision.py
git commit -m "feat(decision-check): run executable checks + --changed + hard:context ratio"
```

---

## Task 8: End-to-end lifecycle test (the §0 story, code side)

**Files:**
- Test: `tests/unit/cli/test_decision.py`

**Step 1: Write the failing test**

```python
def test_executable_check_full_lifecycle(tmp_path):
    root = _init(tmp_path); _seed_clean_src(root)
    inv = lambda *a: CliRunner().invoke(main, ["--workspace", str(root), "decision", *a])
    # 1. author a tier-1 decision (prose + check + counterexample) + ratify → bite-test passes
    _w(root / "docs/decisions/d-pw.md", f"---\nid: d-pw\nstatus: proposed\n---\n{TIER1}")
    assert inv("ratify", "d-pw").exit_code == 0
    assert inv("check").exit_code == 0                      # code honors it

    # 2. code starts violating the decision → check blocks
    (root / "src/bad.py").write_text("pw = md5(user.password)\n")
    assert inv("check").exit_code == 2

    # 3. fix the code → green again (no re-ratify needed; the claim never changed)
    (root / "src/bad.py").write_text("pw = bcrypt(user.password)\n")
    assert inv("check").exit_code == 0
```

**Step 2: Run** — should pass given Tasks 1-7. If it fails, fix the underlying task.

**Step 3: Commit**

```bash
git add tests/unit/cli/test_decision.py
git commit -m "test(decision): end-to-end executable-check lifecycle (honor->violate->fix)"
```

---

## Final verification (before PR)

- `pytest -q` — full suite green (state the count, per project discipline).
- `ruff check` — clean.
- **Dogfood on the branch** (project discipline): author a real tier-1 decision in this
  repo with a check + counterexample, `decision ratify --dry-run` it, watch the bite-test
  pass; introduce a violation, `decision check` blocks; fix, green. Confirm the
  hard:context line + JSON `check_failures`/`hard_context` keys render. Verify the existing
  CI `decision check` job now also runs executable checks (no new workflow).
- Confirm `decision check --changed` against the real repo runs a subset and that a
  non-git checkout falls back to full (no silent under-run).
- Update `private/OPEN-ITEMS.md` SLICE-4: move Tool B from BLOCKED-on-design to
  DONE/this-PR; keep fixture-spill, tier-2, change→route, hook auto-fire as deferred.

---

## Self-host merge-gate (per project discipline, see memory)

This PR runs through the repo's own SuperpowersAdapter gate. To pass merge:
- `plan ready --scope` must cover **every** changed file (the 4 source files + their tests +
  this plan + the design doc + OPEN-ITEMS). A lifecycle without full scope coverage is
  blocked (PR #38 hit this).
- Run the full lifecycle (`change start` → `plan ready --scope ...` → implement →
  `verify` → `attest write`) and submit the attestation, mirroring the Tool A PR (#40).
