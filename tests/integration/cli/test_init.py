from pathlib import Path

from click.testing import CliRunner

from super_harness.cli import main


def test_init_creates_harness_dir(tmp_path: Path):
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "init"])
    assert r.exit_code == 0
    assert (tmp_path / ".harness").is_dir()
    assert (tmp_path / ".harness" / "events.jsonl").exists()
    assert (tmp_path / ".harness" / "policy.yaml").exists()
    assert (tmp_path / ".harness" / "sensors.yaml").exists()
    assert (tmp_path / ".harness" / "verification.yaml").exists()
    assert (tmp_path / ".harness" / "source-paths.yaml").exists()


def test_init_idempotent_without_force(tmp_path: Path):
    runner = CliRunner()
    runner.invoke(main, ["--workspace", str(tmp_path), "init"])
    r2 = runner.invoke(main, ["--workspace", str(tmp_path), "init"])
    assert r2.exit_code == 3  # EXIT_NO_CONFIG-style for already-init
    # I-3: verify the --force hint reaches stderr (Click 8.4 exposes r.stderr
    # directly on the Result; CliRunner no longer takes mix_stderr).
    assert "Hint: pass --force to overwrite" in r2.stderr


def test_init_force_overwrites(tmp_path: Path):
    runner = CliRunner()
    runner.invoke(main, ["--workspace", str(tmp_path), "init"])
    (tmp_path / ".harness" / "policy.yaml").write_text("# user-edit\n")
    r2 = runner.invoke(main, ["--workspace", str(tmp_path), "init", "--force"])
    assert r2.exit_code == 0


def test_init_creates_all_subdirs(tmp_path: Path):
    CliRunner().invoke(main, ["--workspace", str(tmp_path), "init"])
    for d in (
        "anchors",
        "sensor-results",
        "verification-results",
        "operation-logs",
        "pending-l1-updates",
        "pending-reviews",
    ):
        assert (tmp_path / ".harness" / d).is_dir(), f"missing subdir: {d}"


def test_init_creates_gates_and_conventions(tmp_path: Path):
    CliRunner().invoke(main, ["--workspace", str(tmp_path), "init"])
    assert (tmp_path / ".harness" / "gates.yaml").exists()
    assert (tmp_path / ".harness" / "conventions.md").exists()


def test_init_refuses_when_partial_harness_exists(tmp_path: Path):
    (tmp_path / ".harness").mkdir()
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "init"])
    assert r.exit_code == 3
    assert not (tmp_path / ".harness" / "events.jsonl").exists()


def test_init_accepts_noop_flags_silently(tmp_path: Path):
    """v0.1: --setup-github / --framework are accepted but produce no runtime notice.

    Help text carries the placeholder caveat (Phase 4 / Phase 11 will wire these).
    Locks in the Phase 1 convention so a future regression that re-introduces
    a runtime stderr notice would be caught.
    """
    runner = CliRunner()
    r = runner.invoke(
        main,
        [
            "--workspace",
            str(tmp_path),
            "init",
            "--setup-github",
            "--framework",
            "openspec",
        ],
    )
    assert r.exit_code == 0
    assert "no-op" not in r.stderr.lower()
    assert "not yet implemented" not in r.stderr.lower()


def test_init_help_advertises_v01_caveat(tmp_path: Path):
    r = CliRunner().invoke(main, ["init", "--help"])
    assert r.exit_code == 0
    assert "v0.1" in r.output  # caveat is in --help for at least one no-op flag
