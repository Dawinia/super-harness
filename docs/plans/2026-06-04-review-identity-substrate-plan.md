# Reviewer-Identity & Review-Independence Substrate — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

> NOTE: like the design doc, this plan carries NO `change:` / `stage:` frontmatter
> so the SuperpowersAdapter scan does not auto-declare a change from it.

**Goal:** Record real author/reviewer identity (today every event is the constant
`Actor(type="human", identifier="cli")`) and disclose review independence
(self-signed / independent / skipped / unattributed; `ci` forward-compat) at the
merge boundary (`attest verify`). Disclosure, not enforcement.

**Architecture:** New `core/identity.py` resolves an identity (`--as` > env
`SUPER_HARNESS_ACTOR` > `git config user.email` > `"cli"`). `change start` and the
`review` verbs stamp it on `actor.identifier`; `review skip` additionally stamps a
structured `payload["skipped"]=True`. A pure `derive_independence(events)` in
`engineering/attestation.py` classifies via the design §4.1 truth table; the
`attest verify` CLI prints a per-validated-attestation disclosure line
(non-failing) and adds an `independence` field to `--json`.

**Tech Stack:** Python 3.10+, click, pytest. Run tests with
`PATH="$(pwd)/.venv/bin:$PATH"` so console scripts resolve.

**Design:** `docs/plans/2026-06-04-review-identity-substrate-design.md` (read §2
proves/does-not-prove, §3.1 resolve_identity, §4.1 truth table, §4.2 disclosure).

**Honesty guardrail:** this is disclosure substrate. No task may make
`attest verify` pass/fail depend on independence. Every disclosure test asserts
the exit code is unchanged.

---

### Task 1: `core/identity.py` — `resolve_identity` + git seam

**Files:**
- Create: `src/super_harness/core/identity.py`
- Test: `tests/unit/core/test_identity.py`

**Step 1: Write failing tests**

```python
# tests/unit/core/test_identity.py
from pathlib import Path
from unittest.mock import patch

from super_harness.core.identity import resolve_identity


def test_override_wins_over_everything():
    with patch("super_harness.core.identity._git_config_email", return_value="git@x"):
        assert resolve_identity(Path("."), override="me@flag") == "me@flag"


def test_env_wins_over_git(monkeypatch):
    monkeypatch.setenv("SUPER_HARNESS_ACTOR", "env@x")
    with patch("super_harness.core.identity._git_config_email", return_value="git@x"):
        assert resolve_identity(Path(".")) == "env@x"


def test_git_used_when_no_override_or_env(monkeypatch):
    monkeypatch.delenv("SUPER_HARNESS_ACTOR", raising=False)
    with patch("super_harness.core.identity._git_config_email", return_value="git@x"):
        assert resolve_identity(Path(".")) == "git@x"


def test_fallback_cli_when_all_unset(monkeypatch):
    monkeypatch.delenv("SUPER_HARNESS_ACTOR", raising=False)
    with patch("super_harness.core.identity._git_config_email", return_value=None):
        assert resolve_identity(Path(".")) == "cli"


def test_blank_override_and_env_fall_through(monkeypatch):
    monkeypatch.setenv("SUPER_HARNESS_ACTOR", "   ")
    with patch("super_harness.core.identity._git_config_email", return_value=None):
        assert resolve_identity(Path("."), override="  ") == "cli"


def test_git_seam_swallows_nonzero(monkeypatch):
    # not-a-repo / unset email → git exits non-zero or empty → None, no raise
    class _P:
        returncode = 1
        stdout = ""
        stderr = "not a git repo"
    monkeypatch.setattr("super_harness.core.identity.subprocess.run", lambda *a, **k: _P())
    from super_harness.core.identity import _git_config_email
    assert _git_config_email(Path(".")) is None


def test_git_seam_swallows_missing_binary(monkeypatch):
    def _boom(*a, **k):
        raise FileNotFoundError("git")
    monkeypatch.setattr("super_harness.core.identity.subprocess.run", _boom)
    from super_harness.core.identity import _git_config_email
    assert _git_config_email(Path(".")) is None


def test_git_seam_strips_and_blank_is_none(monkeypatch):
    class _P:
        returncode = 0
        stdout = "  \n"
        stderr = ""
    monkeypatch.setattr("super_harness.core.identity.subprocess.run", lambda *a, **k: _P())
    from super_harness.core.identity import _git_config_email
    assert _git_config_email(Path(".")) is None
```

**Step 2: Run, verify fail**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/core/test_identity.py -v`
Expected: FAIL (module not found).

**Step 3: Implement**

```python
# src/super_harness/core/identity.py
# L1 anchor (HG-12 cut 1) — @capability:capability-actor-identity
"""Resolve the identity recorded on emitted events' ``actor.identifier``.

Today every CLI emit uses the placeholder ``"cli"``; this resolves a real
identity so review independence can be disclosed at the merge boundary
(see docs/plans/2026-06-04-review-identity-substrate-design.md §3.1). The
``git config`` call is an isolated, mockable seam — failures (no repo, unset
email, no git binary) fall through to ``"cli"`` and never raise.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

_ENV_VAR = "SUPER_HARNESS_ACTOR"
FALLBACK_IDENTITY = "cli"


def _git_config_email(workspace: Path) -> str | None:
    """Return ``git config user.email`` for ``workspace`` stripped, or None.

    Swallows every failure mode (non-zero exit = not-a-repo / unset email,
    ``FileNotFoundError`` = no git binary) → None. Whitespace-only → None.
    """
    try:
        proc = subprocess.run(
            ["git", "config", "user.email"],
            cwd=str(workspace),
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return None
    if proc.returncode != 0:
        return None
    email = proc.stdout.strip()
    return email or None


def resolve_identity(workspace: Path, override: str | None = None) -> str:
    """Resolve identity: override > env SUPER_HARNESS_ACTOR > git email > "cli".

    First non-empty (after ``.strip()``) wins. Always returns a non-empty str.
    """
    if override and override.strip():
        return override.strip()
    env = os.environ.get(_ENV_VAR)
    if env and env.strip():
        return env.strip()
    git = _git_config_email(workspace)
    if git:
        return git
    return FALLBACK_IDENTITY
```

**Step 4: Run, verify pass.** `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/core/test_identity.py -v`

**Step 5: Commit**

```bash
git add src/super_harness/core/identity.py tests/unit/core/test_identity.py
git commit -m "feat(identity): resolve_identity (--as > env > git email > cli)"
```

---

### Task 2: identity on `intent_declared` (`change start --as`)

**Files:**
- Modify: `src/super_harness/cli/change.py` (the `start` command; delete stale
  TODO/comment at ~lines 63-65)
- Test: `tests/integration/cli/test_change.py`

**Step 1: Write failing tests** — use the REAL patterns in
`tests/integration/cli/test_change.py`: the module-local `_init(tmp_path)` (runs
`init`), `CliRunner().invoke(main, ["--workspace", str(tmp_path), ...])`, and reads
events by parsing `(tmp_path/".harness"/"events.jsonl")` lines with `json.loads`
(existing pattern ~`test_change.py:28-32`). There is NO `run_cli`/`init_harness`/
`last_event` — do not invent them.

```python
# add to tests/integration/cli/test_change.py
import json
from click.testing import CliRunner
from super_harness.cli.main import main  # match the file's existing import


def _last_event(tmp_path, *, type, change_id):
    lines = (tmp_path / ".harness" / "events.jsonl").read_text().splitlines()
    evs = [json.loads(x) for x in lines if x.strip()]
    return [e for e in evs if e["type"] == type and e["change_id"] == change_id][-1]


def test_change_start_records_as_identity(tmp_path):
    _init(tmp_path)
    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "change", "start", "feat-x",
               "--as", "alice@example.com"])
    assert r.exit_code == 0
    ev = _last_event(tmp_path, type="intent_declared", change_id="feat-x")
    assert ev["actor"]["identifier"] == "alice@example.com"


def test_change_start_defaults_via_resolver(tmp_path, monkeypatch):
    _init(tmp_path)
    monkeypatch.setattr(
        "super_harness.cli.change.resolve_identity", lambda ws, override=None: "git@x")
    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "change", "start", "feat-y"])
    assert r.exit_code == 0
    ev = _last_event(tmp_path, type="intent_declared", change_id="feat-y")
    assert ev["actor"]["identifier"] == "git@x"
```

> Test-env identity note: in the hermetic `tmp_path` (NOT a git repo, no `--as`,
> no env), `resolve_identity` falls through to `"cli"`. So **existing `change
> resume` snapshots that show `(cli)` stay `(cli)` in the suite** — do NOT "fix"
> them; the real-email behavior only manifests in a real git workspace.

**Step 2: Run, verify fail.**
`PATH="$(pwd)/.venv/bin:$PATH" pytest tests/integration/cli/test_change.py -k as_identity -v`

**Step 3: Implement**

In `cli/change.py`:
- Add import: `from super_harness.core.identity import resolve_identity`.
- On `start`, add option (note `--as` is a Python keyword → explicit dest):
  ```python
  @click.option("--as", "as_identity", default=None,
                help="Identity recorded on the event (default: git config user.email).")
  ```
  and thread `as_identity` into the signature.
- Replace `actor=Actor(type="human", identifier="cli"),` (in `start`) with:
  ```python
  actor=Actor(type="human", identifier=resolve_identity(root, as_identity)),
  ```
- Delete the stale block at ~lines 63-65 (`TODO(post-v0.1): distinguish CLI
  invocations by user ...` and the now-false `# ... single "cli" identifier is
  used for every Actor(...) below.`). Leave `abandon` unchanged (out of scope).

**Step 4: Run, verify pass.**
`PATH="$(pwd)/.venv/bin:$PATH" pytest tests/integration/cli/test_change.py -v`
Also run the full `change` test file to catch any `change resume` snapshot that
now shows a real identity — update such snapshots to the resolved value, **do NOT
re-pin to `"cli"`**.

**Step 5: Commit**

```bash
git add src/super_harness/cli/change.py tests/integration/cli/test_change.py
git commit -m "feat(change): record real author identity on intent_declared"
```

---

### Task 3: identity on review verdicts + structured `skipped` marker

**Files:**
- Modify: `src/super_harness/cli/review.py` (`_emit_verdict` + approve/reject/skip)
- Test: `tests/unit/cli/test_review.py`

**Step 1: Write failing tests** — use the REAL patterns in
`tests/unit/cli/test_review.py`: the module-local `_seed(ws, slug, *types)` to drive
state, `CliRunner().invoke(main, ["--workspace", str(ws), ...])`, and read events
via `json.loads` of `events.jsonl` lines (existing patterns ~`test_review.py:86-115`).
No `invoke`/`last_event`. To reach `AWAITING_CODE_REVIEW`, seed the full prefix.

```python
# add to tests/unit/cli/test_review.py
def _last(ws, *, type, change_id):
    lines = (ws / ".harness" / "events.jsonl").read_text().splitlines()
    evs = [json.loads(x) for x in lines if x.strip()]
    return [e for e in evs if e["type"] == type and e["change_id"] == change_id][-1]


_PREFIX = ("intent_declared", "plan_ready", "plan_approved",
           "implementation_started", "verification_passed", "implementation_complete")


def test_review_approve_records_as_identity(tmp_path):
    _seed(tmp_path, "feat-x", *_PREFIX)  # → AWAITING_CODE_REVIEW
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "review", "approve",
        "feat-x", "--reviewer", "code-reviewer", "--as", "bob@example.com"])
    assert r.exit_code == 0
    ev = _last(tmp_path, type="code_review_passed", change_id="feat-x")
    assert ev["actor"]["identifier"] == "bob@example.com"
    assert "skipped" not in ev["payload"]          # approve must NOT set the marker


def test_review_reject_records_as_identity(tmp_path):
    _seed(tmp_path, "feat-x", *_PREFIX)
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "review", "reject",
        "feat-x", "--reviewer", "code-reviewer", "--as", "carol@example.com"])
    assert r.exit_code == 0
    ev = _last(tmp_path, type="code_review_failed", change_id="feat-x")
    assert ev["actor"]["identifier"] == "carol@example.com"


def test_review_skip_sets_structured_marker(tmp_path):
    _seed(tmp_path, "feat-x", *_PREFIX)
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "review", "skip",
        "feat-x", "--reviewer", "code-reviewer", "--reason", "on vacation"])
    assert r.exit_code == 0
    ev = _last(tmp_path, type="code_review_passed", change_id="feat-x")
    assert ev["payload"]["skipped"] is True          # marker, not the reason
    assert ev["payload"]["reason"] == "on vacation"  # reason stays free text
```

> Verify the exact `_seed` signature / state-prefix in the file and adjust the
> tuple so the change actually reaches `AWAITING_CODE_REVIEW` (the existing skip/
> approve tests at ~`test_review.py:86-115` show the working prefix — copy it).

**Step 2: Run, verify fail.**
`PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/cli/test_review.py -k "as_identity or structured_marker" -v`

**Step 3: Implement**

In `cli/review.py`:
- Import `resolve_identity`.
- Add `as_identity: str | None = None` param to `_emit_verdict` and an
  `extra_payload: dict | None = None` param.
- Replace `actor=Actor(type="human", identifier="cli")` with
  `actor=Actor(type="human", identifier=resolve_identity(root, as_identity))`.
- Build payload as `{"reviewer": reviewer, "reason": reason, **(extra_payload or {})}`.
- Add the `--as` option (`as_identity` dest) to `approve`, `reject`, `skip` and
  pass it through.
- In `skip` only, pass `extra_payload={"skipped": True}`. `approve`/`reject` pass
  nothing extra.

**Step 4: Run, verify pass.**
`PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/cli/test_review.py -v`

**Step 5: Commit**

```bash
git add src/super_harness/cli/review.py tests/unit/cli/test_review.py
git commit -m "feat(review): record reviewer identity + structured skip marker"
```

---

### Task 4: `derive_independence` truth table + `independence_for_attestation`

**Files:**
- Modify: `src/super_harness/engineering/attestation.py`
- Test: `tests/unit/engineering/test_attestation.py`

**Step 1: Write failing tests** (one row per truth-table case)

```python
# add to tests/unit/engineering/test_attestation.py
from super_harness.core.events import Actor, Event
from super_harness.engineering.attestation import derive_independence


def _ev(t, ident, atype="human", payload=None):
    return Event(event_id="e", type=t, change_id="c", timestamp="t",
                 actor=Actor(type=atype, identifier=ident), framework="plain",
                 payload=payload or {})


def test_independent_when_reviewer_differs():
    evs = [_ev("intent_declared", "alice@x"), _ev("code_review_passed", "bob@x")]
    r = derive_independence(evs)
    assert r["code_review"]["classification"] == "independent"
    assert r["code_review"]["reviewer"] == "bob@x"


def test_self_signed_when_same_identity():
    evs = [_ev("intent_declared", "alice@x"), _ev("code_review_passed", "alice@x")]
    assert derive_independence(evs)["code_review"]["classification"] == "self-signed"


def test_skipped_marker_overrides_identity_match():
    evs = [_ev("intent_declared", "alice@x"),
           _ev("code_review_passed", "alice@x", payload={"skipped": True})]
    assert derive_independence(evs)["code_review"]["classification"] == "skipped"


def test_ci_forward_compat_via_constructed_event():
    evs = [_ev("intent_declared", "alice@x"),
           _ev("code_review_passed", "ci-runner", atype="ci")]
    assert derive_independence(evs)["code_review"]["classification"] == "ci"


def test_unattributed_when_author_is_cli():
    evs = [_ev("intent_declared", "cli"), _ev("code_review_passed", "bob@x")]
    assert derive_independence(evs)["code_review"]["classification"] == "unattributed"


def test_unattributed_when_reviewer_is_cli():
    evs = [_ev("intent_declared", "alice@x"), _ev("code_review_passed", "cli")]
    assert derive_independence(evs)["code_review"]["classification"] == "unattributed"


def test_unattributed_legacy_cli_pair_not_selfsigned():
    evs = [_ev("intent_declared", "cli"), _ev("code_review_passed", "cli")]
    assert derive_independence(evs)["code_review"]["classification"] == "unattributed"


def test_unattributed_when_no_code_review():
    evs = [_ev("intent_declared", "alice@x")]
    assert derive_independence(evs)["code_review"]["classification"] == "unattributed"


def test_last_code_review_passed_wins():
    evs = [_ev("intent_declared", "alice@x"),
           _ev("code_review_passed", "alice@x"),   # reject cycle, then:
           _ev("code_review_passed", "bob@x")]
    assert derive_independence(evs)["code_review"]["classification"] == "independent"
    assert derive_independence(evs)["code_review"]["reviewer"] == "bob@x"
```

**Step 2: Run, verify fail.**
`PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/engineering/test_attestation.py -k independence -v`

**Step 3: Implement** (in `attestation.py`)

```python
from super_harness.core.events import Event, EventSchemaError, parse_event_line

PLACEHOLDER_IDENTITY = "cli"


def derive_independence(events: list[Event]) -> dict:
    """Classify code-review independence from a change's events (pure).

    Truth table (first match wins) — see design §4.1. Discloses code-review only.
    """
    author = next(
        (e.actor.identifier for e in events if e.type == "intent_declared"), None
    )
    reviews = [e for e in events if e.type == "code_review_passed"]
    if not reviews:
        cls, reviewer, skipped = "unattributed", None, False
    else:
        r = reviews[-1]  # last wins (reject→re-review cycles)
        reviewer = r.actor.identifier
        skipped = bool(r.payload.get("skipped") is True)
        if r.actor.type == "ci":                       # row 2 (forward-compat)
            cls = "ci"
        elif skipped:                                  # row 3 (structured marker)
            cls = "skipped"
        elif reviewer == PLACEHOLDER_IDENTITY or author == PLACEHOLDER_IDENTITY:
            cls = "unattributed"                       # row 4 (before equality)
        elif reviewer == author:                       # row 5
            cls = "self-signed"
        else:                                          # row 6
            cls = "independent"
    return {
        "author": author,
        "code_review": {"classification": cls, "reviewer": reviewer, "skipped": skipped},
    }


def independence_for_attestation(att_path: Path) -> dict:
    """Read an attestation file tolerantly (warn+skip malformed lines, never
    raise — disclosure is non-failing) and derive independence."""
    events: list[Event] = []
    for raw in att_path.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if not s:
            continue
        try:
            events.append(parse_event_line(s))
        except EventSchemaError:
            continue  # tolerant: matches reducer/check_attestation policy
    return derive_independence(events)
```

**Step 4: Run, verify pass.**
`PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/engineering/test_attestation.py -v`

**Step 5: Commit**

```bash
git add src/super_harness/engineering/attestation.py tests/unit/engineering/test_attestation.py
git commit -m "feat(attest): derive_independence truth table (disclosure substrate)"
```

---

### Task 5: `attest verify` discloses independence (non-failing)

**Files:**
- Modify: `src/super_harness/cli/attest.py` (the `attest_verify` command)
- Test: `tests/unit/cli/test_attest.py`

**Step 1: Write failing tests** — the REAL `test_attest.py` pattern monkeypatches
`attest_mod._git_name_status` (~lines 49/66/75) and uses a trivial `_init`. It has
**no committed-attestation fixture** — that lives in
`tests/unit/engineering/test_attestation.py` as `_ready_with_scope(root, slug, files)`
(~lines 160-170), and **all existing fixtures stamp `identifier="cli"`** (so a naive
reuse yields `unattributed`, not `self-signed`/`independent`). So **write the
attestation `.jsonl` by hand** with controllable `actor.identifier` to exercise the
classes.

```python
# add to tests/unit/cli/test_attest.py
import json
from pathlib import Path
from click.testing import CliRunner
import super_harness.cli.attest as attest_mod
from super_harness.cli.main import main


def _line(t, ident, atype="human", payload=None):
    return json.dumps({"event_id": "e", "type": t, "change_id": "feat-x",
        "timestamp": "2026-06-04T00:00:00Z",
        "actor": {"type": atype, "identifier": ident},
        "framework": "plain", "payload": payload or {}})


def _write_attestation(root: Path, slug: str, author: str, reviewer: str,
                       *, extra_lines=()):
    """Hand-write a complete READY_TO_MERGE attestation with chosen identities."""
    d = root / ".harness" / "attestations"
    d.mkdir(parents=True, exist_ok=True)
    lines = [
        _line("intent_declared", author),
        _line("plan_ready", "cli", payload={"scope": {"files": ["src/x.py"]}}),
        _line("plan_approved", "cli"),
        _line("implementation_started", "cli"),
        _line("verification_passed", "cli"),
        _line("implementation_complete", "cli"),
        _line("code_review_passed", reviewer),
        *extra_lines,
    ]
    (d / f"{slug}.jsonl").write_text("\n".join(lines) + "\n")


def _verify(tmp_path, monkeypatch, diff, *, json_mode=False):
    _init(tmp_path)  # reuse the module's existing init helper
    monkeypatch.setattr(attest_mod, "_git_name_status", lambda b, h, c: diff)
    args = ["--workspace", str(tmp_path)]
    if json_mode:
        args.append("--json")
    args += ["attest", "verify", "--base", "main", "--head", "HEAD"]
    return CliRunner().invoke(main, args)


_DIFF = "A\t.harness/attestations/feat-x.jsonl\nM\tsrc/x.py\n"


def test_verify_discloses_self_signed_line(tmp_path, monkeypatch):
    _write_attestation(tmp_path, "feat-x", "alice@x", "alice@x")  # author==reviewer
    r = _verify(tmp_path, monkeypatch, _DIFF)
    assert "review independence: self-signed" in r.output
    assert r.exit_code == 0            # disclosure NEVER changes pass/fail


def test_verify_discloses_independent_line(tmp_path, monkeypatch):
    _write_attestation(tmp_path, "feat-x", "alice@x", "bob@x")
    r = _verify(tmp_path, monkeypatch, _DIFF)
    assert "review independence: independent — bob@x" in r.output
    assert r.exit_code == 0


def test_verify_no_validated_attestation_prints_no_independence_line(tmp_path, monkeypatch):
    # a diff with a subject file but NO added covering attestation → FAIL, no line
    r = _verify(tmp_path, monkeypatch, "M\tsrc/x.py\n")
    assert "review independence:" not in r.output


def test_verify_json_has_independence_field_and_single_line(tmp_path, monkeypatch):
    _write_attestation(tmp_path, "feat-x", "alice@x", "bob@x")
    r = _verify(tmp_path, monkeypatch, _DIFF, json_mode=True)
    # JSON path must NOT leak the human disclosure text and must stay one line:
    assert "review independence:" not in r.output
    payload = json.loads(r.output)
    assert "independence" in payload["data"]


def test_verify_tolerated_malformed_line_still_discloses(tmp_path, monkeypatch):
    _write_attestation(tmp_path, "feat-x", "alice@x", "bob@x",
                       extra_lines=("{ this is not valid json",))
    r = _verify(tmp_path, monkeypatch, _DIFF)
    assert "review independence:" in r.output
    assert r.exit_code == 0            # no crash out of the non-failing path
```

> Confirm `_init`/`main` import names against the real `test_attest.py`; confirm a
> hand-written attestation with this event sequence reaches `READY_TO_MERGE` (mirror
> the sequence `_ready_with_scope` uses in `test_attestation.py`). A
> tolerated-malformed line must NOT break `check_attestation` (it warns+skips) so the
> attestation still validates and the disclosure path still runs.

**Step 2: Run, verify fail.**
`PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/cli/test_attest.py -k independence -v`

**Step 3: Implement.** Two precise edits in `cli/attest.py` `attest_verify`
(current body: `verdict = verify_attestations(...)` → `data = {...}` literal at
`cli/attest.py:142-147` → `if ctx.obj.get("json"): … elif verdict.ok: … else: …`):

**(a) Build `independence` and add it as a key to the EXISTING `data` literal**
(do not append after the branches — it must be in `data` before the JSON branch
reads it):

```python
from super_harness.engineering.attestation import (
    ATTESTATIONS_DIRNAME, independence_for_attestation, verify_attestations,
)

# immediately after `verdict = verify_attestations(...)`:
independence = [
    {"slug": slug, **independence_for_attestation(
        root / ATTESTATIONS_DIRNAME / f"{slug}.jsonl")["code_review"]}
    for slug in verdict.attestations          # validated + newly-ADDED only
]
data: dict[str, Any] = {
    "subjects": verdict.subjects,
    "covered": verdict.covered,
    "attestations": verdict.attestations,
    "blockers": verdict.blockers,
    "independence": independence,             # NEW — sits INSIDE data
}
```

**(b) Restructure the three-way branch so disclosure lines print ONLY in the human
path** (never before the `if json`, or it would corrupt the single-line JSON
envelope — must-fix). Replace the `if json / elif ok / else` with:

```python
if ctx.obj.get("json"):
    click.echo(json_envelope(
        command="attest verify",
        status="pass" if verdict.ok else "fail",
        exit_code=EXIT_OK if verdict.ok else EXIT_VALIDATION,
        data=data,
        errors=[{"code": "validation", "message": b} for b in verdict.blockers],
    ))
else:
    if not ctx.obj.get("quiet"):
        for item in independence:
            click.echo(_independence_line(item))
    if verdict.ok:
        if not ctx.obj.get("quiet"):
            click.echo(f"attest verify: PASS ({len(verdict.subjects)} file(s) covered)")
    else:
        click.echo(format_error(
            subcommand="attest verify",
            message=f"{len(verdict.blockers)} blocker(s):\n  - "
            + "\n  - ".join(verdict.blockers),
            hint="Each changed file must be in a complete lifecycle attestation's scope.",
        ), err=True)
sys.exit(EXIT_OK if verdict.ok else EXIT_VALIDATION)
```

Module-level helper (plain ASCII, no emoji):

```python
def _independence_line(item: dict) -> str:
    cls, who = item["classification"], item.get("reviewer")
    if cls == "self-signed":
        return f"review independence: self-signed (self-review) — {who}"
    if cls == "independent":
        return f"review independence: independent — {who}"
    if cls == "skipped":
        return f"review independence: skipped — {who}"
    if cls == "ci":
        return "review independence: ci"
    return 'review independence: unattributed (legacy "cli" placeholder)'
```

Pass/fail logic and exit codes are byte-for-byte unchanged; only the human-branch
disclosure echo + the `independence` data key are added.

**Step 4: Run, verify pass.**
`PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/cli/test_attest.py -v`

**Step 5: Commit**

```bash
git add src/super_harness/cli/attest.py tests/unit/cli/test_attest.py
git commit -m "feat(attest): disclose review independence at merge boundary (non-failing)"
```

---

### Task 6: cli-reference regen + full suite

**Files:**
- Modify: `docs/cli-reference.md` (regen — **mandatory**, CI-enforced).
- Test: full suite + ruff + mypy.

**Step 1: Regenerate cli-reference (mandatory — drift check is enforced at
`.github/workflows/test.yml`)**

```bash
PATH="$(pwd)/.venv/bin:$PATH" python -m scripts.gen_cli_reference
PATH="$(pwd)/.venv/bin:$PATH" python -m scripts.gen_cli_reference --check
```
The `--as` options on `change start` and `review approve|reject|skip` change the
rendered tables, so the `--check` would FAIL without the regen. No `_EXIT_CODES`
edit is needed (these are existing commands; `--as` is just a new option). Verify
with `git diff docs/cli-reference.md`.

**Step 2: Anchor sanity (informational)**

The `@capability:capability-actor-identity` sentinel added in `identity.py`'s header
needs **no** L1 spec row to be safe: the freshness/presence check runs
L1-anchors→sentinel only (there is no reverse "every sentinel has an L1" check), so
an orphan sentinel is harmless and `anchor sync` simply indexes it. Registering the
L1 capability doc is a deferred follow-up (§5). Optionally run
`PATH="$(pwd)/.venv/bin:$PATH" super-harness anchor sync` to confirm it parses.

**Step 3: Full verification**

```bash
PATH="$(pwd)/.venv/bin:$PATH" ruff check .
PATH="$(pwd)/.venv/bin:$PATH" mypy src
PATH="$(pwd)/.venv/bin:$PATH" pytest -q
```
Expected: all green (prior baseline ~1258 + new tests).

**Step 4: Commit**

```bash
git add docs/cli-reference.md docs/reference/capabilities/ 2>/dev/null; git add -A
git commit -m "docs(attest): regen cli-reference for --as; anchor for identity"
```

---

## Post-implementation (handled outside the plan, by the lifecycle wrapper)

`done` (verify) → independent code-review subagent → `review approve --reviewer
code-reviewer` → `attest write <slug>` → commit attestation → PR → CI green (incl.
required `attest-verify`) → merge → `on-merge` to ARCHIVED → update
OPEN-ITEMS/HARNESS-GAPS/memory with the deferred anchor items (P1/P3, plan_approved
disclosure, other-emit-site identity).
