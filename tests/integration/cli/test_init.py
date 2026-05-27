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


def test_init_force_overwrites(tmp_path: Path):
    runner = CliRunner()
    runner.invoke(main, ["--workspace", str(tmp_path), "init"])
    (tmp_path / ".harness" / "policy.yaml").write_text("# user-edit\n")
    r2 = runner.invoke(main, ["--workspace", str(tmp_path), "init", "--force"])
    assert r2.exit_code == 0
