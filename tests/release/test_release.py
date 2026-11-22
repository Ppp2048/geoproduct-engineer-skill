"""Release metadata that must stay consistent: versions, licences, the changelog, the README's
counts and examples, and CI pins.

These are the release-checklist items a test can check. The rest is in docs/releasing.md.
"""

import hashlib
import itertools
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGES = [ROOT / "packages" / "geoproduct-core", ROOT / "packages" / "geoproduct-mcp"]
SKILL = ROOT / "skills" / "geoproduct-engineer" / "SKILL.md"


def project(folder: Path) -> dict[str, object]:
    data = tomllib.loads((folder / "pyproject.toml").read_text(encoding="utf-8"))
    table = data["project"]
    assert isinstance(table, dict)
    return table


def version() -> str:
    value = project(PACKAGES[0])["version"]
    assert isinstance(value, str)
    return value


def test_every_part_carries_the_same_version() -> None:
    assert [project(folder)["version"] for folder in PACKAGES] == [version()] * len(PACKAGES)
    # The server is tested against exactly this core, so it pins it.
    assert f"geoproduct-core=={version()}" in project(PACKAGES[1])["dependencies"]  # type: ignore[operator]
    skill_version = re.search(r'^  version: "([^"]+)"$', SKILL.read_text(encoding="utf-8"), re.M)
    assert skill_version is not None
    assert skill_version.group(1) == version()


def test_the_changelog_covers_the_current_version() -> None:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    headings = re.findall(r"^## \[([^\]]+)\]", changelog, re.M)
    assert headings, "CHANGELOG.md has no version sections"
    # During development the top section is [Unreleased]; a release renames it.
    current = version()
    assert headings[0] in {"Unreleased", current}
    if not re.search(r"\.dev\d+$", current):
        assert headings[0] == current, f"release {current} needs its own CHANGELOG section"


def test_each_package_ships_the_root_licence_and_notice() -> None:
    for name in ("LICENSE", "NOTICE"):
        root = (ROOT / name).read_bytes()
        for folder in PACKAGES:
            assert (folder / name).read_bytes() == root, f"{folder.name}/{name} differs"
    assert (ROOT / "LICENSE").read_text(encoding="utf-8").lstrip().startswith("Apache License")
    for folder in PACKAGES:
        meta = project(folder)
        assert meta["license"] == "Apache-2.0"
        assert meta["license-files"] == ["LICENSE", "NOTICE"]
    assert re.search(r"^license: Apache-2.0$", SKILL.read_text(encoding="utf-8"), re.M)


DOCS = [
    ROOT / "README.md",
    ROOT / "CHANGELOG.md",
    ROOT / "CONTRIBUTING.md",
    ROOT / "SECURITY.md",
    *sorted((ROOT / "docs").glob("*.md")),
    ROOT / "examples" / "README.md",
]


def test_relative_links_in_the_documentation_resolve() -> None:
    broken = []
    for doc in DOCS:
        text = re.sub(r"```.*?```", "", doc.read_text(encoding="utf-8"), flags=re.S)
        # Markdown links and images, and the HTML src and href attributes the README uses.
        targets = re.findall(r"\]\(([^)\s]+)\)", text) + re.findall(r'(?:src|href)="([^"]+)"', text)
        for target in targets:
            if re.match(r"[a-z]+:", target) or target.startswith("#"):
                continue
            path = (doc.parent / target.split("#")[0]).resolve()
            if not path.exists():
                broken.append(f"{doc.relative_to(ROOT)} -> {target}")
    assert broken == []


def test_the_readme_pipeline_example_compiles() -> None:
    from geoproduct.engine.spec import compile_plan, parse_spec
    from geoproduct.stages import builtin_registry

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    examples = re.findall(r"```yaml\n(apiVersion: geoproduct/v1\n.*?)```", readme, re.S)
    assert len(examples) == 1
    plan = compile_plan(parse_spec(examples[0]), builtin_registry())
    assert [stage.id for stage in plan.stages] == ["clinics", "buildings", "matched", "export"]


def test_the_readme_counts_and_names_match_the_code(tmp_path: Path) -> None:
    import anyio
    from mcp import Client

    from geoproduct.stages import builtin_registry
    from geoproduct.workspace import Workspace
    from geoproduct_mcp.server import create_server
    from geoproduct_mcp.tools.common import ServerState

    names: dict[str, list[str]] = {}

    async def listing() -> None:
        state = ServerState(Workspace(tmp_path, []), env={})
        async with Client(create_server(state)) as client:
            names["tools"] = [tool.name for tool in (await client.list_tools()).tools]
            names["prompts"] = [prompt.name for prompt in (await client.list_prompts()).prompts]

    anyio.run(listing)
    names["stages"] = list(builtin_registry().names())
    workflows = ROOT / "skills" / "geoproduct-engineer" / "workflows"
    names["workflows"] = [path.stem for path in workflows.glob("*.md")]

    # Badges encode spaces in their URLs.
    readme = (ROOT / "README.md").read_text(encoding="utf-8").replace("%20", " ")
    for kind, found in names.items():
        assert f"{len(found)} {kind}" in readme, f"the README does not say '{len(found)} {kind}'"
    for name in names["tools"] + names["prompts"] + names["stages"]:
        assert f"`{name}`" in readme, f"the README does not list `{name}`"


# Words that must not appear anywhere in the repository: the name of a private project this
# toolkit is independent of, and specific places it must not assume. They are stored as
# SHA-256 digests, so this test does not publish them itself.
BANNED_DIGESTS = frozenset(
    {
        "f9584209348e402f0a472b6809c036a7162d1a59e2ce31d7b0dab4d66c34f636",
        "997fd09b1525ef0c91232cbba0b70b83b5ec4599be8e92c8e240e924192c3179",
        "f729e2da20824725f642ba1655098f275fdf7d120b09700fdc721ddc9f66af3a",
        "22f40ce9e4f35c7d4ce2aef9d75a7311f3d675c50ef0a09708160958cfd7b6f3",
        "61d9919382c85fa67720391b148a07d2678260f2be35464ee4298fb046e2802e",
    }
)
TEXT_SUFFIXES = {".md", ".py", ".yaml", ".yml", ".json", ".csv", ".toml", ".txt", ".example"}
TEXT_SUFFIXES |= {".svg"}
# Local, generated or private files that are never committed.
SKIPPED_PARTS = {".git", ".venv", ".geoproduct", "dist", "__pycache__", ".mypy_cache"}
SKIPPED_PARTS |= {".ruff_cache", ".pytest_cache", "prd.txt"}


def banned_words_in(text: str) -> bool:
    """Whether a banned word appears as a word, or as two adjacent words run together."""
    words = re.findall(r"[a-z]+", text.lower())
    candidates = set(words) | {a + b for a, b in itertools.pairwise(words)}
    return any(hashlib.sha256(w.encode()).hexdigest() in BANNED_DIGESTS for w in candidates)


def test_no_private_project_or_place_names_anywhere() -> None:
    found = []
    for path in sorted(ROOT.rglob("*")):
        parts = set(path.relative_to(ROOT).parts)
        if parts & SKIPPED_PARTS or any(p.endswith("-workspace") for p in parts):
            continue
        is_text = path.suffix in TEXT_SUFFIXES or path.name in {"LICENSE", "NOTICE"}
        if (
            path.is_file()
            and is_text
            and banned_words_in(path.read_text(encoding="utf-8", errors="replace"))
        ):
            found.append(str(path.relative_to(ROOT)))
    assert found == []


def test_the_name_check_matches_whole_words_only() -> None:
    # One banned word is a substring of "architecture"; whole-word matching must not flag it.
    assert not banned_words_in("The architecture of Sample Town, in Exampleton.")


def test_workflow_actions_are_pinned_to_commits() -> None:
    workflows = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
    assert workflows
    for workflow in workflows:
        for line in workflow.read_text(encoding="utf-8").splitlines():
            uses = re.match(r"\s*(?:-\s+)?uses:\s*(\S+)", line)
            if uses is None or uses.group(1).startswith("./"):
                continue
            assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", uses.group(1)), (
                f"{workflow.name}: '{uses.group(1)}' is not pinned to a commit SHA"
            )
            assert re.search(r"#\s*v\d", line), f"{workflow.name}: say which tag the SHA is"
