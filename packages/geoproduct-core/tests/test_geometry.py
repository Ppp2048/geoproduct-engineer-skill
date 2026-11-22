from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from geoproduct.schemas import Area, Geometry, GeometryCollection, Point, Polygon

GEOMETRY = TypeAdapter(Geometry)


@pytest.mark.parametrize(
    ("data", "model"),
    [
        ({"type": "Point", "coordinates": [0.1, 0.2]}, "Point"),
        ({"type": "Point", "coordinates": [0.1, 0.2, 15.0]}, "Point"),
        ({"type": "MultiPoint", "coordinates": [[0, 0], [1, 1]]}, "MultiPoint"),
        ({"type": "LineString", "coordinates": [[0, 0], [1, 1]]}, "LineString"),
        ({"type": "MultiLineString", "coordinates": [[[0, 0], [1, 1]]]}, "MultiLineString"),
        (
            {"type": "MultiPolygon", "coordinates": [[[[0, 0], [1, 0], [0, 1], [0, 0]]]]},
            "MultiPolygon",
        ),
    ],
)
def test_each_geometry_type_parses_to_its_model(data: dict[str, Any], model: str) -> None:
    assert type(GEOMETRY.validate_python(data)).__name__ == model


def test_geometry_collection_nests(square: dict[str, Any]) -> None:
    data = {
        "type": "GeometryCollection",
        "geometries": [{"type": "Point", "coordinates": [0, 0]}, square],
    }
    parsed = GEOMETRY.validate_python(data)
    assert isinstance(parsed, GeometryCollection)
    assert isinstance(parsed.geometries[1], Polygon)


@pytest.mark.parametrize(
    "coordinates",
    [[0.1], [0.1, 0.2, 3.0, 4.0], [float("nan"), 0.0], [0.0, float("inf")], ["a", "b"]],
)
def test_bad_positions_are_rejected(coordinates: list[Any]) -> None:
    with pytest.raises(ValidationError):
        Point(coordinates=coordinates)


def test_wrong_nesting_is_rejected() -> None:
    with pytest.raises(ValidationError):
        GEOMETRY.validate_python({"type": "Polygon", "coordinates": [[0, 0], [1, 1]]})


def test_unknown_type_is_rejected() -> None:
    with pytest.raises(ValidationError):
        GEOMETRY.validate_python({"type": "Circle", "coordinates": [0, 0]})


def test_shape_checks_only_an_unclosed_ring_still_loads() -> None:
    """Semantic problems are for validation to report, so broken data can be inspected."""
    polygon = Polygon(coordinates=[[[0, 0], [1, 0], [1, 1]]])
    assert polygon.coordinates[0][-1] != polygon.coordinates[0][0]


class TestArea:
    def test_bbox(self) -> None:
        assert Area(bbox=(0.0, 0.0, 0.01, 0.01)).bbox == (0.0, 0.0, 0.01, 0.01)

    def test_bbox_crossing_the_antimeridian_is_allowed(self) -> None:
        assert Area(bbox=(179.9, -1.0, -179.9, 1.0)).bbox is not None

    def test_bbox_south_above_north_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="south"):
            Area(bbox=(0.0, 1.0, 0.01, 0.5))

    def test_bbox_out_of_range_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Area(bbox=(0.0, 0.0, 181.0, 0.01))

    def test_point_with_radius(self) -> None:
        area = Area(point=(0.0, 0.0), radius_m=500)
        assert area.radius_m == 500

    def test_polygon(self, square: dict[str, Any]) -> None:
        assert isinstance(Area.model_validate({"polygon": square}).polygon, Polygon)

    def test_a_line_is_not_an_area(self) -> None:
        with pytest.raises(ValidationError):
            Area.model_validate(
                {"polygon": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]}}
            )

    @pytest.mark.parametrize(
        "data",
        [
            {},
            {"bbox": (0, 0, 1, 1), "point": (0, 0), "radius_m": 10},
            {"point": (0, 0)},
            {"bbox": (0, 0, 1, 1), "radius_m": 10},
        ],
    )
    def test_exactly_one_form(self, data: dict[str, Any]) -> None:
        with pytest.raises(ValidationError, match=r"exactly one|together"):
            Area.model_validate(data)

    @pytest.mark.parametrize("radius", [0, -5, 50_001])
    def test_radius_bounds(self, radius: float) -> None:
        with pytest.raises(ValidationError):
            Area(point=(0.0, 0.0), radius_m=radius)

    def test_unknown_fields_are_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Extra inputs"):
            Area.model_validate({"bbox": (0, 0, 1, 1), "bbbox": (0, 0, 1, 1)})
