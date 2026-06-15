# Decision Text-Lock (Tool A) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** A ratified decision's body cannot be silently altered — `decision ratify`
freezes a fingerprint of the body, and `decision check` blocks (CI + locally) if the body
later moves without a fresh re-ratify.

**Architecture:** Extend the slice-1 decision record (`docs/decisions/<id>.md`) with one
frontmatter field `ratified_text_hash`. `ratify` computes it over a *minimally normalized*
body; `decision check` recomputes and compares. A mismatch is a standalone blocking
`integrity_violation` (independent of whether any code anchor points at the decision); the
violated decision also drops from the effectively-ratified set so its anchors surface
dangling-up. Read-only — `check` never rewrites a record. Existing ratified records that
predate the field have no hash → **lazy-warn** (warn, don't block; the field fills in on
the next legitimate `ratify`).

**Scope boundary:** This is **Tool A only** (the text-lock — the foundation per design
§2). **Tool B (executable checks + counterexample bite-test + `--changed` + hard:context
ratio)** is explicitly **out of this plan**: its counterexample-vs-repo-level-check
mechanism (how a repo-wide check is shown to "bite" an isolated bad snippet) is an open
design question that needs a brainstorm round before it can be planned. See the closing
section. This plan ships as **PR-1** and is usable on its own.

**Tech Stack:** Python 3.10+, click, PyYAML, pytest. SHA-256 via stdlib `hashlib`.

**Source map (verified against current `main`):**
- `src/super_harness/core/decisions.py` — `Decision` dataclass, `parse_decision_file`,
  `serialize_decision`, `write_decision`. Note `split_frontmatter` already `.strip()`s
  the body and normalizes line endings via `splitlines()`.
- `src/super_harness/core/decision_check.py` — `CheckResult` (`dangling_up`,
  `dangling_down`, `errors`, `ok`), `run_check`.
- `src/super_harness/cli/decision.py` — `ratify_cmd`, `check_cmd`. Exit codes:
  `EXIT_OK=0`, `EXIT_VALIDATION=2`, `EXIT_NO_CONFIG=3` (imported from
  `super_harness.exit_codes`).
- Tests live under `tests/unit/core/` and `tests/unit/cli/`.

**Design ref:** `docs/plans/2026-06-12-decision-text-lock-design.md` §3, §6, §8.

---

## Task 1: `Decision` carries `ratified_text_hash` (parse + serialize round-trip)

**Files:**
- Modify: `src/super_harness/core/decisions.py`
- Test: `tests/unit/core/test_decisions.py`

**Step 1: Write the failing test**

```python
def test_parse_reads_ratified_text_hash(tmp_path):
    text = (
        "---\nid: d-x\nstatus: ratified\nratified_by: a@b.com\n"
        "ratified_at: 2026-06-08T12:00:00Z\n"
        "ratified_text_hash: sha256:abc123\n---\nbody.\n"
    )
    p = _write(tmp_path / "docs/decisions/d-x.md", text)
    d = parse_decision_file(p)
    assert d.ratified_text_hash == "sha256:abc123"


def test_serialize_round_trips_hash(tmp_path):
    d = Decision(
        id="d-x", status="ratified", ratified_by="a@b.com",
        ratified_at="2026-06-08T12:00:00Z", ratified_text_hash="sha256:abc123",
        body="body.",
    )
    out = serialize_decision(d)
    assert "ratified_text_hash: sha256:abc123" in out


def test_parse_missing_hash_is_none(tmp_path):
    p = _write(tmp_path / "docs/decisions/d-y.md",
               "---\nid: d-y\nstatus: ratified\nratified_by: a@b.com\n---\nb\n")
    assert parse_decision_file(p).ratified_text_hash is None
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/unit/core/test_decisions.py -k ratified_text_hash -v`
Expected: FAIL — `Decision` has no `ratified_text_hash` argument.

**Step 3: Write minimal implementation**

In `decisions.py`, add the field to the dataclass (after `superseded_by`):

```python
    superseded_by: str | None = None
    ratified_text_hash: str | None = None
    body: str = ""
```

In `parse_decision_file`, pass it through (alongside the other `data.get(...)`):

```python
        superseded_by=data.get("superseded_by"),
        ratified_text_hash=data.get("ratified_text_hash"),
        body=body,
```

In `serialize_decision`, add the key to the iterated tuple:

```python
    for key in ("ratified_by", "ratified_at", "supersedes",
                "superseded_by", "ratified_text_hash"):
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/unit/core/test_decisions.py -k ratified_text_hash -v`
Expected: PASS (3 tests).

**Step 5: Commit**

```bash
git add src/super_harness/core/decisions.py tests/unit/core/test_decisions.py
git commit -m "feat(decisions): Decision carries ratified_text_hash field"
```

---

## Task 2: `normalize_body` + `compute_body_hash` (pure, minimal normalization)

The design (§3) demands *minimal* normalization: only line endings, per-line trailing
whitespace, and leading/trailing blank lines. Changing punctuation/wording/typos *is*
changing what the human approved → must re-ratify. Prefer false alarm over silent miss.

**Files:**
- Modify: `src/super_harness/core/decisions.py`
- Test: `tests/unit/core/test_decisions.py`

**Step 1: Write the failing test**

```python
from super_harness.core.decisions import compute_body_hash, normalize_body


def test_normalize_collapses_only_whitespace_noise():
    a = "line one  \r\nline two\n"      # CRLF + trailing spaces + trailing newline
    b = "\n\nline one\nline two"        # leading blank lines, no trailing
    assert normalize_body(a) == normalize_body(b) == "line one\nline two"


def test_hash_is_stable_and_prefixed():
    h = compute_body_hash("hello")
    assert h.startswith("sha256:")
    assert h == compute_body_hash("hello\n")  # trailing newline is noise


def test_hash_changes_on_wording():
    # punctuation/wording is NOT normalized away — it must move the hash
    assert compute_body_hash("never MD5.") != compute_body_hash("prefer bcrypt.")
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/unit/core/test_decisions.py -k "normalize or hash" -v`
Expected: FAIL — `normalize_body` / `compute_body_hash` not defined.

**Step 3: Write minimal implementation**

Add near the top of `decisions.py` (after imports add `import hashlib`):

```python
def normalize_body(body: str) -> str:
    """Minimal normalization for fingerprinting: line endings, per-line trailing
    whitespace, leading/trailing blank lines. Nothing else (§3)."""
    unified = body.replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln.rstrip() for ln in unified.split("\n")]
    return "\n".join(lines).strip()


def compute_body_hash(body: str) -> str:
    digest = hashlib.sha256(normalize_body(body).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/unit/core/test_decisions.py -k "normalize or hash" -v`
Expected: PASS (3 tests).

**Step 5: Commit**

```bash
git add src/super_harness/core/decisions.py tests/unit/core/test_decisions.py
git commit -m "feat(decisions): normalize_body + compute_body_hash (minimal normalization)"
```

---

## Task 3: `decision ratify` stamps the fingerprint

**Files:**
- Modify: `src/super_harness/cli/decision.py:98-118` (`ratify_cmd`)
- Test: `tests/unit/cli/test_decision.py`

**Step 1: Write the failing test**

Use the file's real pattern (verified): `CliRunner().invoke(main, ["--workspace",
str(root), "decision", ...])` + the `_init(tmp_path)` helper. There is **no**
`run_decision` fixture. Imports at top: `from super_harness.core.decisions import
parse_decision_file, compute_body_hash`.

```python
def test_ratify_stamps_text_hash(tmp_path):
    root = _init(tmp_path)
    CliRunner().invoke(main, ["--workspace", str(root), "decision", "new",
                              "d-pw", "--text", "Passwords never stored with MD5."])
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "ratify", "d-pw"])
    assert r.exit_code == 0, r.output
    d = parse_decision_file(root / "docs/decisions/d-pw.md")
    assert d.status == "ratified"
    assert d.ratified_text_hash == compute_body_hash("Passwords never stored with MD5.")
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/unit/cli/test_decision.py -k stamps_text_hash -v`
Expected: FAIL — `ratified_text_hash` is None after ratify.

**Step 3: Write minimal implementation**

In `ratify_cmd`, import `compute_body_hash` and set it before `write_decision`:

```python
    d.status = "ratified"
    d.ratified_by = resolve_identity(root)
    d.ratified_at = utc_now_iso()
    d.ratified_text_hash = compute_body_hash(d.body)
    write_decision(d)
```

Add `compute_body_hash` to the existing `from super_harness.core.decisions import (...)`.

**Step 4: Run test to verify it passes**

Run: `pytest tests/unit/cli/test_decision.py -k stamps_text_hash -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add src/super_harness/cli/decision.py tests/unit/cli/test_decision.py
git commit -m "feat(decision): ratify freezes ratified_text_hash"
```

---

## Task 4: `run_check` detects integrity violations (hash mismatch → block)

A ratified decision whose current body hash ≠ stored `ratified_text_hash` is an
**integrity violation**: standalone blocking (regardless of anchors), `ok` becomes False,
and the violated id drops from the *effectively-ratified* set used for dangling-up.

**Files:**
- Modify: `src/super_harness/core/decision_check.py`
- Test: `tests/unit/core/test_decision_check.py`

**Step 1: Write the failing test**

```python
from super_harness.core.decision_check import run_check


def test_tampered_body_is_integrity_violation(tmp_path):
    # ratified record whose stored hash does NOT match its body
    body = "Passwords never stored with MD5."
    _write(tmp_path / "docs/decisions/d-pw.md",
           f"---\nid: d-pw\nstatus: ratified\nratified_by: a@b.com\n"
           f"ratified_text_hash: sha256:deadbeef\n---\n{body}\n")
    res = run_check(tmp_path)
    assert [v.id for v in res.integrity_violations] == ["d-pw"]
    assert res.ok is False


def test_matching_hash_is_clean(tmp_path):
    from super_harness.core.decisions import compute_body_hash
    body = "Passwords never stored with MD5."
    _write(tmp_path / "docs/decisions/d-pw.md",
           f"---\nid: d-pw\nstatus: ratified\nratified_by: a@b.com\n"
           f"ratified_text_hash: {compute_body_hash(body)}\n---\n{body}\n")
    res = run_check(tmp_path)
    assert res.integrity_violations == []
    assert res.ok is True


def test_violated_decision_drops_from_effective_ratified(tmp_path):
    from super_harness.core.decisions import compute_body_hash
    body = "Claim X."
    _write(tmp_path / "docs/decisions/d-x.md",
           f"---\nid: d-x\nstatus: ratified\nratified_by: a@b.com\n"
           f"ratified_text_hash: sha256:deadbeef\n---\n{body}\n")
    _write(tmp_path / "src/m.py", "# @decision:d-x\nx = 1\n")
    res = run_check(tmp_path)
    assert any(d.id == "d-x" for d in res.dangling_up)  # anchor now dangles up


def test_superseded_stale_hash_is_ignored(tmp_path):
    # integrity check only fires for status == ratified; a superseded record with a
    # stale hash must NOT be flagged (locks the Task-4 status guard intent)
    _write(tmp_path / "docs/decisions/d-old.md",
           "---\nid: d-old\nstatus: superseded\nsuperseded_by: d-new\n"
           "ratified_text_hash: sha256:deadbeef\n---\nstale body.\n")
    _write(tmp_path / "docs/decisions/d-new.md",
           "---\nid: d-new\nstatus: proposed\n---\nx\n")
    res = run_check(tmp_path)
    assert res.integrity_violations == []


def test_violated_and_unanchored_shows_both(tmp_path):
    # realistic tamper case: a tampered decision with no anchor is BOTH an integrity
    # violation AND still dangling-down (its down-ness is over the full ratified set)
    _write(tmp_path / "docs/decisions/d-x.md",
           "---\nid: d-x\nstatus: ratified\nratified_by: a@b.com\n"
           "ratified_text_hash: sha256:deadbeef\n---\nbody.\n")
    res = run_check(tmp_path)
    assert [v.id for v in res.integrity_violations] == ["d-x"]
    assert "d-x" in res.dangling_down
```

> **Review note (C1):** no source-scope scaffolding needed. `load_source_scope` defaults
> to `include=["**/*"], exclude=["docs/**"]` when no `.harness/source-paths.yaml` exists,
> and the existing `test_decision_check.py` tests write bare `src/*.py` under `tmp_path`
> with no `.harness` dir and the anchor is scanned fine. Mirror
> `test_decision_check.py::test_clean_repo` (`_write` + bare `src/`).

**Step 2: Run test to verify it fails**

Run: `pytest tests/unit/core/test_decision_check.py -k "integrity or effective or matching" -v`
Expected: FAIL — `CheckResult` has no `integrity_violations`.

**Step 3: Write minimal implementation**

In `decision_check.py`:

```python
from dataclasses import dataclass, field
from super_harness.core.decisions import (
    RecordError, compute_body_hash, load_decisions,
)


@dataclass
class IntegrityViolation:
    id: str
    file: str


@dataclass
class CheckResult:
    dangling_up: list[DanglingUp]
    dangling_down: list[str]
    errors: list[RecordError]
    # New fields get default_factory so the early `errors`-return construction
    # site (decision_check.py:44) keeps working untouched — no TypeError, no
    # need to thread them through every CheckResult(...) by hand.
    integrity_violations: list[IntegrityViolation] = field(default_factory=list)
    unhashed_ratified: list[str] = field(default_factory=list)  # added in Task 5

    @property
    def ok(self) -> bool:
        return not self.dangling_up and not self.errors and not self.integrity_violations
```

> **Review note (B3):** because both new fields carry `default_factory=list`, the early
> `errors` return at `decision_check.py:44` and the final return both stay valid without
> edits. Only the *final* return needs to pass the computed `integrity_violations=...`
> (Task 4) and `unhashed_ratified=...` (Task 5).

In `run_check`, after `ratified = {...}` compute violations and an effective set:

```python
    ratified = {d.id for d in decisions if d.status == "ratified"}

    integrity_violations: list[IntegrityViolation] = []
    for d in decisions:
        if d.status != "ratified" or d.ratified_text_hash is None:
            continue  # missing hash → lazy-warn path, not a violation (Task 5)
        if compute_body_hash(d.body) != d.ratified_text_hash:
            rel = str(d.path.relative_to(workspace_root)) if d.path else d.id
            integrity_violations.append(IntegrityViolation(id=d.id, file=rel))
    integrity_violations.sort(key=lambda v: v.id)

    violated = {v.id for v in integrity_violations}
    effective_ratified = ratified - violated
```

Then use `effective_ratified` (not `ratified`) for the dangling-up loop, and pass
`integrity_violations=integrity_violations` on the **final** return only (the early
`errors` return relies on the `default_factory`). Keep
`dangling_down = sorted(ratified - anchored_ids)` over the **full** ratified set (a
violated decision still "exists"; its down-ness is unchanged — only its up-binding is
revoked).

**Step 4: Run test to verify it passes**

Run: `pytest tests/unit/core/test_decision_check.py -v`
Expected: PASS (new + all existing).

**Step 5: Commit**

```bash
git add src/super_harness/core/decision_check.py tests/unit/core/test_decision_check.py
git commit -m "feat(decision-check): integrity_violations on body-hash mismatch"
```

---

## Task 5: Lazy-warn migration — ratified-without-hash warns, never blocks

**Files:**
- Modify: `src/super_harness/core/decision_check.py`
- Test: `tests/unit/core/test_decision_check.py`

**Step 1: Write the failing test**

```python
def test_ratified_without_hash_warns_not_blocks(tmp_path):
    _write(tmp_path / "docs/decisions/d-old.md",
           "---\nid: d-old\nstatus: ratified\nratified_by: a@b.com\n---\nlegacy.\n")
    res = run_check(tmp_path)
    assert res.unhashed_ratified == ["d-old"]
    assert res.integrity_violations == []
    assert res.ok is True   # warn-only, must NOT block
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/unit/core/test_decision_check.py -k without_hash -v`
Expected: FAIL — no `unhashed_ratified`.

**Step 3: Write minimal implementation**

The `unhashed_ratified` field was already added to `CheckResult` (with
`default_factory`) in Task 4 and is NOT part of `ok`. Populate it in `run_check`:

```python
    unhashed_ratified = sorted(
        d.id for d in decisions
        if d.status == "ratified" and d.ratified_text_hash is None
    )
```

Pass `unhashed_ratified=unhashed_ratified` on the **final** return only.

**Step 4: Run test to verify it passes**

Run: `pytest tests/unit/core/test_decision_check.py -k without_hash -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add src/super_harness/core/decision_check.py tests/unit/core/test_decision_check.py
git commit -m "feat(decision-check): lazy-warn ratified records missing a hash"
```

---

## Task 6: `decision check` CLI surfaces violations (text + JSON + exit code)

**Files:**
- Modify: `src/super_harness/cli/decision.py:208-258` (`check_cmd`)
- Test: `tests/unit/cli/test_decision.py`

**Step 1: Write the failing test**

Real pattern (the global `--json` flag goes **before** the `decision` subgroup, per the
existing `test_check_json_envelope`). A small local helper to write the tampered record:

```python
def _w(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


TAMPERED = ("---\nid: d-pw\nstatus: ratified\nratified_by: a@b.com\n"
            "ratified_text_hash: sha256:deadbeef\n---\nClaim.\n")


def test_check_blocks_on_integrity_violation(tmp_path):
    root = _init(tmp_path)
    _w(root / "docs/decisions/d-pw.md", TAMPERED)
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "check"])
    assert r.exit_code == 2                       # EXIT_VALIDATION
    assert "INTEGRITY" in r.output


def test_check_json_lists_integrity_violations(tmp_path):
    root = _init(tmp_path)
    _w(root / "docs/decisions/d-pw.md", TAMPERED)
    r = CliRunner().invoke(main, ["--workspace", str(root), "--json", "decision", "check"])
    payload = json.loads(r.output)
    assert payload["data"]["integrity_violations"] == [
        {"id": "d-pw", "file": "docs/decisions/d-pw.md"}
    ]
    assert payload["status"] == "fail"
```

> Note: `INTEGRITY-LOCK …` is echoed to **stderr**; `CliRunner` by default merges
> stderr into `.output`, so the assertion holds. Confirm the runner isn't constructed
> with `mix_stderr=False` in this file (it isn't, as of `main`).

**Step 2: Run test to verify it fails**

Run: `pytest tests/unit/cli/test_decision.py -k "integrity" -v`
Expected: FAIL — no integrity handling / key.

**Step 3: Write minimal implementation**

In `check_cmd`, make integrity violations the **highest-priority block** (a tampered
ruler invalidates everything downstream):

```python
    if result.errors:
        exit_code, status = EXIT_NO_CONFIG, "fail"
    elif result.integrity_violations:
        exit_code, status = EXIT_VALIDATION, "fail"
    elif result.dangling_up:
        exit_code, status = EXIT_VALIDATION, "fail"
    elif result.dangling_down or result.unhashed_ratified:
        exit_code, status = EXIT_OK, "warning"
    else:
        exit_code, status = EXIT_OK, "pass"
```

JSON envelope `data` gains:

```python
                    "integrity_violations": [
                        {"id": v.id, "file": v.file}
                        for v in result.integrity_violations
                    ],
                    "unhashed_ratified": list(result.unhashed_ratified),
```

Text branch, before the dangling-up loop:

```python
        for v in result.integrity_violations:
            click.echo(
                f"INTEGRITY-LOCK {v.file} @decision:{v.id} "
                f"(ratified body changed without re-ratification → re-ratify)",
                err=True,
            )
        for did in result.unhashed_ratified:
            click.echo(f"warning: {did} ratified before text-lock (no hash; "
                       f"re-ratify to lock)")
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/unit/cli/test_decision.py -k "integrity" -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add src/super_harness/cli/decision.py tests/unit/cli/test_decision.py
git commit -m "feat(decision-check): CLI surfaces integrity-lock violations + lazy-warn"
```

---

## Task 7: Re-ratify is the only unlock path (`ratify` accepts `ratified`)

Design §3: re-ratify is the *only* legal way to clear an integrity-lock — "re-run
`decision ratify` → fresh identity, time, fingerprint." Today `ratify_cmd` rejects
anything not `proposed` (`decision.py:105`). **Decision (review-confirmed): reuse the
same `ratify` verb** (design says "re-run ratify", not a new verb) — relax the guard to
accept `proposed` *or* `ratified`, keep rejecting `superseded`/`retired`.

**This deliberately changes an existing guarantee:** `test_ratify_only_from_proposed`
(`tests/unit/cli/test_decision.py:61-66`) asserts re-ratifying a `ratified` decision
returns exit 2. That test must be **consciously rewritten** to the new contract (not
silently deleted).

**Files:**
- Modify: `src/super_harness/cli/decision.py:105-112` (the `ratify_cmd` guard)
- Test: `tests/unit/cli/test_decision.py` (rewrite `test_ratify_only_from_proposed`;
  add `test_reratify_restamps_all_three`)

**Step 1: Write/rewrite the failing tests**

```python
# REWRITE of the old test_ratify_only_from_proposed — new contract:
def test_ratify_rejects_superseded_and_retired(tmp_path):
    root = _init(tmp_path)
    CliRunner().invoke(main, ["--workspace", str(root), "decision", "new", "d-a", "--text", "x"])
    CliRunner().invoke(main, ["--workspace", str(root), "decision", "ratify", "d-a"])
    CliRunner().invoke(main, ["--workspace", str(root), "decision", "retire", "d-a"])
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "ratify", "d-a"])
    assert r.exit_code == 2  # retired cannot be re-ratified


def test_reratify_restamps_all_three(tmp_path, monkeypatch):
    root = _init(tmp_path)
    monkeypatch.setenv("SUPER_HARNESS_ACTOR", "alice@example.com")
    CliRunner().invoke(main, ["--workspace", str(root), "decision", "new", "d-a", "--text", "v1"])
    CliRunner().invoke(main, ["--workspace", str(root), "decision", "ratify", "d-a"])
    first = parse_decision_file(root / "docs/decisions/d-a.md")
    # edit the body, then re-ratify → fresh hash + identity + time
    p = root / "docs/decisions/d-a.md"
    p.write_text(p.read_text().replace("v1", "v2"), encoding="utf-8")
    monkeypatch.setenv("SUPER_HARNESS_ACTOR", "bob@example.com")
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "ratify", "d-a"])
    assert r.exit_code == 0, r.output
    second = parse_decision_file(p)
    assert second.ratified_text_hash == compute_body_hash("v2") != first.ratified_text_hash
    assert second.ratified_by == "bob@example.com"
    assert second.ratified_at != first.ratified_at
```

**Step 2: Run to verify failures**

Run: `pytest tests/unit/cli/test_decision.py -k "reratify or rejects_superseded" -v`
Expected: FAIL — re-ratify currently exits 2.

**Step 3: Relax the guard**

```python
    if d.status not in ("proposed", "ratified"):
        click.echo(
            format_error(subcommand="decision ratify",
                         message=f"{decision_id!r} is {d.status}, not proposed/ratified",
                         hint="Only a proposed or already-ratified decision can be ratified."),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
```

The existing body of `ratify_cmd` already re-stamps `ratified_by` / `ratified_at` /
(Task 3) `ratified_text_hash` unconditionally, so no other change is needed.

**Step 4: Run to verify pass** (and the rewritten guard test)

Run: `pytest tests/unit/cli/test_decision.py -v`
Expected: PASS (incl. rewritten `test_ratify_rejects_superseded_and_retired`).

**Step 5: Commit**

```bash
git add src/super_harness/cli/decision.py tests/unit/cli/test_decision.py
git commit -m "feat(decision): re-ratify (proposed|ratified) is the only text-lock unlock"
```

---

## Task 8: End-to-end lifecycle test (the §0 story)

**Files:**
- Test: `tests/unit/cli/test_decision.py`

**Step 1: Write the failing test**

```python
def test_text_lock_full_lifecycle(tmp_path):
    root = _init(tmp_path)
    inv = lambda *a: CliRunner().invoke(main, ["--workspace", str(root), "decision", *a])
    # 1. author + ratify → clean
    inv("new", "d-pw", "--text", "Passwords never stored with MD5.")
    inv("ratify", "d-pw")
    assert inv("check").exit_code == 0

    # 2. tamper the claim (soften it) → check blocks
    p = root / "docs/decisions/d-pw.md"
    p.write_text(p.read_text().replace(
        "never stored with MD5", "preferably not MD5"), encoding="utf-8")
    assert inv("check").exit_code == 2

    # 3. human re-ratifies → fresh hash → clean again
    inv("ratify", "d-pw")
    assert inv("check").exit_code == 0
```

**Step 2: Run** — should already pass given Tasks 1-7 (this is the integration assertion).
If it fails, fix the underlying task, not this test.

**Step 3: Commit**

```bash
git add tests/unit/cli/test_decision.py
git commit -m "test(decision): end-to-end text-lock lifecycle (author→tamper→reratify)"
```

---

## Final verification (before PR)

- `pytest -q` — full suite green (mention the count, per project discipline).
- `ruff check` / the repo's configured linter — clean.
- **Dogfood the lifecycle on the branch** (project discipline): run the real
  `decision ratify` / edit a body / `decision check` against this repo's own records to
  confirm the gate fires and the JSON shape is stable. (Adding `integrity_violations` /
  `unhashed_ratified` *inside* `data` is additive — the 6 frozen top-level envelope keys
  are untouched and `test_check_json_envelope` asserts sub-keys by presence, not
  exclusivity, so it does not break.)
- Confirm the CI `decision check` job (the existing standalone doc-check / decision gate)
  now also blocks on integrity violations — no new workflow, same gate.

---

## Out of scope here — Tool B (executable checks): needs a brainstorm round first

Tool B (design §4) is **not planned in this document**. It is blocked on one unresolved
design question, not on effort:

- A check is **repo-level** (e.g. `! grep -rn "md5(.*password" src/`), but the
  counterexample is an **isolated bad snippet**. "Show it biting" requires running the
  repo-level check *as if the snippet were present* — i.e. materializing the snippet into
  the scanned tree, running, then reverting. The composition of "arbitrary repo check ×
  isolated counterexample" was never nailed in the brainstorm (design §4 says *that* it
  must fail on the counterexample, not *how*).
- Until that is resolved, the bite-test, the inline check/counterexample body format, the
  `--changed` scoping, and the hard:context ratio report cannot be planned honestly.

**Next step for Tool B:** a focused brainstorm on the counterexample mechanism →
update design §4 → then its own TDD plan (PR-2). Tracked in `private/OPEN-ITEMS.md`.
