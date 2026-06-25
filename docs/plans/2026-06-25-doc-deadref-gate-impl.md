# Doc dead-reference gate (B-layer) + semantic doc-impact (C-layer) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a mechanical, zero-LLM gate that flags hand-written prose docs whose backtick code-symbol references no longer resolve against source (B-layer), plus a one-item extension to the forced code-review checklist that forces an agent to dispose "doc impact" semantically (C-layer).

**Architecture:** A new pure engine `core/doc_refs.py` builds a set of identifiers present in the *source* scope (reusing `anchor_scanner`'s git-aware file walk), extracts backtick code-spans from in-scope *doc* files, keeps only spans that look like code symbols, and reports any that are absent from source. A new `doc refs` CLI command exposes it with graded exit codes (default = warn / `--gate` = block), mirroring `decision check` vs `decision check --gate-reconcile`. The block teeth are wired at exactly two pre-existing enforcement points (the `review approve --reviewer code-reviewer` emit and the CI `doc-check.yml` workflow) plus a non-blocking warn at `done`. C-layer is a single new checklist item (`doc-impact`) added to the code-reviewer default; the existing `check_coverage` emit-gate already forces it to be disposed.

**Tech Stack:** Python 3.10+, click, PyYAML, pytest. The harness never runs an LLM — B-layer is pure string/set work; C-layer reuses the existing forced-verdict machinery.

---

## Design provenance & locked decisions

SSOT for *why*: `docs/plans/2026-06-25-doc-lifecycle-research-reframe.md` §5. This plan
locks the three engineering 口子 left open there (§5.1 "TBD at plan time"):

1. **Reuse vs new module → NEW module `core/doc_refs.py`.** `anchor_scanner.py` is a
   keyword-*sentinel* scanner (finds `@decision:<id>` that *we* embed); `doc_check.py` is a
   regen-and-diff engine. Neither fits "extract loose symbol refs from prose + resolve
   against source." We *reuse* `anchor_scanner`'s file-discovery primitives (`_list_files`,
   `_matches_any`, `_excluded`) so the git-aware walk cannot drift.
2. **Symbol existence resolution → token-set membership over source-scope files** (no
   subprocess-per-symbol). Build `present_identifiers: set[str]` once from source-scope file
   contents; a backtick symbol "resolves" iff it is in that set. **Fail-open toward silence:**
   only backtick spans that pass a code-shape heuristic are ever candidates, and the finding
   is framed as "does not resolve in current source" (true whether the symbol was deleted,
   renamed, or never existed — sidesteps any need for git history).
3. **C-layer "doc impact" item → add `"doc-impact"` to `DEFAULT_CHECKLISTS["code-reviewer"]`.**
   The existing `check_coverage` emit-gate already rejects a verdict that omits any required
   item, so this single line is the whole mechanism.

**Two scopes, kept separate:**
- **Source scope** (`.harness/source-paths.yaml`, existing): where symbols *live* — the
  resolution target. The shipped default excludes only `docs/**`; *this repo* additionally
  excludes `tests/**` via its own `source-paths.yaml`. Unchanged by this plan.
- **Doc scope** (`.harness/doc-paths.yaml`, NEW, optional): which prose docs to *scan*.
  Defaults: include `**/*.md`; exclude `docs/plans/**` (archival history per §5.1) and the
  machine-managed derived docs (`docs/cli-reference.md`, `docs/state-machine.md`, governed by
  `doc check`). Missing/corrupt → defaults (fail-open, mirrors `source_scope.py`).

**Confidence axis (precision-first, §5.1):** the engine tags each finding `high` (backtick
code-span). `--gate` blocks only on `high`. **Bare qualified-name (`core.reducer.fold` in
prose) warn-tier is explicitly DEFERRED** to OPEN-ITEMS — scanning prose for dotted names is
noisy and never block-worthy; this cut ships only the block-worthy backtick mechanism. The
`confidence` field exists in the data model so the deferred tier slots in without a reshape.

**Teeth placement (one engine, graded exits — mirrors `decision check --gate-reconcile`):**
- `done` → **warn** (non-blocking; prints to stderr after a verification pass).
- `review approve --reviewer code-reviewer` emit → **BLOCK** (inside `_validate_code_review_verdict`).
- CI `doc-check.yml` → **BLOCK** (`super-harness doc refs --gate`; agent-agnostic cold floor).

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `src/super_harness/core/doc_refs.py` | Pure engine: doc-scope loader, backtick extraction, code-shape heuristic, source-identifier collection, `scan_doc_refs` orchestrator + result types | **Create** |
| `src/super_harness/cli/doc.py` | Add `doc refs` command (graded exits) to the existing `doc` group | **Modify** |
| `src/super_harness/cli/review.py` | Block in `_validate_code_review_verdict` on a high-confidence dead ref | **Modify** |
| `src/super_harness/cli/done.py` | Non-blocking warn after a verification pass | **Modify** |
| `src/super_harness/core/review_checklist.py` | Add `"doc-impact"` to `DEFAULT_CHECKLISTS["code-reviewer"]` | **Modify** |
| `.github/workflows/doc-check.yml` | Add a `doc refs --gate` step | **Modify** |
| `tests/unit/core/test_doc_refs.py` | Unit tests for the pure engine | **Create** |
| `tests/unit/cli/test_doc_refs_cli.py` | CLI tests for `doc refs` graded exits + `done` warn helper | **Create** |
| `tests/unit/cli/test_review_verdict_gate.py` | Add dead-ref block test + fix verdict fixtures (doc-impact) | **Modify** |
| `tests/unit/core/test_review_bundle.py` | Update bundle checklist assertion (doc-impact) | **Modify** |
| `tests/unit/core/test_review_checklist.py` | Update default-checklist assertion | **Modify** |
| `docs/cli-reference.md`, `AGENTS.md` | Regenerated CLI-surface docs | **Regenerate (not hand-edit)** |
| `private/OPEN-ITEMS.md` | Register deferred bare-name warn tier + `done` coupling note | **Modify** |

---

## Task 1: Doc-scope config loader

**Files:**
- Create: `src/super_harness/core/doc_refs.py`
- Test: `tests/unit/core/test_doc_refs.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/core/test_doc_refs.py
"""Unit tests for core.doc_refs (doc dead-reference engine)."""
from __future__ import annotations

from pathlib import Path

from super_harness.core.doc_refs import (
    DEFAULT_DOC_EXCLUDE,
    DEFAULT_DOC_INCLUDE,
    load_doc_scope,
)


def _harness(tmp_path: Path) -> Path:
    (tmp_path / ".harness").mkdir(parents=True, exist_ok=True)
    return tmp_path


def test_doc_scope_defaults_when_no_config(tmp_path: Path) -> None:
    _harness(tmp_path)
    include, exclude = load_doc_scope(tmp_path)
    assert include == DEFAULT_DOC_INCLUDE
    assert exclude == DEFAULT_DOC_EXCLUDE


def test_doc_scope_override(tmp_path: Path) -> None:
    root = _harness(tmp_path)
    (root / ".harness" / "doc-paths.yaml").write_text(
        "doc_paths:\n  include:\n    - 'docs/**/*.md'\n  exclude:\n    - 'docs/legacy/**'\n"
    )
    include, exclude = load_doc_scope(root)
    assert include == ["docs/**/*.md"]
    assert exclude == ["docs/legacy/**"]


def test_doc_scope_corrupt_yaml_falls_back(tmp_path: Path) -> None:
    root = _harness(tmp_path)
    (root / ".harness" / "doc-paths.yaml").write_text("doc_paths: [unbalanced\n")
    include, exclude = load_doc_scope(root)
    assert include == DEFAULT_DOC_INCLUDE
    assert exclude == DEFAULT_DOC_EXCLUDE
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/core/test_doc_refs.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'super_harness.core.doc_refs'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/super_harness/core/doc_refs.py
"""Dead code-reference gate for hand-written prose docs (design 2026-06-25 §5.1).

Pure engine. The harness never runs an LLM: this is string/set work only. Scans
in-scope *doc* files for backtick code-spans that look like code symbols and are
absent from the *source* scope's identifier set — the §2.1-validated, mechanically
detectable doc-rot mechanism. Fail-open toward silence: only backtick spans that
pass a code-shape heuristic are candidates, and "resolution" is membership in the
source identifier set (deleted / renamed / never-existed all read the same — the
finding says "does not resolve in current source", which is true in every case).

Known false-negative (accepted, fail-open): a symbol renamed in source whose OLD name
still appears anywhere in-source-scope (a back-compat alias, a `# renamed from X`
comment, a test) stays in the identifier set, so the stale doc reference is NOT
flagged. This is consistent with the silence-over-noise policy; see OPEN-ITEMS.

Two scopes, deliberately separate:
- SOURCE scope (`.harness/source-paths.yaml`): where symbols live (resolution target).
- DOC scope (`.harness/doc-paths.yaml`, this module): which prose docs to scan.

Reuses `anchor_scanner`'s git-aware file walk so discovery cannot drift.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

# Both globs are needed: `fnmatch` does NOT match a top-level file against `**/*.md`
# (verified review R1 — `fnmatch("README.md", "**/*.md")` is False), so `*.md` catches
# root-level docs (README.md, AGENTS.md — the §4 agent-facing target) while `**/*.md`
# catches nested ones.
DEFAULT_DOC_INCLUDE: list[str] = ["**/*.md", "*.md"]
# Archival plan history (§5.1) + machine-managed derived docs (governed by `doc check`)
# + vendored sample repos (their backtick refs resolve against their own absent source).
DEFAULT_DOC_EXCLUDE: list[str] = [
    "docs/plans/**",
    "docs/cli-reference.md",
    "docs/state-machine.md",
    "examples/**",
]


def doc_paths_file(workspace_root: Path) -> Path:
    return workspace_root / ".harness" / "doc-paths.yaml"


def load_doc_scope(workspace_root: Path) -> tuple[list[str], list[str]]:
    """Return (include, exclude) doc globs. Missing/corrupt → defaults (fail-open)."""
    f = doc_paths_file(workspace_root)
    if not f.is_file():
        return list(DEFAULT_DOC_INCLUDE), list(DEFAULT_DOC_EXCLUDE)
    try:
        data: Any = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, OSError, UnicodeDecodeError):
        return list(DEFAULT_DOC_INCLUDE), list(DEFAULT_DOC_EXCLUDE)
    dp = data.get("doc_paths") if isinstance(data, dict) else None
    if not isinstance(dp, dict):
        return list(DEFAULT_DOC_INCLUDE), list(DEFAULT_DOC_EXCLUDE)
    include = dp.get("include")
    if not isinstance(include, list) or not include or any(not isinstance(i, str) for i in include):
        include = DEFAULT_DOC_INCLUDE
    exclude = dp.get("exclude")
    if not isinstance(exclude, list) or any(not isinstance(i, str) for i in exclude):
        exclude = DEFAULT_DOC_EXCLUDE
    return list(include), list(exclude)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/core/test_doc_refs.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/super_harness/core/doc_refs.py tests/unit/core/test_doc_refs.py
git commit -m "feat(doc-refs): doc-scope config loader (B-layer task 1)"
```

---

## Task 2: Backtick extraction + code-shape heuristic

**Files:**
- Modify: `src/super_harness/core/doc_refs.py`
- Test: `tests/unit/core/test_doc_refs.py`

The heuristic is the precision crux. A backtick span is a high-confidence code symbol iff,
after stripping a trailing `()`, it is a single identifier (`^[A-Za-z_][A-Za-z0-9_]*$`) AND
"looks like code": it contains a `_` OR has an internal uppercase letter (camelCase /
PascalCase). This keeps `_format_rows`, `derive_state`, `DocRefsResult`, `assembleBundle()`
in; it keeps prose-in-backticks (`` `ok` ``, `` `id` ``, `` `TODO` ``), multi-word spans,
flags (`` `--json` ``), dotted names, and paths (`` `a/b.py` ``) out.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/unit/core/test_doc_refs.py
from super_harness.core.doc_refs import extract_backtick_symbols, looks_like_symbol


def test_looks_like_symbol_accepts_code_shaped() -> None:
    assert looks_like_symbol("_format_rows")
    assert looks_like_symbol("derive_state")
    assert looks_like_symbol("DocRefsResult")
    assert looks_like_symbol("assembleBundle")


def test_looks_like_symbol_rejects_prose_and_non_symbols() -> None:
    assert not looks_like_symbol("ok")          # bare lowercase word
    assert not looks_like_symbol("id")
    assert not looks_like_symbol("TODO")         # all-caps, no internal lowercase boundary
    assert not looks_like_symbol("--json")       # flag
    assert not looks_like_symbol("core.reducer.fold")  # dotted -> deferred warn tier
    assert not looks_like_symbol("a/b.py")       # path
    assert not looks_like_symbol("two words")
    assert not looks_like_symbol("")


def test_extract_strips_trailing_parens_and_records_line() -> None:
    text = "intro\nsee `derive_state()` and `_format_rows` here\nplain `ok` word\n"
    found = extract_backtick_symbols(text)
    assert found == [("derive_state", 2), ("_format_rows", 2)]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/core/test_doc_refs.py -k "looks_like or extract" -v`
Expected: FAIL with `ImportError: cannot import name 'extract_backtick_symbols'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to src/super_harness/core/doc_refs.py (imports + body)
import re

_BACKTICK_RE = re.compile(r"`([^`\n]+)`")
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_HAS_INTERNAL_UPPER_RE = re.compile(r"[a-z][A-Z]|[A-Z][a-z]")


def looks_like_symbol(span: str) -> bool:
    """True if `span` is a single code identifier that looks like code (precision crux).

    Accepts a single identifier (optionally with a trailing `()`) that either contains
    an underscore or shows a camelCase / PascalCase boundary. Rejects prose words,
    flags, dotted names, paths, and multi-token spans. See module docstring + design §5.1.
    """
    candidate = span[:-2] if span.endswith("()") else span
    if not _IDENT_RE.match(candidate):
        return False
    return "_" in candidate or bool(_HAS_INTERNAL_UPPER_RE.search(candidate))


def extract_backtick_symbols(text: str) -> list[tuple[str, int]]:
    """Return [(symbol, 1-based-line)] for backtick spans that pass `looks_like_symbol`.

    A trailing `()` is stripped from the recorded symbol so resolution matches the
    bare identifier. Order preserved; duplicates kept (caller may dedupe per file).
    """
    out: list[tuple[str, int]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for m in _BACKTICK_RE.finditer(line):
            span = m.group(1).strip()
            if looks_like_symbol(span):
                out.append((span[:-2] if span.endswith("()") else span, lineno))
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/core/test_doc_refs.py -k "looks_like or extract" -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/super_harness/core/doc_refs.py tests/unit/core/test_doc_refs.py
git commit -m "feat(doc-refs): backtick extraction + code-shape heuristic (B-layer task 2)"
```

---

## Task 3: Source-identifier collection (resolution target)

**Files:**
- Modify: `src/super_harness/core/doc_refs.py`
- Test: `tests/unit/core/test_doc_refs.py`

Build the set of identifiers present anywhere in source-scope files. Reuses
`anchor_scanner._list_files` (git-aware walk) + `_matches_any` / `_excluded` so discovery
cannot drift. Tokenize each readable file with `\b[A-Za-z_][A-Za-z0-9_]*\b`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/unit/core/test_doc_refs.py
import subprocess


def _git_init(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=root, check=True,
    )


def test_collect_source_identifiers_reads_source_excludes_docs(tmp_path: Path) -> None:
    from super_harness.core.doc_refs import collect_source_identifiers

    root = _harness(tmp_path)
    (root / "src").mkdir()
    (root / "src" / "mod.py").write_text("def derive_state():\n    return _format_rows\n")
    (root / "docs").mkdir()
    # an identifier that exists ONLY in docs must NOT count as present-in-source
    (root / "docs" / "x.md").write_text("`only_in_docs`\n")
    _git_init(root)

    idents = collect_source_identifiers(root, include=["**/*"], exclude=["docs/**"])
    assert "derive_state" in idents
    assert "_format_rows" in idents
    assert "only_in_docs" not in idents
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/core/test_doc_refs.py -k collect_source -v`
Expected: FAIL with `ImportError: cannot import name 'collect_source_identifiers'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to src/super_harness/core/doc_refs.py
from super_harness.core.anchor_scanner import _excluded, _list_files, _matches_any

_TOKEN_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")


def _in_scope(rel: Path, include: list[str], exclude: list[str]) -> bool:
    return _matches_any(rel, include) and not _excluded(rel, exclude)


def collect_source_identifiers(
    root: Path, *, include: list[str], exclude: list[str]
) -> set[str]:
    """Every identifier token present in any source-scope file. Binary/unreadable skipped."""
    idents: set[str] = set()
    for f in _list_files(root):
        if not f.is_file():
            continue
        rel = f.relative_to(root)
        if not _in_scope(rel, include, exclude):
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError, OSError):
            continue
        idents.update(_TOKEN_RE.findall(text))
    return idents
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/core/test_doc_refs.py -k collect_source -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/super_harness/core/doc_refs.py tests/unit/core/test_doc_refs.py
git commit -m "feat(doc-refs): source-identifier collection (B-layer task 3)"
```

---

## Task 4: `scan_doc_refs` orchestrator + result types

**Files:**
- Modify: `src/super_harness/core/doc_refs.py`
- Test: `tests/unit/core/test_doc_refs.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/unit/core/test_doc_refs.py
def test_scan_doc_refs_flags_dead_backtick_ref(tmp_path: Path) -> None:
    from super_harness.core.doc_refs import scan_doc_refs

    root = _harness(tmp_path)
    (root / "src").mkdir()
    (root / "src" / "mod.py").write_text("def _render():\n    return 1\n")
    (root / "docs").mkdir()
    # _render exists; _format_rows was renamed away -> dead
    (root / "docs" / "guide.md").write_text(
        "use `_render` now; old `_format_rows` is gone\n"
    )
    (root / "docs" / "plans").mkdir()
    (root / "docs" / "plans" / "old.md").write_text("`_format_rows` archived\n")
    _git_init(root)

    result = scan_doc_refs(root)
    dead = [(f.symbol, f.doc_file) for f in result.findings]
    assert ("_format_rows", "docs/guide.md") in dead
    assert all(f.symbol != "_render" for f in result.findings)
    # archival docs/plans/** is excluded by default -> not reported
    assert all("plans/" not in f.doc_file for f in result.findings)
    assert all(f.confidence == "high" for f in result.findings)


def test_scan_doc_refs_clean_when_all_resolve(tmp_path: Path) -> None:
    from super_harness.core.doc_refs import scan_doc_refs

    root = _harness(tmp_path)
    (root / "src").mkdir()
    (root / "src" / "mod.py").write_text("def _render():\n    return 1\n")
    (root / "docs").mkdir()
    (root / "docs" / "guide.md").write_text("call `_render` to draw\n")
    _git_init(root)

    assert scan_doc_refs(root).findings == []


def test_scan_doc_refs_catches_top_level_md(tmp_path: Path) -> None:
    """Top-level README.md/AGENTS.md must be scanned (the `*.md` include glob)."""
    from super_harness.core.doc_refs import scan_doc_refs

    root = _harness(tmp_path)
    (root / "src").mkdir()
    (root / "src" / "mod.py").write_text("def _render():\n    return 1\n")
    (root / "README.md").write_text("legacy `_format_rows` is gone\n")
    _git_init(root)

    dead = [(f.symbol, f.doc_file) for f in scan_doc_refs(root).findings]
    assert ("_format_rows", "README.md") in dead
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/core/test_doc_refs.py -k scan_doc_refs -v`
Expected: FAIL with `ImportError: cannot import name 'scan_doc_refs'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to src/super_harness/core/doc_refs.py
from dataclasses import dataclass, field

from super_harness.core.source_scope import load_source_scope


@dataclass(frozen=True)
class DocRef:
    doc_file: str   # repo-relative
    line: int       # 1-based
    symbol: str
    confidence: str  # "high" (backtick); "low" reserved for the deferred bare-name tier


@dataclass
class DocRefsResult:
    findings: list[DocRef] = field(default_factory=list)


def scan_doc_refs(workspace_root: Path) -> DocRefsResult:
    """Flag backtick code-symbols in in-scope docs that do not resolve in source.

    Pure: reads files only; emits nothing, touches no state. Deterministic (sorted).
    """
    src_include, src_exclude = load_source_scope(workspace_root)
    present = collect_source_identifiers(
        workspace_root, include=src_include, exclude=src_exclude
    )
    doc_include, doc_exclude = load_doc_scope(workspace_root)

    findings: list[DocRef] = []
    for f in _list_files(workspace_root):
        if not f.is_file():
            continue
        rel = f.relative_to(workspace_root)
        if not _in_scope(rel, doc_include, doc_exclude):
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError, OSError):
            continue
        rel_str = str(rel)
        for symbol, lineno in extract_backtick_symbols(text):
            if symbol not in present:
                findings.append(DocRef(rel_str, lineno, symbol, "high"))
    findings.sort(key=lambda d: (d.doc_file, d.line, d.symbol))
    return DocRefsResult(findings=findings)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/core/test_doc_refs.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add src/super_harness/core/doc_refs.py tests/unit/core/test_doc_refs.py
git commit -m "feat(doc-refs): scan_doc_refs orchestrator + result types (B-layer task 4)"
```

---

## Task 5: `doc refs` CLI command (graded exits)

**Files:**
- Modify: `src/super_harness/cli/doc.py`
- Test: `tests/unit/cli/test_doc_refs_cli.py`

Default mode → warn (exit 0, status `warning` if any finding). `--gate` → block (exit 2 if
any `high` finding). Mirrors `decision check` (warn) vs `decision check --gate-reconcile`
(block). Honors global `--json`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/cli/test_doc_refs_cli.py
"""CLI tests for `super-harness doc refs` graded exit codes."""
from __future__ import annotations

import subprocess
from pathlib import Path

from click.testing import CliRunner

from super_harness.cli import main  # root click group lives in cli/__init__.py


def _repo_with_dead_ref(tmp_path: Path) -> Path:
    (tmp_path / ".harness").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "mod.py").write_text("def _render():\n    return 1\n")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "guide.md").write_text("old `_format_rows` is gone\n")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "i"],
        cwd=tmp_path, check=True,
    )
    return tmp_path


def test_doc_refs_default_warns_exit_0(tmp_path: Path) -> None:
    root = _repo_with_dead_ref(tmp_path)
    res = CliRunner().invoke(main, ["--workspace", str(root), "doc", "refs"])
    assert res.exit_code == 0
    assert "_format_rows" in res.output


def test_doc_refs_gate_blocks_exit_2(tmp_path: Path) -> None:
    root = _repo_with_dead_ref(tmp_path)
    res = CliRunner().invoke(main, ["--workspace", str(root), "doc", "refs", "--gate"])
    assert res.exit_code == 2
    assert "_format_rows" in res.output


def test_doc_refs_gate_clean_exit_0(tmp_path: Path) -> None:
    root = _repo_with_dead_ref(tmp_path)
    (root / "docs" / "guide.md").write_text("call `_render`\n")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "fix"],
        cwd=root, check=True,
    )
    res = CliRunner().invoke(main, ["--workspace", str(root), "doc", "refs", "--gate"])
    assert res.exit_code == 0
```

> **Verified (review R1):** the root click group is `main`, defined in
> `src/super_harness/cli/__init__.py` (`main.add_command(...)`); existing tests do
> `from super_harness.cli import main` then `invoke(main, ...)`. There is no `cli/main.py`.

- [ ] **Step 2: Run test to verify it fails**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/cli/test_doc_refs_cli.py -v`
Expected: FAIL — `doc refs` is not a command (`No such command 'refs'`).

- [ ] **Step 3: Write minimal implementation**

```python
# add to src/super_harness/cli/doc.py
from super_harness.core.doc_refs import scan_doc_refs
from super_harness.exit_codes import EXIT_OK, EXIT_VALIDATION


@doc_group.command("refs")
@click.option("--gate", is_flag=True,
              help="Merge-boundary teeth: exit 2 on any high-confidence (backtick) "
                   "dead code-reference (default mode only warns).")
@click.pass_context
def refs_cmd(ctx: click.Context, gate: bool) -> None:
    """Flag backtick code-symbols in prose docs that no longer resolve in source.

    Default: warn (exit 0). `--gate`: block (exit 2) on any high-confidence finding.
    Honors the global --json flag.
    """
    root = _resolve(ctx, "doc refs")
    result = scan_doc_refs(root)
    high = [f for f in result.findings if f.confidence == "high"]

    if gate and high:
        exit_code, status = EXIT_VALIDATION, "fail"
    elif result.findings:
        exit_code, status = EXIT_OK, "warning"
    else:
        exit_code, status = EXIT_OK, "pass"

    if ctx.obj.get("json"):
        click.echo(json_envelope(
            command="doc refs",
            status=status,
            exit_code=exit_code,
            data={"findings": [
                {"doc_file": f.doc_file, "line": f.line,
                 "symbol": f.symbol, "confidence": f.confidence}
                for f in result.findings
            ]},
        ))
    else:
        for f in result.findings:
            label = "DEAD-REF" if (gate and f.confidence == "high") else "warning: dead-ref"
            click.echo(
                f"{label} {f.doc_file}:{f.line} `{f.symbol}` "
                f"(does not resolve in source)",
                err=True,
            )
        if status == "pass":
            click.echo("doc refs: clean")
    sys.exit(exit_code)
```

> The existing `doc.py` already imports `Status, json_envelope` from `cli.output` and
> `EXIT_NO_CONFIG` from `exit_codes`. Add only the new imports shown above (avoid a
> duplicate `EXIT_NO_CONFIG` import).

- [ ] **Step 4: Run test to verify it passes**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/cli/test_doc_refs_cli.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/super_harness/cli/doc.py tests/unit/cli/test_doc_refs_cli.py
git commit -m "feat(doc-refs): doc refs CLI command with graded exits (B-layer task 5)"
```

---

## Task 6: C-layer — `doc-impact` checklist item (+ fix the fixtures it breaks)

> **Ordering (review R1):** this task runs BEFORE the review-block task (Task 7) because
> adding a required checklist item changes the coverage gate repo-wide. If Task 7 ran first,
> its dead-ref test would trip the *coverage* gate before reaching the dead-ref check.

**Files:**
- Modify: `src/super_harness/core/review_checklist.py:18-30` (`DEFAULT_CHECKLISTS`)
- Modify: `tests/unit/core/test_review_checklist.py`
- Modify: `tests/unit/core/test_review_bundle.py:73` (bundle checklist assertion)
- Modify: `tests/unit/cli/test_review_verdict_gate.py` (`_good_verdict` line 50, `_verdict_with_prior` line 124)

Adding `"doc-impact"` to the code-reviewer default forces every code-review verdict to
dispose it (pass/fail/na) via the existing `check_coverage` emit-gate — no new mechanism.
**This is repo-wide:** the bundle checklist and every "happy path" verdict fixture must add
the item, or the existing suite goes red (verified review R1).

- [ ] **Step 1: Write/adjust the failing tests**

```python
# tests/unit/core/test_review_checklist.py — strengthen the default assertion
def test_default_when_no_config(tmp_path: Path) -> None:
    _harness(tmp_path)
    items = resolve_checklist(tmp_path, "code-reviewer")
    assert items == DEFAULT_CHECKLISTS["code-reviewer"]
    assert "doc-impact" in items  # C-layer: semantic doc-impact must be disposed
```

```python
# tests/unit/core/test_review_bundle.py:73 — the bundle now carries 5 items
    assert b["checklist"] == [
        "spec-compliance", "scope-adherence", "code-quality", "edge-cases", "doc-impact",
    ]
```

```python
# tests/unit/cli/test_review_verdict_gate.py — add doc-impact to BOTH verdict builders
# in _good_verdict (line 50) and _verdict_with_prior (line 124), change the items list to:
    items = "\n".join(f"  - item: {i}\n    status: pass"
                      for i in ["spec-compliance", "scope-adherence", "code-quality",
                                "edge-cases", "doc-impact"])
```

- [ ] **Step 2: Run tests to verify they fail (red)**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/core/test_review_checklist.py tests/unit/core/test_review_bundle.py tests/unit/cli/test_review_verdict_gate.py -v`
Expected: FAIL — `doc-impact` not yet in the default list (assertions + coverage-gate happy-path tests fail).

- [ ] **Step 3: Write minimal implementation**

```python
# src/super_harness/core/review_checklist.py
DEFAULT_CHECKLISTS: dict[str, list[str]] = {
    "code-reviewer": [
        "spec-compliance",
        "scope-adherence",
        "code-quality",
        "edge-cases",
        "doc-impact",
    ],
    "plan-reviewer": [
        "spec-coverage",
        "design-soundness",
        "scope-declared",
    ],
}
```

- [ ] **Step 4: Run tests to verify they pass (green)**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/core/test_review_checklist.py tests/unit/core/test_review_bundle.py tests/unit/cli/test_review_verdict_gate.py -v`
Expected: PASS (all). If another fixture surfaces, grep `tests/` for the literal
`"edge-cases"` to find any other hard-coded 4-item code-reviewer checklist and add the item.

- [ ] **Step 5: Commit**

```bash
git add src/super_harness/core/review_checklist.py tests/unit/core/test_review_checklist.py \
        tests/unit/core/test_review_bundle.py tests/unit/cli/test_review_verdict_gate.py
git commit -m "feat(doc-refs): C-layer doc-impact checklist item (forced verdict disposition)"
```

---

## Task 7: Block teeth at `review approve --reviewer code-reviewer`

**Files:**
- Modify: `src/super_harness/cli/review.py:155-218` (`_validate_code_review_verdict`)
- Modify: `tests/unit/cli/test_review_verdict_gate.py` (reuse its lifecycle fixtures)

A high-confidence dead ref must block the code-review approve emit (the primary ③ gate). Add
the check inside `_validate_code_review_verdict`, after the existing coverage/freshness/
dispose gates, before `return verdict`. The test reuses the existing
`test_review_verdict_gate.py` fixtures (`_repo_change`, `_prepare_digest`, `_good_verdict`),
which after Task 6 emit the full 5-item verdict — so it passes coverage/freshness and the
ONLY thing that can fail it is the dead ref.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/unit/cli/test_review_verdict_gate.py
def test_dead_doc_ref_blocks_code_review_approve(tmp_path: Path) -> None:
    ws = _repo_change(tmp_path)
    # commit a doc with a backtick symbol that resolves nowhere in source scope
    (ws / "docs").mkdir()
    (ws / "docs" / "guide.md").write_text("the old `_totally_gone` helper is removed\n")
    _git(ws, "add", "docs/guide.md")
    _git(ws, "commit", "-qm", "doc")
    digest = _prepare_digest(ws)          # digest is over in-scope src/ diff, unaffected by the doc
    p = _good_verdict(ws, digest)         # full 5-item, fresh verdict → only the dead ref can fail it
    r = CliRunner().invoke(main, ["--workspace", str(ws), "review", "approve", "c",
                                  "--reviewer", "code-reviewer", "--verdict-file", str(p)])
    assert r.exit_code == EXIT_VALIDATION, r.output
    assert "_totally_gone" in r.output    # blocked specifically on the dead ref


def test_clean_docs_allow_code_review_approve(tmp_path: Path) -> None:
    ws = _repo_change(tmp_path)
    (ws / "docs").mkdir()
    (ws / "docs" / "guide.md").write_text("see `src` for details\n")  # no code-shaped symbol
    _git(ws, "add", "docs/guide.md")
    _git(ws, "commit", "-qm", "doc")
    digest = _prepare_digest(ws)
    p = _good_verdict(ws, digest)
    r = CliRunner().invoke(main, ["--workspace", str(ws), "review", "approve", "c",
                                  "--reviewer", "code-reviewer", "--verdict-file", str(p)])
    assert r.exit_code == EXIT_OK, r.output
```

> **Why this is the real gate path (not a stub):** `_repo_change` builds a full committed
> lifecycle to AWAITING_CODE_REVIEW; `review approve --reviewer code-reviewer` runs the
> production `_validate_code_review_verdict`. `_totally_gone` has `_`, passes
> `looks_like_symbol`, and appears nowhere in `src/` → high-confidence dead ref → block.

- [ ] **Step 2: Run test to verify it fails**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/cli/test_review_verdict_gate.py -k dead_doc_ref -v`
Expected: FAIL — the approve currently SUCCEEDS (exit 0) because the dead-ref block does not
yet exist in `_validate_code_review_verdict`.

- [ ] **Step 3: Write minimal implementation**

```python
# in src/super_harness/cli/review.py — add import near the other core imports
from super_harness.core.doc_refs import scan_doc_refs

# in _validate_code_review_verdict, immediately BEFORE `return verdict`:
    dead = [f for f in scan_doc_refs(root).findings if f.confidence == "high"]
    if dead:
        listing = "; ".join(f"{f.doc_file}:{f.line} `{f.symbol}`" for f in dead)
        click.echo(format_error(subcommand=subcommand,
            message=f"docs reference code symbol(s) that no longer resolve in source: {listing}",
            hint="Fix or remove the dead reference(s), or run `super-harness doc refs` to list them."),
            err=True)
        sys.exit(EXIT_VALIDATION)
    return verdict
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/cli/test_review_verdict_gate.py -k "dead_doc_ref or clean_docs" -v`
Expected: PASS (both)

- [ ] **Step 5: Commit**

```bash
git add src/super_harness/cli/review.py tests/unit/cli/test_review_verdict_gate.py
git commit -m "feat(doc-refs): block code-review approve on dead backtick ref (B-layer task 7)"
```

---

## Task 8: Non-blocking warn at `done`

**Files:**
- Modify: `src/super_harness/cli/done.py` (after a verification pass, before `_report_done_success`)
- Test: `tests/unit/cli/test_doc_refs_cli.py` (add a `done`-warn assertion)

`done` already does a lot; keep this minimal and strictly non-blocking: after the
verification pass and before emitting/reporting success, scan and print any findings to
stderr. **Never** changes `done`'s exit code. The print happens regardless of `--skip-verify`
(put it in the shared success path so both branches get it).

- [ ] **Step 1: Write the failing test**

```python
# append to tests/unit/cli/test_doc_refs_cli.py — assert the warn helper prints, never raises
def test_done_warn_helper_prints_findings(tmp_path, capsys) -> None:
    from super_harness.cli.done import _warn_dead_refs  # added in Step 3

    root = _repo_with_dead_ref(tmp_path)
    _warn_dead_refs(root)  # must not raise, must not sys.exit
    err = capsys.readouterr().err
    assert "_format_rows" in err
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/cli/test_doc_refs_cli.py -k done_warn -v`
Expected: FAIL with `ImportError: cannot import name '_warn_dead_refs'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to src/super_harness/cli/done.py
from super_harness.core.doc_refs import scan_doc_refs


def _warn_dead_refs(root: Path) -> None:
    """Non-blocking heads-up: print any dead doc code-refs to stderr. Never raises/exits."""
    try:
        findings = scan_doc_refs(root).findings
    except Exception:  # noqa: BLE001 — a warn must never break `done`
        return
    for f in findings:
        click.echo(
            f"warning: dead-ref {f.doc_file}:{f.line} `{f.symbol}` "
            f"(does not resolve in source; fix before review)",
            err=True,
        )
```

Then call `_warn_dead_refs(root)` inside `_report_done_success` (it receives `slug` but not
`root`; add a `root: Path` parameter to `_report_done_success` and pass it from both call
sites — the default path at line ~361 and the skip-verify path at line ~399). Review R1
confirmed those are the ONLY two call sites and `root` is a local at both.

```python
# change signature:
def _report_done_success(ctx: click.Context, root: Path, slug: str, data: dict[str, Any] | None) -> None:
    _warn_dead_refs(root)
    # ... existing body unchanged ...

# update both call sites:
#   _report_done_success(ctx, root, resolved, result.details)
#   _report_done_success(ctx, root, slug, data=None)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/cli/test_doc_refs_cli.py -k done_warn -v`
Expected: PASS

Also re-run the done suite to confirm the signature change broke nothing:
Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/ -k done -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/super_harness/cli/done.py tests/unit/cli/test_doc_refs_cli.py
git commit -m "feat(doc-refs): non-blocking dead-ref warn at done (B-layer task 8)"
```

---

## Task 9: CI cold-floor — `doc refs --gate` in doc-check.yml

**Files:**
- Modify: `.github/workflows/doc-check.yml`

- [ ] **Step 1: Add the gate step**

```yaml
# .github/workflows/doc-check.yml — add after the existing "Managed-artifact drift" step
      - name: Dead code-reference gate (docs)
        run: super-harness doc refs --gate
```

- [ ] **Step 2: Validate locally**

Run: `PATH="$(pwd)/.venv/bin:$PATH" super-harness doc refs --gate; echo "exit=$?"`
Expected: `exit=0` (this repo's own docs must be clean — if not, fix the flagged refs first;
that is the gate dogfooding itself). If a *generated* doc trips it, fix the doc-scope default
exclude in Task 1, regenerate, and re-run.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/doc-check.yml
git commit -m "ci(doc-refs): add dead code-reference gate to doc-check workflow"
```

---

## Task 10: Regenerate CLI-surface docs (cli-reference + AGENTS.md)

**Files:**
- Regenerate: `docs/cli-reference.md` (via `doc check --fix`), `AGENTS.md` (via `sync --agents-md`)

Adding the `doc refs` command changes the generated CLI reference and the agent-facing
AGENTS.md. Both CI gates (`doc-check.yml` regen-diff + `sync --check`) will fail if these are
not regenerated and committed. See memory `reference-agents-md-regen-via-sync`.

- [ ] **Step 1: Regenerate both**

```bash
PATH="$(pwd)/.venv/bin:$PATH" super-harness doc check --fix
PATH="$(pwd)/.venv/bin:$PATH" super-harness sync --agents-md
```

- [ ] **Step 2: Verify both gates pass**

```bash
PATH="$(pwd)/.venv/bin:$PATH" super-harness doc check; echo "doc-check exit=$?"
PATH="$(pwd)/.venv/bin:$PATH" super-harness sync --check; echo "sync-check exit=$?"
```
Expected: both `exit=0`.

- [ ] **Step 3: Commit the regenerated docs**

```bash
git add docs/cli-reference.md AGENTS.md
git commit -m "docs(doc-refs): regenerate cli-reference + AGENTS.md for doc refs command"
```

---

## Task 11: Full-suite green + lint/type + OPEN-ITEMS

**Files:**
- Modify: `private/OPEN-ITEMS.md`

- [ ] **Step 1: Whole-repo gates**

```bash
PATH="$(pwd)/.venv/bin:$PATH" ruff check src tests
PATH="$(pwd)/.venv/bin:$PATH" mypy src
PATH="$(pwd)/.venv/bin:$PATH" pytest -q
```
Expected: all green. (mypy note: `_excluded`/`_list_files`/`_matches_any` are imported from
`anchor_scanner`; if mypy flags the private import, add a short `# imported shared
file-discovery primitives` comment — do not silence with `type: ignore` unless it is a real
stub gap.)

- [ ] **Step 2: Register deferred work in OPEN-ITEMS**

Add entries (DOABLE-NOW, deprioritized — not blocked):
- **Bare qualified-name warn tier** (`core.reducer.fold` in prose → WARN). Deferred from this
  cut: noisy, never block-worthy. `DocRef.confidence == "low"` is the reserved slot.
- **`done`→warn coupling**: `done.py` now imports `scan_doc_refs`; revisit if `done` should
  instead surface this via the sensor/verification path.
- **Symbol resolution precision**: token-set membership cannot distinguish deleted-vs-typo;
  finding framed as "does not resolve in source". Revisit if false-positive reports surface.

- [ ] **Step 3: Commit**

```bash
git add private/OPEN-ITEMS.md
git commit -m "docs(open-items): register deferred bare-name tier + doc-refs precision notes"
```

---

## Self-Review (run before handoff)

**Spec coverage (design §5):**
- §5.1 B-layer dead-ref gate → Tasks 1-5 (engine + CLI), 7 (review block), 8 (done warn), 9 (CI block). ✓
- §5.1 precision policy (backtick=block, bare=warn, fail-open) → Task 2 heuristic + Task 4 high-only + bare deferred (Task 11). ✓
- §5.1 respect source-paths / not fire on archival → Task 1 doc-scope default excludes `docs/plans/**` + `examples/**` + Task 4 honors both scopes. ✓
- §5.1 graded exits mirror `decision check --gate-reconcile` → Task 5. ✓
- §5.2 C-layer doc-impact via forced verdict → Task 6. ✓
- §5.3 firing table (done/review/CI) → Tasks 8/7/9. ✓
- Regen discipline (cli-reference + AGENTS.md, both CI gates) → Task 10. ✓

**Placeholder scan:** no TBD/TODO in steps; every code step shows code. The earlier
review-block stub is replaced (Task 7) with a concrete lifecycle-driven `review approve` test
that reuses `test_review_verdict_gate.py`'s committed-lifecycle fixtures.

**Type consistency:** `DocRef(doc_file, line, symbol, confidence)` / `DocRefsResult.findings`
used identically in Tasks 4, 5, 7, 8. `scan_doc_refs(root) -> DocRefsResult` signature stable.
`looks_like_symbol` / `extract_backtick_symbols` / `collect_source_identifiers` signatures
match across tasks. `_report_done_success` gains a `root` param consistently at both call sites.

**Resolved by review R1 (ran code against the real repo):** CLI entrypoint is
`from super_harness.cli import main` (no `cli/main.py`); the heuristic behaves as the Task 2
asserts (TODO→False, DocRefsResult→True, etc.); `doc refs --gate` is clean (0 findings) on
this repo's real docs, so the CI gate will not block its own PR; the C-layer blast radius is
the three fixture files now patched in Task 6; reused private helpers / `_report_done_success`
call sites / exit-code imports all line up.

**Known soft spots for round-2 adversarial review:**
1. Heuristic precision (Task 2) — confirm acceptable false-positive rate; the `--gate` block
   is only as trustworthy as this heuristic.
2. `collect_source_identifiers` reads every source file each run — confirm acceptable cost at
   the review/CI boundary for large adopter repos (note, don't prematurely optimize).
3. Token-set false-negative on surviving old names (accepted, fail-open; noted in OPEN-ITEMS
   + engine docstring).
4. `examples/**` default exclude — confirm it doesn't mask a real adopter scenario.
