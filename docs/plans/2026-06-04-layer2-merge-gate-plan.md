# Layer-2 CI merge gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an `attest write` + `attest verify` command pair so a CI job can re-derive, from a PR's git diff + committed per-change attestation files, whether every changed file went through a complete + correctly-ordered lifecycle whose scope covers it — and fail CI otherwise.

**Architecture:** A pure-function domain module (`engineering/attestation.py`) does the work (path canonicalization, `git --name-status` parsing, attestation extraction, and the verdict over diff+attestations, reusing `find_ordering_violations`/`derive_state` unchanged). A thin CLI group (`cli/attest.py`) keeps the `git`/filesystem boundary patchable. CI wiring runs `attest verify` on every PR.

**Tech Stack:** Python 3.10+, Click, pytest, PyYAML. Reuses `super_harness.core.{reducer,emit_validation,state}`.

**Design doc:** `docs/plans/2026-06-03-layer2-merge-gate-design.md` (read §2 security boundary + §4.2 algorithm before starting).

---

## File Structure

- Create `src/super_harness/engineering/attestation.py` — all pure logic + dataclasses.
- Create `tests/unit/engineering/test_attestation.py` — domain matrix.
- Create `src/super_harness/cli/attest.py` — `attest` Click group (`write` + `verify`); `git` boundary lives here.
- Create `tests/unit/cli/test_attest.py` — CLI tests (patch the git boundary).
- Modify `src/super_harness/cli/__init__.py` — register `attest_group`.
- Modify `src/super_harness/templates/super_harness_workflow.yml` — add `attest-verify` job.
- Modify `tests/unit/templates/test_super_harness_workflow.py` — assert the new job.
- Create `.github/workflows/merge-gate.yml` — run the gate on this repo's own PRs.
- Modify `docs/cli-reference.md` — regenerate (deterministic; CI drift-checked).

Constants (in `attestation.py`): `ATTESTATIONS_DIRNAME = ".harness/attestations"`, `MILESTONE_EVENTS = frozenset({"plan_approved","implementation_complete","code_review_passed"})`, `REQUIRED_STATE = "READY_TO_MERGE"`.

---

## Task 1: Path canonicalization

**Files:**
- Create: `src/super_harness/engineering/attestation.py`
- Test: `tests/unit/engineering/test_attestation.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/engineering/test_attestation.py
from super_harness.engineering.attestation import canonical_path


def test_canonical_path_strips_dot_slash_and_normalizes():
    assert canonical_path("./src/x.py") == "src/x.py"
    assert canonical_path("src/x.py") == "src/x.py"
    assert canonical_path("src/../src/x.py") == "src/x.py"
    assert canonical_path("docs/a/") == "docs/a"
    assert canonical_path("  src/x.py  ") == "src/x.py"
    assert canonical_path(".") == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PATH="$(pwd)/.venv/bin:$PATH" python -m pytest tests/unit/engineering/test_attestation.py -v`
Expected: FAIL (ImportError / module not found).

- [ ] **Step 3: Write minimal implementation**

```python
# src/super_harness/engineering/attestation.py
# L1 anchor (HG-DF C) — @capability:capability-merge-gate
"""Layer-2 CI merge gate (HG-DF item C) — attestation write + verify logic.

Pure-function domain layer. The CLI (`cli/attest.py`) owns the git/filesystem
boundary and calls these. Reuses `find_ordering_violations` + `derive_state`
unchanged. See docs/plans/2026-06-03-layer2-merge-gate-design.md.
"""
from __future__ import annotations

import json
import posixpath
from dataclasses import dataclass, field
from pathlib import Path

from super_harness.core.emit_validation import find_ordering_violations
from super_harness.core.reducer import derive_state

ATTESTATIONS_DIRNAME = ".harness/attestations"
MILESTONE_EVENTS: frozenset[str] = frozenset(
    {"plan_approved", "implementation_complete", "code_review_passed"}
)
REQUIRED_STATE = "READY_TO_MERGE"


def canonical_path(raw: str) -> str:
    """Normalize to repo-root-relative POSIX form (forward slashes, no leading
    './', collapsed '..'/'.'). Applied to BOTH git-diff output and stored
    scope.files so membership is spelling-independent."""
    p = raw.replace("\\", "/").strip()
    p = posixpath.normpath(p)
    return "" if p == "." else p
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PATH="$(pwd)/.venv/bin:$PATH" python -m pytest tests/unit/engineering/test_attestation.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/super_harness/engineering/attestation.py tests/unit/engineering/test_attestation.py
git commit -m "feat(attest): path canonicalization for merge-gate"
```

---

## Task 2: Parse `git diff --name-status`

**Files:**
- Modify: `src/super_harness/engineering/attestation.py`
- Test: `tests/unit/engineering/test_attestation.py`

- [ ] **Step 1: Write the failing test**

```python
from super_harness.engineering.attestation import DiffEntry, parse_name_status


def test_parse_name_status_handles_amd_and_rename():
    raw = "A\tsrc/new.py\nM\tdocs/x.md\nD\told.py\nR096\tsrc/a.py\tsrc/b.py\n\n"
    entries = parse_name_status(raw)
    assert entries == [
        DiffEntry(status="A", paths=("src/new.py",)),
        DiffEntry(status="M", paths=("docs/x.md",)),
        DiffEntry(status="D", paths=("old.py",)),
        DiffEntry(status="R096", paths=("src/a.py", "src/b.py")),
    ]


def test_parse_name_status_canonicalizes_paths():
    entries = parse_name_status("A\t./src/x.py\n")
    assert entries[0].paths == ("src/x.py",)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PATH="$(pwd)/.venv/bin:$PATH" python -m pytest tests/unit/engineering/test_attestation.py -k name_status -v`
Expected: FAIL (ImportError).

- [ ] **Step 3: Write minimal implementation** (append to `attestation.py`)

```python
@dataclass(frozen=True)
class DiffEntry:
    """One `git diff --name-status` row. `paths` is 1-tuple for A/M/D, 2-tuple
    (old, new) for renames/copies (R<score>/C<score>)."""

    status: str
    paths: tuple[str, ...]


def parse_name_status(raw: str) -> list[DiffEntry]:
    """Parse `git diff --name-status` output. Lines are tab-separated:
    `STATUS<TAB>PATH` (A/M/D) or `R<score><TAB>OLD<TAB>NEW` (rename/copy).
    Paths are canonicalized; blank lines skipped."""
    entries: list[DiffEntry] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        status = parts[0].strip()
        paths = tuple(canonical_path(p) for p in parts[1:] if p.strip())
        if not status or not paths:
            continue
        entries.append(DiffEntry(status=status, paths=paths))
    return entries
```

- [ ] **Step 4: Run test to verify it passes** — same `-k name_status` command. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(attest): parse git --name-status diff"
```

---

## Task 3: Extract + write attestation

**Files:**
- Modify: `src/super_harness/engineering/attestation.py`
- Test: `tests/unit/engineering/test_attestation.py`

- [ ] **Step 1: Write the failing test**

```python
import pytest
from super_harness.engineering.attestation import write_attestation, extract_change_events


def _ev(change_id: str, etype: str) -> str:
    import json
    return json.dumps({"change_id": change_id, "type": etype, "event_id": "x"})


def test_extract_filters_by_change_id(tmp_path):
    ef = tmp_path / "events.jsonl"
    ef.write_text(_ev("a", "intent_declared") + "\n" + _ev("b", "intent_declared") + "\n")
    assert extract_change_events(ef, "a") == [_ev("a", "intent_declared")]


def test_extract_raises_when_no_match(tmp_path):
    ef = tmp_path / "events.jsonl"
    ef.write_text(_ev("b", "intent_declared") + "\n")
    with pytest.raises(ValueError):
        extract_change_events(ef, "a")


def test_extract_raises_when_file_missing(tmp_path):
    with pytest.raises(ValueError):
        extract_change_events(tmp_path / "nope.jsonl", "a")


def test_write_attestation_roundtrips(tmp_path):
    ef = tmp_path / "events.jsonl"
    ef.write_text(_ev("a", "intent_declared") + "\n")
    out = write_attestation(ef, tmp_path / "att", "a")
    assert out.name == "a.jsonl"
    assert out.read_text().strip() == _ev("a", "intent_declared")
```

- [ ] **Step 2: Run test to verify it fails** — Expected: FAIL (ImportError).

- [ ] **Step 3: Write minimal implementation** (append)

```python
def extract_change_events(events_file: Path, slug: str) -> list[str]:
    """Return verbatim events.jsonl lines whose change_id == slug, in append
    order. Raises ValueError if the file is missing or no line matches (a
    silent-empty attestation would be a useless / misleading artifact)."""
    if not events_file.exists():
        raise ValueError(f"events file not found: {events_file}")
    out: list[str] = []
    for raw in events_file.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if not s:
            continue
        try:
            obj = json.loads(s)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("change_id") == slug:
            out.append(raw)
    if not out:
        raise ValueError(f"no events for change {slug!r} in {events_file}")
    return out


def write_attestation(events_file: Path, attestations_dir: Path, slug: str) -> Path:
    """Snapshot the per-change event slice to `<attestations_dir>/<slug>.jsonl`
    (idempotent overwrite)."""
    lines = extract_change_events(events_file, slug)
    attestations_dir.mkdir(parents=True, exist_ok=True)
    out_path = attestations_dir / f"{slug}.jsonl"
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path
```

- [ ] **Step 4: Run test to verify it passes.** Expected: PASS.
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(attest): extract + write per-change attestation"`

---

## Task 4: Single-attestation integrity check

**Files:**
- Modify: `src/super_harness/engineering/attestation.py`
- Test: `tests/unit/engineering/test_attestation.py`

Uses a helper that emits a real ordered stream via `EventWriter` so tests
exercise the actual reducer/validator (not hand-faked state).

- [ ] **Step 1: Write the failing test**

```python
from super_harness.core.events import Actor, Event
from super_harness.core.writer import EventWriter
from super_harness.core.clock import utc_now_iso
from super_harness.engineering.attestation import check_attestation


def _emit(writer, etype, slug, payload=None):
    writer.emit(Event(event_id=__import__("super_harness.core.ulid", fromlist=["new_event_id"]).new_event_id(),
                      type=etype, change_id=slug, timestamp=utc_now_iso(),
                      actor=Actor(type="human", identifier="t"), framework="plain",
                      payload=payload or {}))


def _ready_stream(path: Path, slug: str):
    w = EventWriter(path)
    _emit(w, "intent_declared", slug)
    _emit(w, "plan_ready", slug, {"scope": {"files": ["src/x.py"]}})
    _emit(w, "plan_approved", slug)
    _emit(w, "implementation_started", slug)
    _emit(w, "verification_passed", slug)
    _emit(w, "implementation_complete", slug)
    _emit(w, "code_review_passed", slug)


def test_check_attestation_clean_ready_stream_passes(tmp_path):
    att = tmp_path / "s.jsonl"
    _ready_stream(att, "s")
    assert check_attestation(att, "s") == []


def test_check_attestation_not_ready_fails(tmp_path):
    att = tmp_path / "s.jsonl"
    w = EventWriter(att)
    _emit(w, "intent_declared", "s")
    _emit(w, "plan_ready", "s", {"scope": {"files": ["src/x.py"]}})
    blockers = check_attestation(att, "s")
    assert any("READY_TO_MERGE" in b for b in blockers)


def test_check_attestation_filename_content_mismatch_fails(tmp_path):
    att = tmp_path / "wrong.jsonl"
    _ready_stream(att, "s")  # content is change_id "s", filename slug "wrong"
    blockers = check_attestation(att, "wrong")
    assert any("does not match" in b for b in blockers)


def test_check_attestation_withdrawn_shortcut_fails_milestone(tmp_path):
    att = tmp_path / "s.jsonl"
    w = EventWriter(att)
    _emit(w, "intent_declared", "s")
    _emit(w, "plan_ready", "s", {"scope": {"files": ["src/x.py"]}})
    _emit(w, "plan_approved", "s")
    _emit(w, "implementation_started", "s")
    _emit(w, "verification_passed", "s")
    _emit(w, "implementation_complete", "s")
    _emit(w, "implementation_withdrawn", "s")  # → READY_TO_MERGE without review
    blockers = check_attestation(att, "s")
    assert any("milestone" in b for b in blockers)
```

- [ ] **Step 2: Run test to verify it fails.** Expected: FAIL (ImportError).

- [ ] **Step 3: Write minimal implementation** (append)

```python
def check_attestation(attestation_path: Path, slug: str) -> list[str]:
    """Return blocker strings for one attestation file (empty = OK). The
    filename↔content binding is checked FIRST so a slug mismatch FAILs cleanly
    rather than KeyError-ing on the state lookup."""
    blockers: list[str] = []
    states = derive_state(attestation_path)
    if set(states.keys()) != {slug}:
        blockers.append(
            f"attestation {attestation_path.name}: filename slug {slug!r} does "
            f"not match contained change_id(s) {sorted(states.keys())}"
        )
        return blockers
    violations = find_ordering_violations(attestation_path, slug)
    if violations:
        blockers.append(
            f"attestation {slug}: lifecycle ordering invalid "
            f"({len(violations)} violation(s); first: {violations[0].reason})"
        )
    cs = states[slug]
    if cs.current_state != REQUIRED_STATE:
        blockers.append(
            f"attestation {slug}: state is {cs.current_state}, not {REQUIRED_STATE}"
        )
    missing = sorted(MILESTONE_EVENTS - set(cs.event_counts.keys()))
    if missing:
        blockers.append(f"attestation {slug}: missing milestone event(s) {missing}")
    return blockers
```

- [ ] **Step 4: Run test to verify it passes.** Expected: PASS (all 4).
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(attest): single-attestation integrity check"`

---

## Task 5: Verdict over diff + attestations

**Files:**
- Modify: `src/super_harness/engineering/attestation.py`
- Test: `tests/unit/engineering/test_attestation.py`

- [ ] **Step 1: Write the failing test**

```python
from super_harness.engineering.attestation import DiffEntry, verify_attestations


def _ready_with_scope(root: Path, slug: str, files: list[str]):
    att_dir = root / ".harness" / "attestations"
    att_dir.mkdir(parents=True, exist_ok=True)
    w = EventWriter(att_dir / f"{slug}.jsonl")
    _emit(w, "intent_declared", slug)
    _emit(w, "plan_ready", slug, {"scope": {"files": files}})
    _emit(w, "plan_approved", slug)
    _emit(w, "implementation_started", slug)
    _emit(w, "verification_passed", slug)
    _emit(w, "implementation_complete", slug)
    _emit(w, "code_review_passed", slug)


def test_verify_covered_subject_passes(tmp_path):
    _ready_with_scope(tmp_path, "s", ["src/x.py"])
    diff = [DiffEntry("A", (".harness/attestations/s.jsonl",)),
            DiffEntry("M", ("src/x.py",))]
    v = verify_attestations(tmp_path, diff)
    assert v.ok, v.blockers


def test_verify_uncovered_subject_fails_bypass(tmp_path):
    # A file changed with NO attestation at all — the Bash-bypass case.
    diff = [DiffEntry("A", ("src/snuck_in.py",))]
    v = verify_attestations(tmp_path, diff)
    assert not v.ok
    assert any("snuck_in.py" in b for b in v.blockers)


def test_verify_scope_drift_fails(tmp_path):
    _ready_with_scope(tmp_path, "s", ["src/x.py"])
    diff = [DiffEntry("A", (".harness/attestations/s.jsonl",)),
            DiffEntry("M", ("src/x.py",)),
            DiffEntry("M", ("src/UNDECLARED.py",))]
    v = verify_attestations(tmp_path, diff)
    assert not v.ok
    assert any("UNDECLARED" in b for b in v.blockers)


def test_verify_modified_attestation_fails(tmp_path):
    _ready_with_scope(tmp_path, "s", ["src/x.py"])
    diff = [DiffEntry("M", (".harness/attestations/s.jsonl",)),
            DiffEntry("M", ("src/x.py",))]
    v = verify_attestations(tmp_path, diff)
    assert not v.ok
    assert any("only newly-ADDED" in b for b in v.blockers)


def test_verify_attestation_only_diff_fails(tmp_path):
    _ready_with_scope(tmp_path, "s", ["src/x.py"])  # x.py NOT in this diff
    diff = [DiffEntry("A", (".harness/attestations/s.jsonl",))]
    v = verify_attestations(tmp_path, diff)
    assert not v.ok
    assert any("covers no file in this diff" in b for b in v.blockers)


def test_verify_deletion_must_be_in_scope(tmp_path):
    _ready_with_scope(tmp_path, "s", ["src/x.py", "src/gone.py"])
    diff = [DiffEntry("A", (".harness/attestations/s.jsonl",)),
            DiffEntry("M", ("src/x.py",)),
            DiffEntry("D", ("src/gone.py",))]
    v = verify_attestations(tmp_path, diff)
    assert v.ok, v.blockers  # deletion declared in scope → covered
```

- [ ] **Step 2: Run test to verify it fails.** Expected: FAIL (ImportError).

- [ ] **Step 3: Write minimal implementation** (append)

```python
@dataclass
class AttestationVerdict:
    ok: bool
    blockers: list[str] = field(default_factory=list)
    subjects: list[str] = field(default_factory=list)
    covered: list[str] = field(default_factory=list)
    attestations: list[str] = field(default_factory=list)


def _is_attestation_path(canonical: str) -> bool:
    return canonical.startswith(ATTESTATIONS_DIRNAME + "/")


def verify_attestations(root: Path, diff_entries: list[DiffEntry]) -> AttestationVerdict:
    """Core merge-gate verdict (design §4.2). Fail-closed: any blocker → not ok."""
    blockers: list[str] = []
    subjects: set[str] = set()
    attestation_entries: list[DiffEntry] = []

    for e in diff_entries:
        att = [p for p in e.paths if _is_attestation_path(p)]
        if att:
            attestation_entries.append(e)
        for p in e.paths:
            if not _is_attestation_path(p):
                subjects.add(p)

    # Attestation files must be ADD-only (closes the "edit a trusted attestation
    # to fabricate" vector). Collect the slugs of added attestations.
    added_slugs: list[str] = []
    for e in attestation_entries:
        if e.status != "A":
            blockers.append(
                f"attestation file changed with status {e.status!r} (only "
                f"newly-ADDED attestations are allowed): {list(e.paths)}"
            )
            continue
        for p in e.paths:
            if _is_attestation_path(p) and p.endswith(".jsonl"):
                added_slugs.append(posixpath.basename(p)[: -len(".jsonl")])

    covered: set[str] = set()
    validated: list[str] = []
    for slug in added_slugs:
        att_path = root / ATTESTATIONS_DIRNAME / f"{slug}.jsonl"
        if not att_path.exists():
            blockers.append(f"attestation file for {slug!r} not found at head")
            continue
        att_blockers = check_attestation(att_path, slug)
        if att_blockers:
            blockers.extend(att_blockers)
            continue
        cs = derive_state(att_path)[slug]
        this_covered = {canonical_path(f) for f in cs.scope.get("files", [])}
        if not (this_covered & subjects):
            blockers.append(
                f"attestation {slug}: its scope covers no file in this diff "
                "(stale or forward-planted)"
            )
            continue
        covered |= this_covered
        validated.append(slug)

    for f in sorted(subjects - covered):
        blockers.append(f"changed file not covered by any complete lifecycle: {f}")

    return AttestationVerdict(
        ok=not blockers,
        blockers=blockers,
        subjects=sorted(subjects),
        covered=sorted(covered),
        attestations=validated,
    )
```

- [ ] **Step 4: Run test to verify it passes.** Expected: PASS (all 6).
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(attest): merge-gate verdict over diff + attestations"`

---

## Task 6: `attest write` CLI command

**Files:**
- Create: `src/super_harness/cli/attest.py`
- Modify: `src/super_harness/cli/__init__.py`
- Test: `tests/unit/cli/test_attest.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/cli/test_attest.py
from pathlib import Path
from click.testing import CliRunner
from super_harness.cli import main


def _init(root: Path):
    (root / ".harness").mkdir(parents=True, exist_ok=True)
    (root / ".harness" / "events.jsonl").write_text(
        '{"change_id":"s","type":"intent_declared","event_id":"e1",'
        '"timestamp":"2026-06-04T00:00:00Z","actor":{"type":"human","identifier":"t"},'
        '"framework":"plain","payload":{}}\n'
    )


def test_attest_write_creates_file(tmp_path):
    _init(tmp_path)
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "attest", "write", "s"])
    assert r.exit_code == 0, r.output
    assert (tmp_path / ".harness" / "attestations" / "s.jsonl").exists()


def test_attest_write_no_events_for_slug_errors(tmp_path):
    _init(tmp_path)
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "attest", "write", "other"])
    assert r.exit_code == 1
```

- [ ] **Step 2: Run test to verify it fails** — `PATH="$(pwd)/.venv/bin:$PATH" python -m pytest tests/unit/cli/test_attest.py -v`. Expected: FAIL.

- [ ] **Step 3: Write minimal implementation**

```python
# src/super_harness/cli/attest.py
"""`super-harness attest` — Layer-2 merge gate (HG-DF C). `write` snapshots a
change's committed attestation; `verify` is the CI gate over diff + attestations.
The git boundary lives here (patchable in tests), mirroring `cli/pr.py`'s gh."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import click

from super_harness.cli.errors import format_error
from super_harness.cli.output import json_envelope
from super_harness.core.paths import (
    HarnessNotInitialized,
    events_path,
    find_harness_root,
)
from super_harness.engineering.attestation import (
    ATTESTATIONS_DIRNAME,
    parse_name_status,
    verify_attestations,
    write_attestation,
)
from super_harness.exit_codes import (
    EXIT_EXTERNAL_TOOL,
    EXIT_GENERIC,
    EXIT_NO_CONFIG,
    EXIT_OK,
    EXIT_VALIDATION,
)


class _GitError(Exception):
    pass


@click.group("attest")
def attest_group() -> None:
    """Lifecycle attestation: snapshot evidence + verify it covers a diff."""


@attest_group.command("write")
@click.argument("slug")
@click.pass_context
def attest_write(ctx: click.Context, slug: str) -> None:
    """Snapshot the per-change event slice to .harness/attestations/<slug>.jsonl."""
    try:
        root = find_harness_root(Path(ctx.obj.get("workspace") or "."))
    except HarnessNotInitialized as e:
        click.echo(format_error(subcommand="attest write", message=e.message, hint=e.hint), err=True)
        sys.exit(EXIT_NO_CONFIG)
    try:
        out = write_attestation(events_path(root), root / ATTESTATIONS_DIRNAME, slug)
    except ValueError as e:
        click.echo(
            format_error(
                subcommand="attest write",
                message=str(e),
                hint="Run the lifecycle for this change first (events.jsonl must have its events).",
            ),
            err=True,
        )
        sys.exit(EXIT_GENERIC)
    rel = out.relative_to(root).as_posix()
    if ctx.obj.get("json"):
        click.echo(json_envelope(command="attest write", status="pass", exit_code=EXIT_OK,
                                 data={"change": slug, "attestation_path": rel}))
    elif not ctx.obj.get("quiet"):
        click.echo(f"super-harness: wrote attestation {rel}")
    sys.exit(EXIT_OK)
```

Then register in `src/super_harness/cli/__init__.py`: add `from super_harness.cli.attest import attest_group` with the other imports, and `main.add_command(attest_group)` after `main.add_command(on_merge_cli)`.

- [ ] **Step 4: Run test to verify it passes.** Expected: PASS (both).
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(attest): attest write CLI command"`

---

## Task 7: `attest verify` CLI command

**Files:**
- Modify: `src/super_harness/cli/attest.py`
- Test: `tests/unit/cli/test_attest.py`

- [ ] **Step 1: Write the failing test**

```python
import super_harness.cli.attest as attest_mod


def test_attest_verify_fails_on_uncovered(tmp_path, monkeypatch):
    _init(tmp_path)
    monkeypatch.setattr(attest_mod, "_git_name_status", lambda base, head, cwd: "A\tsrc/snuck.py\n")
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "attest", "verify",
                                  "--base", "main", "--head", "HEAD"])
    assert r.exit_code == 2
    assert "snuck.py" in r.output


def test_attest_verify_fail_closed_on_git_error(tmp_path, monkeypatch):
    _init(tmp_path)
    def boom(base, head, cwd):
        raise attest_mod._GitError("no merge base")
    monkeypatch.setattr(attest_mod, "_git_name_status", boom)
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "attest", "verify",
                                  "--base", "main", "--head", "HEAD"])
    assert r.exit_code == 4
```

- [ ] **Step 2: Run test to verify it fails.** Expected: FAIL.

- [ ] **Step 3: Write minimal implementation** (append to `cli/attest.py`)

```python
def _git_name_status(base: str, head: str, cwd: Path) -> str:
    """Run `git diff --name-status base...head`. Raises _GitError on failure
    (the CLI translates that to a FAIL-CLOSED exit 4)."""
    proc = subprocess.run(
        ["git", "-c", "core.quotePath=false", "diff", "--name-status", f"{base}...{head}"],
        cwd=str(cwd), capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise _GitError(proc.stderr.strip() or f"git diff failed (exit {proc.returncode})")
    return proc.stdout


@attest_group.command("verify")
@click.option("--base", required=True, help="Base ref/SHA (e.g. PR base.sha).")
@click.option("--head", required=True, help="Head ref/SHA (e.g. PR head.sha).")
@click.pass_context
def attest_verify(ctx: click.Context, base: str, head: str) -> None:
    """Fail if any changed file lacks a complete, ordered, scope-covering attestation."""
    try:
        root = find_harness_root(Path(ctx.obj.get("workspace") or "."))
    except HarnessNotInitialized as e:
        click.echo(format_error(subcommand="attest verify", message=e.message, hint=e.hint), err=True)
        sys.exit(EXIT_NO_CONFIG)
    try:
        raw = _git_name_status(base, head, root)
    except _GitError as e:
        click.echo(
            format_error(
                subcommand="attest verify",
                message=f"git diff failed: {e}",
                hint="Ensure a full checkout (fetch-depth: 0) with a reachable merge-base.",
            ),
            err=True,
        )
        sys.exit(EXIT_EXTERNAL_TOOL)  # FAIL-CLOSED — never a vacuous pass
    verdict = verify_attestations(root, parse_name_status(raw))
    data: dict[str, Any] = {
        "subjects": verdict.subjects,
        "covered": verdict.covered,
        "attestations": verdict.attestations,
        "blockers": verdict.blockers,
    }
    if ctx.obj.get("json"):
        click.echo(json_envelope(
            command="attest verify",
            status="pass" if verdict.ok else "fail",
            exit_code=EXIT_OK if verdict.ok else EXIT_VALIDATION,
            data=data,
            errors=[{"code": "validation", "message": b} for b in verdict.blockers],
        ))
    elif verdict.ok:
        if not ctx.obj.get("quiet"):
            click.echo(f"attest verify: PASS ({len(verdict.subjects)} files covered)")
    else:
        click.echo(
            format_error(
                subcommand="attest verify",
                message=f"{len(verdict.blockers)} blocker(s):\n  - " + "\n  - ".join(verdict.blockers),
                hint="Each changed file must be in a complete lifecycle attestation's scope.",
            ),
            err=True,
        )
    sys.exit(EXIT_OK if verdict.ok else EXIT_VALIDATION)
```

- [ ] **Step 4: Run test to verify it passes.** Expected: PASS.
- [ ] **Step 5: Run full unit suite + lint** — `PATH="$(pwd)/.venv/bin:$PATH" python -m pytest -q -m "not e2e" && PATH="$(pwd)/.venv/bin:$PATH" ruff check . && PATH="$(pwd)/.venv/bin:$PATH" mypy src`. Expected: all green.
- [ ] **Step 6: Commit** — `git add -A && git commit -m "feat(attest): attest verify CLI merge gate (fail-closed)"`

---

## Task 8: CI wiring (template + this repo)

**Files:**
- Modify: `src/super_harness/templates/super_harness_workflow.yml`
- Modify: `tests/unit/templates/test_super_harness_workflow.py`
- Create: `.github/workflows/merge-gate.yml`

- [ ] **Step 1: Write the failing test** (append to `test_super_harness_workflow.py`)

```python
def test_template_has_attest_verify_job():
    import yaml
    from importlib.resources import files
    text = (files("super_harness.templates") / "super_harness_workflow.yml").read_text()
    doc = yaml.safe_load(text)
    assert "attest-verify" in doc["jobs"]
    job = doc["jobs"]["attest-verify"]
    # must do a full-history checkout so base...head merge-base is reachable
    checkout = next(s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/checkout"))
    assert checkout["with"]["fetch-depth"] == 0
```

(If the file's existing tests use a different loader/helper, match that style; the assertion is what matters.)

- [ ] **Step 2: Run to verify it fails** — `PATH="$(pwd)/.venv/bin:$PATH" python -m pytest tests/unit/templates/test_super_harness_workflow.py -k attest_verify -v`. Expected: FAIL (KeyError).

- [ ] **Step 3: Add the job to the template** (append under `jobs:` in `super_harness_workflow.yml`)

```yaml
  # Layer-2 merge gate (HG-DF C): re-derive from the PR diff + committed
  # attestations whether every changed file went through a complete, ordered
  # lifecycle whose scope covers it. Pure git + committed evidence — no daemon,
  # no events.jsonl, agent-agnostic. NOTE: requires a super-harness release that
  # ships `attest verify`; bump the pinned version below when that release lands.
  attest-verify:
    if: github.event_name == 'pull_request'
    runs-on: ubuntu-latest
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }
      - uses: actions/setup-python@v5
        with: { python-version: '3.12' }
      - name: Install super-harness
        run: pipx install super-harness==0.1.0
      - name: Verify lifecycle attestations cover the diff
        env:
          BASE_SHA: ${{ github.event.pull_request.base.sha }}
          HEAD_SHA: ${{ github.event.pull_request.head.sha }}
        run: super-harness attest verify --base "$BASE_SHA" --head "$HEAD_SHA"
```

- [ ] **Step 4: Create this repo's own gate** `.github/workflows/merge-gate.yml`

```yaml
name: merge-gate
on:
  pull_request: {}
jobs:
  attest-verify:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - name: Install package
        run: pip install -e ".[dev]"
      - name: Verify lifecycle attestations cover the diff
        env:
          BASE_SHA: ${{ github.event.pull_request.base.sha }}
          HEAD_SHA: ${{ github.event.pull_request.head.sha }}
        run: super-harness attest verify --base "$BASE_SHA" --head "$HEAD_SHA"
```

- [ ] **Step 5: Run template test + existing injection-guard suite** — `PATH="$(pwd)/.venv/bin:$PATH" python -m pytest tests/unit/templates/test_super_harness_workflow.py -v`. Expected: PASS (new + existing).
- [ ] **Step 6: Commit** — `git add -A && git commit -m "feat(attest): wire attest-verify into CI template + repo merge-gate"`

---

## Task 9: Regenerate cli-reference + final full verification

**Files:**
- Modify: `docs/cli-reference.md`
- Modify: `scripts/gen_cli_reference.py` (only if it has a hand-maintained exit-code map needing the new commands)

- [ ] **Step 1: Regenerate** — `PATH="$(pwd)/.venv/bin:$PATH" python -m scripts.gen_cli_reference`
- [ ] **Step 2: Verify drift check passes** — `PATH="$(pwd)/.venv/bin:$PATH" python -m scripts.gen_cli_reference --check`. Expected: in-sync (exit 0). If it complains about a missing exit-code map entry for `attest write`/`attest verify`, add them to `scripts/gen_cli_reference.py._EXIT_CODES` (write: `0/1/3`; verify: `0/2/3/4`) and re-run.
- [ ] **Step 3: Full suite + lint + types** — `PATH="$(pwd)/.venv/bin:$PATH" python -m pytest -q && PATH="$(pwd)/.venv/bin:$PATH" ruff check . && PATH="$(pwd)/.venv/bin:$PATH" mypy src`. Expected: all green.
- [ ] **Step 4: Commit** — `git add -A && git commit -m "docs(attest): regenerate cli-reference for attest commands"`

---

## Execution / self-host lifecycle (manual, per project flow)

Lifecycle verbs go through **Bash** (not the edit-time gate). Order matters:
`implementation_complete` (done) precedes `code_review_passed` (review approve).

1. `git checkout -b 2026-06-04-layer2-merge-gate` (from latest main).
2. `super-harness change start 2026-06-04-layer2-merge-gate --description "Layer-2 CI merge gate (HG-DF C)"`
3. `super-harness plan ready 2026-06-04-layer2-merge-gate --tier-hint Large --scope @/tmp/scope.yaml` where `/tmp/scope.yaml` lists **every** subject file (STRICT policy):
   - `src/super_harness/engineering/attestation.py`
   - `src/super_harness/cli/attest.py`
   - `src/super_harness/cli/__init__.py`
   - `src/super_harness/templates/super_harness_workflow.yml`
   - `.github/workflows/merge-gate.yml`
   - `tests/unit/engineering/test_attestation.py`
   - `tests/unit/cli/test_attest.py`
   - `tests/unit/templates/test_super_harness_workflow.py`
   - `docs/plans/2026-06-03-layer2-merge-gate-design.md`
   - `docs/plans/2026-06-04-layer2-merge-gate-plan.md`
   - `docs/cli-reference.md`
   - (add `scripts/gen_cli_reference.py` only if Task 9 Step 2 required editing it)
4. Dispatch an **independent plan-reviewer subagent** (my own Task, not harness-spawned); fold fixes; `super-harness review approve 2026-06-04-layer2-merge-gate --reviewer plan-reviewer --reason "..."`.
5. `super-harness implementation start 2026-06-04-layer2-merge-gate` → run Tasks 1–9 TDD under PLAN_APPROVED.
6. `super-harness done 2026-06-04-layer2-merge-gate` (emits `implementation_complete` → AWAITING_CODE_REVIEW; runs verification).
7. Dispatch an **independent code-reviewer subagent**; fold fixes; `super-harness review approve 2026-06-04-layer2-merge-gate --reviewer code-reviewer --reason "..."` (→ READY_TO_MERGE).
8. `super-harness attest write 2026-06-04-layer2-merge-gate` → commit `.harness/attestations/2026-06-04-layer2-merge-gate.jsonl`.
9. **Load-bearing dogfood (real verification, not ritual):** on a throwaway branch, write a source file via Bash heredoc (bypassing the edit-time gate) with NO attestation; run `super-harness attest verify --base main --head HEAD`; **confirm exit ≠ 0**. This proves the gate catches what the edit-time gate misses (framed as "missing coverage fails", per §2/§8 — NOT "bypass impossible"). Throw the branch away.
10. Open the PR (write full title/body at `gh pr create` — `gh pr edit` is unavailable). The repo's own `merge-gate.yml` runs `attest verify` on this PR; it must go green (self-proof).
11. Post-merge bookkeeping (gitignored `private/`, kill switch if a frozen-state edit is needed): register deferred items (HG-12 / HG-DF D / event-store sharding) in `private/OPEN-ITEMS.md`; update HG-DF status in `private/HARNESS-GAPS.md`; update memory.
