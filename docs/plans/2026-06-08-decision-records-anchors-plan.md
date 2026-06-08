# Decision records + rooted anchors + dangling check — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `code ↔ decision` link: human-ratified decision records on disk, `@decision:<id>` code anchors rooted in them, and a deterministic CI `decision check`.

**Architecture:** Pure logic in `core/` (decision records, source scope, dangling check), a thin click `decision` CLI group on top, plus a parametrized (backward-compatible) anchor scanner. The check is a whole-repo invariant scan; exit codes follow the repo's global convention (`exit_codes.py`).

**Why a cold-path CLI command, not a Sensor:** the existing `@capability` checks are Sensors because they are *change-scoped + tier-aware* (read a change's `affected_anchors`, gate per-tier, dispatched on lifecycle events). `decision check` is a *whole-repo, change-agnostic, deterministic invariant* ("every anchor has a ratified root; every ratified decision has code") that CI runs from a checkout — so it belongs on the cold path as a CLI command, exactly like the sibling merge gate `attest verify` (also a CLI command, not a Sensor). Unlike `attest verify` (diff-based: "is each *changed* file covered"), this check is whole-repo because a dangling-up can be introduced by *deleting* a decision in another PR — a diff would miss it. This is additive: the `@capability` Sensor rail is untouched this slice.

**Tech Stack:** Python 3.10+, click, PyYAML, pytest. Spec: `docs/plans/2026-06-08-decision-records-anchors-design.md`. Run tests with `PATH="$(pwd)/.venv/bin:$PATH"` so console scripts resolve.

**Conventions:** Exit codes from `super_harness.exit_codes` (`EXIT_OK=0`, `EXIT_VALIDATION=2`, `EXIT_NO_CONFIG=3`). Identity via `core/identity.py::resolve_identity`. Timestamps via `core/clock.py::utc_now_iso`. Errors via `cli/errors.py::format_error`.

---

## Task 1: Shared frontmatter splitter + decision record model

**Files:**
- Create: `src/super_harness/core/frontmatter.py` (shared, no-drift splitter)
- Create: `src/super_harness/core/decisions.py`
- Modify: `src/super_harness/adapters/framework/superpowers.py` (delegate to shared splitter)
- Test: `tests/unit/core/test_frontmatter.py`, `tests/unit/core/test_decisions.py`

> Why the shared splitter: `adapters/framework/superpowers.py::_parse_frontmatter`
> already parses leading `--- … ---` frontmatter. Duplicating it in `decisions.py`
> is exactly the drift the codebase refuses (cf. `clock.py` / `anchor_scanner.py`
> "stops copies from drifting"). One splitter; callers pick the policy (adapter maps
> malformed→`{}`, decisions raise fail-closed).

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/core/test_frontmatter.py
from super_harness.core.frontmatter import split_frontmatter


def test_splits_mapping_and_body():
    assert split_frontmatter("---\na: 1\n---\nbody\n") == ({"a": 1}, "body")


def test_none_on_no_fence():
    assert split_frontmatter("no fence\n") is None


def test_none_on_unclosed():
    assert split_frontmatter("---\na: 1\n") is None


def test_none_on_non_mapping():
    assert split_frontmatter("---\n- a\n- b\n---\nx\n") is None


def test_none_on_bad_yaml():
    assert split_frontmatter("---\n::: bad\n---\nx\n") is None
```

```python
# tests/unit/core/test_decisions.py
from pathlib import Path

import pytest

from super_harness.core.decisions import (
    Decision,
    RecordError,
    decisions_dir,
    load_decisions,
    parse_decision_file,
    serialize_decision,
)


def _write(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


RATIFIED = """---
id: d-auth-stateless
status: ratified
ratified_by: a@b.com
ratified_at: 2026-06-08T12:00:00Z
---
Authentication must be stateless.
"""


def test_parse_valid_ratified(tmp_path):
    p = _write(tmp_path / "docs/decisions/d-auth-stateless.md", RATIFIED)
    d = parse_decision_file(p)
    assert d.id == "d-auth-stateless"
    assert d.status == "ratified"
    assert d.ratified_by == "a@b.com"
    assert d.body == "Authentication must be stateless."


def test_parse_rejects_filename_mismatch(tmp_path):
    p = _write(tmp_path / "docs/decisions/other.md", RATIFIED)
    with pytest.raises(ValueError, match="filename"):
        parse_decision_file(p)


def test_parse_rejects_bad_status(tmp_path):
    p = _write(tmp_path / "docs/decisions/d-x.md", "---\nid: d-x\nstatus: draft\n---\nx\n")
    with pytest.raises(ValueError, match="status"):
        parse_decision_file(p)


def test_parse_rejects_uppercase_id(tmp_path):
    p = _write(tmp_path / "docs/decisions/d-X.md", "---\nid: d-X\nstatus: proposed\n---\nx\n")
    with pytest.raises(ValueError, match="id"):
        parse_decision_file(p)


def test_load_skips_reserved_and_dotfiles(tmp_path):
    root = tmp_path
    _write(root / "docs/decisions/d-a.md", "---\nid: d-a\nstatus: proposed\n---\na\n")
    _write(root / "docs/decisions/README.md", "# readme\n")
    _write(root / "docs/decisions/_template.md", "template\n")
    decisions, errors = load_decisions(root)
    assert [d.id for d in decisions] == ["d-a"]
    assert errors == []


def test_load_reports_malformed(tmp_path):
    _write(tmp_path / "docs/decisions/d-a.md", "no frontmatter here\n")
    decisions, errors = load_decisions(tmp_path)
    assert decisions == []
    assert len(errors) == 1 and errors[0].kind == "malformed"


def test_load_reports_casefolded_duplicate(tmp_path):
    # On a case-sensitive FS both files exist; ids collide under casefold.
    _write(tmp_path / "docs/decisions/d-a.md", "---\nid: d-a\nstatus: proposed\n---\na\n")
    _write(tmp_path / "docs/decisions/d-A.md", "---\nid: d-A\nstatus: proposed\n---\na\n")
    decisions, errors = load_decisions(tmp_path)
    kinds = {e.kind for e in errors}
    # d-A is rejected: invalid uppercase id (malformed) — still an error, gate blocks.
    assert errors and "malformed" in kinds


def test_load_missing_dir_is_empty(tmp_path):
    decisions, errors = load_decisions(tmp_path)
    assert decisions == [] and errors == []


def test_serialize_roundtrip(tmp_path):
    d = Decision(id="d-a", status="proposed", body="hello", path=tmp_path / "d-a.md")
    text = serialize_decision(d)
    assert text.startswith("---\nid: d-a\nstatus: proposed\n")
    assert text.rstrip().endswith("hello")


def test_is_valid_id():
    from super_harness.core.decisions import is_valid_id

    assert is_valid_id("d-auth-stateless")
    assert not is_valid_id("d-Auth")
    assert not is_valid_id("d auth")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/core/test_decisions.py -v`
Expected: FAIL with `ModuleNotFoundError: super_harness.core.decisions`.

- [ ] **Step 3a: Write the shared frontmatter splitter**

```python
# src/super_harness/core/frontmatter.py
"""Shared leading-YAML-frontmatter splitter (single source, no drift).

``--- … ---`` block at the top of a file → ``(mapping, body)``. Returns None
when there is no opening fence, no closing fence, a YAML parse error, or the
frontmatter is not a mapping. Callers pick the policy: the read-only adapter
scan maps None→{}; decision-record loading raises (fail-closed).
"""
from __future__ import annotations

import yaml


def split_frontmatter(text: str) -> tuple[dict, str] | None:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            try:
                data = yaml.safe_load("\n".join(lines[1:i]))
            except yaml.YAMLError:
                return None
            if not isinstance(data, dict):
                return None
            return data, "\n".join(lines[i + 1 :]).strip()
    return None
```

- [ ] **Step 3b: Refactor `superpowers._parse_frontmatter` to delegate**

In `src/super_harness/adapters/framework/superpowers.py`, replace the body of
`_parse_frontmatter` (keep its signature + `{}`-on-malformed contract):

```python
from super_harness.core.frontmatter import split_frontmatter  # add to imports


def _parse_frontmatter(text: str) -> dict[str, Any]:
    parsed = split_frontmatter(text)
    return parsed[0] if parsed is not None else {}
```

Run its existing tests to confirm no regression:
`PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/adapters/framework/test_superpowers.py -v`

- [ ] **Step 3c: Write the decision record module**

```python
# src/super_harness/core/decisions.py
"""Decision records — the human-ratified unit anchors root in.

One file per decision at ``docs/decisions/<id>.md`` (markdown + YAML
frontmatter). Pure: parse / validate / load / serialize. No CLI, no events.
See docs/plans/2026-06-08-decision-records-anchors-design.md §2 / §4.4.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

from super_harness.core.frontmatter import split_frontmatter

DecisionStatus = Literal["proposed", "ratified", "superseded", "retired"]
_VALID_STATUSES = frozenset({"proposed", "ratified", "superseded", "retired"})
_ID_RE = re.compile(r"^[a-z0-9_-]+$")
_RESERVED_NAMES = frozenset({"README.md"})


@dataclass
class Decision:
    id: str
    status: DecisionStatus
    ratified_by: str | None = None
    ratified_at: str | None = None
    supersedes: str | None = None
    superseded_by: str | None = None
    body: str = ""
    path: Path | None = None


@dataclass
class RecordError:
    kind: Literal["duplicate_id", "malformed"]
    file: str
    detail: str
    id: str | None = None


def decisions_dir(workspace_root: Path) -> Path:
    return workspace_root / "docs" / "decisions"


def is_valid_id(candidate: str) -> bool:
    return bool(_ID_RE.match(candidate))


def parse_decision_file(path: Path) -> Decision:
    """Parse one record. Raises ValueError if malformed (§4.4 predicate)."""
    parsed = split_frontmatter(path.read_text(encoding="utf-8"))
    if parsed is None:
        raise ValueError("missing or malformed frontmatter")
    data, body = parsed
    did = data.get("id")
    if not isinstance(did, str) or not _ID_RE.match(did):
        raise ValueError(f"missing or invalid id (must match {_ID_RE.pattern})")
    if path.stem != did:
        raise ValueError(f"filename stem {path.stem!r} != id {did!r}")
    status = data.get("status")
    if status not in _VALID_STATUSES:
        raise ValueError(f"invalid status {status!r}")
    return Decision(
        id=did,
        status=status,
        ratified_by=data.get("ratified_by"),
        ratified_at=data.get("ratified_at"),
        supersedes=data.get("supersedes"),
        superseded_by=data.get("superseded_by"),
        body=body,
        path=path,
    )


def load_decisions(workspace_root: Path) -> tuple[list[Decision], list[RecordError]]:
    """Enumerate + validate every record. Fail-closed: malformed/dup → errors."""
    ddir = decisions_dir(workspace_root)
    decisions: list[Decision] = []
    errors: list[RecordError] = []
    if not ddir.is_dir():
        return decisions, errors
    seen: dict[str, str] = {}
    for p in sorted(ddir.glob("*.md")):
        if p.name in _RESERVED_NAMES or p.name.startswith(("_", ".")):
            continue
        rel = str(p.relative_to(workspace_root))
        try:
            d = parse_decision_file(p)
        except (ValueError, OSError, yaml.YAMLError) as e:
            errors.append(RecordError(kind="malformed", file=rel, detail=str(e)))
            continue
        cf = d.id.casefold()
        if cf in seen:
            errors.append(
                RecordError(
                    kind="duplicate_id",
                    id=d.id,
                    file=rel,
                    detail=f"duplicate (case-folded) of {seen[cf]!r}",
                )
            )
            continue
        seen[cf] = d.id
        decisions.append(d)
    return decisions, errors


def serialize_decision(decision: Decision) -> str:
    fm: dict[str, str] = {"id": decision.id, "status": decision.status}
    for key in ("ratified_by", "ratified_at", "supersedes", "superseded_by"):
        val = getattr(decision, key)
        if val:
            fm[key] = val
    fm_text = yaml.safe_dump(fm, sort_keys=False).strip()
    return f"---\n{fm_text}\n---\n{decision.body}\n"


def write_decision(decision: Decision) -> None:
    assert decision.path is not None
    decision.path.parent.mkdir(parents=True, exist_ok=True)
    decision.path.write_text(serialize_decision(decision), encoding="utf-8")
```

- [ ] **Step 4: Run tests to verify they pass (incl. superpowers regression)**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/core/test_frontmatter.py tests/unit/core/test_decisions.py tests/unit/adapters/framework/test_superpowers.py -v`
Expected: PASS (all, including the unchanged superpowers adapter tests).

- [ ] **Step 5: Commit**

```bash
git add src/super_harness/core/frontmatter.py src/super_harness/core/decisions.py \
        src/super_harness/adapters/framework/superpowers.py \
        tests/unit/core/test_frontmatter.py tests/unit/core/test_decisions.py
git commit -m "feat(decisions): record model + shared frontmatter splitter (dedup superpowers)"
```

---

## Task 2: source-paths scope loader (`core/source_scope.py`)

**Files:**
- Create: `src/super_harness/core/source_scope.py`
- Test: `tests/unit/core/test_source_scope.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/core/test_source_scope.py
from super_harness.core.source_scope import (
    DEFAULT_EXCLUDE,
    DEFAULT_INCLUDE,
    load_source_scope,
)


def _write(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_missing_file_returns_defaults(tmp_path):
    inc, exc = load_source_scope(tmp_path)
    assert inc == DEFAULT_INCLUDE and exc == DEFAULT_EXCLUDE


def test_reads_nested_source_paths_key(tmp_path):
    _write(
        tmp_path / ".harness/source-paths.yaml",
        "source_paths:\n  include:\n    - 'src/**'\n  exclude:\n    - 'src/vendor/**'\n",
    )
    inc, exc = load_source_scope(tmp_path)
    assert inc == ["src/**"] and exc == ["src/vendor/**"]


def test_corrupt_yaml_falls_back_to_defaults(tmp_path):
    _write(tmp_path / ".harness/source-paths.yaml", "source_paths: [::: bad")
    inc, exc = load_source_scope(tmp_path)
    assert inc == DEFAULT_INCLUDE and exc == DEFAULT_EXCLUDE


def test_missing_keys_fall_back(tmp_path):
    _write(tmp_path / ".harness/source-paths.yaml", "source_paths: {}\n")
    inc, exc = load_source_scope(tmp_path)
    assert inc == DEFAULT_INCLUDE and exc == DEFAULT_EXCLUDE
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/core/test_source_scope.py -v`
Expected: FAIL with `ModuleNotFoundError: super_harness.core.source_scope`.

- [ ] **Step 3: Write the implementation**

```python
# src/super_harness/core/source_scope.py
"""Loader for ``.harness/source-paths.yaml`` (include/exclude glob lists).

Keys are nested under a top-level ``source_paths:`` mapping. Missing file or
key → defaults. Corrupt YAML → defaults (a source-paths typo must not brick the
gate). See design §3.2.
"""
from __future__ import annotations

from pathlib import Path

import yaml

DEFAULT_INCLUDE: list[str] = ["**/*"]
DEFAULT_EXCLUDE: list[str] = ["docs/**"]


def source_paths_file(workspace_root: Path) -> Path:
    return workspace_root / ".harness" / "source-paths.yaml"


def load_source_scope(workspace_root: Path) -> tuple[list[str], list[str]]:
    f = source_paths_file(workspace_root)
    if not f.is_file():
        return list(DEFAULT_INCLUDE), list(DEFAULT_EXCLUDE)
    try:
        data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, OSError, UnicodeDecodeError):
        return list(DEFAULT_INCLUDE), list(DEFAULT_EXCLUDE)
    sp = data.get("source_paths") if isinstance(data, dict) else None
    if not isinstance(sp, dict):
        return list(DEFAULT_INCLUDE), list(DEFAULT_EXCLUDE)
    include = sp.get("include") or DEFAULT_INCLUDE
    exclude = sp.get("exclude") or DEFAULT_EXCLUDE
    return list(include), list(exclude)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/core/test_source_scope.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/super_harness/core/source_scope.py tests/unit/core/test_source_scope.py
git commit -m "feat(source-scope): load source-paths.yaml include/exclude"
```

---

## Task 3: Parametrize the scanner — keyword + exclude (`core/anchor_scanner.py`)

**Files:**
- Modify: `src/super_harness/core/anchor_scanner.py`
- Test: `tests/unit/core/test_anchor_scanner.py` (add cases; keep existing green)

Backward-compat is mandatory: defaults must reproduce today's `@capability:`,
no-exclude behavior so the running capability machinery + its tests stay green.

- [ ] **Step 1: Write the failing tests (append to existing file)**

```python
# tests/unit/core/test_anchor_scanner.py  (append)
from super_harness.core.anchor_scanner import (
    scan_sentinel_locations,
    scan_sentinels,
)


def _w(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_keyword_param_scans_decision(tmp_path):
    _w(tmp_path / "src/a.py", "# @decision:d-foo\n")
    _w(tmp_path / "src/b.py", "# @capability:cap-foo\n")
    assert scan_sentinels(tmp_path, keyword="@decision:") == {"d-foo"}
    # default keyword unchanged:
    assert scan_sentinels(tmp_path) == {"cap-foo"}


def test_exclude_globs_drops_matches(tmp_path):
    _w(tmp_path / "src/a.py", "# @decision:d-foo\n")
    _w(tmp_path / "docs/decisions/d-foo.md", "@decision:d-foo in prose\n")
    found = scan_sentinels(
        tmp_path, keyword="@decision:", exclude_globs=["docs/**", "docs/decisions/**"]
    )
    assert found == {"d-foo"}  # the docs occurrence is excluded


def test_locations_keyword_and_exclude(tmp_path):
    _w(tmp_path / "src/a.py", "x = 1  # @decision:d-foo\n")
    _w(tmp_path / "docs/decisions/d-foo.md", "@decision:d-foo\n")
    locs = scan_sentinel_locations(
        tmp_path, keyword="@decision:", exclude_globs=["docs/decisions/**"]
    )
    assert locs == {"d-foo": [("src/a.py", 1)]}


def test_uppercase_anchor_is_captured(tmp_path):
    # fail-open guard: an uppercase id must still be SEEN (so it can dangle-up).
    _w(tmp_path / "src/a.py", "# @decision:d-Foo\n")
    assert scan_sentinels(tmp_path, keyword="@decision:") == {"d-Foo"}
```

- [ ] **Step 2: Run to verify they fail**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/core/test_anchor_scanner.py -v`
Expected: FAIL (`scan_sentinels() got an unexpected keyword argument 'keyword'`).

- [ ] **Step 3: Modify the scanner (additive, backward-compatible)**

Replace the module-level `_SENTINEL_RE` usage with a per-keyword builder, and
add an exclude filter. Concretely:

```python
# near the top, replace `_SENTINEL_RE = re.compile(...)` with:
_DEFAULT_KEYWORD = "@capability:"
_CHARSET = r"([A-Za-z0-9_-]+)"  # permissive/case-preserving (design §3.1)


def _build_re(keyword: str) -> "re.Pattern[str]":
    return re.compile(re.escape(keyword) + _CHARSET)


def _excluded(rel_path: Path, exclude_globs: list[str] | None) -> bool:
    if not exclude_globs:
        return False
    rel_str = str(rel_path)
    return any(fnmatch(rel_str, g) for g in exclude_globs)
```

Then update both functions' signatures + bodies:

```python
def scan_sentinel_locations(
    root: Path,
    file_globs: list[str] | None = None,
    *,
    keyword: str = _DEFAULT_KEYWORD,
    exclude_globs: list[str] | None = None,
) -> dict[str, list[tuple[str, int]]]:
    pattern = _build_re(keyword)
    locations: dict[str, list[tuple[str, int]]] = {}
    files = _list_files(root)
    if file_globs is not None:
        files = [f for f in files if _matches_any(f.relative_to(root), file_globs)]
    for f in sorted(files):
        if not f.is_file():
            continue
        rel = f.relative_to(root)
        if _excluded(rel, exclude_globs):
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError, OSError):
            continue
        rel_str = str(rel)
        for lineno, line in enumerate(text.splitlines(), start=1):
            for m in pattern.finditer(line):
                locations.setdefault(m.group(1), []).append((rel_str, lineno))
    return locations


def scan_sentinels(
    root: Path,
    file_globs: list[str] | None = None,
    *,
    keyword: str = _DEFAULT_KEYWORD,
    exclude_globs: list[str] | None = None,
) -> set[str]:
    pattern = _build_re(keyword)
    found: set[str] = set()
    files = _list_files(root)
    if file_globs is not None:
        files = [f for f in files if _matches_any(f.relative_to(root), file_globs)]
    for f in files:
        if not f.is_file():
            continue
        if _excluded(f.relative_to(root), exclude_globs):
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError, OSError):
            continue
        for m in pattern.finditer(text):
            found.add(m.group(1))
    return found
```

Keep the existing module docstring. Do NOT remove `_list_files` /
`_matches_any` / `_MATCH_ALL_GLOBS` (still used by the include path).

- [ ] **Step 4: Run the full scanner suite to verify pass (old + new)**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/core/test_anchor_scanner.py -v`
Expected: PASS (all, including pre-existing `@capability:` tests).

- [ ] **Step 5: Commit**

```bash
git add src/super_harness/core/anchor_scanner.py tests/unit/core/test_anchor_scanner.py
git commit -m "feat(scanner): keyword + exclude_globs params (backward-compatible)"
```

---

## Task 4: Dangling check pure logic (`core/decision_check.py`)

**Files:**
- Create: `src/super_harness/core/decision_check.py`
- Test: `tests/unit/core/test_decision_check.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/core/test_decision_check.py
from pathlib import Path

from super_harness.core.decision_check import run_check


def _w(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _ratified(root: Path, did: str) -> None:
    _w(root / f"docs/decisions/{did}.md", f"---\nid: {did}\nstatus: ratified\n---\nx\n")


def test_clean_repo(tmp_path):
    _ratified(tmp_path, "d-a")
    _w(tmp_path / "src/a.py", "# @decision:d-a\n")
    r = run_check(tmp_path)
    assert r.dangling_up == [] and r.dangling_down == [] and r.errors == []
    assert r.ok is True


def test_dangling_up_anchor_no_decision(tmp_path):
    _w(tmp_path / "src/a.py", "# @decision:d-ghost\n")
    r = run_check(tmp_path)
    assert [(d.id, d.file, d.line) for d in r.dangling_up] == [("d-ghost", "src/a.py", 1)]
    assert r.ok is False


def test_anchor_to_proposed_is_dangling_up(tmp_path):
    _w(tmp_path / "docs/decisions/d-a.md", "---\nid: d-a\nstatus: proposed\n---\nx\n")
    _w(tmp_path / "src/a.py", "# @decision:d-a\n")
    r = run_check(tmp_path)
    assert [d.id for d in r.dangling_up] == ["d-a"]


def test_dangling_down_ratified_no_anchor(tmp_path):
    _ratified(tmp_path, "d-a")
    r = run_check(tmp_path)
    assert r.dangling_down == ["d-a"]
    assert r.dangling_up == [] and r.errors == []
    assert r.ok is True  # down is warn-only → ok stays True


def test_superseded_not_counted_down(tmp_path):
    _w(tmp_path / "docs/decisions/d-a.md", "---\nid: d-a\nstatus: superseded\n---\nx\n")
    r = run_check(tmp_path)
    assert r.dangling_down == []


def test_anchor_to_superseded_is_dangling_up(tmp_path):
    _w(tmp_path / "docs/decisions/d-a.md", "---\nid: d-a\nstatus: superseded\n---\nx\n")
    _w(tmp_path / "src/a.py", "# @decision:d-a\n")
    r = run_check(tmp_path)
    assert [d.id for d in r.dangling_up] == ["d-a"]  # leftover anchor blocks


def test_errors_surface(tmp_path):
    _w(tmp_path / "docs/decisions/d-a.md", "no frontmatter\n")
    r = run_check(tmp_path)
    assert len(r.errors) == 1 and r.ok is False


def test_docs_decisions_never_self_match(tmp_path):
    # A record mentioning its own anchor in prose must NOT count as an anchor.
    _w(tmp_path / "docs/decisions/d-a.md",
       "---\nid: d-a\nstatus: ratified\n---\nuse @decision:d-a in code\n")
    r = run_check(tmp_path)
    # no source anchor exists → d-a is dangling-down, NOT satisfied by its own prose
    assert r.dangling_down == ["d-a"] and r.dangling_up == []


def test_dangling_up_sorted(tmp_path):
    _w(tmp_path / "src/z.py", "# @decision:d-b\n")
    _w(tmp_path / "src/a.py", "# @decision:d-a\n")
    r = run_check(tmp_path)
    assert [d.id for d in r.dangling_up] == ["d-a", "d-b"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/core/test_decision_check.py -v`
Expected: FAIL (`ModuleNotFoundError: super_harness.core.decision_check`).

- [ ] **Step 3: Write the implementation**

```python
# src/super_harness/core/decision_check.py
"""Pure dangling check: decisions + anchors → up / down / errors.

Whole-repo invariant. Referential integrity only (design §4): blocks anchors
that name no ratified decision; warns about ratified decisions with no anchor.
``docs/decisions/**`` is ALWAYS excluded from anchor scanning so records never
self-match.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from super_harness.core.anchor_scanner import scan_sentinel_locations
from super_harness.core.decisions import RecordError, load_decisions
from super_harness.core.source_scope import load_source_scope

ANCHOR_KEYWORD = "@decision:"
ALWAYS_EXCLUDE = ["docs/decisions/**"]


@dataclass
class DanglingUp:
    id: str
    file: str
    line: int


@dataclass
class CheckResult:
    dangling_up: list[DanglingUp]
    dangling_down: list[str]
    errors: list[RecordError]

    @property
    def ok(self) -> bool:
        return not self.dangling_up and not self.errors


def run_check(workspace_root: Path) -> CheckResult:
    decisions, errors = load_decisions(workspace_root)
    ratified = {d.id for d in decisions if d.status == "ratified"}

    include, exclude = load_source_scope(workspace_root)
    locations = scan_sentinel_locations(
        workspace_root,
        file_globs=include,
        keyword=ANCHOR_KEYWORD,
        exclude_globs=exclude + ALWAYS_EXCLUDE,
    )
    anchored_ids = set(locations.keys())

    dangling_up: list[DanglingUp] = []
    for aid, locs in locations.items():
        if aid not in ratified:
            for f, ln in locs:
                dangling_up.append(DanglingUp(id=aid, file=f, line=ln))
    dangling_up.sort(key=lambda d: (d.id, d.file, d.line))

    dangling_down = sorted(ratified - anchored_ids)
    return CheckResult(dangling_up=dangling_up, dangling_down=dangling_down, errors=errors)
```

- [ ] **Step 4: Run to verify they pass**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/core/test_decision_check.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/super_harness/core/decision_check.py tests/unit/core/test_decision_check.py
git commit -m "feat(decision-check): pure dangling up/down/errors logic"
```

---

## Task 5: `decision` group + `decision new` + register

**Files:**
- Create: `src/super_harness/cli/decision.py`
- Modify: `src/super_harness/cli/__init__.py` (import + `main.add_command`)
- Test: `tests/unit/cli/test_decision.py`

CLI tests use click's `CliRunner` against `super_harness.cli.main`, in an
initialized workspace (`super-harness init` or the existing test fixture for an
initialized tree — mirror a sibling test in `tests/unit/cli/`). Each test
`chdir`s into the workspace or passes `--workspace`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/cli/test_decision.py
from pathlib import Path

from click.testing import CliRunner

from super_harness.cli import main


def _init(tmp_path: Path) -> Path:
    (tmp_path / ".harness").mkdir()
    return tmp_path


def test_new_creates_proposed(tmp_path):
    root = _init(tmp_path)
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "new",
                                  "d-auth", "--text", "Auth must be stateless."])
    assert r.exit_code == 0, r.output
    f = root / "docs/decisions/d-auth.md"
    assert f.exists()
    assert "status: proposed" in f.read_text()
    assert "Auth must be stateless." in f.read_text()


def test_new_rejects_bad_id(tmp_path):
    root = _init(tmp_path)
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "new",
                                  "d-Auth", "--text", "x"])
    assert r.exit_code == 2


def test_new_refuses_casefold_collision(tmp_path):
    root = _init(tmp_path)
    CliRunner().invoke(main, ["--workspace", str(root), "decision", "new",
                              "d-a", "--text", "x"])
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "new",
                                  "d-A", "--text", "y"])
    assert r.exit_code == 2
```

> Note on `--workspace`: confirm the root group accepts `--workspace` and stores
> it in `ctx.obj["workspace"]` (it is read by `find_harness_root` in siblings). If
> the flag name differs, align the test + commands with the real global option in
> `cli/group_options.py`.

- [ ] **Step 2: Run to verify it fails**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/cli/test_decision.py -v`
Expected: FAIL (`No such command 'decision'`).

- [ ] **Step 3: Create the group + `new` and register it**

```python
# src/super_harness/cli/decision.py
"""`decision` subgroup — author, ratify, and check decision records.

See docs/plans/2026-06-08-decision-records-anchors-design.md §6. Each verb does
exactly one thing (no hidden cross-entity side effects).
"""
from __future__ import annotations

import sys
from pathlib import Path

import click

from super_harness.cli.errors import format_error
from super_harness.core.clock import utc_now_iso
from super_harness.core.decisions import (
    Decision,
    decisions_dir,
    is_valid_id,
    parse_decision_file,
    write_decision,
)
from super_harness.core.identity import resolve_identity
from super_harness.core.paths import HarnessNotInitialized, find_harness_root
from super_harness.exit_codes import EXIT_NO_CONFIG, EXIT_OK, EXIT_VALIDATION


def _resolve(ctx: click.Context, sub: str) -> Path:
    try:
        return find_harness_root(Path(ctx.obj.get("workspace") or "."))
    except HarnessNotInitialized as e:
        click.echo(format_error(subcommand=sub, message=e.message, hint=e.hint), err=True)
        sys.exit(EXIT_NO_CONFIG)


def _casefold_exists(root: Path, decision_id: str) -> bool:
    ddir = decisions_dir(root)
    if not ddir.is_dir():
        return False
    target = decision_id.casefold()
    return any(p.stem.casefold() == target for p in ddir.glob("*.md"))


@click.group("decision")
def decision_group() -> None:
    """Author, ratify, and check decision records."""


@decision_group.command("new")
@click.argument("decision_id")
@click.option("--text", "text", required=True, help="One-line decision.")
@click.pass_context
def new_cmd(ctx: click.Context, decision_id: str, text: str) -> None:
    """Create a `proposed` decision at docs/decisions/<id>.md."""
    root = _resolve(ctx, "decision new")
    if not is_valid_id(decision_id):
        click.echo(
            format_error(
                subcommand="decision new",
                message=f"invalid id {decision_id!r}",
                hint="Use lowercase [a-z0-9_-].",
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    if _casefold_exists(root, decision_id):
        click.echo(
            format_error(
                subcommand="decision new",
                message=f"decision {decision_id!r} already exists",
                hint="Pick a different id or edit the existing record.",
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    d = Decision(
        id=decision_id,
        status="proposed",
        body=text,
        path=decisions_dir(root) / f"{decision_id}.md",
    )
    write_decision(d)
    click.echo(f"created {d.path.relative_to(root)} (proposed)")
    sys.exit(EXIT_OK)
```

Then register in `src/super_harness/cli/__init__.py` — add the import near the
other subgroup imports and the `add_command` near the others:

```python
from super_harness.cli.decision import decision_group
# ...
main.add_command(decision_group)
```

- [ ] **Step 4: Run to verify it passes**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/cli/test_decision.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/super_harness/cli/decision.py src/super_harness/cli/__init__.py tests/unit/cli/test_decision.py
git commit -m "feat(cli): decision group + decision new"
```

---

## Task 6: `decision ratify`

**Files:**
- Modify: `src/super_harness/cli/decision.py`
- Test: `tests/unit/cli/test_decision.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/cli/test_decision.py  (append)
def test_ratify_stamps_identity_and_time(tmp_path, monkeypatch):
    root = _init(tmp_path)
    monkeypatch.setenv("SUPER_HARNESS_ACTOR", "alice@example.com")
    CliRunner().invoke(main, ["--workspace", str(root), "decision", "new",
                              "d-a", "--text", "x"])
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "ratify", "d-a"])
    assert r.exit_code == 0, r.output
    text = (root / "docs/decisions/d-a.md").read_text()
    assert "status: ratified" in text
    assert "ratified_by: alice@example.com" in text
    assert "ratified_at:" in text


def test_ratify_missing_decision(tmp_path):
    root = _init(tmp_path)
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "ratify", "d-x"])
    assert r.exit_code == 2


def test_ratify_only_from_proposed(tmp_path):
    root = _init(tmp_path)
    CliRunner().invoke(main, ["--workspace", str(root), "decision", "new", "d-a", "--text", "x"])
    CliRunner().invoke(main, ["--workspace", str(root), "decision", "ratify", "d-a"])
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "ratify", "d-a"])
    assert r.exit_code == 2  # already ratified
```

- [ ] **Step 2: Run to verify it fails**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/cli/test_decision.py -k ratify -v`
Expected: FAIL (`No such command 'ratify'`).

- [ ] **Step 3: Add the command (append to `cli/decision.py`)**

```python
def _load_one(root: Path, sub: str, decision_id: str) -> Decision:
    path = decisions_dir(root) / f"{decision_id}.md"
    if not path.is_file():
        click.echo(
            format_error(subcommand=sub, message=f"no decision {decision_id!r}",
                         hint="Run `decision list` to see ids."),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    return parse_decision_file(path)


@decision_group.command("ratify")
@click.argument("decision_id")
@click.pass_context
def ratify_cmd(ctx: click.Context, decision_id: str) -> None:
    """Mark a proposed decision ratified (stamps who/when). Ratifies only this one."""
    root = _resolve(ctx, "decision ratify")
    d = _load_one(root, "decision ratify", decision_id)
    if d.status != "proposed":
        click.echo(
            format_error(subcommand="decision ratify",
                         message=f"{decision_id!r} is {d.status}, not proposed",
                         hint="Only a proposed decision can be ratified."),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    d.status = "ratified"
    d.ratified_by = resolve_identity(root)
    d.ratified_at = utc_now_iso()
    write_decision(d)
    click.echo(f"ratified {decision_id} (by {d.ratified_by})")
    sys.exit(EXIT_OK)
```

- [ ] **Step 4: Run to verify it passes**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/cli/test_decision.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/super_harness/cli/decision.py tests/unit/cli/test_decision.py
git commit -m "feat(cli): decision ratify"
```

---

## Task 7: `decision supersede`

**Files:**
- Modify: `src/super_harness/cli/decision.py`
- Test: `tests/unit/cli/test_decision.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/cli/test_decision.py  (append)
def _new_ratified(root, did):
    CliRunner().invoke(main, ["--workspace", str(root), "decision", "new", did, "--text", "x"])
    CliRunner().invoke(main, ["--workspace", str(root), "decision", "ratify", did])


def test_supersede_links_both(tmp_path):
    root = _init(tmp_path)
    _new_ratified(root, "d-old")
    _new_ratified(root, "d-new")
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision",
                                  "supersede", "d-old", "--by", "d-new"])
    assert r.exit_code == 0, r.output
    old = (root / "docs/decisions/d-old.md").read_text()
    new = (root / "docs/decisions/d-new.md").read_text()
    assert "status: superseded" in old and "superseded_by: d-new" in old
    assert "supersedes: d-old" in new


def test_supersede_requires_ratified_successor(tmp_path):
    root = _init(tmp_path)
    _new_ratified(root, "d-old")
    CliRunner().invoke(main, ["--workspace", str(root), "decision", "new", "d-new", "--text", "x"])
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision",
                                  "supersede", "d-old", "--by", "d-new"])
    assert r.exit_code == 2  # d-new not ratified
```

- [ ] **Step 2: Run to verify it fails**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/cli/test_decision.py -k supersede -v`
Expected: FAIL (`No such command 'supersede'`).

- [ ] **Step 3: Add the command (append to `cli/decision.py`)**

```python
@decision_group.command("supersede")
@click.argument("old_id")
@click.option("--by", "new_id", required=True, help="The ratified successor id.")
@click.pass_context
def supersede_cmd(ctx: click.Context, old_id: str, new_id: str) -> None:
    """Retire <old_id> in favor of a ratified <new_id>; link both directions."""
    root = _resolve(ctx, "decision supersede")
    old = _load_one(root, "decision supersede", old_id)
    new = _load_one(root, "decision supersede", new_id)
    if new.status != "ratified":
        click.echo(
            format_error(subcommand="decision supersede",
                         message=f"successor {new_id!r} is {new.status}, not ratified",
                         hint="Ratify the successor first."),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    old.status = "superseded"
    old.superseded_by = new_id
    new.supersedes = old_id
    write_decision(old)
    write_decision(new)
    click.echo(f"superseded {old_id} by {new_id}")
    sys.exit(EXIT_OK)
```

- [ ] **Step 4: Run to verify it passes**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/cli/test_decision.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/super_harness/cli/decision.py tests/unit/cli/test_decision.py
git commit -m "feat(cli): decision supersede"
```

---

## Task 8: `decision retire`

**Files:**
- Modify: `src/super_harness/cli/decision.py`
- Test: `tests/unit/cli/test_decision.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/cli/test_decision.py  (append)
def test_retire_sets_retired(tmp_path):
    root = _init(tmp_path)
    _new_ratified(root, "d-a")
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "retire", "d-a"])
    assert r.exit_code == 0, r.output
    assert "status: retired" in (root / "docs/decisions/d-a.md").read_text()


def test_retire_missing(tmp_path):
    root = _init(tmp_path)
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "retire", "d-x"])
    assert r.exit_code == 2
```

- [ ] **Step 2: Run to verify it fails**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/cli/test_decision.py -k retire -v`
Expected: FAIL (`No such command 'retire'`).

- [ ] **Step 3: Add the command (append to `cli/decision.py`)**

```python
@decision_group.command("retire")
@click.argument("decision_id")
@click.pass_context
def retire_cmd(ctx: click.Context, decision_id: str) -> None:
    """Retire a decision (tombstone): no successor, not anchorable, not dangling-down."""
    root = _resolve(ctx, "decision retire")
    d = _load_one(root, "decision retire", decision_id)
    d.status = "retired"
    write_decision(d)
    click.echo(f"retired {decision_id}")
    sys.exit(EXIT_OK)
```

- [ ] **Step 4: Run to verify it passes**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/cli/test_decision.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/super_harness/cli/decision.py tests/unit/cli/test_decision.py
git commit -m "feat(cli): decision retire"
```

---

## Task 9: `decision list`

**Files:**
- Modify: `src/super_harness/cli/decision.py`
- Test: `tests/unit/cli/test_decision.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/cli/test_decision.py  (append)
def test_list_shows_status(tmp_path):
    root = _init(tmp_path)
    _new_ratified(root, "d-a")
    CliRunner().invoke(main, ["--workspace", str(root), "decision", "new", "d-b", "--text", "x"])
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "list"])
    assert r.exit_code == 0
    assert "d-a" in r.output and "ratified" in r.output
    assert "d-b" in r.output and "proposed" in r.output


def test_list_filter_status(tmp_path):
    root = _init(tmp_path)
    _new_ratified(root, "d-a")
    CliRunner().invoke(main, ["--workspace", str(root), "decision", "new", "d-b", "--text", "x"])
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "list",
                                  "--status", "proposed"])
    assert "d-b" in r.output and "d-a" not in r.output


def test_list_dangling_shows_down(tmp_path):
    root = _init(tmp_path)
    _new_ratified(root, "d-a")  # ratified, no anchor → dangling down
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "list", "--dangling"])
    assert r.exit_code == 0 and "d-a" in r.output
```

- [ ] **Step 2: Run to verify it fails**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/cli/test_decision.py -k list -v`
Expected: FAIL (`No such command 'list'`).

- [ ] **Step 3: Add the command (append to `cli/decision.py`)**

Add imports at the top of the file:

```python
from super_harness.core.decision_check import run_check
from super_harness.core.decisions import load_decisions
```

Then the command:

```python
@decision_group.command("list")
@click.option("--status", "status_filter", default=None,
              type=click.Choice(["proposed", "ratified", "superseded", "retired"]))
@click.option("--dangling", is_flag=True, help="Show ratified decisions with no code anchor.")
@click.pass_context
def list_cmd(ctx: click.Context, status_filter: str | None, dangling: bool) -> None:
    """List decisions (optionally filtered); --dangling shows the down set."""
    root = _resolve(ctx, "decision list")
    if dangling:
        for did in run_check(root).dangling_down:
            click.echo(f"{did}\tdangling-down")
        sys.exit(EXIT_OK)
    decisions, _ = load_decisions(root)
    for d in sorted(decisions, key=lambda x: x.id):
        if status_filter and d.status != status_filter:
            continue
        click.echo(f"{d.id}\t{d.status}")
    sys.exit(EXIT_OK)
```

- [ ] **Step 4: Run to verify it passes**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/cli/test_decision.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/super_harness/cli/decision.py tests/unit/cli/test_decision.py
git commit -m "feat(cli): decision list"
```

---

## Task 10: `decision show`

**Files:**
- Modify: `src/super_harness/cli/decision.py`
- Test: `tests/unit/cli/test_decision.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/cli/test_decision.py  (append)
def test_show_lists_fields_and_anchors(tmp_path):
    root = _init(tmp_path)
    _new_ratified(root, "d-a")
    (root / "src").mkdir()
    (root / "src/x.py").write_text("# @decision:d-a\n", encoding="utf-8")
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "show", "d-a"])
    assert r.exit_code == 0, r.output
    assert "d-a" in r.output and "ratified" in r.output
    assert "src/x.py:1" in r.output


def test_show_missing(tmp_path):
    root = _init(tmp_path)
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "show", "d-x"])
    assert r.exit_code == 2
```

- [ ] **Step 2: Run to verify it fails**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/cli/test_decision.py -k show -v`
Expected: FAIL (`No such command 'show'`).

- [ ] **Step 3: Add the command (append to `cli/decision.py`)**

Add imports at the top:

```python
from super_harness.core.anchor_scanner import scan_sentinel_locations
from super_harness.core.decision_check import ANCHOR_KEYWORD, ALWAYS_EXCLUDE
from super_harness.core.source_scope import load_source_scope
```

Then the command:

```python
@decision_group.command("show")
@click.argument("decision_id")
@click.pass_context
def show_cmd(ctx: click.Context, decision_id: str) -> None:
    """Show a decision's fields + the code anchors currently pointing at it."""
    root = _resolve(ctx, "decision show")
    d = _load_one(root, "decision show", decision_id)
    click.echo(f"id:     {d.id}")
    click.echo(f"status: {d.status}")
    if d.ratified_by:
        click.echo(f"ratified_by: {d.ratified_by}")
    if d.ratified_at:
        click.echo(f"ratified_at: {d.ratified_at}")
    if d.supersedes:
        click.echo(f"supersedes: {d.supersedes}")
    if d.superseded_by:
        click.echo(f"superseded_by: {d.superseded_by}")
    include, exclude = load_source_scope(root)
    locs = scan_sentinel_locations(
        root, file_globs=include, keyword=ANCHOR_KEYWORD,
        exclude_globs=exclude + ALWAYS_EXCLUDE,
    ).get(decision_id, [])
    click.echo("anchors:")
    for f, ln in sorted(locs):
        click.echo(f"  {f}:{ln}")
    sys.exit(EXIT_OK)
```

- [ ] **Step 4: Run to verify it passes**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/cli/test_decision.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/super_harness/cli/decision.py tests/unit/cli/test_decision.py
git commit -m "feat(cli): decision show"
```

---

## Task 11: `decision check` (the CI gate — exit codes + `--json`)

**Files:**
- Modify: `src/super_harness/cli/decision.py`
- Test: `tests/unit/cli/test_decision.py` (append)

Exit codes (design §4.2): `0` clean/warn-only, `2` dangling-up, `3` record/config
error. Errors dominate (precedence).

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/cli/test_decision.py  (append)
import json


def test_check_clean_exit0(tmp_path):
    root = _init(tmp_path)
    _new_ratified(root, "d-a")
    (root / "src").mkdir()
    (root / "src/x.py").write_text("# @decision:d-a\n", encoding="utf-8")
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "check"])
    assert r.exit_code == 0, r.output


def test_check_dangling_up_exit2(tmp_path):
    root = _init(tmp_path)
    (root / "src").mkdir()
    (root / "src/x.py").write_text("# @decision:d-ghost\n", encoding="utf-8")
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "check"])
    assert r.exit_code == 2
    assert "d-ghost" in r.output


def test_check_dangling_down_is_warn_exit0(tmp_path):
    root = _init(tmp_path)
    _new_ratified(root, "d-a")  # ratified, no anchor
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "check"])
    assert r.exit_code == 0
    assert "d-a" in r.output  # warning surfaced


def test_check_malformed_exit3(tmp_path):
    root = _init(tmp_path)
    (root / "docs/decisions").mkdir(parents=True)
    (root / "docs/decisions/d-a.md").write_text("no frontmatter\n", encoding="utf-8")
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "check"])
    assert r.exit_code == 3


def test_check_error_dominates_dangling_up(tmp_path):
    # precedence: a record error (3) wins over a dangling-up (2)
    root = _init(tmp_path)
    (root / "docs/decisions").mkdir(parents=True)
    (root / "docs/decisions/d-a.md").write_text("no frontmatter\n", encoding="utf-8")
    (root / "src").mkdir()
    (root / "src/x.py").write_text("# @decision:d-ghost\n", encoding="utf-8")
    r = CliRunner().invoke(main, ["--workspace", str(root), "decision", "check"])
    assert r.exit_code == 3


def test_check_json_envelope(tmp_path):
    # --json is the GLOBAL flag (root position) → frozen 6-key envelope.
    root = _init(tmp_path)
    (root / "src").mkdir()
    (root / "src/x.py").write_text("# @decision:d-ghost\n", encoding="utf-8")
    r = CliRunner().invoke(main, ["--workspace", str(root), "--json", "decision", "check"])
    payload = json.loads(r.output)
    assert payload["command"] == "decision check"
    assert payload["status"] == "fail"
    assert payload["exit_code"] == 2
    assert payload["data"]["dangling_up"] == [{"id": "d-ghost", "file": "src/x.py", "line": 1}]
    assert payload["data"]["dangling_down"] == []
    assert payload["errors"] == []
```

- [ ] **Step 2: Run to verify they fail**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/cli/test_decision.py -k check -v`
Expected: FAIL (`No such command 'check'`).

- [ ] **Step 3: Add the command (append to `cli/decision.py`)**

Add import at top: `from super_harness.cli.output import json_envelope`. Then:

```python
@decision_group.command("check")
@click.pass_context
def check_cmd(ctx: click.Context) -> None:
    """Whole-repo dangling check: up=block(2) / down=warn / record error=3.

    Honors the GLOBAL --json flag (ctx.obj["json"]) → frozen json_envelope shape.
    """
    root = _resolve(ctx, "decision check")
    result = run_check(root)
    if result.errors:
        exit_code, status = EXIT_NO_CONFIG, "fail"
    elif result.dangling_up:
        exit_code, status = EXIT_VALIDATION, "fail"
    elif result.dangling_down:
        exit_code, status = EXIT_OK, "warning"
    else:
        exit_code, status = EXIT_OK, "pass"

    if ctx.obj.get("json"):
        click.echo(
            json_envelope(
                command="decision check",
                status=status,
                exit_code=exit_code,
                data={
                    "dangling_up": [
                        {"id": d.id, "file": d.file, "line": d.line}
                        for d in result.dangling_up
                    ],
                    "dangling_down": list(result.dangling_down),
                },
                errors=[
                    {"code": e.kind, "message": e.detail, "file": e.file}
                    for e in result.errors
                ],
            )
        )
    else:
        for e in result.errors:
            click.echo(f"ERROR [{e.kind}] {e.file}: {e.detail}", err=True)
        for d in result.dangling_up:
            click.echo(
                f"DANGLING-UP {d.file}:{d.line} @decision:{d.id} (no ratified decision)",
                err=True,
            )
        for did in result.dangling_down:
            click.echo(f"warning: dangling-down {did} (ratified, no code anchor)")
        if status == "pass":
            click.echo("decision check: clean")
    sys.exit(exit_code)
```

The global `--json` lives on the root group (`ctx.obj["json"]`, mirroring
`attest verify`); it must precede the subcommand (`... --json decision check`) or
be repositioned by `GroupAwareCommand` — do NOT define a per-command `--json`.

- [ ] **Step 4: Run to verify they pass**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/cli/test_decision.py -v`
Expected: PASS (whole decision suite).

- [ ] **Step 5: Commit**

```bash
git add src/super_harness/cli/decision.py tests/unit/cli/test_decision.py
git commit -m "feat(cli): decision check (CI gate, exit 0/2/3 + --json)"
```

---

## Task 12: CI wiring — bundled template + self-host workflow + cli-reference

**Files:**
- Modify: `src/super_harness/templates/super_harness_workflow.yml` (adopter-facing)
- Create: `.github/workflows/decision-check.yml` (this repo's self-host)
- Modify: `scripts/gen_cli_reference.py` (`_EXIT_CODES` entries)
- Modify: `docs/cli-reference.md` (regenerated)

- [ ] **Step 1: Add a `decision-check` job to the bundled adopter template**

Append this job to `src/super_harness/templates/super_harness_workflow.yml` (same
`pipx install super-harness==0.1.0` shape as the sibling jobs):

```yaml
  # Decision-conformance dangling check (referential integrity): block anchors
  # that name no ratified decision; warn on ratified decisions with no code.
  # Mark this required in branch protection for it to actually block.
  decision-check:
    if: github.event_name == 'pull_request'
    runs-on: ubuntu-latest
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.12' }
      - name: Install super-harness
        run: pipx install super-harness==0.1.0
      - name: Decision conformance (dangling check)
        run: super-harness decision check
```

- [ ] **Step 2: Create the self-host workflow**

```yaml
# .github/workflows/decision-check.yml
name: decision-check
on:
  pull_request: {}

jobs:
  decision-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - name: Install package
        run: pip install -e ".[dev]"
      - name: Decision conformance (dangling check)
        run: super-harness decision check
```

- [ ] **Step 3: Add `_EXIT_CODES` entries for the new commands**

In `scripts/gen_cli_reference.py`, add to the `_EXIT_CODES` dict:

```python
    "decision new": [
        "`0` success",
        "`2` invalid id, or id already exists (case-folded)",
        "`3` no `.harness/` (run `init` first)",
    ],
    "decision ratify": [
        "`0` success",
        "`2` no such decision, or not in `proposed` state",
        "`3` no `.harness/`",
    ],
    "decision supersede": [
        "`0` success",
        "`2` missing decision, or successor not ratified",
        "`3` no `.harness/`",
    ],
    "decision retire": [
        "`0` success",
        "`2` no such decision",
        "`3` no `.harness/`",
    ],
    "decision list": ["`0` success", "`3` no `.harness/`"],
    "decision show": ["`0` success", "`2` no such decision", "`3` no `.harness/`"],
    "decision check": [
        "`0` clean, or only dangling-down warnings",
        "`2` one or more dangling-up anchors (gate violation)",
        "`3` record/config error (duplicate id / malformed record) or no `.harness/`",
    ],
```

- [ ] **Step 4: Regenerate the CLI reference and verify the drift check passes**

```bash
PATH="$(pwd)/.venv/bin:$PATH" python -m scripts.gen_cli_reference
PATH="$(pwd)/.venv/bin:$PATH" python -m scripts.gen_cli_reference --check
```
Expected: the first writes `docs/cli-reference.md` (now including the `decision`
commands); the second exits `0` (in sync).

- [ ] **Step 5: Commit**

```bash
git add src/super_harness/templates/super_harness_workflow.yml \
        .github/workflows/decision-check.yml \
        scripts/gen_cli_reference.py docs/cli-reference.md
git commit -m "ci(decision): bundled + self-host decision-check workflow + cli-reference"
```

---

## Task 13: Self-host validation (dogfood on real source)

Prove the slice end to end on this repo (a generic instance). NOT the
`@capability` migration — that is the next slice; this only adds a couple of
`@decision` records + anchors for the modules built here.

**Files:**
- Create: `docs/decisions/d-decision-records.md`, `docs/decisions/d-dangling-check.md`
  (via the CLI, then ratify)
- Modify: `src/super_harness/core/decisions.py`, `src/super_harness/core/decision_check.py`
  (add one `@decision:` anchor comment each)

- [ ] **Step 1: Create + ratify two real decisions**

```bash
PATH="$(pwd)/.venv/bin:$PATH" super-harness decision new d-decision-records \
  --text "Decisions are one-file-per-record under docs/decisions/, four-state lifecycle."
PATH="$(pwd)/.venv/bin:$PATH" super-harness decision new d-dangling-check \
  --text "CI checks referential integrity: dangling-up blocks, dangling-down warns."
PATH="$(pwd)/.venv/bin:$PATH" super-harness decision ratify d-decision-records
PATH="$(pwd)/.venv/bin:$PATH" super-harness decision ratify d-dangling-check
```

- [ ] **Step 2: Anchor the real source**

Add a comment line near the top of each module (below any existing line-1 sentinel):

- In `src/super_harness/core/decisions.py`: `# @decision:d-decision-records`
- In `src/super_harness/core/decision_check.py`: `# @decision:d-dangling-check`

- [ ] **Step 3: Run the check — expect clean**

Run: `PATH="$(pwd)/.venv/bin:$PATH" super-harness decision check`
Expected: exit `0`, "decision check: clean" (the two decisions each have an anchor;
no dangling either way).

- [ ] **Step 4: Negative check — break one anchor, confirm it blocks, then fix**

```bash
# temporarily point an anchor at a ghost id
printf '\n# @decision:d-ghost\n' >> src/super_harness/core/decisions.py
PATH="$(pwd)/.venv/bin:$PATH" super-harness decision check; echo "exit=$?"
# expected: exit=2, DANGLING-UP ... @decision:d-ghost
git checkout -- src/super_harness/core/decisions.py   # revert the break
```
Expected: the broken run prints a dangling-up line and exits `2`; after revert the
check is clean again.

- [ ] **Step 5: Run the full test suite, then commit the validated decisions + anchors**

```bash
PATH="$(pwd)/.venv/bin:$PATH" pytest -q
git add docs/decisions/ src/super_harness/core/decisions.py src/super_harness/core/decision_check.py
git commit -m "test(decision): dogfood two ratified decisions + anchors (slice-1 validation)"
```

> Branch protection: enabling the `decision-check` check as a required status on
> this repo is a separate operational step (design §8) — it may hit the account /
> token constraints noted in memory; handle at that time, outside this plan.

---

## Self-Review (plan author)

**Spec coverage** — every spec section maps to a task:
- §2 record format/fields/lifecycle/states → T1. §2.4 anchorable=ratified-only → enforced in T4.
- §3 anchor syntax + permissive/case-preserving capture (fail-open guard) → T3.
- §3.2 scan scope (source-paths loader + exclude + ALWAYS-exclude `docs/decisions/**`) → T2/T3/T4.
- §4 up=block / down=warn / precedence / sorting → T4 + T11. §4.2 exit 0/2/3 + global `--json` frozen envelope (`output.py::json_envelope`) → T11.
- §4.3 case-folded duplicate → T1. §4.4 malformed predicate + candidate-file rule → T1.
- §5 edge cases: deletion/proposed/superseded → dangling-up (T4 tests incl. superseded), new collision (T5), supersede-requires-ratified (T7), empty repo (T4), error-dominates-precedence (T11).
- §6 all seven verbs → T5–T11. §7 bundled + self-host CI + required-doc → T12. §8 validation → T13.

**Placeholder scan:** none — every code step carries complete code; commands carry expected output.

**Type consistency:** `Decision` (mutable dataclass — intentional for the edit verbs) fields, `CheckResult`(`dangling_up`/`dangling_down`/`errors`/`ok`), `DanglingUp`(`id`/`file`/`line`), `RecordError`(`kind`/`file`/`detail`/`id`), `split_frontmatter`(shared, returns `tuple|None`), `run_check`, `scan_sentinel_locations(root, file_globs, *, keyword, exclude_globs)`, `is_valid_id`, `_load_one`, `ANCHOR_KEYWORD`/`ALWAYS_EXCLUDE`, and `json_envelope(command,status,exit_code,data,errors)` are referenced identically across tasks. Verified consistent.

**Architecture review applied (round 1):** must-fixes B1 (global `--json` + frozen envelope, not per-command/bespoke — spec §4.2 also updated), B2 (shared `core/frontmatter.py`, no duplicate splitter; `superpowers._parse_frontmatter` delegates), S1 (CLI-vs-Sensor + whole-repo-vs-diff rationale added to the Architecture header) are all folded in.

**One honest caveat for the implementer:** the global `--workspace` flag + `ctx.obj["workspace"]` shape is assumed from sibling commands (`cli/anchor.py`); confirm against `cli/group_options.py` and align test invocations if the real flag differs.

## Execution

Plan complete and saved to `docs/plans/2026-06-08-decision-records-anchors-plan.md`.
Execute task-by-task with **superpowers:subagent-driven-development** (fresh subagent
per task + two-stage review), or **superpowers:executing-plans** for batch execution
with checkpoints.
