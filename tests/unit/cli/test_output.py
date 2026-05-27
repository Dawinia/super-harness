import json

from super_harness.cli.output import json_envelope


def test_envelope_shape():
    out = json_envelope(command="verify", status="pass", exit_code=0, data={"checks_run": 3})
    parsed = json.loads(out)
    assert parsed["command"] == "verify"
    assert parsed["status"] == "pass"
    assert parsed["exit_code"] == 0
    assert parsed["data"] == {"checks_run": 3}
    assert parsed["version"]  # version field present
    assert parsed["errors"] == []


def test_envelope_with_errors():
    errs = [{"code": 2, "message": "missing field"}]
    out = json_envelope(command="state verify", status="fail", exit_code=2, errors=errs)
    parsed = json.loads(out)
    assert parsed["status"] == "fail"
    assert parsed["errors"] == errs
    assert parsed["data"] == {}  # default empty dict


def test_envelope_omits_no_keys():
    """All 6 top-level keys are always present (deterministic schema for CI parsers)."""
    out = json_envelope(command="x", status="pass", exit_code=0)
    parsed = json.loads(out)
    assert set(parsed.keys()) == {"command", "version", "status", "exit_code", "data", "errors"}


def test_envelope_single_line():
    """Output must be a single line (newline-delimited JSON-friendly)."""
    out = json_envelope(command="x", status="pass", exit_code=0, data={"a": 1, "b": "two"})
    assert "\n" not in out
    assert out == json.dumps(json.loads(out), separators=(",", ":"), sort_keys=False)
