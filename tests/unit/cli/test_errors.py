from super_harness.cli.errors import format_error


def test_error_format_has_hint_and_docs():
    msg = format_error(
        subcommand="verify",
        message="verification_failed (2 of 3 must_pass failed)",
        hint="see .harness/verification-results/foo/2026-05-27T10:00:00Z/summary.json",
        docs_anchor="verify",
    )
    assert "super-harness verify:" in msg
    assert "Hint:" in msg
    assert "Docs:" in msg
    assert "https://super-harness.dev/docs/verify" in msg


def test_error_format_message_only():
    msg = format_error(subcommand="init", message="already initialized")
    assert msg == "super-harness init: already initialized"


def test_error_format_with_hint_only():
    msg = format_error(subcommand="init", message="x", hint="run super-harness init --force")
    assert "Hint: run super-harness init --force" in msg
    assert "Docs:" not in msg


def test_error_format_with_docs_only():
    msg = format_error(subcommand="status", message="x", docs_anchor="status")
    assert "Docs: https://super-harness.dev/docs/status" in msg
    assert "Hint:" not in msg
