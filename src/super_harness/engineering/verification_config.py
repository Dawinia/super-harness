"""Loader + schema for `.harness/verification.yaml` (engineering-integration §2.3).

Hand-rolled, dataclass-based parser — deliberately pydantic-free to avoid an
extra runtime dependency (PyYAML is already vendored in). Mirrors the corrupt-
yaml + error-type conventions of `core.state_yaml`, `adapters.registry`, and
`core._registry`.

Schema (the canonical empty shape lives in
`src/super_harness/templates/verification_defaults.yaml`, written by `init`):

    layers:
      baseline: { enabled: true }            # bool, default true
      framework_adapter: { enabled: true }   # bool, default true
      user_checks: { enabled: true }         # bool, default true
    defaults:
      timeout_seconds: 300                   # int
      must_pass: true                        # bool
      capture: both                          # stdout | stderr | both | none
      workdir: .                             # str (relative to repo root)
      env: { CI: "1" }                       # dict[str, str]
    execution:
      mode: parallel                         # parallel | sequential
      max_parallelism: 4                     # int
      fail_fast: false                       # bool
    checks:                                  # list of user CheckSpecs
      - id: tests                            # str, REQUIRED, unique-per-layer
        command: npm test                    # str, REQUIRED
        must_pass: true                      # optional → inherits defaults.must_pass
        timeout_seconds: 600                 # optional → inherits defaults.timeout_seconds
        capture: stdout                      # optional → inherits defaults.capture
        workdir: .                           # optional → inherits defaults.workdir
        env: {}                              # optional per-check env (NOT merged here)
    adapter_provided:                        # list, same CheckSpec shape + provided_by
      - id: openspec-validate
        command: openspec validate --strict
        provided_by: openspec-adapter        # str, adapter_provided only

**Default inheritance (load time):** a CheckSpec parsed from a check that omits
`must_pass`/`timeout_seconds`/`capture`/`workdir` inherits the corresponding
`defaults.*` value, so every CheckSpec downstream code sees carries concrete
scalars. `env` is the ONE exception: `CheckSpec.env` stays the per-check dict
and is NOT merged with `defaults.env` here — the os.environ + defaults.env +
check.env merge happens later at execution time (a different task), which needs
all three layers separately. So we preserve `Defaults.env` and `CheckSpec.env`
side by side.

**Error contract:**
- Missing file → `FileNotFoundError`. The CLI maps this to EXIT_NO_CONFIG
  (cli-command-surface §2.2). `verify` / `done` treat absence as a hard error
  because `init` always writes the file; a missing file means an uninitialized
  or hand-deleted workspace, not a "use defaults" signal.
- Wrong shape / invalid enum / missing-required / duplicate id →
  `VerificationConfigError` (a `ValueError` subclass) → CLI maps to
  EXIT_VALIDATION.
- A check `command` (user `checks` or `adapter_provided`) that references a
  non-allowlisted `${NAME}` placeholder → `InterpolationError` (a
  `VerificationConfigError`, hence a `ValueError`) raised at LOAD time, so a
  placeholder that could never run is caught by the `verify` / `done` pre-load
  rather than deep in the sensor's thread pool.
- Syntax-corrupt yaml → `yaml.YAMLError` propagates unwrapped (same pattern as
  `adapters.registry.load_adapters`). NOTE `yaml.YAMLError` subclasses
  `Exception`, NOT `ValueError` — so a CLI catch tuple aiming to surface BOTH
  wrong-shape and syntax-error as EXIT_VALIDATION must list `yaml.YAMLError`
  explicitly alongside `ValueError`. The two families are intentionally distinct
  so callers *can* distinguish them if they want.

API stability: **experimental** (v0.1).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

__all__ = [
    "INTERPOLATION_ALLOWLIST",
    "CheckSpec",
    "Defaults",
    "Execution",
    "InterpolationError",
    "Layers",
    "VerificationCheckConflict",
    "VerificationConfig",
    "VerificationConfigError",
    "interpolate",
    "load_verification_config",
    "merge_adapter_provided",
    "merge_adapter_provided_list",
]

_CAPTURE_VALUES = ("stdout", "stderr", "both", "none")
_MODE_VALUES = ("parallel", "sequential")

# Built-in defaults applied when a block / key is absent from the yaml. These
# mirror the shipped `verification_defaults.yaml` template so a minimal or empty
# config behaves identically to the canonical one.
_DEFAULT_LAYER_ENABLED = True
_DEFAULT_TIMEOUT_SECONDS = 300
_DEFAULT_MUST_PASS = True
_DEFAULT_CAPTURE = "both"
_DEFAULT_WORKDIR = "."
_DEFAULT_MAX_PARALLELISM = 4
_DEFAULT_MODE = "parallel"
_DEFAULT_FAIL_FAST = False

# Variable-interpolation allowlist for check `command` strings (engineering-
# integration §2.3 / OI-6). The gate is on the placeholder NAME, not its value:
# all four names are always *accepted* (an allowlisted-but-empty value
# substitutes to `""`); only a non-allowlisted name (`${PR_URL}`,
# `${COMMIT_SHA}`, …) raises. `${SLUG}` and `${CHANGE_ID}` are aliases of the
# same change id. PR-context variables are deliberately excluded so user yaml
# cannot reference untrusted PR content (those checks belong in CI).
INTERPOLATION_ALLOWLIST: frozenset[str] = frozenset(
    {"SLUG", "CHANGE_ID", "SPEC_PATH", "PLAN_PATH"}
)

# Matches a `${NAME}` placeholder where NAME is a Python-style identifier. A
# bare `$`, an unbraced `$NAME`, or empty `${}` is NOT a placeholder and is
# left untouched.
_PLACEHOLDER_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class VerificationConfigError(ValueError):
    """Malformed `verification.yaml` schema (wrong shape / bad enum / missing field).

    Subclasses `ValueError` so CLI wrap sites that already catch `ValueError`
    (mirroring the sensor/gate/adapter loaders) map it to EXIT_VALIDATION. This
    is deliberately distinct from `yaml.YAMLError` (syntax corruption, which
    subclasses `Exception` not `ValueError`) and from `FileNotFoundError`
    (missing file → EXIT_NO_CONFIG).
    """


class InterpolationError(VerificationConfigError):
    """A check `command` references a `${NAME}` placeholder outside the allowlist.

    A focused subclass of `VerificationConfigError` (hence still a `ValueError`,
    so the CLI maps it to EXIT_VALIDATION) — distinct only so callers can tell a
    bad placeholder apart from a wrong-shape schema error if they wish.
    """


class VerificationCheckConflict(VerificationConfigError):
    """Two adapters contribute an `adapter_provided` check with the SAME `id`.

    Raised by `merge_adapter_provided_list` (and its file-I/O wrapper
    `merge_adapter_provided`) when an incoming check shares its `id` with an
    EXISTING row owned by a DIFFERENT `provided_by` — the OI-3 conflict-reject
    case. Same-id + same-`provided_by` is NOT a conflict (it is an idempotent
    re-register / re-install → REPLACE in place).

    A focused subclass of `VerificationConfigError` (hence still a `ValueError`),
    so the shared CLI convention `ValueError → EXIT_VALIDATION` maps it to exit 2
    on BOTH the `adapter install` and `verification register` paths with zero
    per-caller branching.
    """


@dataclass(frozen=True)
class Layers:
    """Per-layer enable flags (engineering-integration §2.3 `layers`)."""

    baseline: bool = True
    framework_adapter: bool = True
    user_checks: bool = True


@dataclass(frozen=True)
class Defaults:
    """Fallback scalar values inherited by checks that omit them.

    `env` is the process-default env contribution; it is kept SEPARATE from each
    `CheckSpec.env` (the three-way os.environ + defaults.env + check.env merge
    happens at execution time, a later task).
    """

    timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS
    must_pass: bool = _DEFAULT_MUST_PASS
    capture: str = _DEFAULT_CAPTURE
    workdir: str = _DEFAULT_WORKDIR
    env: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Execution:
    """Scheduling knobs for the runner (engineering-integration §2.3 `execution`)."""

    mode: str = _DEFAULT_MODE
    max_parallelism: int = _DEFAULT_MAX_PARALLELISM
    fail_fast: bool = _DEFAULT_FAIL_FAST


@dataclass(frozen=True)
class CheckSpec:
    """A single verification check, with scalar defaults already resolved.

    `provided_by` is set only for `adapter_provided` entries (the adapter id
    that contributed the check); it is `None` for user `checks`. The loader
    rejects a `provided_by` key on a user `checks` entry (it is adapter-injected
    metadata only).

    `env` is the per-check env dict exactly as written in the yaml — NOT merged
    with `defaults.env` (see module docstring).
    """

    id: str
    command: str
    must_pass: bool
    timeout_seconds: int
    capture: str
    workdir: str
    env: dict[str, str]
    provided_by: str | None = None


@dataclass(frozen=True)
class VerificationConfig:
    """Fully parsed + validated `verification.yaml`."""

    layers: Layers
    defaults: Defaults
    execution: Execution
    checks: list[CheckSpec]
    adapter_provided: list[CheckSpec]


def load_verification_config(path: Path) -> VerificationConfig:
    """Load + validate `.harness/verification.yaml` from `path`.

    Args:
        path: Path to the verification yaml. This task accepts a `Path`
            directly; the `verification_yaml_path(root)` helper is a later task.

    Returns:
        A fully validated `VerificationConfig` with scalar defaults already
        applied to every `CheckSpec`.

    Raises:
        FileNotFoundError: `path` does not exist. `init` always writes the file,
            so absence is a hard error (CLI → EXIT_NO_CONFIG), not a fall-back-to-
            defaults signal.
        yaml.YAMLError: `path` is not syntactically valid YAML (propagated
            unwrapped from `yaml.safe_load`). Subclasses `Exception`, NOT
            `ValueError` — CLI catch tuples must list it explicitly to map it to
            EXIT_VALIDATION.
        VerificationConfigError: the YAML is syntactically valid but its shape is
            wrong (top-level not a mapping, a block has the wrong type, an enum
            value is invalid, a required `id`/`command` is missing/empty, or a
            check `id` is duplicated within its layer). Subclasses `ValueError`
            → CLI maps to EXIT_VALIDATION.
        InterpolationError: a check `command` references a `${NAME}` placeholder
            outside `INTERPOLATION_ALLOWLIST`. A `VerificationConfigError`
            subclass (hence a `ValueError`) → CLI maps to EXIT_VALIDATION.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"verification config not found at {path}; run `super-harness init`"
        )

    # Let yaml.YAMLError propagate unwrapped (mirrors adapters.registry /
    # core._registry). `or {}` treats an empty file as an empty mapping → all
    # defaults.
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise VerificationConfigError(
            f"{path}: top-level must be a mapping, got {type(raw).__name__}"
        )

    layers = _parse_layers(_require_mapping(raw, "layers", path))
    defaults = _parse_defaults(_require_mapping(raw, "defaults", path))
    execution = _parse_execution(_require_mapping(raw, "execution", path))
    checks = _parse_checks(raw.get("checks"), defaults, path, layer="checks")
    adapter_provided = _parse_checks(
        raw.get("adapter_provided"), defaults, path, layer="adapter_provided"
    )

    return VerificationConfig(
        layers=layers,
        defaults=defaults,
        execution=execution,
        checks=checks,
        adapter_provided=adapter_provided,
    )


# --------------------------------------------------------------------------- #
# adapter_provided merge (shared by `adapter install` + `verification register`)
# --------------------------------------------------------------------------- #


def merge_adapter_provided_list(
    existing: list[dict[str, Any]], new: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Merge `new` adapter-check dicts into `existing`, keyed on check `id`.

    Pure, directly unit-testable core of the shared merge — it operates on RAW
    dicts (NOT parsed `CheckSpec`s) so it matches the shipped install path and
    preserves file fidelity when the file-I/O wrapper round-trips it. Both
    `adapter install` and `verification register` funnel through here so the
    OI-3 conflict-reject rule can never drift between the two surfaces.

    Each incoming check carries its own `provided_by` (the install path gets it
    from `adapter.verification_checks()`; the register path stamps it from the
    `<adapter-name>` arg). Merge semantics, applied per incoming check, keyed on
    `id`:

    - existing row with the same `id` + the SAME `provided_by` → REPLACE in
      place (idempotent re-register / re-install — fixes the duplicate-
      accumulation bug where re-installing appended another identical row).
    - existing row with the same `id` + a DIFFERENT `provided_by` → raise
      `VerificationCheckConflict` (naming the id + both `provided_by` values).
    - no existing row with that `id` → append (preserving order).

    Returns a NEW list (the input `existing` is not mutated in place); callers
    that read→merge→write get a clean value to write back.
    """
    # Shallow-copy so we never mutate the caller's list object; the dict
    # elements are shared by reference (we only replace/append whole dicts).
    merged: list[dict[str, Any]] = list(existing)
    for check in new:
        incoming_id = check.get("id")
        incoming_by = check.get("provided_by")
        for idx, row in enumerate(merged):
            if not isinstance(row, dict) or row.get("id") != incoming_id:
                continue
            existing_by = row.get("provided_by")
            if existing_by != incoming_by:
                raise VerificationCheckConflict(
                    f"verification check id {incoming_id!r} is already provided by "
                    f"{existing_by!r}; refusing to let {incoming_by!r} override it"
                )
            # Same id + same provided_by → idempotent replace in place.
            merged[idx] = check
            break
        else:
            # No existing row with this id → append.
            merged.append(check)
    return merged


def merge_adapter_provided(path: Path, checks: list[dict[str, Any]]) -> None:
    """Read→merge→write `checks` into a verification.yaml's `adapter_provided`.

    The file-I/O wrapper used by BOTH `adapter install` and `verification
    register`. Empty-safe + absent-file-tolerant exactly like the install path
    it replaces: an empty `checks` is a true no-op (no read, no write), and an
    absent file is treated as an empty config (the file + its parent dir are
    created lazily only when there is something to write). Preserves all other
    top-level keys already present in the file (no silent drop).

    Raises:
        yaml.YAMLError: `path` exists but is syntactically invalid YAML
            (propagated unwrapped from `yaml.safe_load`; callers map it to
            EXIT_NO_CONFIG, matching the shipped install/uninstall convention).
        VerificationCheckConflict: an incoming check collides with an existing
            row owned by a different `provided_by` (OI-3 reject; a `ValueError`
            → CLI maps it to EXIT_VALIDATION / exit 2).
    """
    if not checks:
        return

    cfg: dict[str, Any] = {}
    if path.exists():
        # yaml.YAMLError propagates — callers catch + surface it.
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if isinstance(loaded, dict):
            cfg = loaded
    existing = cfg.get("adapter_provided")
    if not isinstance(existing, list):
        existing = []
    existing = [row for row in existing if isinstance(row, dict)]

    cfg["adapter_provided"] = merge_adapter_provided_list(existing, checks)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(cfg, sort_keys=False, default_flow_style=False), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Variable interpolation
# --------------------------------------------------------------------------- #


def interpolate(command: str, variables: dict[str, str]) -> str:
    """Substitute allowlisted `${NAME}` placeholders in a check `command`.

    Pure function. Recognizes only `${NAME}` tokens where `NAME` is a Python-
    style identifier; a bare `$`, an unbraced `$NAME`, or empty `${}` is left
    untouched.

    The allowlist gate is on the placeholder NAME, not its value:

    - `NAME` in `INTERPOLATION_ALLOWLIST` → replaced with
      `variables.get(NAME, "")`. An allowlisted-but-missing/empty value
      substitutes to `""` and does NOT raise. (In v0.1 `${SPEC_PATH}` /
      `${PLAN_PATH}` are always empty, so they reduce to `""`.)
    - `NAME` not in the allowlist (`${PR_URL}`, `${COMMIT_SHA}`, …) → raises
      `InterpolationError` (a `ValueError` subclass → CLI maps to
      EXIT_VALIDATION) naming the offending placeholder.

    All occurrences of each placeholder are replaced.

    Args:
        command: The raw check command string from `verification.yaml`.
        variables: The resolved interpolation variables (built by a later task,
            with `SPEC_PATH`/`PLAN_PATH` hardcoded empty in v0.1).

    Returns:
        The command with allowlisted placeholders substituted.

    Raises:
        InterpolationError: a `${NAME}` placeholder is outside the allowlist.
    """

    def _replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in INTERPOLATION_ALLOWLIST:
            raise InterpolationError(
                f"unknown interpolation placeholder ${{{name}}} in command "
                f"{command!r}; allowed: "
                f"{sorted('${' + n + '}' for n in INTERPOLATION_ALLOWLIST)}"
            )
        return variables.get(name, "")

    return _PLACEHOLDER_RE.sub(_replace, command)


def _validate_command_placeholders(command: str, *, check_id: str) -> None:
    """Reject any non-allowlisted `${NAME}` placeholder in `command` at load time.

    Catches a bad placeholder when the config is loaded (the `verify` / `done`
    CLI pre-load), not only at run time deep in the sensor's thread pool. A
    placeholder outside `INTERPOLATION_ALLOWLIST` can never run, so surfacing it
    eagerly turns a swallowed sensor crash into a clean EXIT_VALIDATION.

    Shares `_PLACEHOLDER_RE` + `INTERPOLATION_ALLOWLIST` with `interpolate` so
    the load-time gate and the run-time gate can never drift.

    Raises:
        InterpolationError: a `${NAME}` placeholder is outside the allowlist,
            naming the offending placeholder and the owning check id.
    """
    for match in _PLACEHOLDER_RE.finditer(command):
        name = match.group(1)
        if name not in INTERPOLATION_ALLOWLIST:
            raise InterpolationError(
                f"check {check_id!r} command references unknown interpolation "
                f"placeholder ${{{name}}}; allowed: "
                f"{sorted('${' + n + '}' for n in INTERPOLATION_ALLOWLIST)}"
            )


# --------------------------------------------------------------------------- #
# Block parsers
# --------------------------------------------------------------------------- #


def _parse_layers(block: dict[str, Any]) -> Layers:
    # Use the module-level _DEFAULT_LAYER_ENABLED constant rather than the
    # dataclass class-attr defaults (Layers.baseline, …): the constant is robust
    # if a field ever switches to field(default_factory=...), matching the other
    # block parsers' use of _DEFAULT_* constants.
    return Layers(
        baseline=_layer_enabled(block, "baseline", _DEFAULT_LAYER_ENABLED),
        framework_adapter=_layer_enabled(
            block, "framework_adapter", _DEFAULT_LAYER_ENABLED
        ),
        user_checks=_layer_enabled(block, "user_checks", _DEFAULT_LAYER_ENABLED),
    )


def _parse_defaults(block: dict[str, Any]) -> Defaults:
    return Defaults(
        timeout_seconds=_int(
            block, "timeout_seconds", _DEFAULT_TIMEOUT_SECONDS, "defaults"
        ),
        must_pass=_bool(block, "must_pass", _DEFAULT_MUST_PASS, "defaults"),
        capture=_enum(
            block, "capture", _DEFAULT_CAPTURE, _CAPTURE_VALUES, "defaults"
        ),
        workdir=_str(block, "workdir", _DEFAULT_WORKDIR, "defaults"),
        env=_env(block.get("env"), "defaults.env"),
    )


def _parse_execution(block: dict[str, Any]) -> Execution:
    return Execution(
        mode=_enum(block, "mode", _DEFAULT_MODE, _MODE_VALUES, "execution"),
        max_parallelism=_int(
            block, "max_parallelism", _DEFAULT_MAX_PARALLELISM, "execution"
        ),
        fail_fast=_bool(block, "fail_fast", _DEFAULT_FAIL_FAST, "execution"),
    )


def _parse_checks(
    raw: Any,
    defaults: Defaults,
    path: Path,
    *,
    layer: str,
) -> list[CheckSpec]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise VerificationConfigError(
            f"{path}: {layer!r} must be a list, got {type(raw).__name__}"
        )

    specs: list[CheckSpec] = []
    # Uniqueness is per-layer: a user check and an adapter_provided check may
    # share an id (they run in different layers), but two entries in the SAME
    # list may not.
    seen_ids: set[str] = set()
    for index, entry in enumerate(raw):
        spec = _parse_check_entry(entry, defaults, path, layer, index)
        if spec.id in seen_ids:
            raise VerificationConfigError(
                f"{path}: duplicate check id {spec.id!r} in {layer!r}"
            )
        seen_ids.add(spec.id)
        specs.append(spec)
    return specs


def _parse_check_entry(
    entry: Any,
    defaults: Defaults,
    path: Path,
    layer: str,
    index: int,
) -> CheckSpec:
    if not isinstance(entry, dict):
        raise VerificationConfigError(
            f"{path}: {layer}[{index}] must be a mapping, got {type(entry).__name__}"
        )

    where = f"{layer}[{index}]"
    check_id = entry.get("id")
    if not isinstance(check_id, str) or not check_id:
        raise VerificationConfigError(
            f"{path}: {where} is missing a non-empty string 'id'"
        )
    command = entry.get("command")
    if not isinstance(command, str) or not command:
        raise VerificationConfigError(
            f"{path}: check {check_id!r} is missing a non-empty string 'command'"
        )
    # Reject non-allowlisted `${NAME}` placeholders at LOAD time (not only at
    # run time in the sensor thread pool) so a typo'd `${PR_URL}` surfaces as a
    # clean EXIT_VALIDATION via the CLI pre-load. A bad placeholder can never
    # run, so it is never valid to defer.
    _validate_command_placeholders(command, check_id=check_id)

    provided_by = entry.get("provided_by")
    if provided_by is not None:
        # `provided_by` is adapter-injected metadata: it is meaningful only in
        # the adapter_provided layer. Reject it on user `checks` so a stray key
        # is a loud error rather than silently accepted (spec §2.3 frames it as
        # adapter-only).
        if layer != "adapter_provided":
            raise VerificationConfigError(
                f"{path}: check {check_id!r} in {layer!r} may not set "
                f"'provided_by' (it is for adapter_provided entries only)"
            )
        if not isinstance(provided_by, str):
            raise VerificationConfigError(
                f"{path}: check {check_id!r} 'provided_by' must be a string, "
                f"got {type(provided_by).__name__}"
            )

    field_ctx = f"check {check_id!r}"
    return CheckSpec(
        id=check_id,
        command=command,
        must_pass=_bool(entry, "must_pass", defaults.must_pass, field_ctx),
        timeout_seconds=_int(
            entry, "timeout_seconds", defaults.timeout_seconds, field_ctx
        ),
        capture=_enum(entry, "capture", defaults.capture, _CAPTURE_VALUES, field_ctx),
        workdir=_str(entry, "workdir", defaults.workdir, field_ctx),
        env=_env(entry.get("env"), f"{field_ctx} env"),
        provided_by=provided_by,
    )


# --------------------------------------------------------------------------- #
# Typed scalar / container helpers
# --------------------------------------------------------------------------- #


def _require_mapping(raw: dict[str, Any], key: str, path: Path) -> dict[str, Any]:
    """Return `raw[key]` as a mapping, or `{}` if absent. Reject non-mappings."""
    value = raw.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise VerificationConfigError(
            f"{path}: {key!r} must be a mapping, got {type(value).__name__}"
        )
    return value


def _layer_enabled(block: dict[str, Any], name: str, default: bool) -> bool:
    sub = block.get(name)
    if sub is None:
        return default
    if not isinstance(sub, dict):
        raise VerificationConfigError(
            f"layers.{name} must be a mapping with an 'enabled' key, "
            f"got {type(sub).__name__}"
        )
    return _bool(sub, "enabled", default, f"layers.{name}")


def _bool(block: dict[str, Any], key: str, default: bool, ctx: str) -> bool:
    value = block.get(key, default)
    # `isinstance(True, int)` is True, so check bool FIRST / exclusively here.
    if not isinstance(value, bool):
        raise VerificationConfigError(
            f"{ctx}.{key} must be a bool, got {type(value).__name__}"
        )
    return value


def _int(block: dict[str, Any], key: str, default: int, ctx: str) -> int:
    value = block.get(key, default)
    # Reject bool explicitly: `isinstance(True, int)` is True in Python, and a
    # boolean timeout/parallelism is almost certainly a mistake.
    if isinstance(value, bool) or not isinstance(value, int):
        raise VerificationConfigError(
            f"{ctx}.{key} must be an int, got {type(value).__name__}"
        )
    return value


def _str(block: dict[str, Any], key: str, default: str, ctx: str) -> str:
    value = block.get(key, default)
    if not isinstance(value, str):
        raise VerificationConfigError(
            f"{ctx}.{key} must be a string, got {type(value).__name__}"
        )
    return value


def _enum(
    block: dict[str, Any],
    key: str,
    default: str,
    allowed: tuple[str, ...],
    ctx: str,
) -> str:
    value = block.get(key, default)
    if not isinstance(value, str) or value not in allowed:
        raise VerificationConfigError(
            f"{ctx}.{key} must be one of {list(allowed)}, got {value!r}"
        )
    return value


def _env(value: Any, ctx: str) -> dict[str, str]:
    """Validate + return an env mapping (string→string); `None` → empty dict."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise VerificationConfigError(
            f"{ctx} must be a mapping, got {type(value).__name__}"
        )
    result: dict[str, str] = {}
    for k, v in value.items():
        if not isinstance(k, str) or not isinstance(v, str):
            raise VerificationConfigError(
                f"{ctx} must map strings to strings; "
                f"got {type(k).__name__}->{type(v).__name__}"
            )
        result[k] = v
    return result
