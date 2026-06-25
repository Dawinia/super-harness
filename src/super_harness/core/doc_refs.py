"""Dead code-reference gate for hand-written prose docs (design 2026-06-25 §5.1).

Pure engine. The harness never runs an LLM: this is string/set work only. Scans
in-scope *doc* files for backtick code-spans that look like code symbols and are
absent from the *source* scope's identifier set — the §2.1-validated, mechanically
detectable doc-rot mechanism. Fail-open toward silence: only backtick spans that
pass a code-shape heuristic are candidates, and "resolution" is membership in the
source identifier set (deleted / renamed / never-existed all read the same — the
finding says "does not resolve in current source", which is true in every case).

Known false-negative (accepted, fail-open): a symbol renamed in source whose OLD
name still appears anywhere in-source-scope (a back-compat alias, a `# renamed from
X` comment, a test) stays in the identifier set, so the stale doc reference is NOT
flagged. This is consistent with the silence-over-noise policy; see OPEN-ITEMS.

Two scopes, deliberately separate:
- SOURCE scope (`.harness/source-paths.yaml`): where symbols live (resolution target).
- DOC scope (`.harness/doc-paths.yaml`, this module): which prose docs to scan.

Reuses `anchor_scanner`'s git-aware file walk so discovery cannot drift.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# Shared file-discovery primitives — reused so the git-aware walk cannot drift.
from super_harness.core.anchor_scanner import _excluded, _list_files, _matches_any
from super_harness.core.source_scope import load_source_scope

# Both globs are needed: `fnmatch` does NOT match a top-level file against `**/*.md`
# (`fnmatch("README.md", "**/*.md")` is False), so `*.md` catches root-level docs
# (README.md, AGENTS.md — the agent-facing target) while `**/*.md` catches nested.
DEFAULT_DOC_INCLUDE: list[str] = ["**/*.md", "*.md"]
# Archival plan history + machine-managed derived docs (governed by `doc check`)
# + vendored sample repos (their backtick refs resolve against their own absent source).
DEFAULT_DOC_EXCLUDE: list[str] = [
    "docs/plans/**",
    "docs/cli-reference.md",
    "docs/state-machine.md",
    "examples/**",
]

_BACKTICK_RE = re.compile(r"`([^`\n]+)`")
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_HAS_INTERNAL_UPPER_RE = re.compile(r"[a-z][A-Z]|[A-Z][a-z]")
_TOKEN_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")


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
    if (
        not isinstance(include, list)
        or not include
        or any(not isinstance(i, str) for i in include)
    ):
        include = DEFAULT_DOC_INCLUDE
    exclude = dp.get("exclude")
    if not isinstance(exclude, list) or any(not isinstance(i, str) for i in exclude):
        exclude = DEFAULT_DOC_EXCLUDE
    return list(include), list(exclude)


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
    doc_include, doc_exclude = load_doc_scope(workspace_root)
    # Doc files must NOT contribute to the source identifier set: code symbols are
    # defined in code, not in prose. Without this, a top-level doc (README.md /
    # AGENTS.md — not under the source-scope `docs/**` exclude) would resolve its
    # own backtick symbols against itself and the gate could never fire on it.
    present = collect_source_identifiers(
        workspace_root, include=src_include, exclude=src_exclude + doc_include
    )

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
