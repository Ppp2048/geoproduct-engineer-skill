"""A complete research-to-export workflow, driven through the MCP protocol, then the exports
checked as a user would check them.

The agent's side is scripted: it registers and fetches two sources (a facility register as
CSV and a web page), adds one record it researched with an excerpt, collects map data, and
runs every step to a GeoJSON and CSV export with a quality report. Nothing touches the
network: the web and the geocoder answer from mocks, map data comes from the offline
fixture town, whose ground truth is known.

Planted problems, each with the outcome the toolkit must give:

- The map has two hospital records with nearly the same name, 11 m apart, that share a
  website but disagree on beds. A website is organisation-level: it does not identify a
  site, and nothing else shows they are one, so the two are kept apart, linked as one
  organisation, and marked as possible duplicates for review. Each keeps its own bed
  count with its evidence.
- REG-1 and REG-4 repeat a mapped hospital and clinic by name and position only, with no
  shared identifier or address to confirm it: each is kept apart from its map record and
  both are marked as possible duplicates for review.
- REG-2 has an address but no coordinates: geocoded, citing the geocoder.
- REG-3 has latitude 95: rejected on load, with the reason.
- The researched record has an address the geocoder cannot find: kept, unresolved, with
  its limitations, never given a made-up position.
"""

import csv
import hashlib
import io
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import anyio
import httpx2
import pytest
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from mcp import Client

from geoproduct.providers.fixture import AREA_BBOX
from geoproduct.providers.registry import builtin_providers
from geoproduct.schemas import Record
from geoproduct.stages.output import FIXED_COLUMNS
from geoproduct.workspace import Workspace
from geoproduct_mcp.server import create_server
from geoproduct_mcp.tools.common import ServerState

REPO = Path(__file__).resolve().parents[2]
SCHEMAS = REPO / "schemas" / "v1"
CHECKER = REPO / "skills" / "geoproduct-engineer" / "scripts" / "check_dataset.py"
AREA = {"bbox": list(AREA_BBOX)}
BBOX_TEXT = ",".join(str(v) for v in AREA_BBOX)

ENV = {
    "GEOPRODUCT_USER_AGENT": "geoproduct-e2e-tests/1.0 (+https://example.org/contact)",
    # A self-hosted geocoder: no public batch cap, and no pacing to slow the test down.
    "GEOPRODUCT_NOMINATIM_URL": "https://geocoder.example.org",
    "GEOPRODUCT_NOMINATIM_RATE": "100",
}

REGISTER_CSV = (
    "id,name,category,address,lon,lat,beds\n"
    "REG-1,Sample General Hospital,amenity=hospital,1 Hospital Road,0.00432,0.00431,110\n"
    "REG-2,Eastgate Clinic,amenity=clinic,12 Market Lane,,,\n"
    "REG-3,Broken Clinic,amenity=clinic,,0.0050,95.0,\n"
    "REG-4,Riverside Clinic,amenity=clinic,,0.00291,0.00431,\n"
)
PAGE = (
    "<html><body><h1>About us</h1><p>Southgate Health Centre, 8 South Street, opened in "
    "2021 and offers general practice.</p></body></html>"
)
MARKET_LANE = {
    "osm_type": "node",
    "osm_id": 9001,
    "lat": "0.0021",
    "lon": "0.0090",
    "category": "amenity",
    "type": "clinic",
    "place_rank": 30,
    "display_name": "12, Market Lane, Sample Town",
    "address": {"house_number": "12", "road": "Market Lane", "town": "Sample Town"},
    "boundingbox": ["0.00205", "0.00215", "0.00895", "0.00905"],
}


def web(request: httpx2.Request) -> httpx2.Response:
    if request.url.path == "/health-facilities.csv":
        return httpx2.Response(200, text=REGISTER_CSV, headers={"content-type": "text/csv"})
    if request.url.path == "/about":
        return httpx2.Response(200, text=PAGE, headers={"content-type": "text/html"})
    return httpx2.Response(404)


def geocoder(request: httpx2.Request) -> httpx2.Response:
    """Finds Market Lane, and nothing else."""
    if request.url.path.endswith("/search"):
        found = "Market Lane" in request.url.params.get("q", "")
        return httpx2.Response(200, json=[MARKET_LANE] if found else [])
    return httpx2.Response(404)


def rating(score: int) -> dict[str, Any]:
    names = (
        "authority",
        "accuracy",
        "freshness",
        "coverage",
        "licensing",
        "geographic_precision",
        "verifiability",
    )
    return {n: {"score": score, "rationale": f"test rating for {n}"} for n in names}


@pytest.fixture
def state(tmp_path: Path) -> ServerState:
    workspace = Workspace(tmp_path / "ws", [tmp_path / "exports"]).ensure()
    providers = builtin_providers(workspace, ENV, transport=httpx2.MockTransport(geocoder))
    state = ServerState(workspace, providers=providers, env=ENV)
    state.fetch_transport = httpx2.MockTransport(web)
    return state


async def call(client: Client, tool: str, **arguments: Any) -> dict[str, Any]:
    result = await client.call_tool(tool, arguments)
    text = result.content[0].text if result.content else ""  # type: ignore[union-attr]
    assert not result.is_error, f"{tool}: {text}"
    assert result.structured_content is not None, tool
    return dict(result.structured_content)


def dataset_id(result: dict[str, Any]) -> str:
    return str(result["dataset"]["dataset_id"])


async def research(client: Client, exports: Path) -> dict[str, Any]:
    """The agent's tool calls, in the order the skill's geographic-research workflow gives."""
    seen: dict[str, Any] = {}
    register = await call(
        client,
        "register_source",
        title="Sample Town health facility register",
        type="official_government",
        url_or_ref="https://register.example.org/health-facilities.csv",
        publisher="Sample Town health department",
        licence="CC-BY-4.0",
        attribution="Sample Town health department",
        evaluation=rating(3),
    )
    page = await call(
        client,
        "register_source",
        title="Southgate Health Centre: about us",
        type="official_organisation",
        url_or_ref="https://southgate.example.org/about",
        licence="CC-BY-4.0",
        evaluation=rating(2),
    )
    register_id, page_id = register["source"]["id"], page["source"]["id"]
    seen["sources"] = {"register": register_id, "page": page_id}

    fetched = await call(client, "fetch_source_data", source_id=register_id)
    assert fetched["loadable"] is True
    await call(client, "fetch_source_data", source_id=page_id)

    listed = await call(client, "create_dataset", source_id=register_id, name="register")
    seen["register"] = listed["dataset"]
    researched = await call(
        client,
        "create_dataset",
        name="researched",
        records=[
            {
                "id": "southgate",
                "name": "Southgate Health Centre",
                "category": "amenity=clinic",
                "address": {"full": "8 South Street, Sample Town"},
                "evidence_ids": ["ev-southgate"],
                "attributes": {
                    "opened": {"value": 2021, "basis": "fact", "evidence_ids": ["ev-southgate"]}
                },
            }
        ],
        evidence=[
            {
                "id": "ev-southgate",
                "source_id": page_id,
                "supports": ["name", "address", "attributes.opened"],
                "excerpt": "Southgate Health Centre, 8 South Street, opened in 2021",
            }
        ],
    )
    seen["researched"] = researched
    mapped = await call(
        client,
        "search_osm_features",
        area=AREA,
        tags={"amenity": ["hospital", "clinic"]},
        provider="fixture",
        name="mapped",
    )
    combined = await call(
        client,
        "create_dataset",
        dataset_ids=[dataset_id(listed), dataset_id(researched), dataset_id(mapped)],
        name="combined",
    )
    seen["combined"] = combined["dataset"]
    normal = await call(client, "normalize_records", dataset_id=dataset_id(combined))
    merged = await call(client, "deduplicate_entities", dataset_id=dataset_id(normal))
    seen["merged"] = merged["dataset"]
    located = await call(
        client,
        "geocode_records",
        dataset_id=dataset_id(merged),
        bias=AREA,
        country_codes=["zz"],
    )
    buildings = await call(client, "get_building_footprints", area=AREA, provider="fixture")
    matched = await call(
        client,
        "match_building_footprints",
        dataset_id=dataset_id(located),
        buildings_dataset_id=dataset_id(buildings),
    )
    scored = await call(client, "score_confidence", dataset_id=dataset_id(matched))
    validated = await call(client, "validate_dataset", dataset_id=dataset_id(scored), area=AREA)
    seen["validated"] = validated
    seen["report"] = await call(
        client,
        "generate_quality_report",
        report_dataset_id=dataset_id(validated),
        name="sample-town-health-quality",
        copy_to=str(exports),
    )
    for fmt in ("geojson", "csv"):
        seen[fmt] = await call(
            client,
            "export_geo_dataset",
            dataset_id=dataset_id(scored),
            format=fmt,
            name="sample-town-health",
            copy_to=str(exports),
        )
    # The user, later, loads the export back in.
    seen["reloaded"] = await call(
        client,
        "create_dataset",
        file=str(exports / "sample-town-health.geojson"),
        name="reloaded",
    )
    return seen


@pytest.fixture
def run(state: ServerState, tmp_path: Path) -> dict[str, Any]:
    exports = tmp_path / "exports"
    seen: dict[str, Any] = {}

    async def go() -> None:
        async with Client(create_server(state)) as client:
            seen.update(await research(client, exports))

    anyio.run(go)
    seen["exports"] = exports
    return seen


def validator(name: str) -> Draft202012Validator:
    schema = json.loads((SCHEMAS / f"{name}.schema.json").read_text(encoding="utf-8"))
    return Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)


def records_of(collection: dict[str, Any]) -> dict[str, Record]:
    return {f["id"]: Record.from_feature(f) for f in collection["features"]}


def by_name(records: dict[str, Record]) -> dict[str | None, list[Record]]:
    """Records grouped by name; a name kept on two records lists both."""
    named: dict[str | None, list[Record]] = {}
    for record in records.values():
        named.setdefault(record.name, []).append(record)
    return named


def match_status(record: Record) -> str | None:
    return record.building_match.status.value if record.building_match else None


def test_the_workflow_reaches_the_ground_truth(run: dict[str, Any]) -> None:
    assert (run["register"]["item_count"], run["register"]["rejected"]) == (3, 1)
    assert "1 not found" not in str(run["researched"]), "the excerpt is on the fetched page"
    assert run["combined"]["item_count"] == 3 + 1 + 5
    assert run["merged"]["item_count"] == 9, "no two records share a site identifier or address"

    collection = json.loads((run["exports"] / "sample-town-health.geojson").read_text("utf-8"))
    records = records_of(collection)
    named = by_name(records)
    assert {name: len(found) for name, found in named.items()} == {
        "Sample General Hospital": 2,
        "Sample General Hosp.": 1,
        "Northside Hospital": 1,
        "Riverside Clinic": 2,
        "Hilltop Clinic": 1,
        "Eastgate Clinic": 1,
        "Southgate Health Centre": 1,
    }

    # The map's two records of the hospital share only a website: one organisation, two
    # records, each marked as a possible duplicate of the other, each with its own beds.
    [general] = [r for r in named["Sample General Hospital"] if r.id != "REG-1"]
    [abbreviated] = named["Sample General Hosp."]
    assert general.organisation_id is not None
    assert general.organisation_id == abbreviated.organisation_id
    for record, other in ((general, abbreviated), (abbreviated, general)):
        assert not record.merged
        assert not record.conflicts
        assert any(
            x.startswith(f"Possible duplicate of {other.id} (same website; ")
            and x.endswith("which do not identify a site).")
            for x in record.limitations
        ), record.id
    assert {general.attributes["beds"].value, abbreviated.attributes["beds"].value} == {110, 120}

    # The register repeats a mapped hospital and clinic by name and position alone. Nothing
    # confirms it, so each pair is kept apart and marked for review on both records.
    [clinic] = [r for r in named["Riverside Clinic"] if r.id != "REG-4"]
    for listed, mapped in ((records["REG-1"], general), (records["REG-4"], clinic)):
        assert not listed.merged, listed.id
        for record, other in ((listed, mapped), (mapped, listed)):
            assert any(
                x.startswith(f"Possible duplicate of {other.id} (") and "not merged" in x
                for x in record.limitations
            ), record.id

    [eastgate] = named["Eastgate Clinic"]
    assert eastgate.geometry is not None
    assert eastgate.geometry.model_dump()["coordinates"] == [0.009, 0.0021]
    assert eastgate.location.method == "geocode:nominatim"
    assert eastgate.location.kind.value == "address_point"

    southgate = records["southgate"]
    assert southgate.geometry is None, "no position is made up"
    assert southgate.status.value == "unresolved"
    assert any("no confident result" in x for x in southgate.limitations)
    assert any(x.startswith("Building match: ") for x in southgate.limitations)
    assert southgate.confidence is not None
    assert southgate.confidence.level.value == "unresolved"

    assert match_status(general) == match_status(records["REG-1"]) == "exact"
    assert match_status(abbreviated) == "exact"
    assert [match_status(r) for r in named["Northside Hospital"]] == ["probable"]
    assert [match_status(r) for r in named["Riverside Clinic"]] == ["ambiguous", "ambiguous"]
    assert [match_status(r) for r in named["Hilltop Clinic"]] == ["none"]
    for name in ("Northside Hospital", "Riverside Clinic", "Hilltop Clinic"):
        for record in named[name]:
            assert any(x.startswith("Building match: ") for x in record.limitations), record.id
    assert not any(x.startswith("Building match: ") for x in general.limitations)


def test_validation_and_the_report_name_the_open_problems(run: dict[str, Any]) -> None:
    validated = run["validated"]
    assert 0 < validated["score"] < 1
    checks = Counter(f["check"] for f in validated["findings"])
    assert checks["missing_geometry"] == 1
    assert checks["conflicts"] == 0, "nothing merged, so no values disagree within a record"
    duplicates = sorted(
        (f["record_id"], f["message"]) for f in validated["findings"] if f["check"] == "duplicates"
    )
    # Every pair kept apart for review is flagged again: the hospital's three records and
    # the register's repeat of Riverside Clinic.
    assert duplicates == [
        ("fixture:poi-01", "same name as REG-1 within 25 m"),
        ("fixture:poi-02", "same name as REG-1 within 25 m"),
        ("fixture:poi-02", "same name as fixture:poi-01 within 25 m"),
        ("fixture:poi-04", "same name as REG-4 within 25 m"),
    ]
    report = json.loads(
        (run["exports"] / "sample-town-health-quality.json").read_text(encoding="utf-8")
    )
    assert not list(validator("quality-report").iter_errors(report))
    markdown = (run["exports"] / "sample-town-health-quality.md").read_text(encoding="utf-8")
    assert "Southgate Health Centre" in markdown or "southgate" in markdown


def test_the_geojson_export_is_valid_and_carries_its_provenance(run: dict[str, Any]) -> None:
    path = run["exports"] / "sample-town-health.geojson"
    collection = json.loads(path.read_text(encoding="utf-8"))

    # RFC 7946: a FeatureCollection in WGS 84, with no crs member.
    assert collection["type"] == "FeatureCollection"
    assert "crs" not in collection
    record_schema = validator("record")
    for feature in collection["features"]:
        assert feature["type"] == "Feature"
        assert set(feature) == {"type", "id", "geometry", "properties"}
        as_record = {**feature["properties"], "id": feature["id"]}
        if feature["geometry"] is not None:
            as_record["geometry"] = feature["geometry"]
        assert not list(record_schema.iter_errors(as_record)), feature["id"]

    records = records_of(collection)
    sources = {s["id"]: s for s in collection["sources"]}
    evidence = {e["id"]: e for e in collection["evidence"]}
    for record in records.values():
        assert record.confidence is not None, record.id
        assert record.confidence.reasons, record.id
        cited = set(record.evidence_ids) | set(record.location.evidence_ids)
        for attribute in record.attributes.values():
            cited |= set(attribute.evidence_ids)
        assert cited <= set(evidence), record.id
    assert {e["source_id"] for e in evidence.values()} <= set(sources)
    # Every listed source is used: cited by evidence, or the footprints a building match names.
    named = {
        f"buildings via provider '{ref.provider}'"
        for r in records.values()
        if r.building_match
        for ref in [
            *([r.building_match.building_ref] if r.building_match.building_ref else []),
            *(c.ref for c in r.building_match.candidates),
        ]
    }
    unused = {
        sid
        for sid, s in sources.items()
        if sid not in {e["source_id"] for e in evidence.values()}
        and s.get("coverage_note") not in named
    }
    assert not unused, "a listed source is neither cited nor named by a building match"
    assert set(run["sources"].values()) <= set(sources)
    assert {s.get("licence") for s in sources.values()} >= {"CC-BY-4.0", "CC0-1.0", "ODbL-1.0"}
    checks = {
        e["excerpt_check"] for e in evidence.values() if e["source_id"] == run["sources"]["page"]
    }
    assert checks == {"verified"}

    manifest = json.loads(
        (run["exports"] / "sample-town-health.manifest.json").read_text(encoding="utf-8")
    )
    assert not list(validator("dataset-manifest").iter_errors(manifest))
    assert manifest["record_count"] == len(records)
    [entry] = manifest["files"]
    assert entry["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert entry["size_bytes"] == path.stat().st_size
    licences = {n["licence"] for n in manifest["licences"]}
    assert {"CC-BY-4.0", "ODbL-1.0"} <= licences
    west, south, east, north = manifest["bbox"]
    assert AREA_BBOX[0] <= west <= east <= AREA_BBOX[2]
    assert AREA_BBOX[1] <= south <= north <= AREA_BBOX[3]


def test_an_independent_checker_finds_no_errors(run: dict[str, Any]) -> None:
    """The skill's standard-library checker agrees: only the unresolved record lacks a place."""
    path = run["exports"] / "sample-town-health.geojson"
    result = subprocess.run(
        [sys.executable, "-I", str(CHECKER), str(path), "--bbox", BBOX_TEXT],
        capture_output=True,
        text=True,
        check=False,
    )
    report = json.loads(result.stdout)
    errors = [f for f in report["findings"] if f["severity"] == "error"]
    assert result.returncode == 0, errors
    missing = [f["item"] for f in report["findings"] if f["check"] == "missing"]
    assert missing == ["southgate"]


def test_the_csv_export_has_one_row_per_record(run: dict[str, Any]) -> None:
    raw = (run["exports"] / "sample-town-health.csv").read_bytes()
    assert raw.startswith("﻿".encode())
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
    collection = json.loads(
        (run["exports"] / "sample-town-health.geojson").read_text(encoding="utf-8")
    )
    assert [r["id"] for r in rows] == [f["id"] for f in collection["features"]]
    header = list(rows[0])
    assert header[: len(FIXED_COLUMNS)] == list(FIXED_COLUMNS)
    assert {"attributes.beds", "attributes.opened"} <= set(header)
    southgate = next(r for r in rows if r["id"] == "southgate")
    assert (southgate["lon"], southgate["lat"]) == ("", "")
    assert "no confident result" in southgate["limitations"]


def test_the_export_loads_back_unchanged(run: dict[str, Any], state: ServerState) -> None:
    reloaded = run["reloaded"]["dataset"]
    assert reloaded["rejected"] == 0
    collection = json.loads(
        (run["exports"] / "sample-town-health.geojson").read_text(encoding="utf-8")
    )
    data = state.datasets.load(reloaded["dataset_id"])
    exported = records_of(collection)
    assert {r.id: r for r in data.records} == exported  # type: ignore[union-attr]
