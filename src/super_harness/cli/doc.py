"""`doc` subgroup — regen-and-diff gate for derivable docs (design 2026-06-11)."""
from __future__ import annotations

import sys
from pathlib import Path

import click

from super_harness.cli.errors import format_error
from super_harness.cli.output import Status, json_envelope
from super_harness.core.doc_check import run_doc_check
from super_harness.core.paths import HarnessNotInitialized, find_harness_root
from super_harness.exit_codes import EXIT_NO_CONFIG


def _resolve(ctx: click.Context, sub: str) -> Path:
    try:
        return find_harness_root(Path(ctx.obj.get("workspace") or "."))
    except HarnessNotInitialized as e:
        click.echo(format_error(subcommand=sub, message=e.message, hint=e.hint), err=True)
        sys.exit(EXIT_NO_CONFIG)


@click.group("doc")
def doc_group() -> None:
    """Check that derivable docs match their generators."""


@doc_group.command("check")
@click.option("--fix", is_flag=True, help="Regenerate drifted docs in place.")
@click.pass_context
def check_cmd(ctx: click.Context, fix: bool) -> None:
    """Regen-and-diff every registered derived doc. Honors global --json."""
    root = _resolve(ctx, "doc check")

    result = run_doc_check(root, fix=fix)
    status: Status = "fail" if (result.drift or result.failed or result.errors) else "pass"

    if ctx.obj.get("json"):
        click.echo(json_envelope(
            command="doc check",
            status=status,
            exit_code=result.exit_code,
            data={
                "in_sync": [d.path for d in result.in_sync],
                "drift": [{"path": d.path, "diff": d.diff} for d in result.drift],
                "failed": [{"path": f.path, "command": f.command, "error": f.error}
                           for f in result.failed],
            },
            errors=[{"code": e.code, "message": e.message, "file": e.file}
                    for e in result.errors],
        ))
    else:
        for e in result.errors:
            click.echo(f"ERROR [{e.code}] {e.file}: {e.message}", err=True)
        for f in result.failed:
            click.echo(f"FAILED {f.path}: {f.error} ({f.command})", err=True)
        for d in result.drift:
            click.echo(f"DRIFT {d.path}", err=True)
            click.echo(d.diff, err=True)
        if status == "pass":
            click.echo("doc check: clean" if not fix else "doc check: fixed")
    sys.exit(result.exit_code)
