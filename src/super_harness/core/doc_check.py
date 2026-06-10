"""Derivable-doc registry + regen-and-diff engine (design 2026-06-11).

Loader mirrors source_scope.py's YAML shape but decisions.py's fail-CLOSED
error handling: a malformed registry blocks (RegistryError), never silently
defaults to "no docs".
"""
from __future__ import annotations

import difflib
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from super_harness.exit_codes import (
    EXIT_EXTERNAL_TOOL,
    EXIT_NO_CONFIG,
    EXIT_OK,
    EXIT_VALIDATION,
)

_GENERATOR_TIMEOUT_S = 30
_DIFF_MAX_LINES = 40


@dataclass(frozen=True)
class DerivedDoc:
    path: str       # repo-relative, validated inside-repo
    command: str    # generator invocation; emits canonical content to stdout


@dataclass(frozen=True)
class RegistryError:
    code: str       # malformed_registry | path_escape | duplicate_path
    # all three codes route to EXIT_NO_CONFIG (exit 3) at the CLI layer
    message: str
    file: str = ".harness/derived-docs.yaml"


def derived_docs_file(workspace_root: Path) -> Path:
    return workspace_root / ".harness" / "derived-docs.yaml"


def _escapes_repo(workspace_root: Path, rel: str) -> bool:
    if Path(rel).is_absolute():
        return True
    resolved = (workspace_root / rel).resolve()
    root = workspace_root.resolve()
    return root != resolved and root not in resolved.parents


def load_derived_docs(
    workspace_root: Path,
) -> tuple[list[DerivedDoc], list[RegistryError]]:
    f = derived_docs_file(workspace_root)
    if not f.is_file():
        return [], []
    try:
        data = yaml.safe_load(f.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError, UnicodeDecodeError) as exc:
        return [], [RegistryError("malformed_registry", f"unparseable YAML: {exc}")]
    if data is None:
        return [], []
    if not isinstance(data, dict):
        return [], [RegistryError("malformed_registry", "top-level must be a mapping")]
    entries = data.get("derived_docs")
    if not isinstance(entries, list):
        return [], [RegistryError("malformed_registry", "`derived_docs` must be a list")]

    docs: list[DerivedDoc] = []
    errors: list[RegistryError] = []
    seen: set[str] = set()
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            errors.append(RegistryError("malformed_registry", f"entry {i} is not a mapping"))
            continue
        path = entry.get("path")
        command = entry.get("command")
        if not isinstance(path, str) or not isinstance(command, str):
            errors.append(
                RegistryError("malformed_registry", f"entry {i} needs string path+command")
            )
            continue
        if not shlex.split(command):
            errors.append(RegistryError("malformed_registry", f"entry {i} has empty command"))
            continue
        if path.strip() == "":
            errors.append(RegistryError("malformed_registry", f"entry {i} has empty path"))
            continue
        if (workspace_root / path).resolve() == workspace_root.resolve():
            errors.append(
                RegistryError(
                    "malformed_registry",
                    f"entry {i} path resolves to repo root: {path!r}",
                )
            )
            continue
        if _escapes_repo(workspace_root, path):
            errors.append(RegistryError("path_escape", f"path escapes repo: {path!r}"))
            continue
        if path in seen:
            errors.append(RegistryError("duplicate_path", f"duplicate path: {path!r}"))
            continue
        seen.add(path)
        docs.append(DerivedDoc(path=path, command=command))
    return docs, errors


@dataclass
class InSync:
    path: str


@dataclass
class Drift:
    path: str
    diff: str   # full unified diff; CLI truncates for the JSON envelope, prints whole to stderr


@dataclass
class Failed:
    path: str
    command: str
    error: str


@dataclass
class DocCheckResult:
    in_sync: list[InSync] = field(default_factory=list)
    drift: list[Drift] = field(default_factory=list)
    failed: list[Failed] = field(default_factory=list)
    errors: list[RegistryError] = field(default_factory=list)
    exit_code: int = EXIT_OK


def _normalize(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _run_generator(workspace_root: Path, command: str) -> tuple[str | None, str]:
    """Return (generated_text, error). text is None on failure."""
    argv = shlex.split(command)
    try:
        proc = subprocess.run(
            argv, cwd=workspace_root, capture_output=True,
            timeout=_GENERATOR_TIMEOUT_S,
        )
    except FileNotFoundError:
        return None, "command not found"
    except subprocess.TimeoutExpired:
        return None, f"timed out after {_GENERATOR_TIMEOUT_S}s"
    if proc.returncode != 0:
        return None, f"exit {proc.returncode}"
    try:
        return proc.stdout.decode("utf-8"), ""
    except UnicodeDecodeError:
        return None, "invalid UTF-8 stdout"


def truncate_diff(diff: str) -> str:
    lines = diff.splitlines(keepends=True)
    if len(lines) <= _DIFF_MAX_LINES:
        return diff
    extra = len(lines) - _DIFF_MAX_LINES
    return "".join(lines[:_DIFF_MAX_LINES]) + f"... ({extra} more lines; full diff on stderr)\n"


def run_doc_check(workspace_root: Path, *, fix: bool = False) -> DocCheckResult:
    docs, errors = load_derived_docs(workspace_root)
    if errors:
        return DocCheckResult(errors=errors, exit_code=EXIT_NO_CONFIG)

    result = DocCheckResult()
    for doc in docs:
        generated, err = _run_generator(workspace_root, doc.command)
        if generated is None:
            result.failed.append(Failed(path=doc.path, command=doc.command, error=err))
            continue
        generated = _normalize(generated)
        target = workspace_root / doc.path
        try:
            on_disk: str | None = _normalize(target.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            on_disk = None
        if on_disk == generated:
            result.in_sync.append(InSync(path=doc.path))
            continue
        diff = "".join(difflib.unified_diff(
            (on_disk or "").splitlines(keepends=True),
            generated.splitlines(keepends=True),
            fromfile=doc.path, tofile=f"{doc.path} (regenerated)",
        ))
        if fix:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(generated, encoding="utf-8")
            result.in_sync.append(InSync(path=doc.path))
        else:
            result.drift.append(Drift(path=doc.path, diff=diff))

    result.in_sync.sort(key=lambda x: x.path)
    result.drift.sort(key=lambda x: x.path)
    result.failed.sort(key=lambda x: x.path)
    if result.failed:
        result.exit_code = EXIT_EXTERNAL_TOOL
    elif result.drift:
        result.exit_code = EXIT_VALIDATION
    else:
        result.exit_code = EXIT_OK
    return result
