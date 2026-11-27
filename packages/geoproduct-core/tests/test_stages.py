"""The built-in stages, one at a time, with a standalone context.

Provider data comes from the fixture provider (Sample Town, synthetic); other inputs are
small invented records near 0°, 0°.
"""

import csv
import json
from pathlib import Path
from typing import Any

import pytest

from geoproduct.engine import BuildingSet, FileSet, RecordSet, StageDef, StandaloneContext
from geoproduct.engine.stage import StageResult
from geoproduct.errors import GeoProductError
from geoproduct.providers.base import ProviderInfo, UsagePolicy
from geoproduct.providers.fixture import AREA_BBOX, FixtureProvider
from geoproduct.providers.registry import ProviderRegistry, default_providers
from geoproduct.schemas import (
    ConfidenceLevel,
    ErrorCode,
    Evidence,
    LocationKind,
    MatchStatus,
    QualityReport,
    Record,
    RecordStatus,
    Source,
    SourceType,
)
from geoproduct.stages import builtin_registry
from geoproduct.workspace import Workspace

AREA = {"bbox": list(AREA_BBOX)}
STAGES = {d.name: d for d in builtin_registry()}


class Env:
    def __init__(self, root: Path, providers: ProviderRegistry | None = None) -> None:
        self.workspace = Workspace(root / "ws").ensure()
        self.root = root
        self.providers = providers or default_providers()
        self.runs = 0

    def ctx(self, **refs: Any) -> StandaloneContext:
        self.runs += 1
        out = self.root / "out" / str(self.runs)
        out.mkdir(parents=True)
        return StandaloneContext(
            self.workspace, out, providers=self.providers, refs=refs, stage_id=f"s{self.runs}"
        )

    def run(
        self,
        stage_name: str,
        data: Any = None,
        refs: dict[str, Any] | None = None,
        /,
        **params: Any,
    ) -> StageResult:
        definition: StageDef[Any] = STAGES[stage_name]
        return definition(self.ctx(**(refs or {})), data, definition.params.model_validate(params))

    def records(
        self,
        stage_name: str,
        data: Any = None,
        refs: dict[str, Any] | None = None,
        /,
        **params: Any,
    ) -> RecordSet:
        result = self.run(stage_name, data, refs, **params)
        assert isinstance(result.data, RecordSet)
        return result.data


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return Env(tmp_path)


def by_id(data: RecordSet) -> dict[str, Record]:
    return {r.id: r for r in data.records}


def point(lon: float, lat: float) -> dict[str, Any]:
    return {"type": "Point", "coordinates": [lon, lat]}


def square(lon: float, lat: float, size: float = 0.0002) -> dict[str, Any]:
    ring = [[lon, lat], [lon + size, lat], [lon + size, lat + size], [lon, lat + size], [lon, lat]]
    return {"type": "Polygon", "coordinates": [ring]}


SOURCE = Source(id="src_test", title="Test list", type=SourceType.user_file, url_or_ref="file:x")


def recordset(*records: dict[str, Any], cited: bool = True) -> RecordSet:
    built, evidence = [], []
    for data in records:
        record = Record.model_validate(data)
        if cited:
            ev = Evidence(id=f"ev_{record.id}", source_id=SOURCE.id, supports=["name"])
            record = record.model_copy(update={"evidence_ids": [ev.id]})
            evidence.append(ev)
        built.append(record)
    return RecordSet(records=built, sources=[SOURCE], evidence=evidence)


# Collection ---------------------------------------------------------------------------------


def test_collect_records_validates_each_record(env: Env) -> None:
    result = env.run(
        "collect.records",
        records=[{"id": "a", "name": "A"}, {"id": "", "name": "B"}, {"id": "a", "name": "C"}],
    )
    assert [r.id for r in result.data.records] == ["a"]
    assert [r.reason for r in result.rejects][1] == "duplicate id"
    assert result.rejects[0].reason.startswith("id:")


def test_collect_file_reads_a_csv_and_cites_every_row(env: Env) -> None:
    path = env.workspace.root / "places.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["id", "name", "longitude", "latitude", "beds"])
        writer.writerow(["p1", "Example Clinic", "0.001", "0.002", "12"])
        writer.writerow(["p2", "Bad Row", "500", "0.002", ""])
        writer.writerow(["", "No Id", "", "", ""])
    result = env.run("collect.file", path=str(path), licence="CC0-1.0", title="Places")

    data = result.data
    assert [r.id for r in data.records] == ["p1", "places:row-3"]
    assert "outside -180..180" in result.rejects[0].reason
    clinic = data.records[0]
    assert clinic.geometry is not None
    assert clinic.attributes["beds"].value == "12"
    assert clinic.attributes["beds"].evidence_ids == clinic.evidence_ids
    assert data.sources[0].licence == "CC0-1.0"
    assert data.sources[0].type == SourceType.user_file


def test_collect_file_refuses_paths_outside_the_allowed_folders(env: Env) -> None:
    outside = env.root / "elsewhere.csv"
    outside.write_text("name\nx\n", encoding="utf-8")
    with pytest.raises(GeoProductError) as caught:
        env.run("collect.file", path=str(outside))
    assert caught.value.code == ErrorCode.path_not_allowed


def test_collect_file_refuses_geojson_in_another_crs(env: Env) -> None:
    path = env.workspace.root / "projected.geojson"
    path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "crs": {"type": "name", "properties": {"name": "EPSG:3857"}},
                "features": [],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(GeoProductError) as caught:
        env.run("collect.file", path=str(path))
    assert "WGS 84" in caught.value.error.message


def test_collect_features_cites_the_provider(env: Env) -> None:
    data = env.records(
        "collect.features", provider="fixture", area=AREA, tags={"amenity": "hospital"}
    )
    assert sorted(r.id for r in data.records) == [
        "fixture:poi-01",
        "fixture:poi-02",
        "fixture:poi-03",
    ]
    assert all(r.category == "amenity=hospital" for r in data.records)
    assert {s.licence for s in data.sources} == {"CC0-1.0"}
    cited = {e.id for e in data.evidence}
    assert all(set(r.evidence_ids) <= cited for r in data.records)


def test_an_empty_answer_adds_no_source(env: Env) -> None:
    """Nothing cites an empty answer, so it must not add a source or a licence duty."""
    result = env.run("collect.features", provider="fixture", area=AREA, tags={"amenity": "zoo"})
    assert result.data.records == []
    assert result.data.sources == []
    assert [p.provider for p in result.providers] == ["fixture"]


def test_truncated_provider_results_are_an_error_unless_allowed(env: Env) -> None:
    query = {"provider": "fixture", "area": AREA, "tags": {"highway": "bus_stop"}, "limit": 2}
    with pytest.raises(GeoProductError) as caught:
        env.run("collect.features", **query)
    assert caught.value.code == ErrorCode.too_large
    result = env.run("collect.features", **query, allow_truncated=True)
    assert len(result.data.records) == 2
    assert any("truncated" in note or "limit" in note for note in result.notes)


def test_unknown_provider_lists_the_available_ones(env: Env) -> None:
    with pytest.raises(GeoProductError) as caught:
        env.run("collect.buildings", provider="nowhere", area=AREA)
    assert caught.value.code == ErrorCode.not_found
    assert caught.value.error.details["available"] == ["fixture"]


# Preparation --------------------------------------------------------------------------------


def test_normalize_tidies_text_maps_categories_and_rounds(env: Env) -> None:
    data = recordset(
        {
            "id": "a",
            "name": "  Example   Clinic ",
            "aliases": ["Example Clinic", "The Clinic"],
            "category": "Amenity=Clinic",
            "geometry": point(0.123456789123, 0.1),
        }
    )
    out = env.records(
        "normalize.records", data, categories={"amenity=clinic": "health.clinic"}
    ).records[0]
    assert out.name == "Example Clinic"
    assert out.aliases == ["The Clinic"]
    assert out.category == "health.clinic"
    assert out.geometry is not None
    assert out.geometry.model_dump()["coordinates"][0] == 0.1234568


def test_combine_appends_records_and_rejects_repeated_ids(env: Env) -> None:
    first = recordset({"id": "a", "name": "A"})
    second = recordset({"id": "a", "name": "A again"}, {"id": "b", "name": "B"})
    result = env.run("combine.records", first, {"two": second}, add=[{"stage": "two"}])
    assert [r.id for r in result.data.records] == ["a", "b"]
    assert result.rejects[0].record_id == "a"
    assert result.input_count == 3


def test_the_fixture_hospital_records_share_only_a_website(env: Env) -> None:
    """Sample Town has two hospital records 11 m apart, "Sample General Hospital" and
    "Sample General Hosp.". They share a website, which an organisation's sites can share,
    and no address, register code or other site identifier. Nothing shows they are one
    site, so they stay two records: one organisation, marked for review."""
    hospitals = env.records(
        "collect.features", provider="fixture", area=AREA, tags={"amenity": "hospital"}
    )
    result = env.run("resolve.deduplicate", hospitals)
    records = by_id(result.data)
    assert sorted(records) == ["fixture:poi-01", "fixture:poi-02", "fixture:poi-03"]
    first, second = records["fixture:poi-01"], records["fixture:poi-02"]
    assert not first.merged
    assert not second.merged
    assert first.organisation_id is not None
    assert first.organisation_id == second.organisation_id
    assert records["fixture:poi-03"].organisation_id is None
    assert [first.attributes["beds"].value, second.attributes["beds"].value] == [110, 120]
    assert not first.conflicts
    [line] = possible_of(first)
    assert line == (
        "Possible duplicate of fixture:poi-02 (same website; name similarity 1.00; 11 m apart; "
        "not merged because they share only organisation-level details (website), which do "
        "not identify a site)."
    )
    assert possible_of(second)[0].startswith("Possible duplicate of fixture:poi-01 (")
    assert result.notes == [
        "merged 0 duplicate record(s)",
        "linked 1 multi-site organisation(s)",
        "kept 1 possible duplicate pair(s) apart for review",
    ]


def test_a_site_identifier_merges_and_keeps_disagreements(env: Env) -> None:
    """With a site-level identifier, such as a health register code, the same two records
    merge, and their bed counts are both kept."""
    hospitals = env.records(
        "collect.features", provider="fixture", area=AREA, tags={"amenity": "hospital"}
    )
    register = {"register": "HOSP-0001"}
    with_code = hospitals.model_copy(
        update={
            "records": [
                r.model_copy(update={"external_ids": {**r.external_ids, **register}})
                if r.id in {"fixture:poi-01", "fixture:poi-02"}
                else r
                for r in hospitals.records
            ]
        }
    )
    merged = env.records("resolve.deduplicate", with_code, site_keys=["register"])
    assert len(merged.records) == 2
    hospital = next(r for r in merged.records if r.merged)
    assert hospital.name == "Sample General Hospital"
    assert "same register" in hospital.merged[0].reasons
    assert hospital.status == RecordStatus.conflicting
    assert [c.field for c in hospital.conflicts] == ["attributes.beds"]
    assert hospital.attributes["beds"].value is None
    assert len(hospital.evidence_ids) == 2


def test_far_apart_sites_with_one_website_are_linked_not_merged(env: Env) -> None:
    offices = env.records("collect.features", provider="fixture", area=AREA, tags={"office": None})
    result = env.records("resolve.deduplicate", offices)
    assert len(result.records) == 2
    organisations = {r.organisation_id for r in result.records}
    assert len(organisations) == 1
    assert None not in organisations


def test_merging_keeps_the_fuller_spelling_of_a_website(env: Env) -> None:
    data = recordset(
        {
            "id": "a",
            "name": "Example Clinic",
            "geometry": point(0.003, 0.003),
            "external_ids": {"website": "example.org", "wikidata": "Q4115189"},
        },
        {
            "id": "b",
            "name": "Example Clinic",
            "geometry": point(0.00301, 0.003),
            "external_ids": {"website": "https://www.example.org/", "wikidata": "Q4115189"},
        },
    )
    merged = env.records("resolve.deduplicate", data, wikidata_sites=[SITE_ITEM]).records
    assert len(merged) == 1
    assert merged[0].external_ids == {
        "website": "https://www.example.org/",
        "wikidata": "Q4115189",
    }


STEP = 0.0009
"""About 100 m east-west near 0°, 0°."""

SITE_ITEM = "Q4115189"
"""An invented Wikidata item the tests name in ``wikidata_sites``: one establishment."""


def clinic(rid: str, east: float, **fields: Any) -> dict[str, Any]:
    """An "Example Clinic" ``east`` steps of about 100 m east of a fixed point."""
    where = point(0.003 + east * STEP, 0.003)
    return {"id": rid, "name": "Example Clinic", "geometry": where, **fields}


def possible_of(record: Record) -> list[str]:
    return [x for x in record.limitations if x.startswith("Possible duplicate of ")]


def test_a_chain_of_nearby_same_name_records_is_not_merged(env: Env) -> None:
    """Five branches 100 m apart: each is near its neighbours, the ends are 400 m apart. A
    similar name nearby confirms nothing, and a chain of near pairs never joins its ends."""
    data = recordset(*(clinic(f"c{k}", k) for k in range(5)))
    result = env.run("resolve.deduplicate", data)
    records = by_id(result.data)
    assert sorted(records) == ["c0", "c1", "c2", "c3", "c4"]
    assert not any(r.merged for r in records.values())
    assert [len(possible_of(records[f"c{k}"])) for k in range(5)] == [1, 2, 2, 2, 1]
    [line] = possible_of(records["c0"])
    assert line == (
        "Possible duplicate of c1 (name similarity 1.00; 100 m apart; not merged because no "
        "site-level identifier or matching address confirms it)."
    )
    assert "merged 0 duplicate record(s)" in result.notes
    assert "kept 4 possible duplicate pair(s) apart for review" in result.notes


def test_a_merged_group_needs_every_pair_to_match(env: Env) -> None:
    """A shares a Wikidata item known to name a site with B, and B an address with C, but
    nothing confirms A and C are one place. The stronger pair merges; C stays apart, marked
    for review on both."""
    address = {"house_number": "1", "street": "Hospital Road"}
    data = recordset(
        clinic("a", 0, external_ids={"wikidata": SITE_ITEM}),
        clinic("b", 0.1, external_ids={"wikidata": SITE_ITEM}, address=address),
        clinic("c", 0.4, address=address),
    )
    merged, kept = env.records("resolve.deduplicate", data, wikidata_sites=[SITE_ITEM]).records
    assert (merged.id, kept.id) == ("a", "c")
    assert [m.record_id for m in merged.merged] == ["b"]
    assert "same wikidata" in merged.merged[0].reasons
    assert not kept.merged
    for record, other in ((kept, "a"), (merged, "c")):
        [line] = possible_of(record)
        assert line.startswith(f"Possible duplicate of {other} (same address; ")
        assert line.endswith(
            "not merged because the records merged with one of them do not all match the other)."
        )


def test_records_that_all_match_each_other_still_merge(env: Env) -> None:
    wikidata = {"external_ids": {"wikidata": SITE_ITEM}}
    data = recordset(*(clinic(rid, k * 0.1, **wikidata) for k, rid in enumerate("abc")))
    [merged] = env.records("resolve.deduplicate", data, wikidata_sites=[SITE_ITEM]).records
    assert sorted(m.record_id for m in merged.merged) == ["b", "c"]
    assert not possible_of(merged)


@pytest.mark.parametrize(
    ("first", "second", "params", "reason"),
    [
        (
            {"external_ids": {"wikidata": SITE_ITEM}},
            {"external_ids": {"wikidata": SITE_ITEM}},
            {"wikidata_sites": [SITE_ITEM]},
            "same wikidata",
        ),
        (
            {"external_ids": {"register": "HF-0042"}},
            {"external_ids": {"register": "HF-0042"}},
            {"site_keys": ["register"]},
            "same register",
        ),
        (
            {"external_ids": {"register": "HF-0042"}},
            {"external_ids": {"register": "HF-0042"}},
            {"id_keys": ["website", "register"]},
            "same register",
        ),
        (
            {"address": {"house_number": "1", "street": "Hospital Road"}},
            {"address": {"full": "1 Hospital Rd"}},
            {},
            "same address",
        ),
    ],
    ids=["wikidata-site-item", "site-key", "older-id-keys", "address"],
)
def test_a_site_identifier_or_address_confirms_a_duplicate(
    env: Env, first: dict[str, Any], second: dict[str, Any], params: dict[str, Any], reason: str
) -> None:
    data = recordset(clinic("a", 0, **first), clinic("b", 0.2, **second))
    [merged] = env.records("resolve.deduplicate", data, **params).records
    [merge] = merged.merged
    assert reason in merge.reasons
    assert not possible_of(merged)


@pytest.mark.parametrize(
    ("first", "second", "why"),
    [
        (
            {"external_ids": {"wikidata": "Q1000001"}, "address": {"full": "1 Hospital Road"}},
            {"external_ids": {"wikidata": "Q1000002"}, "address": {"full": "1 Hospital Road"}},
            "they differ in wikidata",
        ),
        (
            {"external_ids": {"website": "example.org"}, "address": {"full": "1 Hospital Road"}},
            {"external_ids": {"website": "example.org"}, "address": {"full": "9 Hospital Road"}},
            "they differ in address",
        ),
        (
            {"address": {"house_number": "1", "street": "Hospital Road", "postcode": "AB1 2CD"}},
            {"address": {"house_number": "1", "street": "Hospital Road", "postcode": "XY9 8ZZ"}},
            "they differ in address",
        ),
    ],
    ids=["wikidata", "address", "postcode"],
)
def test_conflicting_evidence_keeps_records_apart_for_review(
    env: Env, first: dict[str, Any], second: dict[str, Any], why: str
) -> None:
    data = recordset(clinic("a", 0, **first), clinic("b", 0.2, **second))
    result = env.run("resolve.deduplicate", data)
    a, b = result.data.records
    assert not a.merged
    assert not b.merged
    [line] = possible_of(a)
    assert line.startswith("Possible duplicate of b (")
    assert line.endswith(f"not merged because {why}).")
    assert possible_of(b)[0].startswith("Possible duplicate of a (")
    assert "kept 1 possible duplicate pair(s) apart for review" in result.notes


def test_different_organisation_details_do_not_keep_one_address_apart(env: Env) -> None:
    """One street address, two records: a direct line and a central booking line, two
    websites. Phone numbers and websites are organisation-level, so they do not show two
    sites. The records merge and both values are kept."""
    address = {"house_number": "1", "street": "Hospital Road"}
    data = recordset(
        clinic("a", 0, address=address, external_ids={"phone": "020 7946 0958"}),
        clinic(
            "b",
            0.2,
            address=address,
            external_ids={"phone": "0300 123 4567", "website": "example.org"},
        ),
    )
    result = env.run("resolve.deduplicate", data)
    [merged] = result.data.records
    assert merged.merged[0].reasons[:2] == ["same address", "different phone"]
    assert merged.external_ids == {
        "phone": "020 7946 0958",
        "phone~2": "0300 123 4567",
        "website": "example.org",
    }
    assert result.notes[0] == "merged 1 duplicate record(s)"


def test_branches_sharing_a_wikidata_item_of_unknown_meaning_are_reported(env: Env) -> None:
    """Three branches 60 m apart carry one Wikidata item. Nothing says whether it names the
    establishment or the chain, so they are linked, not merged, and the stage says how to
    classify it."""
    item = {"external_ids": {"wikidata": "Q1000001"}}
    data = recordset(*(clinic(rid, k * 0.6, **item) for k, rid in enumerate("abc")))
    result = env.run("resolve.deduplicate", data)
    assert len(result.data.records) == 3
    assert result.notes == [
        "merged 0 duplicate record(s)",
        "linked 1 multi-site organisation(s)",
        "kept 3 possible duplicate pair(s) apart for review",
        "merged nothing on 1 shared Wikidata item(s) of unknown meaning: list items that name "
        "one establishment in wikidata_sites, or organisations in wikidata_organisations",
    ]
    classified = env.run("resolve.deduplicate", data, wikidata_organisations=["Q1000001"])
    assert classified.notes[:3] == result.notes[:3]
    assert len(classified.notes) == 3
    one_site = env.records("resolve.deduplicate", data, wikidata_sites=["Q1000001"])
    assert len(one_site.records) == 1


@pytest.mark.parametrize(
    "params",
    [{}, {"id_keys": ["website", "phone", "email", "wikidata"]}, {"site_keys": ["register"]}],
    ids=["defaults", "older-id-keys", "site-keys-only"],
)
def test_an_organisation_identifier_links_but_never_merges(
    env: Env, params: dict[str, Any]
) -> None:
    shared = {"external_ids": {"website": "example.org", "phone": "+44 20 7946 0958"}}
    data = recordset(clinic("a", 0, **shared), clinic("b", 0.2, **shared))
    result = env.run("resolve.deduplicate", data, **params)
    a, b = result.data.records
    assert not a.merged
    assert a.organisation_id is not None
    assert a.organisation_id == b.organisation_id
    assert result.notes == [
        "merged 0 duplicate record(s)",
        "linked 1 multi-site organisation(s)",
        "kept 1 possible duplicate pair(s) apart for review",
    ]


def test_a_key_cannot_be_both_site_and_organisation_level() -> None:
    params = STAGES["resolve.deduplicate"].params
    with pytest.raises(ValueError, match="website cannot be both"):
        params.model_validate({"site_keys": ["website"], "organisation_keys": ["website"]})
    settings = params.model_validate({"site_keys": ["website", "register"]}).settings()
    assert settings.site_keys == ("website", "register")
    assert settings.organisation_keys == ("phone", "email", "brand:wikidata", "operator:wikidata")


@pytest.mark.parametrize(
    ("given", "error"),
    [
        ({"site_keys": ["wikidata"]}, "wikidata cannot be a site key"),
        (
            {"site_keys": ["brand:wikidata"], "organisation_keys": ["phone"]},
            "brand:wikidata cannot be a site key",
        ),
        (
            {"wikidata_sites": ["Q1"], "wikidata_organisations": ["q1"]},
            "Q1 cannot be both in wikidata_sites and in wikidata_organisations",
        ),
        ({"wikidata_sites": ["Hospital"]}, "String should match pattern"),
        ({"wikidata_organisations": ["Q0"]}, "String should match pattern"),
    ],
    ids=["wikidata-site-key", "brand-site-key", "item-in-both-lists", "not-an-item", "q0"],
)
def test_wikidata_items_are_classified_one_by_one(given: dict[str, Any], error: str) -> None:
    params = STAGES["resolve.deduplicate"].params
    with pytest.raises(ValueError, match=error):
        params.model_validate(given)


def test_the_older_id_keys_list_leaves_wikidata_unclassified() -> None:
    params = STAGES["resolve.deduplicate"].params
    settings = params.model_validate(
        {"id_keys": ["website", "register", "wikidata"], "wikidata_sites": [" q42 "]}
    ).settings()
    assert settings.site_keys == ("register",)
    assert settings.organisation_keys == ("website",)
    assert settings.wikidata_sites == frozenset({"Q42"})


def test_a_spread_site_identifier_is_reported(env: Env) -> None:
    brand = {"external_ids": {"wikidata": SITE_ITEM}}
    data = recordset(clinic("a", 0, **brand), clinic("b", 20, **brand))
    result = env.run("resolve.deduplicate", data)
    assert len(result.data.records) == 2
    assert result.notes[-1] == (
        "compared 1 identifier value(s) as organisation-level: records at distinct sites (more "
        "than 150 m apart, at different addresses, or with different site identifiers) carry each"
    )


def test_a_record_lists_at_most_five_possible_duplicates(env: Env) -> None:
    data = recordset(*(clinic(f"c{k}", k * 0.1) for k in range(7)))
    lines = possible_of(by_id(env.records("resolve.deduplicate", data))["c0"])
    assert len(lines) == 6
    assert lines[-1] == "Possible duplicate of 1 more record(s)."


def test_geocode_locates_addresses_and_leaves_misses_unresolved(env: Env) -> None:
    data = recordset(
        {"id": "a", "name": "Corner Market", "address": {"full": "9 Third Avenue"}},
        {"id": "b", "name": "Nowhere Hall", "address": {"full": "4 Fifth Avenue"}},
        {"id": "c", "name": "Has A Place", "geometry": point(0.003, 0.003)},
    )
    result = env.run("geocode.records", data, provider="fixture")
    records = by_id(result.data)
    located = records["a"]
    assert located.location.kind == LocationKind.address_point
    assert located.location.method == "geocode:fixture"
    assert len(located.location.evidence_ids) == 1
    assert records["b"].geometry is None
    assert "gave no confident result" in records["b"].limitations[0]
    assert records["c"].geometry == data.records[2].geometry
    assert result.notes == ["located 1 of 2 record(s) needing a location"]


def test_geocode_respects_a_provider_batch_limit(tmp_path: Path) -> None:
    class Limited(FixtureProvider):
        @property
        def info(self) -> ProviderInfo:
            return super().info.model_copy(update={"policy": UsagePolicy(max_batch=1)})

    env = Env(tmp_path, ProviderRegistry([Limited()]))
    data = recordset({"id": "a", "name": "Corner Market"}, {"id": "b", "name": "Example Bank"})
    with pytest.raises(GeoProductError) as caught:
        env.run("geocode.records", data, provider="fixture")
    assert caught.value.code == ErrorCode.policy_refused


# Spatial ------------------------------------------------------------------------------------


@pytest.fixture
def town(env: Env) -> dict[str, Any]:
    health = env.records(
        "collect.features", provider="fixture", area=AREA, tags={"amenity": ["hospital", "clinic"]}
    )
    buildings = env.run("collect.buildings", provider="fixture", area=AREA).data
    assert isinstance(buildings, BuildingSet)
    return {"health": env.records("resolve.deduplicate", health), "buildings": buildings}


def test_building_matches_say_how_sure_they_are(env: Env, town: dict[str, Any]) -> None:
    matched = env.run(
        "match.buildings",
        town["health"],
        {"b": town["buildings"]},
        buildings={"stage": "b"},
    ).data
    status = {r.name: r.building_match.status for r in matched.records if r.building_match}
    assert status == {
        "Sample General Hospital": MatchStatus.exact,
        "Sample General Hosp.": MatchStatus.exact,
        "Northside Hospital": MatchStatus.probable,
        "Riverside Clinic": MatchStatus.ambiguous,
        "Hilltop Clinic": MatchStatus.none,
    }
    riverside = next(r for r in matched.records if r.name == "Riverside Clinic")
    assert riverside.building_match is not None
    assert riverside.building_match.building_ref is None
    assert len(riverside.building_match.candidates) >= 2


def test_what_a_building_match_leaves_open_is_a_limitation(env: Env, town: dict[str, Any]) -> None:
    def match(data: RecordSet) -> RecordSet:
        out = env.run("match.buildings", data, {"b": town["buildings"]}, buildings={"stage": "b"})
        assert isinstance(out.data, RecordSet)
        return out.data

    def gaps(data: RecordSet) -> dict[str | None, list[str]]:
        return {
            r.name: [x for x in r.limitations if x.startswith("Building match: ")]
            for r in data.records
        }

    once = match(town["health"])
    found = gaps(once)
    assert found["Sample General Hospital"] == []
    assert found["Northside Hospital"] == [
        "Building match: probably fixture:b-231, but no name, address or identifier confirms it."
    ]
    assert found["Riverside Clinic"] == [
        "Building match: ambiguous between fixture:b-010 and fixture:b-011; none was chosen."
    ]
    assert found["Hilltop Clinic"] == [
        "Building match: no building footprint was found near the point."
    ]
    assert gaps(match(once)) == found, "a second match replaces the line rather than adding one"


def test_footprints_are_credited_only_when_a_record_names_one(
    env: Env, town: dict[str, Any]
) -> None:
    def sources(data: RecordSet) -> set[str]:
        out = env.run("match.buildings", data, {"b": town["buildings"]}, buildings={"stage": "b"})
        assert isinstance(out.data, RecordSet)
        return {s.id for s in out.data.sources}

    footprints = {s.id for s in town["buildings"].sources}
    assert footprints <= sources(town["health"])
    unplaced = recordset({"id": "x", "name": "Nowhere Clinic"})
    assert not footprints & sources(unplaced)


def test_matched_footprints_can_replace_the_geometry(env: Env, town: dict[str, Any]) -> None:
    matched = env.run(
        "match.buildings",
        town["health"],
        {"b": town["buildings"]},
        buildings={"stage": "b"},
        assign_geometry="exact",
    ).data
    hospital = next(r for r in matched.records if r.name == "Sample General Hospital")
    assert hospital.geometry is not None
    assert hospital.geometry.type == "Polygon"
    assert hospital.location.kind == LocationKind.building_footprint
    cited = {e.id: e for e in matched.evidence}
    assert any("geometry" in cited[e].supports for e in hospital.location.evidence_ids)
    clinic = next(r for r in matched.records if r.name == "Hilltop Clinic")
    assert clinic.geometry is not None
    assert clinic.geometry.type == "Point"


def test_an_assigned_footprint_is_no_surer_than_its_match(env: Env, town: dict[str, Any]) -> None:
    """A probable match's footprint must not score like a confirmed one (found in Stage E)."""

    def northside_score(assign: str) -> float:
        matched = env.run(
            "match.buildings",
            town["health"],
            {"b": town["buildings"]},
            buildings={"stage": "b"},
            assign_geometry=assign,
        ).data
        assert isinstance(matched, RecordSet)
        scored = by_id(env.records("quality.confidence", matched))
        northside = next(r for r in scored.values() if r.name == "Northside Hospital")
        assert northside.confidence is not None
        assert northside.confidence.score is not None
        return northside.confidence.score

    assert northside_score("exact_or_probable") == pytest.approx(northside_score("none"))


def test_enrichment_adds_estimates_with_notes(env: Env) -> None:
    places = recordset(
        {"id": "p", "name": "Site", "geometry": square(0.0, 0.0)},
        {"id": "q", "name": "Far", "geometry": point(0.05, 0.05)},
    )
    stops = recordset(
        {"id": "s1", "name": "Stop 1", "geometry": point(0.0003, 0.0001)},
        {"id": "s2", "name": "Stop 2", "geometry": point(0.003, 0.0)},
    )
    zones = recordset({"id": "z", "name": "Zone A", "geometry": square(-0.001, -0.001, 0.002)})

    nearby = env.records(
        "enrich.nearby", places, {"s": stops}, reference={"stage": "s"}, name="stop"
    )
    value = by_id(nearby)["p"].attributes["nearby_stop"]
    assert value.basis == "estimate"
    assert value.note
    assert value.value == {
        "count": 2,
        "nearest_m": pytest.approx(11.1, abs=0.2),
        "ids": ["s1", "s2"],
    }
    assert by_id(nearby)["q"].attributes["nearby_stop"].value["count"] == 0  # type: ignore[index]

    within = env.records(
        "enrich.within", places, {"z": zones}, reference={"stage": "z"}, name="zone"
    )
    assert by_id(within)["p"].attributes["within_zone"].value == ["Zone A"]
    assert by_id(within)["q"].attributes["within_zone"].value == []

    measured = by_id(env.records("enrich.measure", places))
    # 0.0002° is about 22.26 m at the equator.
    assert measured["p"].attributes["area_m2"].value == pytest.approx(495.4, rel=0.01)
    assert measured["p"].attributes["perimeter_m"].value == pytest.approx(89.1, rel=0.01)
    assert "area_m2" not in measured["q"].attributes


# Quality ------------------------------------------------------------------------------------


def test_confidence_counts_only_sources_that_describe_the_record(env: Env) -> None:
    geocoder = Source(id="src_geo", title="Geocoder", type=SourceType.public_api, url_or_ref="x")
    second = Source(id="src_two", title="Second list", type=SourceType.user_file, url_or_ref="y")
    evidence = [
        Evidence(id="e1", source_id="src_test", supports=["name"]),
        Evidence(id="e2", source_id="src_geo", supports=["geometry"]),
        Evidence(id="e3", source_id="src_two", supports=["name", "category"]),
    ]
    located = {"geometry": point(0.001, 0.001), "location": {"kind": "address_point"}}
    data = RecordSet(
        records=[
            Record.model_validate({"id": "geo", "evidence_ids": ["e1", "e2"], **located}),
            Record.model_validate({"id": "two", "evidence_ids": ["e1", "e3"], **located}),
            Record.model_validate({"id": "none", "evidence_ids": ["e1"]}),
        ],
        sources=[SOURCE, geocoder, second],
        evidence=evidence,
    )
    scored = by_id(env.records("quality.confidence", data))
    assert scored["geo"].confidence is not None
    assert scored["two"].confidence is not None
    assert "one source describes it" in scored["geo"].confidence.reasons
    assert "2 independent sources describe it" in scored["two"].confidence.reasons
    assert scored["two"].confidence.score > scored["geo"].confidence.score  # type: ignore[operator]
    assert scored["none"].status == RecordStatus.unresolved
    assert scored["none"].confidence.level == ConfidenceLevel.unresolved  # type: ignore[union-attr]


def report_of(env: Env, data: RecordSet, refs: dict[str, Any] | None = None, **params: Any):
    report = env.run("validate.dataset", data, refs, **params).data
    assert isinstance(report, QualityReport)
    return report


def findings(report: QualityReport) -> set[tuple[str, str | None, str]]:
    return {(f.check, f.record_id, f.severity.value) for f in report.findings}


def test_validation_finds_coordinate_and_geometry_problems(env: Env) -> None:
    bowtie = {
        "type": "Polygon",
        "coordinates": [
            [[0.003, 0.003], [0.004, 0.004], [0.004, 0.003], [0.003, 0.004], [0.003, 0.003]]
        ],
    }
    unclosed = {
        "type": "Polygon",
        "coordinates": [[[0.003, 0.003], [0.004, 0.003], [0.004, 0.004], [0.003, 0.004]]],
    }
    data = recordset(
        {"id": "ok", "name": "Fine", "geometry": point(0.002, 0.001)},
        {"id": "projected", "name": "Metres", "geometry": point(500_000, 4_200_000)},
        {"id": "range", "name": "Too far", "geometry": point(181, 1)},
        {"id": "nullisland", "name": "Zero", "geometry": point(0, 0)},
        {"id": "bowtie", "name": "Twisted", "geometry": bowtie},
        {"id": "short", "name": "Open", "geometry": unclosed},
        {"id": "missing", "name": "Nowhere"},
    )
    report = report_of(env, data, area={"bbox": [-0.01, -0.01, 0.01, 0.01]})
    found = findings(report)
    assert ("crs", "projected", "error") in found
    assert ("coordinates", "range", "error") in found
    assert ("coordinates", "nullisland", "warning") in found
    assert ("geometry", "bowtie", "error") in found
    assert ("geometry", "short", "error") in found
    assert ("missing_geometry", "missing", "warning") in found
    assert ("within_area", "projected", "error") in found
    assert not any(record == "ok" for _, record, _ in found)
    assert report.metrics.record_count == 7
    assert 0 < report.score < 1


def test_validation_finds_duplicates_outliers_and_missing_evidence(env: Env) -> None:
    data = recordset(
        {"id": "a", "name": "Example Clinic", "geometry": point(0.001, 0.001)},
        {"id": "b", "name": "Example  Clinic", "geometry": point(0.00105, 0.001)},
        {"id": "c", "name": "Depot", "geometry": point(0.002, 0.002)},
        {"id": "d", "name": "Far Away", "geometry": point(0.5, 0.5)},
    )
    uncited = Record.model_validate(
        {
            "id": "e",
            "name": "Unsupported",
            "geometry": point(0.0015, 0.0015),
            "attributes": {"beds": {"value": 3, "basis": "fact"}},
            "evidence_ids": ["ev_missing"],
        }
    )
    data = data.model_copy(update={"records": [*data.records, uncited]})
    found = findings(report_of(env, data))
    assert ("duplicates", "b", "warning") in found
    assert ("outliers", "d", "warning") in found
    assert ("evidence", "e", "error") in found
    report = report_of(env, data)
    assert report.metrics.evidence_coverage == 0.0


def test_spatial_rules(env: Env) -> None:
    street = recordset(
        {
            "id": "street",
            "name": "Main Street",
            "geometry": {"type": "LineString", "coordinates": [[0, 0], [0.01, 0]]},
        }
    )
    park = recordset({"id": "park", "name": "Park", "geometry": square(0.004, 0.0005, 0.001)})
    stops = recordset(
        {"id": "on", "category": "stop", "name": "On", "geometry": point(0.002, 0.0001)},
        {"id": "off", "category": "stop", "name": "Off", "geometry": point(0.002, 0.002)},
        {"id": "park", "category": "stop", "name": "Park", "geometry": point(0.0045, 0.001)},
    )
    rules = [
        {"name": "near-street", "kind": "near", "reference": {"stage": "s"}, "max_distance_m": 30},
        {"name": "no-parks", "kind": "not_within", "reference": {"stage": "p"}},
        {"name": "in-park", "kind": "within", "reference": {"stage": "p"}, "severity": "warning"},
    ]
    found = findings(report_of(env, stops, {"s": street, "p": park}, rules=rules, checks=["rules"]))
    assert found == {
        ("rules", "off", "error"),
        ("rules", "park", "error"),
        ("rules", "on", "warning"),
        ("rules", "off", "warning"),
    }


def test_quality_gate(env: Env) -> None:
    data = recordset(
        {"id": "a", "name": "A", "geometry": point(0.001, 0.001)},
        {"id": "b", "name": "B"},
    )
    report = report_of(env, data)
    passed = env.run("quality.gate", report, max_unresolved_share=1.0).data
    assert passed.notes[-1].startswith("quality gate passed")
    with pytest.raises(GeoProductError) as caught:
        env.run("quality.gate", report, min_score=0.99, fail_on_checks=["missing_geometry"])
    error = caught.value.error
    assert error.code == ErrorCode.quality_gate_failed
    assert len(error.details["violations"]) == 1  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="at least one condition"):
        STAGES["quality.gate"].params.model_validate({})


# Output -------------------------------------------------------------------------------------


def files_of(result: StageResult) -> FileSet:
    assert isinstance(result.data, FileSet)
    return result.data


def test_geojson_export_carries_provenance_and_a_manifest(env: Env) -> None:
    data = recordset({"id": "a", "name": "A", "geometry": point(0.001, 0.002)})
    data = data.model_copy(update={"sources": [SOURCE.model_copy(update={"licence": "CC0-1.0"})]})
    ctx = env.ctx()
    definition = STAGES["export.geojson"]
    files = files_of(definition(ctx, data, definition.params.model_validate({"name": "places"})))

    assert [f.path for f in files.files] == ["places.geojson", "places.manifest.json"]
    content = json.loads((ctx.output_dir / "places.geojson").read_text(encoding="utf-8"))
    assert content["type"] == "FeatureCollection"
    assert content["features"][0]["id"] == "a"
    assert content["sources"][0]["id"] == "src_test"
    assert content["evidence"][0]["id"] == "ev_a"
    manifest = json.loads((ctx.output_dir / "places.manifest.json").read_text(encoding="utf-8"))
    assert manifest["record_count"] == 1
    assert manifest["bbox"] == [0.001, 0.002, 0.001, 0.002]
    assert manifest["licences"][0]["licence"] == "CC0-1.0"
    assert manifest["files"][0]["sha256"] == files.files[0].sha256


def test_copies_never_silently_replace_different_files(env: Env) -> None:
    data = recordset({"id": "a", "name": "A"})
    target = env.workspace.root / "exports"
    target.mkdir()
    (target / "out.csv").write_text("someone's data\n", encoding="utf-8")
    params = {"name": "out", "copy_to": str(target)}

    with pytest.raises(GeoProductError) as caught:
        env.run("export.csv", data, **params)
    assert caught.value.code == ErrorCode.confirmation_required
    assert (target / "out.csv").read_text(encoding="utf-8") == "someone's data\n"

    files = files_of(env.run("export.csv", data, **params, overwrite=True))
    assert files.copies == [str(target / "out.csv")]
    files_of(env.run("export.csv", data, **params))  # identical content: nothing to confirm

    with pytest.raises(GeoProductError) as caught:
        env.run("export.csv", data, name="out", copy_to=str(env.root / "elsewhere"))
    assert caught.value.code == ErrorCode.path_not_allowed


def test_csv_export_escapes_formulas_but_not_numbers(env: Env) -> None:
    data = recordset(
        {
            "id": "a",
            "name": '=HYPERLINK("http://example.invalid")',
            "geometry": point(0.001, 0.002),
            "attributes": {
                "phone": {"value": "+1 555 0100", "basis": "estimate"},
                "note": {"value": "@SUM(1)", "basis": "estimate"},
                "delta": {"value": -3.5, "basis": "estimate"},
                "tags": {"value": ["a", "b"], "basis": "estimate"},
            },
        }
    )
    ctx = env.ctx()
    definition = STAGES["export.csv"]
    definition(ctx, data, definition.params.model_validate({"name": "x"}))
    raw = (ctx.output_dir / "x.csv").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    rows = list(csv.DictReader(raw.decode("utf-8-sig").splitlines()))
    row = rows[0]
    assert row["name"].startswith("'=")
    assert row["attributes.note"] == "'@SUM(1)"
    assert row["attributes.phone"] == "+1 555 0100"
    assert row["attributes.delta"] == "-3.5"
    assert row["attributes.tags"] == '["a","b"]'
    assert (row["lon"], row["lat"]) == ("0.001", "0.002")


def test_quality_report_is_written_as_markdown_and_json(env: Env) -> None:
    data = recordset({"id": "pipe", "name": "Pipe"}, cited=False)
    report = report_of(env, data, name="demo | draft")
    ctx = env.ctx()
    definition = STAGES["report.quality"]
    definition(ctx, report, definition.params.model_validate({"name": "demo"}))
    markdown = (ctx.output_dir / "demo.md").read_text(encoding="utf-8")
    assert markdown.startswith("# Quality report: demo \\| draft")
    assert "| evidence | pipe | no evidence for this record |" in markdown.replace("| warning ", "")
    saved = json.loads((ctx.output_dir / "demo.json").read_text(encoding="utf-8"))
    assert QualityReport.model_validate(saved) == report
