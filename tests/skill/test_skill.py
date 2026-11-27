"""The Agent Skill in skills/geoproduct-engineer: format, links, and agreement with the code.

The skill describes tools, stages, checks, error codes and scores in prose. These tests fail
when the code and the description drift apart, so the skill never teaches a tool that does
not exist or a threshold the server no longer uses.
"""

import ast
import importlib.util
import json
import re
import subprocess
import sys
import textwrap
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import anyio
import pytest
import yaml
from mcp import Client

from geoproduct.engine.spec import compile_plan, validate_spec
from geoproduct.schemas import ErrorCode, Rating, SourceEvaluation
from geoproduct.schemas.sources import CRITERION_WEIGHTS, tier_for
from geoproduct.stages import builtin_registry
from geoproduct.stages.quality import CHECKS
from geoproduct.workspace import Workspace
from geoproduct_mcp.server import create_server
from geoproduct_mcp.tools.common import ServerState

SKILL = Path(__file__).resolve().parents[2] / "skills" / "geoproduct-engineer"
SCRIPTS = SKILL / "scripts"
DOCS = sorted(p for p in SKILL.rglob("*.md") if "evals" not in p.relative_to(SKILL).parts)
WORKFLOWS = [
    "geographic-research",
    "source-discovery",
    "dataset-creation",
    "entity-resolution",
    "building-matching",
    "map-accuracy-audit",
    "infrastructure-research",
    "spatial-analysis",
    "map-integration",
    "quality-assurance",
    "pipeline-construction",
]
FRONTMATTER_KEYS = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}


def frontmatter() -> tuple[dict[str, Any], str]:
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\n")
    head, body = text[4:].split("\n---\n", 1)
    data = yaml.safe_load(head)
    assert isinstance(data, dict)
    return data, body


def load_script(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def server(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict[str, set[str]]]:
    """Tool and prompt names with their parameter names, as an MCP client sees them."""
    state = ServerState(Workspace(tmp_path_factory.mktemp("ws"), []), env={})
    found: dict[str, dict[str, set[str]]] = {"tools": {}, "prompts": {}}

    async def run() -> None:
        async with Client(create_server(state)) as client:
            for tool in (await client.list_tools()).tools:
                found["tools"][tool.name] = set(tool.input_schema.get("properties", {}))
            for prompt in (await client.list_prompts()).prompts:
                found["prompts"][prompt.name] = {a.name for a in prompt.arguments or []}

    anyio.run(run)
    return found


# Format ------------------------------------------------------------------------------------


def test_frontmatter_follows_the_agent_skills_specification() -> None:
    data, _ = frontmatter()
    assert set(data) <= FRONTMATTER_KEYS
    name = data["name"]
    assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name)
    assert len(name) <= 64
    assert name == SKILL.name
    assert 1 <= len(data["description"]) <= 1024
    assert len(data.get("compatibility", "")) <= 500
    metadata = data.get("metadata", {})
    assert all(isinstance(k, str) and isinstance(v, str) for k, v in metadata.items())


def test_skill_md_stays_small() -> None:
    _, body = frontmatter()
    assert len(body.splitlines()) < 500
    assert len(body) / 4 < 5000, "about 5,000 tokens at four characters a token"


def test_every_workflow_and_reference_is_linked_from_skill_md() -> None:
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    present = sorted(p.stem for p in (SKILL / "workflows").glob("*.md"))
    assert present == sorted(WORKFLOWS)
    for path in [*(SKILL / "workflows").glob("*.md"), *(SKILL / "references").glob("*.md")]:
        assert f"]({path.parent.name}/{path.name})" in text, path.name


def links(path: Path) -> Iterator[str]:
    text = re.sub(r"```.*?```", "", path.read_text(encoding="utf-8"), flags=re.DOTALL)
    for target in re.findall(r"\]\(([^)\s]+)\)", text):
        if not re.match(r"[a-z][a-z0-9+.-]*:", target) and not target.startswith("#"):
            yield target.split("#", 1)[0]


@pytest.mark.parametrize("path", DOCS, ids=lambda p: p.relative_to(SKILL).as_posix())
def test_relative_links_resolve(path: Path) -> None:
    for target in links(path):
        assert (path.parent / target).resolve().is_file(), f"{target} in {path.name}"


def test_references_stay_one_level_deep() -> None:
    """Supporting documents link back to SKILL.md's neighbours, never to deeper chains."""
    for path in DOCS:
        for target in links(path):
            resolved = (path.parent / target).resolve()
            assert resolved.parent in {SKILL, SKILL / "workflows", SKILL / "references"} or (
                resolved.parent == SKILL / "scripts"
            ), f"{target} in {path.name}"


# Refusals and the evaluation workflow (audit GP-19) -----------------------------------------

EVALUATIONS = SKILL.parents[1] / "docs" / "evaluations.md"


def test_skill_md_says_not_to_work_around_a_refusal() -> None:
    _, body = frontmatter()
    text = " ".join(body.split())
    assert "don't retry it with another tool, a shell command or a script" in text
    assert "return reports and findings as text" in text


def test_the_evaluation_run_prompt_states_the_reporting_rule() -> None:
    text = EVALUATIONS.read_text(encoding="utf-8")
    templates = re.findall(r"```text\n(Execute this evaluation task\..*?)```", text, re.S)
    assert len(templates) == 1, "docs/evaluations.md holds one run prompt template"
    prompt = " ".join(templates[0].split())
    for phrase in (
        "Return every report, audit, plan or note the task asks for as text",
        'a line "=== FILE: <file name> ==="',
        'a line "=== END FILE ==="',
        "Do not write these files yourself: the orchestrator saves them",
        "do not retry it with another tool, a shell command or a script",
        "Report the refusal in your final response",
    ):
        assert phrase in prompt, phrase


def test_the_evaluation_provenance_records_who_produced_and_saved_each_file() -> None:
    text = EVALUATIONS.read_text(encoding="utf-8")
    [example] = re.findall(r"```json\n(.*?)```", text, re.S)
    provenance = json.loads(example)
    assert {"run_prompt", "files", "refusals", "workarounds", "graded_by"} <= provenance.keys()
    assert provenance["files"]
    for entry in provenance["files"]:
        assert {"path", "produced_by", "delivered_as", "saved_by"} <= entry.keys()
    assert any(entry["saved_by"].startswith("orchestrator") for entry in provenance["files"])


# Agreement with the code -------------------------------------------------------------------


def doc_text() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in DOCS)


def test_the_tool_reference_lists_exactly_the_server_tools(server: dict[str, Any]) -> None:
    text = (SKILL / "references" / "mcp-tools.md").read_text(encoding="utf-8")
    documented = set(re.findall(r"^\| `([a-z_]+)` \|", text, flags=re.MULTILINE))
    assert documented == set(server["tools"])


def test_calls_in_the_docs_use_real_tools_and_parameters(server: dict[str, Any]) -> None:
    callables = {**server["tools"], **server["prompts"]}
    calls = re.findall(r"`([a-z][a-z_]+)\(([^`]*)\)`", doc_text())
    assert calls, "the docs show tool calls"
    for name, arguments in calls:
        assert name in callables, f"{name}() is not a server tool or prompt"
        named = re.findall(r"\b([a-z_]+)=", arguments)
        if name in server["prompts"]:  # prompts are shown with their argument names alone
            named += [a.strip() for a in arguments.split(",") if a.strip() and "=" not in a]
        for argument in named:
            assert argument in callables[name], f"{name}({argument}=...) is not a parameter"


def test_stage_names_in_the_docs_are_registered() -> None:
    names = set(builtin_registry().names())
    families = {name.split(".", 1)[0] for name in names}
    mentioned = {
        f"{family}.{rest}"
        for family, rest in re.findall(r"`([a-z]+)\.([a-z_]+)`", doc_text())
        if family in families
    }
    assert mentioned, "the docs name stages"
    assert mentioned <= names, mentioned - names
    catalogue = (SKILL / "references" / "pipeline-spec.md").read_text(encoding="utf-8")
    assert {n for n in names if f"`{n}`" not in catalogue} == set(), "stages missing"


def test_validation_checks_match_the_quality_stage() -> None:
    text = (SKILL / "references" / "mcp-tools.md").read_text(encoding="utf-8")
    row = next(line for line in text.splitlines() if line.startswith("| `validate_dataset`"))
    listed = re.search(r"`checks` \(default all: ([^)]*)\)", row)
    assert listed is not None
    assert re.findall(r"`([a-z_]+)`", listed.group(1)) == list(CHECKS)


def test_error_codes_in_skill_md_exist() -> None:
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    rows = [line for line in text.splitlines() if re.match(r"\| `[a-z_]+`", line)]
    codes = {code for row in rows for code in re.findall(r"`([a-z_]+)`", row.split("|")[1])}
    assert codes, "SKILL.md has an error table"
    assert codes <= {c.value for c in ErrorCode}, codes - {c.value for c in ErrorCode}


def yaml_blocks() -> Iterator[tuple[str, Any]]:
    fence = re.compile(r"^([ \t]*)```yaml\n(.*?)^\1```", re.MULTILINE | re.DOTALL)
    for path in DOCS:
        for match in fence.finditer(path.read_text(encoding="utf-8")):
            yield path.name, yaml.safe_load(textwrap.dedent(match.group(2)))


def test_yaml_examples_are_valid_pipelines_or_stages() -> None:
    registry = builtin_registry()
    blocks = list(yaml_blocks())
    assert len(blocks) >= 3
    for where, block in blocks:
        if isinstance(block, dict) and block.get("kind") == "Pipeline":
            compile_plan(validate_spec(block), registry)
        else:
            assert isinstance(block, list), where
            for stage in block:
                registry.get(stage["uses"]).params.model_validate(stage.get("with", {}))


# Scripts -----------------------------------------------------------------------------------


@pytest.mark.parametrize("path", sorted(SCRIPTS.glob("*.py")), ids=lambda p: p.name)
def test_scripts_use_only_the_standard_library(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            modules = [node.module or ""]
        else:
            continue
        for module in modules:
            assert module.split(".")[0] in sys.stdlib_module_names, module


@pytest.mark.parametrize("path", sorted(SCRIPTS.glob("*.py")), ids=lambda p: p.name)
def test_scripts_explain_themselves(path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-I", str(path), "--help"], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0
    assert "usage:" in result.stdout


def evaluation(**scores: int) -> SourceEvaluation:
    return SourceEvaluation(
        **{name: Rating(score=score, rationale="test") for name, score in scores.items()}
    )


CRITERIA = [criterion.value for criterion in CRITERION_WEIGHTS]


@pytest.mark.parametrize(
    "scores",
    [
        dict.fromkeys(CRITERIA, 3),
        dict.fromkeys(CRITERIA, 2),
        dict.fromkeys(CRITERIA, 1),
        {**dict.fromkeys(CRITERIA, 3), "licensing": 0},
        {**dict.fromkeys(CRITERIA, 2), "authority": 3, "accuracy": 3},
        {**dict.fromkeys(CRITERIA, 1), "freshness": 3, "verifiability": 0},
    ],
)
def test_score_source_agrees_with_the_server(scores: dict[str, int]) -> None:
    script = load_script("score_source")
    assert {c.value: w for c, w in CRITERION_WEIGHTS.items()} == script.WEIGHTS
    ratings = {str(name): value for name, value in scores.items()}
    result = script.score(ratings)
    server = evaluation(**ratings)
    assert result["score"] == server.weighted_score()
    assert result["tier"] == tier_for(server).value


def test_score_source_refuses_incomplete_ratings() -> None:
    result = subprocess.run(
        [sys.executable, "-I", str(SCRIPTS / "score_source.py"), "--authority", "3"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert result.stdout == ""
    assert "missing ratings" in json.loads(result.stderr)["error"]


def check_dataset(path: Path, *options: str) -> tuple[int, dict[str, Any]]:
    result = subprocess.run(
        [sys.executable, "-I", str(SCRIPTS / "check_dataset.py"), str(path), *options],
        capture_output=True,
        text=True,
        check=False,
    )
    output = result.stdout if result.returncode != 2 else result.stderr
    return result.returncode, json.loads(output)


def feature(fid: str, geometry: dict[str, Any], **properties: Any) -> dict[str, Any]:
    return {"type": "Feature", "id": fid, "geometry": geometry, "properties": properties}


def point(lon: float, lat: float) -> dict[str, Any]:
    return {"type": "Point", "coordinates": [lon, lat]}


BBOX = "0.0015,0.0015,0.0097,0.0097"


def test_check_dataset_passes_a_clean_file(tmp_path: Path) -> None:
    square = [[0.0021, 0.0021], [0.0031, 0.0021], [0.0031, 0.0031], [0.0021, 0.0021]]
    data = {
        "type": "FeatureCollection",
        "features": [
            feature("a", point(0.00412, 0.00315), name="Example Clinic"),
            feature("b", {"type": "Polygon", "coordinates": [square]}, name="Example Hall"),
        ],
    }
    path = tmp_path / "clean.geojson"
    path.write_text(json.dumps(data), encoding="utf-8")
    code, report = check_dataset(path, "--bbox", BBOX)
    assert code == 0
    assert report["errors"] == 0
    assert report["warnings"] == 0
    assert report["items"] == 2


def test_check_dataset_finds_common_coordinate_errors(tmp_path: Path) -> None:
    rows = [
        "code,name,x,y",
        "c1,North Clinic,0.00412,0.00315",
        "c2,East Clinic,0.00500,0.00600",
        "c3,West Clinic,0,0",
        "c4,Far Clinic,2.50000,1.50000",
        "c1,Copy Clinic,0.00700,0.00700",
        "c6,North Clinic,0.00414,0.00317",
        "c7,Rough Clinic,0.005,0.005",
        "c8,No Place Clinic,,",
    ]
    path = tmp_path / "clinics.csv"
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    swapped = tmp_path / "swapped.csv"
    swapped.write_text("lat,lon\n0.0080,0.0020\n", encoding="utf-8")

    code, report = check_dataset(
        path, "--lon-column", "x", "--lat-column", "y", "--id-column", "code", "--bbox", BBOX
    )
    assert code == 1
    by_check = report["by_check"]
    assert by_check["null_island"]["error"] == 1
    assert by_check["bbox"]["error"] == 1
    assert by_check["duplicate_id"]["error"] == 1
    assert by_check["duplicate"]["warning"] == 1
    assert by_check["precision"]["warning"] == 1, "c7 only: c3 at 0, 0 is already an error"
    assert by_check["missing"]["warning"] == 1
    items = {f["item"] for f in report["findings"] if f["check"] == "duplicate"}
    assert items <= {"c1", "c6"}

    code, report = check_dataset(swapped, "--bbox", "0.0015,0.0015,0.0097,0.0040")
    assert code == 1
    assert report["by_check"]["swapped"]["error"] == 1


def test_check_dataset_reads_structure_crs_and_evidence(tmp_path: Path) -> None:
    open_ring = [[0.002, 0.002], [0.003, 0.002], [0.003, 0.003], [0.0021, 0.0021]]
    attributes = {
        "beds": {"value": 40, "basis": "fact", "evidence_ids": []},
        "height_m": {"value": 9, "basis": "estimate", "note": "3 floors x 3 m"},
        "operator": {"value": "Example Trust", "basis": "fact", "evidence_ids": ["ev-1"]},
    }
    data = {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "EPSG:3857"}},
        "features": [
            feature("a", {"type": "Polygon", "coordinates": [open_ring]}),
            feature("b", point(0.00412, 0.00315), attributes=attributes),
            feature("c", point(450000.0, 120000.0)),
            {"type": "Feature", "geometry": point(0.0042, 0.0031), "properties": None},
        ],
    }
    path = tmp_path / "mixed.geojson"
    path.write_text(json.dumps(data), encoding="utf-8")
    code, report = check_dataset(path)
    assert code == 1
    by_check = report["by_check"]
    assert by_check["crs"]["error"] == 1
    assert by_check["structure"]["error"] == 1
    assert by_check["range"]["error"] == 1
    assert by_check["evidence"]["warning"] == 1
    evidence = next(f for f in report["findings"] if f["check"] == "evidence")
    assert evidence["message"].endswith("beds")


def test_check_dataset_judges_precision_as_written(tmp_path: Path) -> None:
    path = tmp_path / "written.geojson"
    path.write_text(
        '{"type": "Feature", "geometry": {"type": "Point", "coordinates": [0.0040, 0.0030]},'
        ' "properties": {}}',
        encoding="utf-8",
    )
    code, report = check_dataset(path)
    assert code == 0
    assert report["warnings"] == 0, "0.0040 has four decimals as written"


def test_check_dataset_reports_unreadable_input(tmp_path: Path) -> None:
    path = tmp_path / "broken.geojson"
    path.write_text("{not json", encoding="utf-8")
    code, error = check_dataset(path)
    assert code == 2
    assert "error" in error


def test_check_dataset_finds_every_error_planted_in_the_audit_eval() -> None:
    planted = SKILL / "evals" / "audit-facility-csv" / "files" / "facilities.csv"
    code, report = check_dataset(planted, "--bbox=-30.20,10.00,-30.00,10.15")
    assert code == 1
    found = {finding["check"]: finding["item"] for finding in report["findings"]}
    assert found == {
        "swapped": "HF-003",
        "null_island": "HF-004",
        "bbox": "HF-007",
        "duplicate": "HF-001",  # the same name as HF-006, 16 m away
        "duplicate_id": "HF-002",  # found through the facility_id column
        "precision": "HF-010",
        "missing": "HF-011",
    }
    assert report["not_checked"] == []


def test_check_dataset_says_what_it_could_not_check(tmp_path: Path) -> None:
    path = tmp_path / "no-ids.csv"
    path.write_text("name,lon,lat\nExample Clinic,0.00412,0.00315\n", encoding="utf-8")
    code, report = check_dataset(path)
    assert code == 0
    assert [line.split(":")[0] for line in report["not_checked"]] == ["bbox", "duplicate_id"]
