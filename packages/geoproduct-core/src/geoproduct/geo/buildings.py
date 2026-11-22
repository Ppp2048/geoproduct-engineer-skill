"""Matching a located record to a building footprint, with reasons for every decision.

The rules, in order:

1. **Containment.** A footprint containing the record's position is a candidate. Alone it
   gives ``probable``; it becomes ``exact`` only when a name, address or identifier agrees.
   Several containing footprints (overlapping parts) are ``ambiguous`` unless exactly one is
   confirmed.
2. **Proximity.** Otherwise, footprints within a search radius that grows with the location's
   imprecision (capped by ``max_distance_m``) are scored. One clear candidate with supporting
   evidence is ``probable``; without evidence the record stays an ``approximate_point``.
   Candidates within ``ambiguity_margin`` of each other make it ``ambiguous``.
3. **Never** attach a record to a nearby building only because it is the nearest.
"""

from dataclasses import dataclass

from shapely import STRtree
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry

from geoproduct.geo.metric import distance_m, metres_to_degrees
from geoproduct.geo.shapes import to_shape
from geoproduct.providers.base import BuildingFootprint
from geoproduct.resolve.text import address_key, best_similarity
from geoproduct.schemas import BuildingCandidate, BuildingMatch, MatchStatus, Record

METHOD = "match.buildings"


@dataclass(frozen=True)
class MatchSettings:
    max_distance_m: float = 30.0
    min_radius_m: float = 5.0
    name_threshold: float = 0.85
    ambiguity_margin: float = 0.1
    max_candidates: int = 5


@dataclass(frozen=True)
class _Candidate:
    building: BuildingFootprint
    distance: float
    contains: bool
    name_sim: float | None
    confirmations: tuple[str, ...]
    score: float

    def to_model(self) -> BuildingCandidate:
        return BuildingCandidate(
            ref=self.building.ref,
            distance_m=round(self.distance, 2),
            contains_point=self.contains,
            name_similarity=None if self.name_sim is None else round(self.name_sim, 3),
            score=round(self.score, 3),
            reasons=list(self.confirmations),
        )


class BuildingIndex:
    def __init__(self, buildings: list[BuildingFootprint]) -> None:
        self.buildings = buildings
        self.shapes: list[BaseGeometry] = [to_shape(b.geometry) for b in buildings]
        self.tree = STRtree(self.shapes)

    def near(self, point: Point, radius_m: float) -> list[int]:
        reach = metres_to_degrees(radius_m, float(point.y))
        return [int(i) for i in self.tree.query(point.buffer(reach))]


def confirmations(
    record: Record, building: BuildingFootprint, threshold: float
) -> tuple[tuple[str, ...], float | None]:
    """Evidence, independent of position, that the record and the footprint are the same."""
    found: list[str] = []
    name_sim = None
    if record.name and building.name:
        name_sim = best_similarity([record.name, *record.aliases], [building.name])
        if name_sim >= threshold:
            found.append(f"name agrees with footprint '{building.name}' ({name_sim:.2f})")
    record_address, building_address = address_key(record.address), address_key(building.address)
    if record_address and record_address == building_address:
        found.append("address agrees with the footprint's address")
    ref = building.ref
    if (
        record.external_ids.get(ref.provider) == ref.id
        or record.external_ids.get("building") == f"{ref.provider}:{ref.id}"
    ):
        found.append(f"record cites footprint {ref.provider}:{ref.id}")
    return tuple(found), name_sim


def match_record(
    record: Record,
    position: Point | None,
    index: BuildingIndex,
    settings: MatchSettings,
    *,
    precision_m: float | None = None,
) -> BuildingMatch:
    if position is None:
        return BuildingMatch(
            status=MatchStatus.none,
            method=METHOD,
            reasons=["the record has no location to match"],
        )
    if precision_m is None:
        radius = settings.max_distance_m
    else:
        radius = min(settings.max_distance_m, max(settings.min_radius_m, precision_m))

    candidates: list[_Candidate] = []
    for i in index.near(position, max(radius, settings.min_radius_m)):
        building, footprint = index.buildings[i], index.shapes[i]
        contains = bool(footprint.covers(position))
        distance = 0.0 if contains else distance_m(position, footprint)
        if not contains and distance > radius:
            continue
        found, name_sim = confirmations(record, building, settings.name_threshold)
        closeness = 1.0 if contains else 1.0 - distance / radius if radius else 0.0
        score = min(1.0, 0.6 * closeness + 0.4 * (name_sim or 0.0) + (0.2 if found else 0.0))
        candidates.append(_Candidate(building, distance, contains, name_sim, found, score))

    candidates.sort(key=lambda c: (-c.contains, -c.score, c.distance, c.building.ref.id))
    listed = [c.to_model() for c in candidates[: settings.max_candidates]]

    containing = [c for c in candidates if c.contains]
    if containing:
        confirmed = [c for c in containing if c.confirmations]
        if len(containing) == 1 or len(confirmed) == 1:
            chosen = confirmed[0] if confirmed else containing[0]
            if chosen.confirmations:
                reasons = ["the position is inside the footprint", *chosen.confirmations]
                status = MatchStatus.exact
            else:
                reasons = [
                    "the position is inside the footprint",
                    "no name, address or identifier confirms it",
                ]
                status = MatchStatus.probable
            return BuildingMatch(
                status=status,
                building_ref=chosen.building.ref,
                method=METHOD,
                score=round(chosen.score, 3),
                reasons=reasons,
                candidates=listed,
            )
        return BuildingMatch(
            status=MatchStatus.ambiguous,
            method=METHOD,
            reasons=[f"the position is inside {len(containing)} overlapping footprints"],
            candidates=listed,
        )

    if not candidates:
        return BuildingMatch(
            status=MatchStatus.none,
            method=METHOD,
            reasons=[f"no footprint within {radius:g} m"],
        )

    top = candidates[0]
    if len(candidates) > 1 and top.score - candidates[1].score < settings.ambiguity_margin:
        return BuildingMatch(
            status=MatchStatus.ambiguous,
            method=METHOD,
            reasons=[
                f"{len(candidates)} footprints within {radius:g} m score within "
                f"{settings.ambiguity_margin:g} of each other"
            ],
            candidates=listed,
        )
    if top.confirmations:
        return BuildingMatch(
            status=MatchStatus.probable,
            building_ref=top.building.ref,
            method=METHOD,
            score=round(top.score, 3),
            reasons=[f"the nearest footprint is {top.distance:.1f} m away", *top.confirmations],
            candidates=listed,
        )
    return BuildingMatch(
        status=MatchStatus.approximate_point,
        method=METHOD,
        score=round(top.score, 3),
        reasons=[
            f"the nearest footprint is {top.distance:.1f} m away",
            "nothing confirms it is this building, so the point is kept as it is",
        ],
        candidates=listed,
    )
