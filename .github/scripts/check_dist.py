"""Check the built packages in a folder (default: dist/) before anything is published.

Usage: python .github/scripts/check_dist.py [DIST_DIR]

It checks that both packages were built as a wheel and an sdist at the same version, and
that each one carries the licence files and Apache-2.0 metadata. It also checks for the
files the packages need at run time, and that the MCP server pins the core it was built
with. Nothing private or local may be packaged: tests, workspaces, environment files or
the PRD. Exits 1 and lists every problem it found.
"""

import sys
import tarfile
import zipfile
from email.parser import Parser
from pathlib import Path

PACKAGES: dict[str, list[str]] = {
    # distribution name -> files the wheel must contain
    "geoproduct_core": ["geoproduct/py.typed"],
    "geoproduct_mcp": ["geoproduct_mcp/py.typed", "geoproduct_mcp/guide.md"],
}
FORBIDDEN = ("/tests/", "/.geoproduct/", "/.env", "prd.txt", "__pycache__", ".pyc")


def main() -> int:
    dist = Path(sys.argv[1] if len(sys.argv) > 1 else "dist")
    problems: list[str] = []
    versions: dict[str, str] = {}

    for name, needs in PACKAGES.items():
        wheels = sorted(dist.glob(f"{name}-*.whl"))
        sdists = sorted(dist.glob(f"{name}-*.tar.gz"))
        if len(wheels) != 1 or len(sdists) != 1:
            problems.append(
                f"{name}: expected one wheel and one sdist, found {len(wheels)} and {len(sdists)}"
            )
            continue
        wheel, sdist = wheels[0], sdists[0]

        with zipfile.ZipFile(wheel) as archive:
            names = archive.namelist()
            info = f"{name}-"
            metadata_name = next(
                (n for n in names if n.startswith(info) and n.endswith(".dist-info/METADATA")), None
            )
            if metadata_name is None:
                problems.append(f"{wheel.name}: no METADATA")
                continue
            metadata = Parser().parsestr(archive.read(metadata_name).decode("utf-8"))
            licences = metadata_name.rsplit("/", 1)[0] + "/licenses/"
            for required in ("LICENSE", "NOTICE"):
                if licences + required not in names:
                    problems.append(f"{wheel.name}: {required} is missing from {licences}")
            for required in needs:
                if required not in names:
                    problems.append(f"{wheel.name}: {required} is missing")
            problems += [
                f"{wheel.name}: must not contain {n}" for n in names if _forbidden("/" + n)
            ]

        version = metadata["Version"]
        versions[name] = version
        if metadata["License-Expression"] != "Apache-2.0":
            problems.append(
                f"{wheel.name}: License-Expression is {metadata['License-Expression']!r}"
            )
        if not metadata["Description-Content-Type"]:
            problems.append(f"{wheel.name}: no README description")
        if name == "geoproduct_mcp":
            pins = [
                r
                for r in metadata.get_all("Requires-Dist") or []
                if r.startswith("geoproduct-core")
            ]
            if pins != [f"geoproduct-core=={version}"]:
                problems.append(
                    f"{wheel.name}: must require geoproduct-core=={version}, not {pins}"
                )

        with tarfile.open(sdist) as archive:
            names = archive.getnames()
        root = f"{name}-{version}/"
        for required in ("LICENSE", "NOTICE", "README.md", "pyproject.toml"):
            if root + required not in names:
                problems.append(f"{sdist.name}: {required} is missing")
        problems += [f"{sdist.name}: must not contain {n}" for n in names if _forbidden("/" + n)]

    if len(set(versions.values())) > 1:
        problems.append(f"the packages have different versions: {versions}")

    for problem in problems:
        print(f"error: {problem}")
    if not problems:
        print(f"ok: {', '.join(f'{n} {v}' for n, v in versions.items())}")
    return 1 if problems else 0


def _forbidden(name: str) -> bool:
    return any(part in name for part in FORBIDDEN)


if __name__ == "__main__":
    sys.exit(main())
