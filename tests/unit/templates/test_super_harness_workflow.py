from __future__ import annotations

from importlib.resources import files

import yaml


def _load_template() -> str:
    return (
        files("super_harness.templates")
        .joinpath("super_harness_workflow.yml")
        .read_text()
    )


def test_workflow_template_yaml_parses() -> None:
    """Template must be syntactically valid YAML (yaml.safe_load succeeds)."""
    text = _load_template()
    # PyYAML Norway-problem: `on:` key is parsed as Python True — that's fine,
    # GitHub Actions' own parser handles it correctly. We only assert no exception.
    yaml.safe_load(text)


def test_workflow_template_command_invocation_counts() -> None:
    """Each shipped command appears exactly the required number of times."""
    text = _load_template()
    assert text.count("super-harness pr emit-opened") == 1
    assert text.count("super-harness pr validate") == 1
    # trailing space is defensive future-proofing (won't match "verify-" prefixed tokens)
    assert text.count("super-harness verify ") == 1
    # appears twice: if/else slug-fallback in the on-merge job's Process step
    assert text.count("super-harness on-merge") == 2


def test_workflow_template_no_stale_tokens() -> None:
    """Template must not contain any stale/legacy tokens from older designs."""
    text = _load_template()
    assert "npx" not in text
    assert "npm" not in text
    assert "setup-node" not in text
    assert "gate check" not in text


def test_workflow_template_no_runline_raw_github_interpolation() -> None:
    """Script-injection regression guard.

    No `run:` line may contain raw `${{ github.` or `${{ steps.` — all GitHub
    context values used inside run blocks must be indirected through env: mappings.
    """
    text = _load_template()
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("run:"):
            assert "${{ github." not in stripped, (
                f"raw github. interpolation on run: line: {line!r}"
            )
            assert "${{ steps." not in stripped, (
                f"raw steps. interpolation on run: line: {line!r}"
            )
