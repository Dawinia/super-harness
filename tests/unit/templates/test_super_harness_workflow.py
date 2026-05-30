from __future__ import annotations

from importlib.resources import files

import pytest
import yaml

_RAW_PATTERNS = ("${{ github.", "${{ steps.")


def _load_template() -> str:
    return (
        files("super_harness.templates")
        .joinpath("super_harness_workflow.yml")
        .read_text()
    )


def _assert_no_runblock_raw_interpolation(yaml_text: str) -> None:
    """Walk yaml_text line-by-line and assert that no shell command content
    contains raw GitHub-context interpolation (${{ github. or ${{ steps.).

    Covers both single-line `run: <cmd>` and multi-line block scalars
    (`run: |` / `run: >`), including every continuation line of the block.
    A continuation block ends when a non-blank line appears whose indentation
    is <= the indentation of the `run:` line that opened it.

    Also handles the GitHub-Actions list-marker shorthand form where the step
    key appears inline with the list item marker (`- run: |`), not on a
    separate line below `- name:`.

    Threat-model scope: ``github.*`` and ``steps.*`` are the only contexts
    whose values can be controlled by PR-author content (branch names, PR
    titles, step outputs derived from those). ``env.`` / ``vars.`` /
    ``secrets.`` are repo-owner controlled; ``matrix.`` / ``runner.`` are
    static. So this guard's scope is intentionally narrow.
    """
    lines = yaml_text.splitlines()
    in_run_block = False
    run_indent = 0

    for line in lines:
        stripped = line.strip()

        if in_run_block:
            # A blank line does not end a block scalar — skip but stay in block.
            if stripped == "":
                continue
            current_indent = len(line) - len(line.lstrip())
            if current_indent <= run_indent:
                # This line is at or above the run: key level — block ended.
                in_run_block = False
                # Fall through: this line may itself be a new `run:` key.
            else:
                # Still inside the run block — this is shell command content.
                for pat in _RAW_PATTERNS:
                    assert pat not in line, (
                        f"raw interpolation {pat!r} on run-block continuation line: {line!r}"
                    )
                continue

        # Not in a run block: check for a `run:` key on this line.
        # Handle GitHub-Actions list-marker shorthand: `- run: ...`.
        if stripped.startswith("- run:"):
            stripped = stripped[2:]  # strip "- " leaving "run: ..."
        if not stripped.startswith("run:"):
            continue

        run_indent = len(line) - len(line.lstrip())
        after_run = stripped[len("run:"):].strip()

        if after_run in ("|", ">", "|-", ">-", "|+", ">+"):
            # Block scalar — continuation lines carry the shell content.
            in_run_block = True
        else:
            # Single-line run: check only this line for raw interpolation.
            for pat in _RAW_PATTERNS:
                assert pat not in stripped, (
                    f"raw interpolation {pat!r} on run: line: {line!r}"
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

    No run block (single-line or multi-line `run: |` / `run: >`) may contain
    raw `${{ github.` or `${{ steps.` — all GitHub context values used inside
    run blocks must be indirected through env: mappings.

    This test walks every continuation line of block-scalar run steps, not
    just the `run:` key line itself, so a future contributor cannot sneak
    `echo "${{ github.head_ref }}"` into a multi-line shell block undetected.
    """
    _assert_no_runblock_raw_interpolation(_load_template())


def test_workflow_template_continuation_lines_also_guarded() -> None:
    """Regression test: the guard catches injection on block-scalar continuation lines.

    Constructs a minimal synthetic yaml that contains the exact vulnerable
    pattern — a ``run: |`` block with ``${{ github.head_ref }}`` on a
    continuation line — and asserts the helper flags it.  This ensures the
    multi-line tracking logic is non-vacuous: removing it would let this test
    silently pass while the real template falsely appears safe.
    """
    # Case 1: full-form step — `- name:` on its own line, `run: |` nested below.
    vulnerable_yaml_fullform = """\
jobs:
  example:
    steps:
      - name: Dangerous step
        run: |
          echo "branch=${{ github.head_ref }}"
"""
    with pytest.raises(AssertionError, match=r"raw interpolation.*continuation line"):
        _assert_no_runblock_raw_interpolation(vulnerable_yaml_fullform)

    # Case 2: list-marker shorthand — `- run: |` inline with the list marker.
    # This is the idiomatic form in every official GitHub Actions doc example;
    # the guard must handle it so a future contributor cannot bypass the check
    # by using shorthand syntax.
    vulnerable_yaml_shorthand = """\
jobs:
  example:
    steps:
      - run: |
          echo "branch=${{ github.head_ref }}"
"""
    with pytest.raises(AssertionError, match=r"raw interpolation.*continuation line"):
        _assert_no_runblock_raw_interpolation(vulnerable_yaml_shorthand)


def test_workflow_template_guard_does_not_false_fire_on_env_indirection() -> None:
    """Positive control: env-mapped ${{ github.* }} must NOT trip the guard.

    The correct hardened pattern maps GitHub context values through env: so
    that shell scripts reference env-var names only (never raw expressions).
    Without this test, an accidental over-broadening of the helper (e.g.
    matching `${{` anywhere instead of only inside run blocks) would silently
    pass all other tests while breaking the legitimate env-indirection pattern.
    """
    safe_yaml = """\
jobs:
  ok:
    steps:
      - name: safe
        env:
          REF: ${{ github.head_ref }}
        run: |
          echo "$REF"
"""
    _assert_no_runblock_raw_interpolation(safe_yaml)  # must NOT raise
