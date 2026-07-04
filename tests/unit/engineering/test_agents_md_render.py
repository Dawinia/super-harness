"""Unit tests for the shared AGENTS.md section renderer.

`render_super_harness_section` is the single source of truth that both
`super-harness init` and (later) `super-harness sync` use to re-render the §2.2
super-harness outer section + plain framework block + installed-adapter
subsections into a repo's root AGENTS.md.

These tests pin its observable behavior and regression-guard the
``_reinject_installed_adapters`` catch tuple (both `yaml.YAMLError` and
`ValueError` families).
"""

from __future__ import annotations

import io
from contextlib import redirect_stderr
from pathlib import Path

from super_harness.adapters.agent.claude_code import ClaudeCodeAdapter
from super_harness.adapters.framework.plain import PlainAdapter
from super_harness.engineering.agents_md_render import render_super_harness_section


def _write_adapters_yaml(root: Path, body: str) -> None:
    harness = root / ".harness"
    harness.mkdir(parents=True, exist_ok=True)
    (harness / "adapters.yaml").write_text(body)


def test_fresh_repo_renders_base_section(tmp_path: Path) -> None:
    """Fresh repo (no AGENTS.md, no adapters.yaml): the outer section is created
    with the version stamp, the plain framework block, and the no-agent anchor —
    and NO literal [*_SECTION_AUTO_INSERTED] placeholders remain (§3.2)."""
    agents = tmp_path / "AGENTS.md"

    render_super_harness_section(tmp_path, agents, "0.1.0")

    assert agents.exists()
    text = agents.read_text()
    assert (
        "<!-- super-harness section begin · v0.1.0 · DO NOT EDIT MANUALLY -->" in text
    )
    assert "<!-- super-harness section end -->" in text
    assert PlainAdapter().agents_md_subsection().rstrip("\n") in text
    assert "<!-- super-harness no-agent-adapter-installed -->" in text
    assert "[FRAMEWORK_SECTION_AUTO_INSERTED]" not in text
    assert "[AGENT_SECTION_AUTO_INSERTED]" not in text


def test_passed_version_appears_in_begin_marker(tmp_path: Path) -> None:
    """The `version` argument is stamped into the begin marker verbatim."""
    agents = tmp_path / "AGENTS.md"

    render_super_harness_section(tmp_path, agents, "9.9.9")

    text = agents.read_text()
    assert "<!-- super-harness section begin · v9.9.9 · DO NOT EDIT MANUALLY -->" in text


def test_installed_agent_subsection_is_injected(tmp_path: Path) -> None:
    """An installed builtin agent (claude-code) listed in adapters.yaml gets its
    subsection re-injected, consuming the no-agent anchor."""
    _write_adapters_yaml(
        tmp_path,
        "adapters:\n"
        "  - {name: claude-code, type: agent, builtin: true, version: 0.1.0, enabled: true}\n",
    )
    agents = tmp_path / "AGENTS.md"

    render_super_harness_section(tmp_path, agents, "0.1.0")

    text = agents.read_text()
    assert ClaudeCodeAdapter().agents_md_subsection().rstrip("\n") in text
    assert "<!-- super-harness agent: claude-code -->" in text
    # The first agent install consumes the no-agent anchor.
    assert "<!-- super-harness no-agent-adapter-installed -->" not in text


def test_rerender_is_idempotent(tmp_path: Path) -> None:
    """Calling render twice does not duplicate the outer section or the adapter
    block (the inject_* primitives replace in place by name)."""
    _write_adapters_yaml(
        tmp_path,
        "adapters:\n"
        "  - {name: claude-code, type: agent, builtin: true, version: 0.1.0, enabled: true}\n",
    )
    agents = tmp_path / "AGENTS.md"

    render_super_harness_section(tmp_path, agents, "0.1.0")
    render_super_harness_section(tmp_path, agents, "0.1.0")

    text = agents.read_text()
    assert text.count("<!-- super-harness section begin ") == 1
    assert text.count("<!-- super-harness section end -->") == 1
    assert text.count("<!-- super-harness framework: plain -->") == 1
    assert text.count("<!-- super-harness agent: claude-code -->") == 1


def test_corrupt_adapters_yaml_wrong_shape_is_nonfatal(tmp_path: Path) -> None:
    """A wrong-shape (valid YAML) adapters.yaml raises ValueError inside
    load_adapters; render swallows it (advisory on stderr) and still writes a
    valid base section. Regression-guards the ValueError arm of the catch tuple."""
    _write_adapters_yaml(tmp_path, "adapters: not-a-list\n")
    agents = tmp_path / "AGENTS.md"

    buf = io.StringIO()
    with redirect_stderr(buf):
        render_super_harness_section(tmp_path, agents, "0.1.0")

    assert "couldn't re-inject installed adapters" in buf.getvalue()
    text = agents.read_text()
    assert "<!-- super-harness section begin " in text
    assert text.count("<!-- super-harness framework: plain -->") == 1
    assert "<!-- super-harness no-agent-adapter-installed -->" in text


def test_outer_section_has_decision_conformance(tmp_path: Path) -> None:
    """The outer framework section teaches the portable §4.1 local sensor: run
    `decision check` at checkpoints, self-test a check with `ratify --dry-run`,
    keep `decision check` / `doc check` green locally. Lives in the OUTER section
    (not a CC-specific adapter block) because these are plain CLI commands any
    agent runs."""
    agents = tmp_path / "AGENTS.md"

    render_super_harness_section(tmp_path, agents, "0.1.0")

    text = agents.read_text()
    assert "### Decision conformance" in text
    assert "super-harness decision check --changed" in text
    assert "super-harness decision ratify <id> --dry-run" in text
    assert "super-harness doc check" in text
    # The section sits inside the managed outer block (before the end marker).
    assert text.index("### Decision conformance") < text.index(
        "<!-- super-harness section end -->"
    )


def test_decision_conformance_has_arming_recipe(tmp_path: Path) -> None:
    """The arming recipe (how to craft a check) renders inside the managed section."""
    agents = tmp_path / "AGENTS.md"
    render_super_harness_section(tmp_path, agents, "0.1.0")
    text = agents.read_text(encoding="utf-8")
    assert "Arming a decision" in text
    assert "brittle one-token signature" in text
    assert "context-only (tier-3)" in text  # the do-NOT-arm rung
    assert "```check" in text
    assert "```counterexample" in text
    # The recipe sits inside the managed outer block (before the end marker).
    assert text.index("Arming a decision") < text.index(
        "<!-- super-harness section end -->"
    )


def test_section_points_to_norm_discovery_skill(tmp_path: Path) -> None:
    """The managed section points adopters' agents at the norm-discovery skill."""
    agents = tmp_path / "AGENTS.md"
    render_super_harness_section(tmp_path, agents, "0.1.0")
    text = agents.read_text(encoding="utf-8")
    assert "discovering-architecture-norms" in text


def test_corrupt_adapters_yaml_broken_syntax_is_nonfatal(tmp_path: Path) -> None:
    """A syntactically-broken adapters.yaml raises yaml.YAMLError inside
    load_adapters; render swallows it (advisory on stderr) and still writes a
    valid base section. Regression-guards the yaml.YAMLError arm of the catch
    tuple (it does NOT derive from ValueError, so must be listed explicitly)."""
    _write_adapters_yaml(tmp_path, "{ this is: not: valid: yaml\n")
    agents = tmp_path / "AGENTS.md"

    buf = io.StringIO()
    with redirect_stderr(buf):
        render_super_harness_section(tmp_path, agents, "0.1.0")

    assert "couldn't re-inject installed adapters" in buf.getvalue()
    text = agents.read_text()
    assert "<!-- super-harness section begin " in text
    assert text.count("<!-- super-harness framework: plain -->") == 1
    assert "<!-- super-harness no-agent-adapter-installed -->" in text
