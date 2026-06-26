"""`super-harness attest` — Layer-2 CI merge gate (HG-DF item C).

`attest write <slug>` snapshots a change's committed attestation
(`.harness/attestations/<slug>.jsonl`); `attest verify --base --head` is the CI
gate that fails when any changed file lacks a complete, ordered, scope-covering
attestation. The `git` boundary lives here (patchable in tests), mirroring how
`cli/pr.py` keeps the `gh` boundary at the CLI import site.

Exit codes:
- `attest write`: 0 ok / 1 no events for the slug / 3 no `.harness/`.
- `attest verify`: 0 pass / 2 blocker(s) (EXIT_VALIDATION) / 3 no `.harness/` /
  4 git failure (EXIT_EXTERNAL_TOOL, FAIL-CLOSED — never a vacuous pass).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import click

from super_harness.cli.errors import format_error
from super_harness.cli.output import json_envelope
from super_harness.core.paths import (
    HarnessNotInitialized,
    events_path,
    find_harness_root,
)
from super_harness.engineering.attestation import (
    ATTESTATIONS_DIRNAME,
    gate_bypass_for_attestation,
    independence_for_attestation,
    parse_name_status,
    verify_attestations,
    write_attestation,
)
from super_harness.exit_codes import (
    EXIT_EXTERNAL_TOOL,
    EXIT_GENERIC,
    EXIT_NO_CONFIG,
    EXIT_OK,
    EXIT_VALIDATION,
)


class _GitError(Exception):
    """`git diff` failed — translated to a FAIL-CLOSED exit 4 by the CLI."""


def _independence_line(item: dict[str, Any]) -> str:
    """One plain-ASCII disclosure line for a validated attestation (HG-12 cut 1).

    Disclosure only — this never affects the verify pass/fail. The `ci` class is
    forward-compat (not producible via the current CLI; see design §4.1 row 2).
    """
    cls, who = item["classification"], item.get("reviewer")
    if cls == "self-signed":
        return f"review independence: self-signed (self-review) — {who}"
    if cls == "independent":
        return f"review independence: independent — {who}"
    if cls == "skipped":
        if item.get("override"):
            return f"review independence: skipped (OVERRIDE: {item.get('reason')}) — {who}"
        return f"review independence: skipped — {who}"
    if cls == "ci":
        return "review independence: ci"
    return 'review independence: unattributed (legacy "cli" placeholder)'


@click.group("attest")
def attest_group() -> None:
    """Lifecycle attestation: snapshot evidence + verify it covers a diff."""


@attest_group.command("write")
@click.argument("slug")
@click.option(
    "--disclose-gate-bypass",
    "disclose_reason",
    default=None,
    help="Disclose+justify that the gate was bypassed during this change "
    "(clears the merge-gate blocker).",
)
@click.pass_context
def attest_write(ctx: click.Context, slug: str, disclose_reason: str | None) -> None:
    """Snapshot the per-change event slice to .harness/attestations/<slug>.jsonl."""
    try:
        root = find_harness_root(Path(ctx.obj.get("workspace") or "."))
    except HarnessNotInitialized as e:
        click.echo(
            format_error(subcommand="attest write", message=e.message, hint=e.hint),
            err=True,
        )
        sys.exit(EXIT_NO_CONFIG)
    if disclose_reason:
        from super_harness.core.clock import utc_now_iso
        from super_harness.core.events import Actor, Event
        from super_harness.core.identity import resolve_identity
        from super_harness.core.ulid import new_event_id
        from super_harness.core.writer import EventWriter

        # Informational event: emit with skip_validation and do NOT refresh derived
        # state (mirrors `_record_bypass` discipline — must not mutate the FSM).
        ev = Event(
            event_id=new_event_id(),
            type="gate_bypass_disclosed",
            change_id=slug,
            timestamp=utc_now_iso(),
            actor=Actor(type="human", identifier=resolve_identity(root, None)),
            framework="plain",
            payload={"reason": disclose_reason},
        )
        EventWriter(events_path(root)).emit(ev, skip_validation=True)
    try:
        out = write_attestation(events_path(root), root / ATTESTATIONS_DIRNAME, slug)
    except ValueError as e:
        click.echo(
            format_error(
                subcommand="attest write",
                message=str(e),
                hint=(
                    "Run the lifecycle for this change first — events.jsonl must "
                    "contain its events before an attestation can be written."
                ),
            ),
            err=True,
        )
        sys.exit(EXIT_GENERIC)
    rel = out.relative_to(root).as_posix()
    if ctx.obj.get("json"):
        click.echo(
            json_envelope(
                command="attest write",
                status="pass",
                exit_code=EXIT_OK,
                data={"change": slug, "attestation_path": rel},
            )
        )
    elif not ctx.obj.get("quiet"):
        click.echo(f"super-harness: wrote attestation {rel}")
    sys.exit(EXIT_OK)


def _git_name_status(base: str, head: str, cwd: Path) -> str:
    """Run `git diff --name-status base...head` in *cwd*.

    Raises ``_GitError`` on a non-zero git exit (the CLI translates that into a
    FAIL-CLOSED exit 4 — an unreachable merge-base must never become a pass).
    """
    # @decision:d-merge-gate-pure-git
    proc = subprocess.run(
        ["git", "-c", "core.quotePath=false", "diff", "--name-status", f"{base}...{head}"],
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise _GitError(proc.stderr.strip() or f"git diff failed (exit {proc.returncode})")
    return proc.stdout


@attest_group.command("verify")
@click.option("--base", required=True, help="Base ref/SHA (e.g. PR base.sha).")
@click.option("--head", required=True, help="Head ref/SHA (e.g. PR head.sha).")
@click.pass_context
def attest_verify(ctx: click.Context, base: str, head: str) -> None:
    """Fail if any changed file lacks a complete, ordered, scope-covering attestation."""
    try:
        root = find_harness_root(Path(ctx.obj.get("workspace") or "."))
    except HarnessNotInitialized as e:
        click.echo(
            format_error(subcommand="attest verify", message=e.message, hint=e.hint),
            err=True,
        )
        sys.exit(EXIT_NO_CONFIG)
    try:
        raw = _git_name_status(base, head, root)
    except _GitError as e:
        click.echo(
            format_error(
                subcommand="attest verify",
                message=f"git diff failed: {e}",
                hint="Ensure a full checkout (fetch-depth: 0) with a reachable merge-base.",
            ),
            err=True,
        )
        sys.exit(EXIT_EXTERNAL_TOOL)  # FAIL-CLOSED — never a vacuous pass

    verdict = verify_attestations(root, parse_name_status(raw))
    # HG-12 cut 1: disclose review independence for each validated (newly-ADDED,
    # scope-covering) attestation. Disclosure only — never changes pass/fail.
    independence = [
        {
            "slug": slug,
            **independence_for_attestation(
                root / ATTESTATIONS_DIRNAME / f"{slug}.jsonl"
            )["code_review"],
        }
        for slug in verdict.attestations
    ]
    data: dict[str, Any] = {
        "subjects": verdict.subjects,
        "covered": verdict.covered,
        "attestations": verdict.attestations,
        "blockers": verdict.blockers,
        "independence": independence,
    }
    if ctx.obj.get("json"):
        click.echo(
            json_envelope(
                command="attest verify",
                status="pass" if verdict.ok else "fail",
                exit_code=EXIT_OK if verdict.ok else EXIT_VALIDATION,
                data=data,
                errors=[{"code": "validation", "message": b} for b in verdict.blockers],
            )
        )
    else:
        # Human path only — disclosure lines must NEVER print before the `--json`
        # branch or they would corrupt the single-line JSON envelope.
        if not ctx.obj.get("quiet"):
            for item in independence:
                click.echo(_independence_line(item))
            for slug in verdict.attestations:
                gb = gate_bypass_for_attestation(
                    root / ATTESTATIONS_DIRNAME / f"{slug}.jsonl"
                )
                if gb["bypassed"]:
                    click.echo(
                        f"gate bypass: {gb['bypassed']} bypass(es), "
                        f"{gb['disclosed']} disclosure(s) — "
                        + "; ".join(r for r in gb["reasons"] if r)
                    )
        if verdict.ok:
            if not ctx.obj.get("quiet"):
                click.echo(f"attest verify: PASS ({len(verdict.subjects)} file(s) covered)")
        else:
            click.echo(
                format_error(
                    subcommand="attest verify",
                    message=f"{len(verdict.blockers)} blocker(s):\n  - "
                    + "\n  - ".join(verdict.blockers),
                    hint="Each changed file must be in a complete lifecycle attestation's scope.",
                ),
                err=True,
            )
    sys.exit(EXIT_OK if verdict.ok else EXIT_VALIDATION)
