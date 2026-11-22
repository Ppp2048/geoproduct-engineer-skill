"""GeoJSON geometries (RFC 7946) and query areas.

Geometry models check *shape* only: the geometry type, how deeply coordinates are nested,
and that every position has two or three finite numbers. Semantic rules (closed rings,
minimum vertex counts, coordinate ranges, self-intersection) are reported by validation
instead, so a dataset with a broken geometry can still be loaded, inspected and repaired.

Query areas are different: they are parameters, so they are checked strictly.
"""

from typing import Annotated, Literal

from pydantic import AfterValidator, Field, model_validator

from geoproduct.schemas._base import Contract, ShortText

Coordinate = Annotated[float, Field(allow_inf_nan=False)]
Position = Annotated[list[Coordinate], Field(min_length=2, max_length=3)]
"""``[longitude, latitude]`` or ``[longitude, latitude, elevation]`` in WGS 84."""

Longitude = Annotated[float, Field(ge=-180.0, le=180.0, allow_inf_nan=False)]
Latitude = Annotated[float, Field(ge=-90.0, le=90.0, allow_inf_nan=False)]


class Point(Contract):
    type: Literal["Point"] = "Point"
    coordinates: Position


class MultiPoint(Contract):
    type: Literal["MultiPoint"] = "MultiPoint"
    coordinates: list[Position]


class LineString(Contract):
    type: Literal["LineString"] = "LineString"
    coordinates: list[Position]


class MultiLineString(Contract):
    type: Literal["MultiLineString"] = "MultiLineString"
    coordinates: list[list[Position]]


class Polygon(Contract):
    """Rings of positions: the first is the outer ring, any others are holes."""

    type: Literal["Polygon"] = "Polygon"
    coordinates: list[list[Position]]


class MultiPolygon(Contract):
    type: Literal["MultiPolygon"] = "MultiPolygon"
    coordinates: list[list[list[Position]]]


class GeometryCollection(Contract):
    type: Literal["GeometryCollection"] = "GeometryCollection"
    geometries: list["Geometry"]


Geometry = Annotated[
    Point | MultiPoint | LineString | MultiLineString | Polygon | MultiPolygon | GeometryCollection,
    Field(discriminator="type"),
]
GeometryCollection.model_rebuild()

PolygonalGeometry = Annotated[Polygon | MultiPolygon, Field(discriminator="type")]


def _south_not_above_north(
    bbox: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    if bbox[1] > bbox[3]:
        raise ValueError("bbox south must not be greater than north")
    return bbox


BBox = Annotated[
    tuple[Longitude, Latitude, Longitude, Latitude], AfterValidator(_south_not_above_north)
]
"""``[west, south, east, north]``. West may exceed east for a box crossing the antimeridian."""


class Area(Contract):
    """Where to look. Give exactly one of ``bbox``, ``polygon``, or ``point`` with ``radius_m``."""

    name: ShortText | None = None
    bbox: BBox | None = None
    polygon: PolygonalGeometry | None = None
    point: tuple[Longitude, Latitude] | None = None
    radius_m: Annotated[float, Field(gt=0, le=50_000, allow_inf_nan=False)] | None = None

    @model_validator(mode="after")
    def _exactly_one_form(self) -> "Area":
        forms = sum(x is not None for x in (self.bbox, self.polygon, self.point))
        if forms != 1:
            raise ValueError("give exactly one of bbox, polygon, or point with radius_m")
        if (self.point is None) != (self.radius_m is None):
            raise ValueError("point and radius_m must be given together")
        return self
