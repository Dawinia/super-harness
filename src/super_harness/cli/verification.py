"""`verification` subgroup — register externally-defined checks into verification.yaml.

Sibling surface to the `adapter` group: where `adapter install` merges the
checks a BUILT-IN FrameworkAdapter declares, ``verification register`` lets an
operator (or an out-of-tree tool / custom framework) contribute checks into
``.harness/verification.yaml``'s ``adapter_provided`` region from a yaml file,
stamping a caller-supplied ``<adapter-name>`` as the authoritative provenance.

Both this command and ``adapter install`` funnel their merge through the SHARED
``engineering.verification_config.merge_adapter_provided`` so the OI-3 conflict-
reject + idempotent replace-in-place semantics can never drift between the two
surfaces:

- same ``id`` + same ``provided_by`` → REPLACE in place (idempotent re-register).
- same ``id`` + a DIFFERENT ``provided_by`` already present → conflict
  (``VerificationCheckConflict`` → EXIT_VALIDATION / exit 2 — the OI-3 reject).
- a new ``id`` → append.

Workspace resolution + error/exit-code conventions mirror the sibling `adapter`
group (cli/adapter.py): walk up for ``.harness/`` and map ``HarnessNotInitialized``
→ ``EXIT_NO_CONFIG``; all error output goes through ``format_error`` on stderr.

Accepted yaml-file shape (``register``): EITHER a bare top-level LIST of check
mappings, OR a mapping carrying a ``checks:`` (alias ``adapter_provided:``) list.
Each check mapping is the §2.3 ``adapter_provided`` CheckSpec shape (``id`` +
``command`` required; ``must_pass`` optional). Any ``provided_by`` written in the
file is OVERWRITTEN with ``<adapter-name>`` — the arg is authoritative for
provenance, so a file can never spoof a different owner.

Exit codes (cli-command-surface §verification register):
- 0 — checks merged.
- 1 — generic error (unreadable file / wrong file shape).
- 2 — conflict (same id, different provided_by already present) — EXIT_VALIDATION.
- 3 — no ``.harness/`` (uninitialized) / corrupt verification.yaml.
- 5 — reserved (concurrency; never emitted in v0.1 — no file locking).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import click
import yaml

from super_harness.cli.errors import format_error
from super_harness.cli.exit_codes import (
    EXIT_GENERIC,
    EXIT_NO_CONFIG,
    EXIT_OK,
    EXIT_VALIDATION,
)
from super_harness.core.paths import (
    HarnessNotInitialized,
    find_harness_root,
    verification_yaml_path,
)
from super_harness.engineering.verification_config import (
    VerificationCheckConflict,
    merge_adapter_provided,
)


@click.group("verification")
def verification_group() -> None:
    """Register / manage verification checks in `.harness/verification.yaml`."""


@verification_group.command("register")
@click.argument("adapter_name")
@click.argument("yaml_file", type=click.Path(dir_okay=False))
@click.pass_context
def verification_register(
    ctx: click.Context, adapter_name: str, yaml_file: str
) -> None:
    """Register <yaml-file>'s checks into adapter_provided under <adapter-name>."""
    root = _resolve_root(ctx, "verification register")

    file_path = Path(yaml_file)
    try:
        checks = _load_checks_file(file_path)
    except FileNotFoundError:
        click.echo(
            format_error(
                subcommand="verification register",
                message=f"check file not found: {file_path}",
                hint="Pass a path to a readable yaml file of checks.",
            ),
            err=True,
        )
        sys.exit(EXIT_GENERIC)
    except yaml.YAMLError as e:
        click.echo(
            format_error(
                subcommand="verification register",
                message=f"{file_path} is not valid YAML: {e}",
                hint="Fix the YAML syntax in the check file.",
            ),
            err=True,
        )
        sys.exit(EXIT_GENERIC)
    except ValueError as e:
        # Wrong file SHAPE (not a list / not a mapping-with-checks / a non-dict
        # check entry) — a user input error, EXIT_GENERIC. NB this is a plain
        # ValueError raised by _load_checks_file, NOT VerificationCheckConflict.
        click.echo(
            format_error(subcommand="verification register", message=str(e)),
            err=True,
        )
        sys.exit(EXIT_GENERIC)

    # Stamp provided_by = <adapter-name> onto every check: the arg is the
    # authoritative provenance, so a file can never spoof a different owner.
    stamped = [{**c, "provided_by": adapter_name} for c in checks]

    # SHARED merge: empty `stamped` is a true no-op; a colliding id owned by a
    # different provided_by raises VerificationCheckConflict → EXIT_VALIDATION;
    # a re-register of the SAME adapter replaces its rows in place (idempotent).
    path = verification_yaml_path(root)
    try:
        merge_adapter_provided(path, stamped)
    except VerificationCheckConflict as e:
        click.echo(
            format_error(subcommand="verification register", message=str(e)),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    except yaml.YAMLError as e:
        click.echo(
            format_error(
                subcommand="verification register",
                message=f"verification.yaml is corrupt or unreadable: {e}",
                hint="Fix or remove .harness/verification.yaml and retry.",
            ),
            err=True,
        )
        sys.exit(EXIT_NO_CONFIG)

    if not ctx.obj.get("quiet"):
        n = len(stamped)
        click.echo(
            f"Registered {n} check{'' if n == 1 else 's'} under "
            f"{adapter_name!r} in .harness/verification.yaml."
        )
    sys.exit(EXIT_OK)


# --- shared helpers ---------------------------------------------------------


def _resolve_root(ctx: click.Context, subcommand: str) -> Path:
    """Resolve the workspace root or exit EXIT_NO_CONFIG (mirrors cli/adapter.py)."""
    try:
        return find_harness_root(Path(ctx.obj.get("workspace") or "."))
    except HarnessNotInitialized as e:
        click.echo(
            format_error(subcommand=subcommand, message=e.message, hint=e.hint),
            err=True,
        )
        sys.exit(EXIT_NO_CONFIG)


def _load_checks_file(path: Path) -> list[dict[str, Any]]:
    """Read a register check file → a list of raw check dicts.

    Accepts EITHER a bare top-level list of check mappings, OR a mapping with a
    ``checks:`` (alias ``adapter_provided:``) list. An empty file is an empty
    list (a no-op register). The per-field schema (required ``id``/``command``,
    ``provided_by`` provenance) is enforced downstream by the shared merge +
    `load_verification_config`; here we only validate the OUTER shape.

    Raises:
        FileNotFoundError: `path` does not exist.
        yaml.YAMLError: `path` is not syntactically valid YAML (propagated).
        ValueError: the outer shape is wrong (not a list / mapping, the
            checks key is not a list, or a check entry is not a mapping).
    """
    if not path.exists():
        raise FileNotFoundError(path)
    raw = yaml.safe_load(path.read_text()) or []
    if isinstance(raw, dict):
        checks = raw.get("checks")
        if checks is None:
            checks = raw.get("adapter_provided")
        if checks is None:
            raise ValueError(
                f"{path}: mapping must carry a 'checks' (or 'adapter_provided') list"
            )
        raw = checks
    if not isinstance(raw, list):
        raise ValueError(
            f"{path}: expected a list of checks (or a mapping with a 'checks' "
            f"list), got {type(raw).__name__}"
        )
    result: list[dict[str, Any]] = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ValueError(
                f"{path}: check[{index}] must be a mapping, got {type(entry).__name__}"
            )
        result.append(entry)
    return result
