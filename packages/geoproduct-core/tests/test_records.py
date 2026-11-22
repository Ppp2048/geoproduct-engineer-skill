from typing import Any

import pytest
from pydantic import ValidationError

from geoproduct.schemas import Basis, BuildingMatch, MatchStatus, Record, RecordStatus

REF = {"provider": "fixture", "id": "b-1"}
CANDIDATE = {"ref": REF, "distance_m": 4.0, "contains_point": False, "score": 0.5}


def test_full_record_parses(record_data: dict[str, Any]) -> None:
    record = Record.model_validate(record_data)
    assert record.attributes["height_m"].basis == Basis.estimate
    assert record.building_match is not None
    assert record.building_match.status == MatchStatus.exact


def test_defaults_describe_an_unresolved_record() -> None:
    record = Record(id="rec_1")
    assert record.status == RecordStatus.unresolved
    assert record.location.kind == "unknown"
    assert record.geometry is None


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        Record.model_validate({"id": "rec_1", "nmae": "typo"})


def test_country_code_is_upper_case_alpha_2() -> None:
    with pytest.raises(ValidationError):
        Record.model_validate({"id": "rec_1", "address": {"country_code": "zz"}})


@pytest.mark.parametrize("bad_id", ["", "-leading-dash", "has space", "x" * 201])
def test_identifier_rules(bad_id: str) -> None:
    with pytest.raises(ValidationError):
        Record(id=bad_id)


class TestBuildingMatch:
    @pytest.mark.parametrize("status", ["exact", "probable"])
    def test_chosen_match_needs_a_building(self, status: str) -> None:
        with pytest.raises(ValidationError, match="must name its building_ref"):
            BuildingMatch(status=status, method="m", reasons=["r"])

    @pytest.mark.parametrize("status", ["ambiguous", "approximate_point", "none"])
    def test_unchosen_match_must_not_name_a_building(self, status: str) -> None:
        with pytest.raises(ValidationError, match="must not name a building_ref"):
            BuildingMatch.model_validate(
                {
                    "status": status,
                    "building_ref": REF,
                    "method": "m",
                    "reasons": ["r"],
                    "candidates": [CANDIDATE, CANDIDATE],
                }
            )

    def test_ambiguous_lists_at_least_two_candidates(self) -> None:
        with pytest.raises(ValidationError, match="at least two"):
            BuildingMatch.model_validate(
                {"status": "ambiguous", "method": "m", "reasons": ["r"], "candidates": [CANDIDATE]}
            )

    def test_every_decision_is_explained(self) -> None:
        with pytest.raises(ValidationError):
            BuildingMatch(status="none", method="m", reasons=[])

    def test_no_match_is_valid(self) -> None:
        match = BuildingMatch(status="none", method="spatial", reasons=["no footprint within 50 m"])
        assert match.building_ref is None


class TestConflicts:
    def test_conflicting_record_lists_its_conflicts(self) -> None:
        with pytest.raises(ValidationError, match="must list its conflicts"):
            Record(id="rec_1", status="conflicting")

    def test_a_conflict_has_at_least_two_values(self) -> None:
        with pytest.raises(ValidationError):
            Record.model_validate(
                {
                    "id": "rec_1",
                    "status": "conflicting",
                    "conflicts": [{"field": "name", "values": [{"value": "A"}]}],
                }
            )

    def test_values_are_kept_side_by_side(self) -> None:
        record = Record.model_validate(
            {
                "id": "rec_1",
                "status": "conflicting",
                "conflicts": [
                    {
                        "field": "attributes.beds",
                        "values": [
                            {"value": 12, "evidence_ids": ["ev_1"]},
                            {"value": 20, "evidence_ids": ["ev_2"]},
                        ],
                    }
                ],
            }
        )
        assert [v.value for v in record.conflicts[0].values] == [12, 20]


class TestGeoJSON:
    def test_round_trip(self, record_data: dict[str, Any]) -> None:
        record = Record.model_validate(record_data)
        feature = record.to_feature()
        assert feature["type"] == "Feature"
        assert feature["id"] == "rec_example_clinic"
        assert feature["geometry"]["type"] == "Polygon"
        assert "id" not in feature["properties"]
        assert "geometry" not in feature["properties"]
        assert Record.from_feature(feature) == record

    def test_null_values_inside_attributes_survive(self) -> None:
        record = Record.model_validate(
            {"id": "rec_1", "attributes": {"hours": {"value": {"mon": None, "tue": "9-17"}}}}
        )
        assert Record.from_feature(record.to_feature()) == record

    def test_record_without_geometry(self) -> None:
        feature = Record(id="rec_1", name="Unlocated").to_feature()
        assert feature["geometry"] is None
        assert Record.from_feature(feature).name == "Unlocated"

    def test_id_may_come_from_properties(self) -> None:
        feature = {"type": "Feature", "geometry": None, "properties": {"id": "rec_9"}}
        assert Record.from_feature(feature).id == "rec_9"

    def test_rejects_non_features(self) -> None:
        with pytest.raises(ValueError, match="not a GeoJSON Feature"):
            Record.from_feature({"type": "FeatureCollection", "features": []})
