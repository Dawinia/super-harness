"""Derivable-doc registry + regen-and-diff engine (design 2026-06-11).

Loader mirrors source_scope.py's YAML shape but decisions.py's fail-CLOSED
error handling: a malformed registry blocks (RegistryError), never silently
defaults to "no docs".
"""
from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path

import yaml

_GENERATOR_TIMEOUT_S = 30


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
