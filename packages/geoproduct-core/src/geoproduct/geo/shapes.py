"""Converting between the GeoJSON contract models and shapely geometries."""

from typing import Any

import shapely
from pydantic import TypeAdapter
from shapely.geometry import box, mapping, shape
from shapely.geometry.base import BaseGeometry

from geoproduct.geo.metric import circle
from geoproduct.schemas import Area, Geometry, Record

_GEOMETRY: TypeAdapter[Any] = TypeAdapter(Geometry)


def to_shape(geometry: Any) -> BaseGeometry:
    """A shapely geometry from a contract model or a GeoJSON mapping."""
    data = geometry.model_dump(mode="json") if hasattr(geometry, "model_dump") else geometry
    return shape(data)


def from_shape(geom: BaseGeometry) -> Geometry:
    return _GEOMETRY.validate_python(_lists(mapping(geom)))  # type: ignore[no-any-return]


def record_shape(record: Record) -> BaseGeometry | None:
    if record.geometry is None:
        return None
    try:
        geom = to_shape(record.geometry)
    except (ValueError, TypeError, AttributeError, shapely.errors.GEOSException):
        return None
    return None if geom.is_empty else geom


def anchor(geom: BaseGeometry) -> tuple[float, float]:
    """One representative position: the point itself, or a point guaranteed inside a shape."""
    point = geom if geom.geom_type == "Point" else geom.representative_point()
    return (float(point.x), float(point.y))  # type: ignore[attr-defined]


def area_shape(area: Area) -> BaseGeometry:
    """The region an :class:`Area` describes, in longitude and latitude."""
    if area.bbox is not None:
        west, south, east, north = area.bbox
        if west <= east:
            return box(west, south, east, north)
        return shapely.union(box(west, south, 180.0, north), box(-180.0, south, east, north))
    if area.polygon is not None:
        return to_shape(area.polygon)
    assert area.point is not None
    assert area.radius_m is not None
    return circle(area.point[0], area.point[1], area.radius_m)


def _lists(value: Any) -> Any:
    """shapely's mapping() uses tuples; the contract models expect lists."""
    if isinstance(value, dict):
        return {key: _lists(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_lists(item) for item in value]
    return value
