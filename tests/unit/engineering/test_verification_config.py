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
    CheckSpec,
    Defaults,
    Execution,
    Layers,
    VerificationConfig,
    VerificationConfigError,
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
