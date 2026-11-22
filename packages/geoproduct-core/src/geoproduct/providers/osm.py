"""Turning OpenStreetMap elements and tags into records, footprints and addresses.

Used by the Nominatim and Overpass providers. Nothing here guesses: a value that cannot be
read from the tags as stated (a height of "about 12 m", a country that is not a two-letter
code) is left out of the typed fields and kept only as the raw tag.
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Final

import shapely
from shapely.geometry import LineString, MultiLineString, MultiPoint, Point, Polygon
from shapely.geometry.base import BaseGeometry

from geoproduct.geo.shapes import from_shape
from geoproduct.providers.base import BuildingFootprint
from geoproduct.schemas import Address, LocationKind, Record

LICENCE: Final = "ODbL-1.0"
ATTRIBUTION: Final = "© OpenStreetMap contributors"
COPYRIGHT_URL: Final = "https://www.openstreetmap.org/copyright"

CATEGORY_KEYS: Final = (
    "amenity",
    "shop",
    "office",
    "healthcare",
    "tourism",
    "leisure",
    "craft",
    "emergency",
    "railway",
    "aeroway",
    "highway",
    "public_transport",
    "man_made",
    "historic",
    "power",
    "building",
    "landuse",
    "natural",
    "place",
    "boundary",
)
"""Which tag names a record's category, in order of preference after the queried keys."""

AREA_KEYS: Final = frozenset(
    {
        "aeroway",
        "amenity",
        "boundary",
        "building",
        "building:part",
        "craft",
        "healthcare",
        "historic",
        "landuse",
        "leisure",
        "man_made",
        "military",
        "natural",
        "office",
        "place",
        "public_transport",
        "shop",
        "tourism",
    }
)
LINE_VALUES: Final = {"natural": {"coastline", "tree_row", "cliff", "ridge"}}
ID_TAGS: Final = {
    "website": ("website", "contact:website", "url"),
    "phone": ("phone", "contact:phone"),
    "email": ("email", "contact:email"),
    "wikidata": ("wikidata",),
}
ALIAS_TAGS: Final = ("official_name", "alt_name", "short_name", "old_name")
_NUMBER = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(m|metres|meters)?\s*$")


def osm_id(element: Mapping[str, Any]) -> str:
    return f"{element['type']}/{element['id']}"


def is_area(tags: Mapping[str, str], closed: bool) -> bool:
    """Whether a closed way describes an area rather than a ring-shaped line."""
    if not closed or tags.get("area") == "no":
        return False
    if tags.get("area") == "yes":
        return True
    return any(key in tags and tags[key] not in LINE_VALUES.get(key, ()) for key in AREA_KEYS)


def _coords(points: Iterable[Mapping[str, Any]]) -> list[tuple[float, float]]:
    return [(float(p["lon"]), float(p["lat"])) for p in points if p is not None]


def _rings(members: Sequence[Mapping[str, Any]], role: str) -> list[Polygon]:
    lines = [
        LineString(coords)
        for m in members
        if m.get("type") == "way"
        and m.get("role", "") == role
        and len(coords := _coords(m.get("geometry") or [])) >= 2
    ]
    if not lines:
        return []
    merged = shapely.line_merge(shapely.union_all(lines))
    found = shapely.polygonize(list(shapely.get_parts(merged)))
    return [p for p in shapely.get_parts(found) if isinstance(p, Polygon)]


def _polygonal(shape: BaseGeometry) -> BaseGeometry | None:
    """Only the polygon parts of a shape; repairing a ring can leave stray lines or points."""
    parts = [
        part
        for part in getattr(shape, "geoms", [shape])
        if part.geom_type in ("Polygon", "MultiPolygon") and not part.is_empty
    ]
    return shapely.union_all(parts) if parts else None


def element_shape(element: Mapping[str, Any]) -> BaseGeometry | None:
    """The geometry of an element returned with ``out geom``, or ``None`` if it has none."""
    tags = element.get("tags") or {}
    match element.get("type"):
        case "node":
            if "lat" not in element:
                return None
            return Point(float(element["lon"]), float(element["lat"]))
        case "way":
            coords = _coords(element.get("geometry") or [])
            if len(coords) < 2:
                return None
            closed = len(coords) >= 4 and coords[0] == coords[-1]
            return Polygon(coords) if is_area(tags, closed) else LineString(coords)
        case "relation":
            members = element.get("members") or []
            if tags.get("type") in ("multipolygon", "boundary"):
                outers = _rings(members, "outer") or _rings(members, "")
                if not outers:
                    return None
                shape = shapely.union_all(outers)
                inners = _rings(members, "inner")
                if inners:
                    shape = shape.difference(shapely.union_all(inners))
                return _polygonal(shapely.make_valid(shape))
            lines = [
                LineString(coords)
                for m in members
                if m.get("type") == "way" and len(coords := _coords(m.get("geometry") or [])) >= 2
            ]
            if lines:
                return MultiLineString(lines)
            points = [
                Point(float(m["lon"]), float(m["lat"]))
                for m in members
                if m.get("type") == "node" and "lat" in m
            ]
            return MultiPoint(points) if points else None
    return None


def rounded(shape: BaseGeometry) -> dict[str, Any]:
    """GeoJSON for a shape, with coordinates rounded to 7 decimals (about 1 cm)."""
    return from_shape(shapely.set_precision(shape, 1e-7)).model_dump(mode="json")


def address(tags: Mapping[str, str]) -> Address | None:
    country = tags.get("addr:country", "").strip().upper()
    fields = {
        "full": tags.get("addr:full"),
        "house_number": tags.get("addr:housenumber"),
        "street": tags.get("addr:street") or tags.get("addr:place"),
        "locality": tags.get("addr:city") or tags.get("addr:town") or tags.get("addr:village"),
        "region": tags.get("addr:state") or tags.get("addr:province"),
        "postcode": tags.get("addr:postcode"),
        "country_code": country if re.fullmatch(r"[A-Z]{2}", country) else None,
    }
    fields = {k: v.strip()[:200] for k, v in fields.items() if v and v.strip()}
    return Address.model_validate(fields) if fields else None


def category(tags: Mapping[str, str], preferred: Sequence[str] = ()) -> tuple[str, str] | None:
    for key in (*preferred, *CATEGORY_KEYS):
        if tags.get(key):
            return key, tags[key]
    return None


def number(text: str | None) -> float | None:
    """A plain number, optionally in metres; anything else (ranges, feet, "approx") is None."""
    if text is None or not (match := _NUMBER.match(text)):
        return None
    return float(match.group(1))


def to_record(
    element: Mapping[str, Any], shape: BaseGeometry, preferred_keys: Sequence[str] = ()
) -> Record:
    tags: dict[str, str] = {str(k): str(v) for k, v in (element.get("tags") or {}).items()}
    ref = osm_id(element)
    found = category(tags, preferred_keys)
    used = {"name", *ALIAS_TAGS}
    if found:
        used.add(found[0])
    external = {"osm": ref}
    for key, names in ID_TAGS.items():
        value = next((tags[n] for n in names if tags.get(n)), None)
        if value and len(value) <= 200:
            external[key] = value
    aliases = [
        alias.strip()[:200]
        for key in ALIAS_TAGS
        for alias in tags.get(key, "").split(";")
        if alias.strip()
    ]
    attributes = {
        key[:200]: {"value": value, "basis": "fact"}
        for key, value in sorted(tags.items())
        if key not in used and not key.startswith("addr:")
    }
    kind = LocationKind.centroid
    if shape.geom_type in ("Polygon", "MultiPolygon") and "building" in tags:
        kind = LocationKind.building_footprint
    elif shape.geom_type == "Point" and "addr:housenumber" in tags:
        kind = LocationKind.address_point
    name = tags.get("name")
    return Record.model_validate(
        {
            "id": f"osm:{ref}",
            "name": name[:200] if name else None,
            "aliases": list(dict.fromkeys(aliases)),
            "category": f"{found[0]}={found[1]}"[:200] if found else None,
            "external_ids": external,
            "address": address(tags),
            "attributes": attributes,
            "geometry": rounded(shape),
            "location": {"kind": kind, "method": f"osm:{element['type']}"},
        }
    )


def to_footprint(element: Mapping[str, Any], shape: BaseGeometry) -> BuildingFootprint | None:
    if shape.geom_type not in ("Polygon", "MultiPolygon"):
        return None
    tags: dict[str, str] = {str(k): str(v) for k, v in (element.get("tags") or {}).items()}
    name = tags.get("name")
    return BuildingFootprint.model_validate(
        {
            "ref": {"provider": "osm", "id": osm_id(element)},
            "geometry": rounded(shape),
            "name": name[:200] if name else None,
            "address": address(tags),
            "levels": number(tags.get("building:levels")),
            "height_m": number(tags.get("height")),
            "tags": {k[:200]: v[:2000] for k, v in sorted(tags.items()) if v},
        }
    )
