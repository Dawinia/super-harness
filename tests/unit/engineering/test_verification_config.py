"""Tests for the `.harness/verification.yaml` schema loader (Phase 8 task 1).

Covers: happy-path parse of the shipped canonical template, default
inheritance onto checks, missing-required fields, bad enum values, duplicate
check ids, wrong-shape top-level (ValueError family), syntax-corrupt yaml
(yaml.YAMLError family — distinct from ValueError), and a missing file
(FileNotFoundError → caller maps to EXIT_NO_CONFIG).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from super_harness.engineering.verification_config import (
    INTERPOLATION_ALLOWLIST,
    CheckSpec,
    Defaults,
    Execution,
    InterpolationError,
    Layers,
    VerificationConfig,
    VerificationConfigError,
    interpolate,
    load_verification_config,
)

# The canonical empty shape shipped by `init`. The loader MUST parse this.
_SHIPPED_TEMPLATE = (
    Path(__file__).parents[3]
    / "src"
    / "super_harness"
    / "templates"
    / "verification_defaults.yaml"
)


def _write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "verification.yaml"
    p.write_text(text)
    return p


# --------------------------------------------------------------------------- #
# Happy path
# --------------------------------------------------------------------------- #


def test_parses_shipped_template() -> None:
    """The canonical empty template parses into a sensible empty config."""
    cfg = load_verification_config(_SHIPPED_TEMPLATE)

    assert isinstance(cfg, VerificationConfig)
    assert isinstance(cfg.layers, Layers)
    assert isinstance(cfg.defaults, Defaults)
    assert isinstance(cfg.execution, Execution)

    assert cfg.layers.baseline is True
    assert cfg.layers.framework_adapter is True
    assert cfg.layers.user_checks is True

    assert cfg.defaults.timeout_seconds == 300
    assert cfg.defaults.must_pass is True
    assert cfg.defaults.capture == "both"
    assert cfg.defaults.workdir == "."
    assert cfg.defaults.env == {"CI": "1"}

    assert cfg.execution.mode == "parallel"
    assert cfg.execution.max_parallelism == 4
    assert cfg.execution.fail_fast is False

    assert cfg.checks == []
    assert cfg.adapter_provided == []


def test_parses_full_config_with_checks(tmp_path: Path) -> None:
    cfg = load_verification_config(
        _write(
            tmp_path,
            yaml.safe_dump(
                {
                    "layers": {
                        "baseline": {"enabled": False},
                        "framework_adapter": {"enabled": True},
                        "user_checks": {"enabled": True},
                    },
                    "defaults": {
                        "timeout_seconds": 300,
                        "must_pass": True,
                        "capture": "both",
                        "workdir": ".",
                        "env": {"CI": "1"},
                    },
                    "execution": {
                        "mode": "sequential",
                        "max_parallelism": 2,
                        "fail_fast": True,
                    },
                    "checks": [
                        {
                            "id": "tests",
                            "command": "npm test",
                            "must_pass": False,
                            "timeout_seconds": 600,
                            "capture": "stdout",
                            "workdir": "frontend",
                            "env": {"NODE_ENV": "test"},
                        },
                        {"id": "lint", "command": "npm run lint"},
                    ],
                    "adapter_provided": [
                        {
                            "id": "openspec-validate",
                            "command": "openspec validate --strict",
                            "provided_by": "openspec-adapter",
                        }
                    ],
                }
            ),
        )
    )

    assert cfg.layers.baseline is False
    assert cfg.execution.mode == "sequential"
    assert cfg.execution.fail_fast is True

    # Explicit per-check values are preserved.
    tests = cfg.checks[0]
    assert isinstance(tests, CheckSpec)
    assert tests.id == "tests"
    assert tests.command == "npm test"
    assert tests.must_pass is False
    assert tests.timeout_seconds == 600
    assert tests.capture == "stdout"
    assert tests.workdir == "frontend"
    assert tests.env == {"NODE_ENV": "test"}
    assert tests.provided_by is None

    # Omitted scalars inherit from defaults at load time.
    lint = cfg.checks[1]
    assert lint.must_pass is True  # defaults.must_pass
    assert lint.timeout_seconds == 300  # defaults.timeout_seconds
    assert lint.capture == "both"  # defaults.capture
    assert lint.workdir == "."  # defaults.workdir
    assert lint.env == {}  # per-check env not merged with defaults.env

    # adapter_provided carries provided_by and inherits scalar defaults.
    ap = cfg.adapter_provided[0]
    assert ap.provided_by == "openspec-adapter"
    assert ap.must_pass is True
    assert ap.timeout_seconds == 300


def test_check_env_not_merged_with_defaults_env(tmp_path: Path) -> None:
    """defaults.env and check.env stay separate (merge happens at exec time)."""
    cfg = load_verification_config(
        _write(
            tmp_path,
            yaml.safe_dump(
                {
                    "defaults": {"env": {"CI": "1"}},
                    "checks": [{"id": "t", "command": "x"}],
                }
            ),
        )
    )
    assert cfg.defaults.env == {"CI": "1"}
    assert cfg.checks[0].env == {}


def test_minimal_config_applies_all_defaults(tmp_path: Path) -> None:
    """A bare config with no layers/defaults/execution blocks gets sane defaults."""
    cfg = load_verification_config(_write(tmp_path, "checks: []\n"))
    assert cfg.layers.baseline is True
    assert cfg.defaults.timeout_seconds == 300
    assert cfg.defaults.capture == "both"
    assert cfg.execution.mode == "parallel"
    assert cfg.checks == []
    assert cfg.adapter_provided == []


def test_empty_file_applies_all_defaults(tmp_path: Path) -> None:
    cfg = load_verification_config(_write(tmp_path, ""))
    assert cfg.defaults.timeout_seconds == 300
    assert cfg.execution.mode == "parallel"


def test_checkspec_is_frozen() -> None:
    spec = CheckSpec(
        id="t",
        command="x",
        must_pass=True,
        timeout_seconds=300,
        capture="both",
        workdir=".",
        env={},
        provided_by=None,
    )
    with pytest.raises((AttributeError, TypeError)):
        spec.id = "other"  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# Missing-required fields
# --------------------------------------------------------------------------- #


def test_missing_check_id_raises(tmp_path: Path) -> None:
    p = _write(tmp_path, yaml.safe_dump({"checks": [{"command": "npm test"}]}))
    with pytest.raises(VerificationConfigError):
        load_verification_config(p)


def test_empty_check_id_raises(tmp_path: Path) -> None:
    p = _write(tmp_path, yaml.safe_dump({"checks": [{"id": "", "command": "x"}]}))
    with pytest.raises(VerificationConfigError):
        load_verification_config(p)


def test_missing_check_command_raises(tmp_path: Path) -> None:
    p = _write(tmp_path, yaml.safe_dump({"checks": [{"id": "tests"}]}))
    with pytest.raises(VerificationConfigError):
        load_verification_config(p)


def test_empty_check_command_raises(tmp_path: Path) -> None:
    p = _write(tmp_path, yaml.safe_dump({"checks": [{"id": "tests", "command": ""}]}))
    with pytest.raises(VerificationConfigError):
        load_verification_config(p)


# --------------------------------------------------------------------------- #
# Bad enum values
# --------------------------------------------------------------------------- #


def test_bad_capture_enum_raises(tmp_path: Path) -> None:
    p = _write(tmp_path, yaml.safe_dump({"defaults": {"capture": "everything"}}))
    with pytest.raises(VerificationConfigError):
        load_verification_config(p)


def test_bad_check_capture_enum_raises(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        yaml.safe_dump({"checks": [{"id": "t", "command": "x", "capture": "nope"}]}),
    )
    with pytest.raises(VerificationConfigError):
        load_verification_config(p)


def test_bad_execution_mode_enum_raises(tmp_path: Path) -> None:
    p = _write(tmp_path, yaml.safe_dump({"execution": {"mode": "turbo"}}))
    with pytest.raises(VerificationConfigError):
        load_verification_config(p)


def test_non_bool_layer_enabled_raises(tmp_path: Path) -> None:
    p = _write(tmp_path, yaml.safe_dump({"layers": {"baseline": {"enabled": "yes"}}}))
    with pytest.raises(VerificationConfigError):
        load_verification_config(p)


def test_non_int_timeout_raises(tmp_path: Path) -> None:
    p = _write(tmp_path, yaml.safe_dump({"defaults": {"timeout_seconds": "300"}}))
    with pytest.raises(VerificationConfigError):
        load_verification_config(p)


def test_non_int_max_parallelism_raises(tmp_path: Path) -> None:
    p = _write(tmp_path, yaml.safe_dump({"execution": {"max_parallelism": "four"}}))
    with pytest.raises(VerificationConfigError):
        load_verification_config(p)


def test_non_bool_must_pass_raises(tmp_path: Path) -> None:
    p = _write(tmp_path, yaml.safe_dump({"defaults": {"must_pass": "true"}}))
    with pytest.raises(VerificationConfigError):
        load_verification_config(p)


def test_bool_is_not_accepted_as_int_timeout(tmp_path: Path) -> None:
    """`True` must not slip through as a timeout int (bool subclasses int)."""
    p = _write(tmp_path, yaml.safe_dump({"defaults": {"timeout_seconds": True}}))
    with pytest.raises(VerificationConfigError):
        load_verification_config(p)


# --------------------------------------------------------------------------- #
# Duplicate ids
# --------------------------------------------------------------------------- #


def test_duplicate_check_ids_rejected(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        yaml.safe_dump(
            {
                "checks": [
                    {"id": "tests", "command": "a"},
                    {"id": "tests", "command": "b"},
                ]
            }
        ),
    )
    with pytest.raises(VerificationConfigError):
        load_verification_config(p)


def test_duplicate_adapter_provided_ids_rejected(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        yaml.safe_dump(
            {
                "adapter_provided": [
                    {"id": "x", "command": "a", "provided_by": "p"},
                    {"id": "x", "command": "b", "provided_by": "p"},
                ]
            }
        ),
    )
    with pytest.raises(VerificationConfigError):
        load_verification_config(p)


def test_same_id_across_layers_allowed(tmp_path: Path) -> None:
    """Uniqueness is per-layer: a user check and an adapter check may share an id."""
    cfg = load_verification_config(
        _write(
            tmp_path,
            yaml.safe_dump(
                {
                    "checks": [{"id": "shared", "command": "a"}],
                    "adapter_provided": [
                        {"id": "shared", "command": "b", "provided_by": "p"}
                    ],
                }
            ),
        )
    )
    assert cfg.checks[0].id == "shared"
    assert cfg.adapter_provided[0].id == "shared"


# --------------------------------------------------------------------------- #
# Wrong-shape top-level → ValueError family
# --------------------------------------------------------------------------- #


def test_top_level_not_mapping_raises_value_error(tmp_path: Path) -> None:
    p = _write(tmp_path, yaml.safe_dump([1, 2, 3]))
    with pytest.raises(VerificationConfigError):
        load_verification_config(p)
    # And it IS a ValueError so the CLI catch-tuple maps it to EXIT_VALIDATION.
    with pytest.raises(ValueError):
        load_verification_config(p)


def test_checks_not_a_list_raises(tmp_path: Path) -> None:
    p = _write(tmp_path, yaml.safe_dump({"checks": {"id": "t"}}))
    with pytest.raises(VerificationConfigError):
        load_verification_config(p)


def test_check_entry_not_a_dict_raises(tmp_path: Path) -> None:
    p = _write(tmp_path, yaml.safe_dump({"checks": ["just-a-string"]}))
    with pytest.raises(VerificationConfigError):
        load_verification_config(p)


def test_layers_not_a_dict_raises(tmp_path: Path) -> None:
    p = _write(tmp_path, yaml.safe_dump({"layers": ["baseline"]}))
    with pytest.raises(VerificationConfigError):
        load_verification_config(p)


def test_defaults_env_not_a_dict_raises(tmp_path: Path) -> None:
    p = _write(tmp_path, yaml.safe_dump({"defaults": {"env": ["CI=1"]}}))
    with pytest.raises(VerificationConfigError):
        load_verification_config(p)


def test_verification_config_error_is_value_error_subclass() -> None:
    assert issubclass(VerificationConfigError, ValueError)


# --------------------------------------------------------------------------- #
# Syntax-corrupt yaml → yaml.YAMLError family (NOT ValueError)
# --------------------------------------------------------------------------- #


def test_corrupt_yaml_raises_yamlerror(tmp_path: Path) -> None:
    # Unbalanced bracket / bad indentation → a parser/scanner error.
    p = _write(tmp_path, "layers: {baseline: {enabled: true}\nchecks: [\n")
    with pytest.raises(yaml.YAMLError):
        load_verification_config(p)


def test_corrupt_yaml_is_not_value_error(tmp_path: Path) -> None:
    """YAMLError subclasses Exception, not ValueError — the two families are distinct."""
    p = _write(tmp_path, "key: : : bad\n")
    with pytest.raises(yaml.YAMLError):
        load_verification_config(p)
    # It must NOT be catchable as our config-shape ValueError.
    with pytest.raises(Exception) as exc_info:
        load_verification_config(p)
    assert not isinstance(exc_info.value, ValueError)


# --------------------------------------------------------------------------- #
# Missing file → FileNotFoundError (caller maps to EXIT_NO_CONFIG)
# --------------------------------------------------------------------------- #


def test_missing_file_raises_filenotfound(tmp_path: Path) -> None:
    p = tmp_path / "does-not-exist.yaml"
    with pytest.raises(FileNotFoundError):
        load_verification_config(p)


def test_missing_file_is_not_value_error(tmp_path: Path) -> None:
    """Missing maps to EXIT_NO_CONFIG, distinct from the EXIT_VALIDATION ValueError."""
    p = tmp_path / "missing.yaml"
    with pytest.raises(FileNotFoundError) as exc_info:
        load_verification_config(p)
    assert not isinstance(exc_info.value, VerificationConfigError)


# --------------------------------------------------------------------------- #
# Variable interpolation (allowlist) — Phase 8 task 2
# --------------------------------------------------------------------------- #


def test_interpolate_allowlist_is_the_four_v01_names() -> None:
    """The v0.1 allowlist is exactly SLUG / CHANGE_ID / SPEC_PATH / PLAN_PATH."""
    assert INTERPOLATION_ALLOWLIST == frozenset(
        {"SLUG", "CHANGE_ID", "SPEC_PATH", "PLAN_PATH"}
    )


def test_interpolate_substitutes_all_four_allowlisted_names() -> None:
    variables = {
        "SLUG": "add-login",
        "CHANGE_ID": "add-login",
        "SPEC_PATH": "specs/add-login.md",
        "PLAN_PATH": "plans/add-login.md",
    }
    command = (
        "validate --change ${SLUG} --id ${CHANGE_ID} "
        "--spec ${SPEC_PATH} --plan ${PLAN_PATH}"
    )
    assert interpolate(command, variables) == (
        "validate --change add-login --id add-login "
        "--spec specs/add-login.md --plan plans/add-login.md"
    )


def test_interpolate_replaces_all_occurrences_of_a_name() -> None:
    out = interpolate("${SLUG}-${SLUG}-${SLUG}", {"SLUG": "x"})
    assert out == "x-x-x"


def test_interpolate_unknown_placeholder_raises_interpolation_error() -> None:
    with pytest.raises(InterpolationError) as exc_info:
        interpolate("deploy ${PR_URL}", {"SLUG": "x"})
    # The message should name the offending placeholder.
    assert "PR_URL" in str(exc_info.value)


def test_interpolate_unknown_placeholder_is_value_error_subclass() -> None:
    """InterpolationError must map to EXIT_VALIDATION via the ValueError catch."""
    with pytest.raises(ValueError):
        interpolate("deploy ${COMMIT_SHA}", {})
    with pytest.raises(VerificationConfigError):
        interpolate("deploy ${COMMIT_SHA}", {})


def test_interpolation_error_is_value_error_subclass() -> None:
    assert issubclass(InterpolationError, ValueError)
    assert issubclass(InterpolationError, VerificationConfigError)


def test_interpolate_allowlisted_but_missing_value_becomes_empty_string() -> None:
    """SPEC_PATH/PLAN_PATH are always empty in v0.1 — allowlisted, no raise."""
    assert interpolate("openspec validate ${SPEC_PATH}", {}) == "openspec validate "


def test_interpolate_allowlisted_explicit_empty_value_becomes_empty_string() -> None:
    out = interpolate("a ${PLAN_PATH} b", {"PLAN_PATH": ""})
    assert out == "a  b"


def test_interpolate_leaves_bare_dollar_and_unbraced_names_untouched() -> None:
    # Only `${...}` is a placeholder; `$FOO` and a lone `$` are literal text.
    assert interpolate("echo $FOO and $ and ${SLUG}", {"SLUG": "x"}) == (
        "echo $FOO and $ and x"
    )


def test_interpolate_no_placeholders_returns_command_verbatim() -> None:
    assert interpolate("npm test --watch=false", {"SLUG": "x"}) == (
        "npm test --watch=false"
    )


def test_interpolate_empty_braces_left_untouched() -> None:
    # `${}` has no valid NAME (regex requires an identifier) so it is literal.
    assert interpolate("a ${} b", {}) == "a ${} b"


# --------------------------------------------------------------------------- #
# Load-time placeholder validation (FIX 1b) — a non-allowlisted ${NAME} in a
# check command is rejected at LOAD time, not only at run time.
# --------------------------------------------------------------------------- #


def test_load_rejects_non_allowlisted_placeholder_in_user_check(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        yaml.safe_dump({"checks": [{"id": "deploy", "command": "deploy ${PR_URL}"}]}),
    )
    with pytest.raises(InterpolationError) as exc_info:
        load_verification_config(p)
    # Names the offending placeholder AND the owning check id.
    assert "PR_URL" in str(exc_info.value)
    assert "deploy" in str(exc_info.value)


def test_load_rejects_non_allowlisted_placeholder_in_adapter_check(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        yaml.safe_dump(
            {
                "adapter_provided": [
                    {
                        "id": "ci",
                        "command": "run ${COMMIT_SHA}",
                        "provided_by": "some-adapter",
                    }
                ]
            }
        ),
    )
    with pytest.raises(InterpolationError) as exc_info:
        load_verification_config(p)
    assert "COMMIT_SHA" in str(exc_info.value)


def test_load_bad_placeholder_is_value_error_subclass(tmp_path: Path) -> None:
    """The load-time rejection maps to EXIT_VALIDATION via the ValueError catch."""
    p = _write(
        tmp_path,
        yaml.safe_dump({"checks": [{"id": "t", "command": "x ${NOPE}"}]}),
    )
    with pytest.raises(ValueError):
        load_verification_config(p)


def test_load_accepts_allowlisted_placeholder_in_check(tmp_path: Path) -> None:
    """Allowlisted ${SLUG} loads fine — only NON-allowlisted names are rejected."""
    cfg = load_verification_config(
        _write(
            tmp_path,
            yaml.safe_dump(
                {"checks": [{"id": "t", "command": "validate --change ${SLUG}"}]}
            ),
        )
    )
    assert cfg.checks[0].command == "validate --change ${SLUG}"


# --------------------------------------------------------------------------- #
# Carryover nits from Task 8.1 code review
# --------------------------------------------------------------------------- #


def test_provided_by_rejected_on_user_checks(tmp_path: Path) -> None:
    """nit 2: `provided_by` is adapter-injected only; reject it on user checks."""
    p = _write(
        tmp_path,
        yaml.safe_dump(
            {"checks": [{"id": "t", "command": "x", "provided_by": "some-adapter"}]}
        ),
    )
    with pytest.raises(VerificationConfigError):
        load_verification_config(p)


def test_provided_by_accepted_on_adapter_provided(tmp_path: Path) -> None:
    """Sanity: `provided_by` is still fine in the adapter_provided layer."""
    cfg = load_verification_config(
        _write(
            tmp_path,
            yaml.safe_dump(
                {
                    "adapter_provided": [
                        {"id": "v", "command": "x", "provided_by": "an-adapter"}
                    ]
                }
            ),
        )
    )
    assert cfg.adapter_provided[0].provided_by == "an-adapter"


def test_non_string_env_value_raises(tmp_path: Path) -> None:
    """nit 3: a non-string env VALUE is rejected (not just non-dict env)."""
    p = _write(tmp_path, yaml.safe_dump({"defaults": {"env": {"CI": 1}}}))
    with pytest.raises(VerificationConfigError):
        load_verification_config(p)


def test_non_string_env_key_raises(tmp_path: Path) -> None:
    """nit 3: a non-string env KEY is rejected too."""
    p = _write(tmp_path, yaml.safe_dump({"defaults": {"env": {1: "x"}}}))
    with pytest.raises(VerificationConfigError):
        load_verification_config(p)
