"""Derivable-doc registry + regen-and-diff engine (design 2026-06-11).

The registry uses the same explicit command contract as verification: generator
commands are direct argv lists, with structured ``workdir`` and ``env`` fields.
A malformed registry blocks (``RegistryError``), never silently defaults to
"no docs".
"""
from __future__ import annotations

import difflib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from super_harness.core.shell_runner import run_command, scrubbed_environ
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
    command: tuple[str, ...]  # direct generator argv; emits canonical stdout
    workdir: str = "."
    env: dict[str, str] = field(default_factory=dict)


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
        raw_command = entry.get("command")
        if not isinstance(path, str):
            errors.append(RegistryError("malformed_registry", f"entry {i} needs a string path"))
            continue
        if not isinstance(raw_command, list) or not raw_command or any(
            not isinstance(argument, str) or argument == "" for argument in raw_command
        ):
            errors.append(
                RegistryError(
                    "malformed_registry",
                    f"entry {i} needs a non-empty command list of non-empty strings",
                )
            )
            continue
        if "shell" in entry:
            errors.append(
                RegistryError(
                    "malformed_registry",
                    f"entry {i} direct command may not set 'shell'",
                )
            )
            continue
        workdir = entry.get("workdir", ".")
        if not isinstance(workdir, str) or not workdir.strip():
            errors.append(
                RegistryError("malformed_registry", f"entry {i} has invalid workdir")
            )
            continue
        if _escapes_repo(workspace_root, workdir):
            errors.append(RegistryError("path_escape", f"workdir escapes repo: {workdir!r}"))
            continue
        env = _parse_env(entry.get("env"), i)
        if env is None:
            errors.append(
                RegistryError("malformed_registry", f"entry {i} env must map strings to strings")
            )
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
        resolved = str((workspace_root / path).resolve())
        if resolved in seen:
            errors.append(RegistryError("duplicate_path", f"duplicate path: {path!r}"))
            continue
        seen.add(resolved)
        docs.append(
            DerivedDoc(
                path=path,
                command=tuple(raw_command),
                workdir=workdir,
                env=env,
            )
        )
    return docs, errors


def _parse_env(value: Any, index: int) -> dict[str, str] | None:
    if value is None:
        return {}
    if not isinstance(value, dict):
        return None
    result: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            return None
        result[key] = item
    return result


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
    command: tuple[str, ...]
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


def _run_generator(workspace_root: Path, doc: DerivedDoc) -> tuple[str | None, str]:
    """Return ``(generated_text, error)``; text is None on failure."""
    env = {**scrubbed_environ(), **doc.env}
    if doc.command and doc.command[0] in {"python", "python3"}:
        toolchain = _project_toolchain_dir(workspace_root)
        if toolchain is None:
            env["PATH"] = ""
        else:
            existing_path = env.get("PATH", "")
            env["PATH"] = (
                f"{toolchain}{os.pathsep}{existing_path}"
                if existing_path
                else str(toolchain)
            )
    result = run_command(
        doc.command,
        cwd=(workspace_root / doc.workdir).resolve(),
        env=env,
        timeout=_GENERATOR_TIMEOUT_S,
        decode_errors="strict",
    )
    if result.decode_error is not None:
        return None, result.decode_error
    if result.spawn_error is not None:
        return None, f"could not run: {result.spawn_error}"
    if result.timed_out:
        return None, f"timed out after {_GENERATOR_TIMEOUT_S}s"
    if result.exit_code != 0:
        return None, f"exit {result.exit_code}"
    return result.stdout, ""


def _project_toolchain_dir(root: Path) -> Path | None:
    for name in ("Scripts", "bin"):
        candidate = root / ".venv" / name
        if candidate.is_dir():
            return candidate
    return None


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
        generated, err = _run_generator(workspace_root, doc)
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
