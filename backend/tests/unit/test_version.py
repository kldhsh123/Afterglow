"""Release version declarations and runtime version resolution."""

import runpy
import subprocess
from pathlib import Path

import pytest

from xuwen import __version__

ROOT = Path(__file__).resolve().parents[3]
VERSION_SCRIPT = runpy.run_path(str(ROOT / "scripts" / "set_version.py"))


def test_runtime_version_matches_repository_version() -> None:
    assert __version__ == (ROOT / "VERSION").read_text(encoding="utf-8").strip()


def test_release_version_declarations_are_synchronized() -> None:
    assert VERSION_SCRIPT["check_versions"]() is True


@pytest.mark.parametrize(
    ("part", "expected"),
    [("major", "2.0.0"), ("minor", "1.3.0"), ("patch", "1.2.4")],
)
def test_version_bump(part: str, expected: str) -> None:
    assert VERSION_SCRIPT["_bump_version"]("1.2.3", part) == expected


def test_version_bump_rejects_prerelease() -> None:
    with pytest.raises(ValueError, match="stable"):
        VERSION_SCRIPT["_bump_version"]("1.2.3rc1", "patch")


def test_set_version_rolls_back_all_files_on_late_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script_globals = VERSION_SCRIPT["set_version"].__globals__
    targets = {
        "VERSION_FILE": tmp_path / "VERSION",
        "DOCKER_ENV_FILE": tmp_path / ".env.docker.example",
        "PYPROJECT_FILE": tmp_path / "pyproject.toml",
        "UV_LOCK_FILE": tmp_path / "uv.lock",
    }
    originals: dict[Path, bytes] = {}
    for index, (name, path) in enumerate(targets.items()):
        content = f"original-{index}".encode()
        path.write_bytes(content)
        originals[path] = content
        monkeypatch.setitem(script_globals, name, path)

    def fake_uv_version(*args: object, **kwargs: object) -> subprocess.CompletedProcess:
        targets["PYPROJECT_FILE"].write_bytes(b"partially-updated-project")
        targets["UV_LOCK_FILE"].write_bytes(b"partially-updated-lock")
        return subprocess.CompletedProcess(args=[], returncode=0)

    def fail_env_write(version: str) -> None:
        targets["DOCKER_ENV_FILE"].write_bytes(b"partially-updated-env")
        raise OSError("simulated env write failure")

    monkeypatch.setattr(subprocess, "run", fake_uv_version)
    monkeypatch.setitem(script_globals, "_replace_env_version", fail_env_write)

    with pytest.raises(OSError, match="simulated env write failure"):
        VERSION_SCRIPT["set_version"]("9.9.9")

    assert {path: path.read_bytes() for path in originals} == originals
