"""``fixture``: an offline provider over Sample Town, a small synthetic town.

Sample Town is generated, not surveyed. It sits near 0°N 0°E, which is open ocean, so it
cannot be mistaken for a real place. It is laid out so that every interesting case
appears at least once:

- streets on a 200 m grid, with numbered house addresses on the avenues;
- footprints, some named, some not;
- two hospital records 11 m apart, "Sample General Hospital" and "Sample General
  Hosp.", with the same website and different bed counts. Nothing in them shows
  whether they are one site or two: no address, register code or site identifier;
- an office company with two far-apart sites sharing one website;
- a clinic between two buildings (an ambiguous building match), a clinic in a park
  (no building at all), and a cafe just outside an unnamed building;
- bus stops beside the streets, and one placed 100 m from any street;
- two neighbourhood polygons.

Everything is deterministic: the same query always gives the same answer.
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from functools import cache
from typing import Any

from shapely.geometry import LineString, Point, box, mapping
from shapely.geometry.base import BaseGeometry

from geoproduct.geo.metric import distance_m
from geoproduct.geo.shapes import area_shape
from geoproduct.providers.base import (
    BuildingFootprint,
    BuildingQuery,
    Capability,
    FeatureQuery,
    GeocodeCandidate,
    GeocodeQuery,
    Provider,
    ProviderInfo,
    ProviderResult,
    ReverseGeocodeQuery,
)
from geoproduct.resolve.text import name_key, similarity
from geoproduct.schemas import Address, LocationKind, Record, SourceType

TOWN = "Sample Town"
COUNTRY = "ZZ"
"""User-assigned ISO 3166 code: no real country."""
DATA_VERSION = "sample-town-v1"

LON0 = 0.002
LAT0 = 0.002
SPACING = 0.0018
"""About 200 m between streets."""

NS_STREETS = ("Alder Street", "Birch Street", "Cedar Street", "Damson Street", "Elm Street")
EW_STREETS = ("First Avenue", "Second Avenue", "Third Avenue", "Fourth Avenue", "Fifth Avenue")
PRIMARY = {"Cedar Street", "Third Avenue"}
CATEGORY_KEYS = ("amenity", "shop", "office", "leisure", "railway", "highway", "place")


def x(i: float) -> float:
    return LON0 + i * SPACING


def y(j: float) -> float:
    return LAT0 + j * SPACING


AREA_BBOX = (x(0) - 0.0005, y(0) - 0.0005, x(4) + 0.0005, y(4) + 0.0005)
"""A box around the whole town, for examples and tests."""


@dataclass(frozen=True)
class Building:
    id: str
    shape: BaseGeometry
    tags: dict[str, str]
    name: str | None = None
    house_number: str | None = None
    street: str | None = None
    levels: float | None = None


@dataclass(frozen=True)
class Feature:
    id: str
    shape: BaseGeometry
    tags: dict[str, Any]
    kind: LocationKind = LocationKind.unknown
    precision_m: float | None = None
    address: dict[str, str] = field(default_factory=dict)


BUILDING_USES: dict[tuple[int, int], dict[int, tuple[str, str | None, float]]] = {
    (1, 1): {0: ("hospital", "Sample General Hospital", 6), 1: ("hospital", None, 3)},
    (2, 3): {1: ("hospital", None, 3)},
    (2, 2): {
        0: ("retail", "Corner Market", 1),
        1: ("office", "Acme House", 4),
        2: ("commercial", None, 2),
        3: ("commercial", "Bank Chambers", 3),
    },
    (3, 0): {0: ("office", "Harbour Point", 5)},
}
PARK_BLOCK = (0, 3)


def centre(i: int, j: int, q: int) -> tuple[float, float]:
    qx, qy = q % 2, q // 2
    return (x(i) + 0.0005 + qx * 0.0008, y(j) + 0.0005 + qy * 0.0008)


@cache
def town() -> tuple[tuple[Building, ...], tuple[Feature, ...]]:
    buildings: list[Building] = []
    for i in range(4):
        for j in range(4):
            if (i, j) == PARK_BLOCK:
                continue
            for q in range(4):
                qx, qy = q % 2, q // 2
                west, south = x(i) + 0.0002 + qx * 0.0008, y(j) + 0.0002 + qy * 0.0008
                use, name, levels = BUILDING_USES.get((i, j), {}).get(q, ("residential", None, 2))
                k = i * 2 + qx
                number = str(2 * k + 1) if qy == 0 else str(2 * k + 2)
                street = EW_STREETS[j] if qy == 0 else EW_STREETS[j + 1]
                buildings.append(
                    Building(
                        id=f"b-{i}{j}{q}",
                        shape=box(west, south, west + 0.0006, south + 0.0006),
                        tags={"building": use},
                        name=name,
                        house_number=number,
                        street=street,
                        levels=levels,
                    )
                )

    features: list[Feature] = []
    for n, street in enumerate(NS_STREETS):
        features.append(_road(f"road-ns-{n}", street, LineString([(x(n), y(0)), (x(n), y(4))])))
    for n, street in enumerate(EW_STREETS):
        features.append(_road(f"road-ew-{n}", street, LineString([(x(0), y(n)), (x(4), y(n))])))

    hospital = centre(1, 1, 0)
    features += [
        _poi(
            "poi-01",
            hospital,
            LocationKind.centroid,
            15,
            amenity="hospital",
            name="Sample General Hospital",
            beds=110,
            website="https://general-hospital.example.org",
            phone="+00 100 0001",
        ),
        _poi(
            "poi-02",
            (hospital[0] + 0.0001, hospital[1]),
            LocationKind.entrance,
            10,
            amenity="hospital",
            name="Sample General Hosp.",
            beds=120,
            website="general-hospital.example.org/",
        ),
        _poi(
            "poi-03",
            centre(2, 3, 1),
            LocationKind.centroid,
            15,
            amenity="hospital",
            name="Northside Hospital",
        ),
        _poi(
            "poi-04",
            (x(0) + 0.0009, y(1) + 0.0005),
            LocationKind.approximate,
            20,
            amenity="clinic",
            name="Riverside Clinic",
        ),
        _poi(
            "poi-05",
            (x(0) + 0.0009, y(3) + 0.0009),
            LocationKind.approximate,
            25,
            amenity="clinic",
            name="Hilltop Clinic",
        ),
        _poi(
            "poi-06",
            centre(2, 2, 0),
            LocationKind.centroid,
            10,
            shop="supermarket",
            name="Corner Market",
        ),
        _poi(
            "poi-07",
            centre(2, 2, 1),
            LocationKind.centroid,
            10,
            office="company",
            name="Acme Offices",
            website="https://acme.example.com",
        ),
        _poi(
            "poi-08",
            centre(3, 0, 0),
            LocationKind.centroid,
            10,
            office="company",
            name="Acme Offices",
            website="https://acme.example.com/",
        ),
        _poi(
            "poi-09",
            centre(2, 2, 3),
            LocationKind.centroid,
            10,
            amenity="bank",
            name="Example Bank",
        ),
        _poi(
            "poi-10",
            (centre(2, 2, 2)[0] + 0.00035, centre(2, 2, 2)[1]),
            LocationKind.approximate,
            15,
            amenity="cafe",
            name="Greenleaf Cafe",
        ),
        _poi(
            "poi-11",
            (x(2) + 0.00007, y(3) + 0.0009),
            LocationKind.entrance,
            5,
            highway="bus_stop",
            name="Cedar Street North",
        ),
        _poi(
            "poi-12",
            (x(0) + 0.0009, y(2) - 0.00006),
            LocationKind.entrance,
            5,
            highway="bus_stop",
            name="Third Avenue West",
        ),
        _poi(
            "poi-13",
            (x(3) + 0.0009, y(2) + 0.00006),
            LocationKind.entrance,
            5,
            highway="bus_stop",
            name="Third Avenue East",
        ),
        _poi(
            "poi-14",
            (x(3) + 0.0009, y(3) + 0.0009),
            LocationKind.entrance,
            5,
            highway="bus_stop",
            name="Orchard Stop",
        ),
        _poi(
            "poi-15",
            (x(4) + 0.0003, y(1) + 0.0009),
            LocationKind.centroid,
            20,
            railway="station",
            name="Sample Town Station",
        ),
        Feature(
            "park-01",
            box(x(0) + 0.0001, y(3) + 0.0001, x(1) - 0.0001, y(4) - 0.0001),
            {"leisure": "park", "name": "Hilltop Park"},
        ),
        Feature(
            "district-n",
            box(x(0), y(2), x(4), y(4)),
            {"place": "neighbourhood", "name": "North Quarter"},
        ),
        Feature(
            "district-s",
            box(x(0), y(0), x(4), y(2)),
            {"place": "neighbourhood", "name": "South Quarter"},
        ),
    ]
    return tuple(buildings), tuple(features)


def _road(feature_id: str, name: str, line: LineString) -> Feature:
    kind = "primary" if name in PRIMARY else "residential"
    return Feature(feature_id, line, {"highway": kind, "name": name})


def _poi(
    feature_id: str,
    lonlat: tuple[float, float],
    kind: LocationKind,
    precision: float,
    **tags: Any,
) -> Feature:
    return Feature(feature_id, Point(lonlat), tags, kind, precision)


def _matches(tags: dict[str, Any], wanted: dict[str, Any]) -> bool:
    for key, value in wanted.items():
        if key not in tags:
            return False
        if value is None:
            continue
        options = value if isinstance(value, list) else [value]
        if str(tags[key]) not in options:
            return False
    return True


def _geometry(shape: BaseGeometry) -> dict[str, Any]:
    def lists(value: Any) -> Any:
        if isinstance(value, list | tuple):
            return [lists(v) for v in value]
        return round(value, 7) if isinstance(value, float) else value

    data = mapping(shape)
    return {"type": data["type"], "coordinates": lists(data["coordinates"])}


def _record(feature: Feature) -> Record:
    tags = dict(feature.tags)
    key = next((k for k in CATEGORY_KEYS if k in tags), None)
    category = f"{key}={tags[key]}" if key else None
    name = tags.pop("name", None)
    if key:
        tags.pop(key)
    website = tags.get("website")
    return Record.model_validate(
        {
            "id": f"fixture:{feature.id}",
            "name": name,
            "category": category,
            "external_ids": {"fixture": feature.id, **({"website": website} if website else {})},
            "attributes": {k: {"value": v, "basis": "fact"} for k, v in sorted(tags.items())},
            "geometry": _geometry(feature.shape),
            "location": {
                "kind": feature.kind,
                "precision_m": feature.precision_m,
                "method": f"fixture:{feature.shape.geom_type.lower()}",
            },
        }
    )


def _footprint(building: Building) -> BuildingFootprint:
    address = None
    if building.street:
        address = Address(
            house_number=building.house_number,
            street=building.street,
            locality=TOWN,
            country_code=COUNTRY,
        )
    return BuildingFootprint.model_validate(
        {
            "ref": {"provider": "fixture", "id": building.id},
            "geometry": _geometry(building.shape),
            "name": building.name,
            "address": address,
            "levels": building.levels,
            "tags": building.tags,
        }
    )


class FixtureProvider(Provider):
    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            name="fixture",
            title="Sample Town (synthetic test data)",
            capabilities=list(Capability),
            source_type=SourceType.other,
            licence="CC0-1.0",
            attribution="Synthetic data generated by geoproduct-core. Not a real place.",
        )

    def _result[T](self, items: list[T], limit: int | None = None) -> dict[str, Any]:
        truncated = limit is not None and len(items) > limit
        return {
            "items": items[:limit] if limit is not None else items,
            "provider": "fixture",
            "licence": "CC0-1.0",
            "attribution": self.info.attribution,
            "data_timestamp": DATA_VERSION,
            "request_count": 0,
            "truncated": truncated,
        }

    def features(self, query: FeatureQuery) -> ProviderResult[Record]:
        region = area_shape(query.area)
        _, features = town()
        hits = [
            _record(f)
            for f in features
            if f.shape.intersects(region) and _matches(f.tags, query.tags)
        ]
        return ProviderResult[Record].model_validate(self._result(hits, query.limit))

    def buildings(self, query: BuildingQuery) -> ProviderResult[BuildingFootprint]:
        region = area_shape(query.area)
        buildings, _ = town()
        hits = [_footprint(b) for b in buildings if b.shape.intersects(region)]
        return ProviderResult[BuildingFootprint].model_validate(self._result(hits, query.limit))

    def geocode(self, query: GeocodeQuery) -> ProviderResult[GeocodeCandidate]:
        candidates = sorted(self._geocode(query.text), key=lambda c: (-(c.score or 0), c.label))
        return ProviderResult[GeocodeCandidate].model_validate(
            self._result(candidates, query.limit)
        )

    def reverse_geocode(self, query: ReverseGeocodeQuery) -> ProviderResult[GeocodeCandidate]:
        point = Point(query.lon, query.lat)
        buildings, _ = town()
        nearest = min(buildings, key=lambda b: (b.shape.distance(point), b.id))
        found: list[GeocodeCandidate] = []
        if distance_m(point, nearest.shape) <= 50:
            found.append(self._address_candidate(nearest, 1.0))
        return ProviderResult[GeocodeCandidate].model_validate(self._result(found))

    def _geocode(self, text: str) -> Iterator[GeocodeCandidate]:
        buildings, features = town()
        if match := re.match(r"^\s*(\d+)\s+(.+?)\s*$", text.split(",")[0]):
            number, street = match.groups()
            for b in buildings:
                if b.house_number == number and name_key(b.street or "") == name_key(street):
                    yield self._address_candidate(b, 1.0)
            return
        wanted = name_key(text.split(",")[0])
        for street_name in NS_STREETS + EW_STREETS:
            if name_key(street_name) == wanted:
                road = next(f for f in features if f.tags.get("name") == street_name)
                mid = road.shape.interpolate(0.5, normalized=True)
                yield GeocodeCandidate(
                    label=f"{street_name}, {TOWN}",
                    lon=mid.x,
                    lat=mid.y,
                    kind=LocationKind.approximate,
                    precision_m=400,
                    score=0.9,
                    category="highway",
                    provider_ref=road.id,
                )
                return
        for f in features:
            name = f.tags.get("name")
            if not name or f.shape.geom_type != "Point":
                continue
            score = similarity(wanted, name_key(name))
            if score >= 0.6:
                lon, lat = f.shape.coords[0]
                yield GeocodeCandidate(
                    label=f"{name}, {TOWN}",
                    lon=lon,
                    lat=lat,
                    kind=LocationKind.centroid,
                    precision_m=f.precision_m or 25,
                    score=round(score, 3),
                    provider_ref=f.id,
                )

    def _address_candidate(self, building: Building, score: float) -> GeocodeCandidate:
        point = building.shape.centroid
        return GeocodeCandidate(
            label=f"{building.house_number} {building.street}, {TOWN}",
            lon=round(point.x, 7),
            lat=round(point.y, 7),
            kind=LocationKind.address_point,
            precision_m=10,
            score=score,
            address=Address(
                house_number=building.house_number,
                street=building.street,
                locality=TOWN,
                country_code=COUNTRY,
            ),
            provider_ref=building.id,
        )
