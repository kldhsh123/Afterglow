#!/usr/bin/env python3
"""Keep release version declarations synchronized from the root VERSION file."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = ROOT / "VERSION"
DOCKER_ENV_FILE = ROOT / ".env.docker.example"
BACKEND_DIR = ROOT / "backend"
PYPROJECT_FILE = BACKEND_DIR / "pyproject.toml"
UV_LOCK_FILE = BACKEND_DIR / "uv.lock"
ENV_VERSION_KEY = "AFTERGLOW_VERSION"
_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[A-Za-z0-9._+-]*)?$")
_STABLE_VERSION_RE = re.compile(r"^([0-9]+)\.([0-9]+)\.([0-9]+)$")


def _read_canonical_version() -> str:
    return VERSION_FILE.read_text(encoding="utf-8").strip()


def _normalize_version(raw: str) -> str:
    version = raw.strip().removeprefix("v")
    if not _VERSION_RE.fullmatch(version):
        raise ValueError(
            f"invalid version {raw!r}; expected X.Y.Z or a compatible prerelease"
        )
    return version


def _bump_version(version: str, part: str) -> str:
    match = _STABLE_VERSION_RE.fullmatch(version)
    if match is None:
        raise ValueError("--bump requires the current VERSION to be stable X.Y.Z")
    major, minor, patch = (int(value) for value in match.groups())
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def _read_env_version() -> str:
    prefix = f"{ENV_VERSION_KEY}="
    values = [
        line[len(prefix) :].strip()
        for line in DOCKER_ENV_FILE.read_text(encoding="utf-8").splitlines()
        if line.startswith(prefix)
    ]
    if len(values) != 1:
        raise ValueError(
            f"expected exactly one {ENV_VERSION_KEY} entry in {DOCKER_ENV_FILE}"
        )
    return values[0]


def _replace_env_version(version: str) -> None:
    prefix = f"{ENV_VERSION_KEY}="
    raw = DOCKER_ENV_FILE.read_bytes().decode("utf-8")
    lines = raw.splitlines(keepends=True)
    replaced = 0
    for index, line in enumerate(lines):
        body = line.rstrip("\r\n")
        ending = line[len(body) :]
        if body.startswith(prefix):
            lines[index] = f"{prefix}{version}{ending}"
            replaced += 1
    if replaced != 1:
        raise ValueError(
            f"expected exactly one {ENV_VERSION_KEY} entry in {DOCKER_ENV_FILE}"
        )
    DOCKER_ENV_FILE.write_bytes("".join(lines).encode("utf-8"))


def _read_python_project_version() -> str:
    with PYPROJECT_FILE.open("rb") as handle:
        project = tomllib.load(handle).get("project")
    if not isinstance(project, dict) or not isinstance(project.get("version"), str):
        raise ValueError(f"missing project.version in {PYPROJECT_FILE}")
    return project["version"]


def _read_uv_lock_version() -> str:
    with UV_LOCK_FILE.open("rb") as handle:
        packages = tomllib.load(handle).get("package")
    if not isinstance(packages, list):
        raise ValueError(f"missing package list in {UV_LOCK_FILE}")
    matches = [
        package.get("version")
        for package in packages
        if isinstance(package, dict) and package.get("name") == "xuwen"
    ]
    if len(matches) != 1 or not isinstance(matches[0], str):
        raise ValueError(f"expected exactly one xuwen package in {UV_LOCK_FILE}")
    return matches[0]


def _declared_versions() -> dict[str, str]:
    return {
        "VERSION": _read_canonical_version(),
        ".env.docker.example": _read_env_version(),
        "backend/pyproject.toml": _read_python_project_version(),
        "backend/uv.lock": _read_uv_lock_version(),
    }


def check_versions() -> bool:
    versions = _declared_versions()
    expected = _normalize_version(versions["VERSION"])
    mismatches = {path: value for path, value in versions.items() if value != expected}
    if mismatches:
        print(f"Version mismatch; VERSION declares {expected}:", file=sys.stderr)
        for path, value in mismatches.items():
            print(f"  {path}: {value}", file=sys.stderr)
        return False
    print(f"Version declarations are synchronized at {expected}.")
    return True


def _version_target_files() -> tuple[Path, ...]:
    return VERSION_FILE, DOCKER_ENV_FILE, PYPROJECT_FILE, UV_LOCK_FILE


def _restore_version_files(originals: dict[Path, bytes]) -> None:
    failures: list[str] = []
    for path, content in originals.items():
        try:
            path.write_bytes(content)
        except OSError as exc:
            failures.append(f"{path}: {exc}")
    if failures:
        raise OSError("; ".join(failures))


def set_version(version: str) -> None:
    originals = {path: path.read_bytes() for path in _version_target_files()}
    try:
        subprocess.run(
            [
                "uv",
                "version",
                version,
                "--project",
                str(BACKEND_DIR),
                "--no-sync",
            ],
            cwd=ROOT,
            check=True,
        )
        VERSION_FILE.write_text(version + "\n", encoding="utf-8")
        _replace_env_version(version)
        if not check_versions():
            raise RuntimeError(
                "version synchronization did not produce a consistent state"
            )
    except BaseException as exc:
        try:
            _restore_version_files(originals)
        except OSError as restore_exc:
            raise RuntimeError(
                f"version update failed and rollback was incomplete: {restore_exc}"
            ) from exc
        raise


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", nargs="?", help="new version, optionally prefixed with v")
    parser.add_argument(
        "--bump",
        choices=("major", "minor", "patch"),
        help="increment the stable version in VERSION",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify declarations without modifying files",
    )
    args = parser.parse_args()
    selected = sum((args.version is not None, args.bump is not None, args.check))
    if selected != 1:
        parser.error("choose exactly one of VERSION, --bump, or --check")
    return args


def main() -> int:
    args = _parse_args()
    try:
        if args.check:
            return 0 if check_versions() else 1
        version = (
            _bump_version(_read_canonical_version(), args.bump)
            if args.bump is not None
            else _normalize_version(args.version)
        )
        set_version(version)
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"set-version failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
