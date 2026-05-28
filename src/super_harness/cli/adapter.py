"""`adapter` subgroup — install / uninstall / list super-harness integrations.

Generalizes the Phase-5 MINIMAL `adapter install claude-code` (a single pinned
subcommand) into a registry-driven surface over BOTH framework and agent
adapters (adapter-architecture §2.1/§2.2/§2.3):

- ``install <name>``   — resolve a built-in adapter, run its install steps, and
                         persist a ``.harness/adapters.yaml`` entry (§2.3).
- ``uninstall <name>`` — full reverse: ``on_uninstall`` + drop the yaml entry.
- ``list``             — enumerate the INSTALLED set (adapters.yaml entries),
                         enriched from the built-in table.

Workspace resolution + error/exit-code conventions mirror the sibling `gate`
group (cli/gate.py): walk up for ``.harness/`` and map
``HarnessNotInitialized`` → ``EXIT_NO_CONFIG``; error output goes through
``format_error`` on stderr.

Free-form ``name`` (NOT a ``click.Choice``): the install exit set is ``0/1/3/5``
(cli-command-surface) — ``2`` is excluded, but click auto-emits exit 2 on a bad
``Choice``. We therefore do the registry membership check ourselves and map an
unknown name to ``EXIT_GENERIC`` (1), the same path as the install RuntimeError.

`.claude/`-absent decision (option (a) — install in a fresh repo) is preserved
from Phase 5: ``ClaudeCodeAdapter.detect(root)`` is NOT a precondition;
``install_hooks`` mkdirs ``.claude/`` and we note its creation.

Phase-6 deferrals (per plan / Out-of-scope):
- AGENTS.md wiring is a TRUE no-op here (deferred to Phase 9) — we never write
  AGENTS.md nor call ``agents_md_subsection()``.
- ``verification.yaml`` merge is empty-safe: built-in adapters return ``[]`` in
  v0.1, so we touch nothing (no read, no write). The non-empty branch is kept
  minimal and tolerates an absent file.
- No ``adapters.yaml`` file locking (so the surface's ``5``/EXIT_CONCURRENCY is
  never emitted in Phase 6).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import click
import yaml

from super_harness.adapters import AgentAdapter, FrameworkAdapter
from super_harness.adapters.registry import get_builtin, list_builtins
from super_harness.cli.errors import format_error
from super_harness.cli.exit_codes import EXIT_GENERIC, EXIT_NO_CONFIG, EXIT_OK
from super_harness.cli.output import json_envelope
from super_harness.core.paths import (
    HarnessNotInitialized,
    adapters_yaml_path,
    find_harness_root,
)

# Leading comment written when CREATING adapters.yaml so users know the file is
# tool-managed (mirrors state.yaml's AUTO-GENERATED header convention).
_ADAPTERS_YAML_HEADER = (
    "# .harness/adapters.yaml\n"
    "# AUTO-MANAGED by super-harness adapter install/uninstall. Do not edit.\n"
)


@click.group("adapter")
def adapter_group() -> None:
    """Install / uninstall / list super-harness integrations for frameworks + agents."""


@adapter_group.command("install")
@click.argument("name")  # free-form: registry membership checked below (NOT click.Choice)
@click.pass_context
def adapter_install(ctx: click.Context, name: str) -> None:
    """Install the <name> adapter (registers hooks + persists adapters.yaml)."""
    root = _resolve_root(ctx, "adapter install")

    adapter = _resolve_builtin_or_exit(name, "adapter install")
    kind = "framework" if isinstance(adapter, FrameworkAdapter) else "agent"

    # AGENTS.md: TRUE no-op (Phase 9). verification.yaml: empty-safe (built-ins
    # return [] in v0.1 → nothing written); only the non-empty branch touches it.
    try:
        _merge_verification_checks(root, adapter)
    except yaml.YAMLError as e:
        click.echo(
            format_error(
                subcommand="adapter install",
                message=f"verification.yaml is corrupt or unreadable: {e}",
                hint="Fix or remove .harness/verification.yaml and retry.",
            ),
            err=True,
        )
        sys.exit(EXIT_NO_CONFIG)

    # Agent adapters install hooks BEFORE we persist the yaml so a failed
    # install_hooks never leaves a stale adapters.yaml entry behind.
    created_claude_dir = False
    if isinstance(adapter, AgentAdapter):
        # `.claude/`-absent is fine — install creates it (decision (a)). Note it
        # so the user knows a new dir appeared (Phase-5 behaviour preserved).
        created_claude_dir = not adapter.detect(root)
        try:
            adapter.install_hooks(root)
        except RuntimeError as e:
            # The documented RuntimeError is "super-harness-hook not on PATH" — a
            # broken install the user must repair. Surface its message verbatim
            # through format_error rather than letting a traceback escape.
            click.echo(
                format_error(subcommand="adapter install", message=str(e)),
                err=True,
            )
            sys.exit(EXIT_GENERIC)

    try:
        _persist_install_entry(root, name=name, kind=kind, version=adapter.version)
    except yaml.YAMLError as e:
        click.echo(
            format_error(
                subcommand="adapter install",
                message=f"adapters.yaml is corrupt or unreadable: {e}",
                hint="Fix or remove .harness/adapters.yaml and retry.",
            ),
            err=True,
        )
        sys.exit(EXIT_NO_CONFIG)

    if not ctx.obj.get("quiet"):
        if created_claude_dir:
            click.echo("Created .claude/settings.json (no .claude/ existed).")
        if isinstance(adapter, AgentAdapter):
            detail = "PreToolUse gate hook registered in .claude/settings.json"
        else:
            detail = "framework adapter registered"
        click.echo(
            f"Installed {name} adapter ({kind}): {detail}; "
            f"recorded in .harness/adapters.yaml."
        )
    sys.exit(EXIT_OK)


@adapter_group.command("uninstall")
@click.argument("name")
@click.pass_context
def adapter_uninstall(ctx: click.Context, name: str) -> None:
    """Uninstall the <name> adapter (reverse of install)."""
    root = _resolve_root(ctx, "adapter uninstall")

    adapter = _resolve_builtin_or_exit(name, "adapter uninstall")

    # Not installed → clear message, EXIT_GENERIC (don't crash).
    try:
        entries = _read_adapter_entries(adapters_yaml_path(root))
    except yaml.YAMLError as e:
        click.echo(
            format_error(
                subcommand="adapter uninstall",
                message=f"adapters.yaml is corrupt or unreadable: {e}",
                hint="Fix or remove .harness/adapters.yaml and retry.",
            ),
            err=True,
        )
        sys.exit(EXIT_NO_CONFIG)
    if not any(e.get("name") == name for e in entries):
        click.echo(
            format_error(
                subcommand="adapter uninstall",
                message=f"adapter {name!r} is not installed",
                hint="Use `adapter list` to see installed adapters.",
            ),
            err=True,
        )
        sys.exit(EXIT_GENERIC)

    # Interactive confirm unless --quiet.
    if not ctx.obj.get("quiet"):
        click.confirm(
            f"Uninstall the {name!r} adapter from this workspace?",
            abort=True,
        )

    # Reverse install: adapter-specific cleanup (agents override on_uninstall to
    # remove their hooks; framework default is no-op), then drop the yaml entry,
    # then prune any verification.yaml.adapter_provided rows it contributed
    # (no-op in v0.1 — none were added — and guarded on file-absent).
    #
    # on_uninstall failure (e.g. PermissionError on .claude/settings.json) aborts
    # the uninstall entirely — the yaml entry is NOT removed so `list` still shows
    # the adapter and the user can retry after fixing the underlying issue.
    try:
        adapter.on_uninstall(root)
    except OSError as e:
        click.echo(
            format_error(
                subcommand="adapter uninstall",
                message=f"failed to clean up {name!r} adapter hooks: {e}",
                hint="Fix the file permissions and re-run `adapter uninstall`.",
            ),
            err=True,
        )
        sys.exit(EXIT_GENERIC)
    try:
        _remove_install_entry(root, name=name)
    except yaml.YAMLError as e:
        click.echo(
            format_error(
                subcommand="adapter uninstall",
                message=f"adapters.yaml is corrupt or unreadable: {e}",
                hint="Fix or remove .harness/adapters.yaml and retry.",
            ),
            err=True,
        )
        sys.exit(EXIT_NO_CONFIG)
    try:
        _remove_verification_checks(root, adapter)
    except yaml.YAMLError as e:
        click.echo(
            format_error(
                subcommand="adapter uninstall",
                message=f"verification.yaml is corrupt or unreadable: {e}",
                hint="Fix or remove .harness/verification.yaml and retry.",
            ),
            err=True,
        )
        sys.exit(EXIT_NO_CONFIG)

    if not ctx.obj.get("quiet"):
        click.echo(f"Uninstalled {name} adapter.")
    sys.exit(EXIT_OK)


@adapter_group.command("list")
@click.option(
    "--type",
    "type_filter",
    type=click.Choice(["framework", "agent"]),
    default=None,
    help="Only list adapters of this kind.",
)
@click.option(
    "--enabled-only", is_flag=True, help="Only list adapters with enabled: true."
)
@click.pass_context
def adapter_list(
    ctx: click.Context, type_filter: str | None, enabled_only: bool
) -> None:
    """List INSTALLED adapters (adapters.yaml entries), enriched from built-ins."""
    root = _resolve_root(ctx, "adapter list")

    try:
        rows = _collect_adapter_rows(adapters_yaml_path(root))
    except yaml.YAMLError as e:
        click.echo(
            format_error(
                subcommand="adapter list",
                message=f"adapters.yaml is corrupt or unreadable: {e}",
                hint="Fix or remove .harness/adapters.yaml and retry.",
            ),
            err=True,
        )
        sys.exit(EXIT_NO_CONFIG)
    filtered = type_filter is not None or enabled_only
    if type_filter is not None:
        rows = [r for r in rows if r["type"] == type_filter]
    if enabled_only:
        rows = [r for r in rows if r["enabled"]]

    if ctx.obj.get("json"):
        click.echo(
            json_envelope(
                command="adapter list",
                status="pass",
                exit_code=EXIT_OK,
                data={"adapters": rows},
            )
        )
    else:
        _render_human_table(rows, filtered=filtered)
    sys.exit(EXIT_OK)


# --- shared helpers ---------------------------------------------------------


def _resolve_root(ctx: click.Context, subcommand: str) -> Path:
    """Resolve the workspace root or exit EXIT_NO_CONFIG (mirrors gate/sensor)."""
    try:
        return find_harness_root(Path(ctx.obj.get("workspace") or "."))
    except HarnessNotInitialized as e:
        click.echo(
            format_error(subcommand=subcommand, message=e.message, hint=e.hint),
            err=True,
        )
        sys.exit(EXIT_NO_CONFIG)


def _resolve_builtin_or_exit(
    name: str, subcommand: str
) -> FrameworkAdapter | AgentAdapter:
    """Resolve a built-in adapter instance, or print + exit EXIT_GENERIC (1).

    Free-form name → registry membership check by hand (NOT a click.Choice, which
    would emit the excluded exit 2). Unknown name maps to EXIT_GENERIC, the same
    path as the install RuntimeError.
    """
    cls = get_builtin(name)
    if cls is None:
        click.echo(
            format_error(
                subcommand=subcommand,
                message=f"unknown adapter {name!r}",
                hint=(
                    f"Use `adapter list` or see the built-in adapters: "
                    f"{', '.join(list_builtins())}."
                ),
            ),
            err=True,
        )
        sys.exit(EXIT_GENERIC)
    return cls()


def _merge_verification_checks(
    root: Path, adapter: FrameworkAdapter | AgentAdapter
) -> None:
    """Merge adapter-provided checks into verification.yaml — EMPTY-SAFE.

    Only FrameworkAdapter declares ``verification_checks``; agents have none. If
    the adapter contributes no checks (plain & claude-code in v0.1) this is a
    TRUE no-op — no read, no write — so a bare ``.harness/`` with no
    verification.yaml (Phase-5 test fixture) is never touched. Only the non-empty
    branch reads/writes, and it tolerates an absent file.
    """
    if not isinstance(adapter, FrameworkAdapter):
        return
    checks = adapter.verification_checks()
    if not checks:
        return

    path = root / ".harness" / "verification.yaml"
    cfg: dict[str, Any] = {}
    if path.exists():
        loaded = yaml.safe_load(path.read_text()) or {}
        if isinstance(loaded, dict):
            cfg = loaded
    provided = cfg.get("adapter_provided")
    if not isinstance(provided, list):
        provided = []
    provided.extend(checks)
    cfg["adapter_provided"] = provided
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(cfg, sort_keys=False, default_flow_style=False))


def _remove_verification_checks(
    root: Path, adapter: FrameworkAdapter | AgentAdapter
) -> None:
    """Remove this adapter's contributed verification.yaml.adapter_provided rows.

    No-op in v0.1 (built-ins contribute none) and guarded on file-absent. Only
    framework adapters can have contributed checks.
    """
    if not isinstance(adapter, FrameworkAdapter):
        return
    checks = adapter.verification_checks()
    if not checks:
        return
    path = root / ".harness" / "verification.yaml"
    if not path.exists():
        return
    loaded = yaml.safe_load(path.read_text()) or {}
    if not isinstance(loaded, dict):
        return
    provided = loaded.get("adapter_provided")
    if not isinstance(provided, list):
        return
    loaded["adapter_provided"] = [c for c in provided if c not in checks]
    path.write_text(yaml.safe_dump(loaded, sort_keys=False, default_flow_style=False))


def _read_adapter_cfg(path: Path) -> dict[str, Any]:
    """Return the full parsed mapping from adapters.yaml ({} if absent/empty).

    Raises:
        yaml.YAMLError: if the file exists but contains invalid YAML (callers
            must catch this and surface it via ``format_error``).
    """
    if not path.exists():
        return {}
    # NOTE: yaml.YAMLError propagates — callers catch it.
    loaded = yaml.safe_load(path.read_text())
    if not isinstance(loaded, dict):
        return {}
    return loaded


def _read_adapter_entries(path: Path) -> list[dict[str, Any]]:
    """Return the list of adapter entries from adapters.yaml ([] if absent/empty).

    Raises:
        yaml.YAMLError: propagated from ``_read_adapter_cfg`` on corrupt YAML.
    """
    cfg = _read_adapter_cfg(path)
    entries = cfg.get("adapters") or []
    if not isinstance(entries, list):
        return []
    return [e for e in entries if isinstance(e, dict)]


def _write_adapter_cfg(path: Path, cfg: dict[str, Any]) -> None:
    """Write the full config mapping back to adapters.yaml (preserving top-level keys).

    Lazily creates parent directories and prepends the AUTO-MANAGED header.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(cfg, sort_keys=False, default_flow_style=False)
    path.write_text(_ADAPTERS_YAML_HEADER + body)


def _persist_install_entry(
    root: Path, *, name: str, kind: str, version: str
) -> None:
    """Write/update the §2.3 adapters.yaml entry — idempotent update-in-place.

    Re-installing rewrites the existing same-name entry rather than appending a
    duplicate; the file is created lazily if absent. Preserves all other
    top-level keys already present in adapters.yaml.
    """
    path = adapters_yaml_path(root)
    cfg = _read_adapter_cfg(path)
    entries: list[dict[str, Any]] = cfg.get("adapters") or []
    if not isinstance(entries, list):
        entries = []
    entries = [e for e in entries if isinstance(e, dict)]
    new_entry: dict[str, Any] = {
        "name": name,
        "type": kind,
        "builtin": True,
        "version": version,
        "enabled": True,
    }
    for idx, entry in enumerate(entries):
        if entry.get("name") == name:
            entries[idx] = new_entry
            break
    else:
        entries.append(new_entry)
    cfg["adapters"] = entries
    _write_adapter_cfg(path, cfg)


def _remove_install_entry(root: Path, *, name: str) -> None:
    """Drop the adapters.yaml entry for `name` (leaving ``adapters: []`` if empty).

    Preserves all other top-level keys already present in adapters.yaml.
    """
    path = adapters_yaml_path(root)
    cfg = _read_adapter_cfg(path)
    entries: list[dict[str, Any]] = cfg.get("adapters") or []
    if not isinstance(entries, list):
        entries = []
    entries = [e for e in entries if isinstance(e, dict)]
    cfg["adapters"] = [e for e in entries if e.get("name") != name]
    _write_adapter_cfg(path, cfg)


def _collect_adapter_rows(path: Path) -> list[dict[str, Any]]:
    """Build display rows for the INSTALLED set (adapters.yaml entries).

    Iterates the installed entries (NOT all built-ins) so uninstalled built-ins
    are never surfaced. Built-in rows are ENRICHED from the registry (the
    authoritative version + agent capabilities). ``capabilities`` is
    AgentAdapter-only — framework rows degrade to ``None`` (never a crashing
    ``getattr`` on a FrameworkAdapter, which has no such attribute).
    """
    rows: list[dict[str, Any]] = []
    for entry in _read_adapter_entries(path):
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            continue
        kind = entry.get("type")
        version = entry.get("version")
        builtin = bool(entry.get("builtin", False))
        enabled = bool(entry.get("enabled", True))
        capabilities: dict[str, bool] | None = None

        if builtin:
            cls = get_builtin(name)
            if cls is not None:
                inst = cls()
                # Re-derive kind/version from the actual class (authoritative).
                kind = "agent" if isinstance(inst, AgentAdapter) else "framework"
                version = cls.version
                # capabilities is AgentAdapter-only — FrameworkAdapter has none.
                if isinstance(inst, AgentAdapter):
                    capabilities = dict(inst.capabilities)

        rows.append(
            {
                "name": name,
                "type": kind,
                "builtin": builtin,
                "version": version,
                "enabled": enabled,
                "capabilities": capabilities,
            }
        )
    return rows


def _capabilities_summary(capabilities: dict[str, bool] | None) -> str:
    """Human-column summary: enabled capability keys, comma-joined ('-' if none)."""
    if not capabilities:
        return "-"
    enabled = sorted(k for k, v in capabilities.items() if v)
    return ", ".join(enabled) if enabled else "-"


def _render_human_table(
    rows: list[dict[str, Any]], *, filtered: bool = False
) -> None:
    """Print an aligned NAME/TYPE/SOURCE/VERSION/ENABLED/CAPABILITIES table.

    Args:
        rows: display rows (already filtered by the caller).
        filtered: True when a ``--type`` or ``--enabled-only`` filter was
            active.  Changes the empty-set message so it doesn't mislead the
            user into thinking nothing is installed when adapters exist but
            none match the active filter.
    """
    if not rows:
        if filtered:
            click.echo("No adapters match the given filter.")
        else:
            click.echo("No adapters installed.")
        return

    display: list[dict[str, str]] = []
    for r in rows:
        display.append(
            {
                "name": str(r["name"]),
                "type": str(r["type"]),
                "source": "built-in" if r["builtin"] else "custom",
                "version": str(r["version"]),
                "enabled": "yes" if r["enabled"] else "no",
                "capabilities": _capabilities_summary(r["capabilities"]),
            }
        )

    headers = {
        "name": "NAME",
        "type": "TYPE",
        "source": "SOURCE",
        "version": "VERSION",
        "enabled": "ENABLED",
        "capabilities": "CAPABILITIES",
    }
    widths = {
        key: max(len(headers[key]), max(len(d[key]) for d in display))
        for key in ("name", "type", "source", "version", "enabled")
    }
    header_line = (
        f"{headers['name']:<{widths['name']}}  "
        f"{headers['type']:<{widths['type']}}  "
        f"{headers['source']:<{widths['source']}}  "
        f"{headers['version']:<{widths['version']}}  "
        f"{headers['enabled']:<{widths['enabled']}}  "
        f"{headers['capabilities']}"
    )
    click.echo(header_line)
    for d in display:
        click.echo(
            f"{d['name']:<{widths['name']}}  "
            f"{d['type']:<{widths['type']}}  "
            f"{d['source']:<{widths['source']}}  "
            f"{d['version']:<{widths['version']}}  "
            f"{d['enabled']:<{widths['enabled']}}  "
            f"{d['capabilities']}"
        )
