"""Release version declarations and runtime version resolution."""

import runpy
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
