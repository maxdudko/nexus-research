#!/usr/bin/env python3
"""Check that every place that names the release version agrees.

    python3 scripts/check_release_version.py              # sources agree
    python3 scripts/check_release_version.py --tag v0.2.0 # and match the tag
    python3 scripts/check_release_version.py --print      # print the version

Sources: apps/api/pyproject.toml, apps/web/package.json and package-lock.json,
the NEXUS_VERSION default in compose.release.yml, NEXUS_VERSION in
.env.release.example, and a "## [x.y.z]" heading in CHANGELOG.md. Exit status is
0 when every check passes.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

failures: list[str] = []


def check(condition: bool, message: str) -> None:
    print(("ok   " if condition else "FAIL ") + message)
    if not condition:
        failures.append(message)


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def env_template_version() -> str:
    match = re.search(
        r"^NEXUS_VERSION=(\S+)$", read(".env.release.example"), re.MULTILINE
    )
    if match is None:
        raise SystemExit(".env.release.example: expected one NEXUS_VERSION= line")
    return match.group(1)


def sources() -> dict[str, set[str]]:
    lock = json.loads(read("apps/web/package-lock.json"))
    return {
        "apps/api/pyproject.toml": {
            tomllib.loads(read("apps/api/pyproject.toml"))["project"]["version"]
        },
        "apps/web/package.json": {json.loads(read("apps/web/package.json"))["version"]},
        "apps/web/package-lock.json": {
            lock["version"],
            lock["packages"][""]["version"],
        },
        "compose.release.yml": set(
            re.findall(r"\$\{NEXUS_VERSION:-([^}]+)\}", read("compose.release.yml"))
        ),
        ".env.release.example": {env_template_version()},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tag", help="release tag, for example v0.2.0")
    parser.add_argument(
        "--print", action="store_true", help="print the version and exit"
    )
    args = parser.parse_args()

    version = env_template_version()
    if args.print:
        print(version)
        return 0

    for path, found in sources().items():
        check(
            found == {version},
            f"{path} names {version}"
            + (
                ""
                if found == {version}
                else f" (found {', '.join(sorted(found)) or 'none'})"
            ),
        )

    heading = re.compile(
        rf"^## \[{re.escape(version)}\] - \d{{4}}-\d{{2}}-\d{{2}}$", re.MULTILINE
    )
    check(
        heading.search(read("CHANGELOG.md")) is not None,
        f"CHANGELOG.md has a dated [{version}] entry",
    )

    if args.tag is not None:
        check(args.tag == f"v{version}", f"tag {args.tag} matches v{version}")

    if failures:
        print(f"\n{len(failures)} check(s) failed", file=sys.stderr)
        return 1
    print(f"\nall release version checks passed for {version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
