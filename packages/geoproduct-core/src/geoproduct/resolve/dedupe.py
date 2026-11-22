"""Finding and merging duplicate records, with the reasons for every merge.

Two records are duplicates when they share an identifier (website, phone, ...) and are not
far apart, or when their names are similar and they are close together (or, without
locations, have the same address). Far-apart records that share a website or similar
identifier are one organisation with several sites: they stay separate records, linked by
``organisation_id``.

Merging never picks a winner silently. Differing attribute values become a ``Conflict``
and the attribute's value is cleared, so nobody reads one source's value as settled.
"""

import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import JsonValue
from shapely import STRtree
from shapely.geometry import Point

from geoproduct.engine.fingerprint import short_id
from geoproduct.geo.metric import distance_m, metres_to_degrees
from geoproduct.geo.shapes import anchor, record_shape
from geoproduct.resolve.text import address_key, best_similarity, fold, name_key
from geoproduct.schemas import (
    AttributeValue,
    Conflict,
    ConflictingValue,
    LocationKind,
    Merge,
    Record,
    RecordStatus,
)

MAX_WORD_BLOCK = 200

LOCATION_RANK = {
    LocationKind.building_footprint: 0,
    LocationKind.entrance: 1,
    LocationKind.address_point: 2,
    LocationKind.centroid: 3,
    LocationKind.approximate: 4,
    LocationKind.unknown: 5,
}


@dataclass(frozen=True)
class DedupeSettings:
    name_threshold: float = 0.88
    max_distance_m: float = 150.0
    id_keys: tuple[str, ...] = ("website", "phone", "email", "wikidata")
    same_category: bool = True


@dataclass
class Link:
    a: int
    b: int
    score: float
    reasons: list[str]


@dataclass
class Resolution:
    records: list[Record]
    merges: int = 0
    organisations: int = 0
    notes: list[str] = field(default_factory=list)


def identifiers(record: Record, keys: Sequence[str]) -> dict[str, str]:
    found: dict[str, str] = {}
    for key in keys:
        value: Any = record.external_ids.get(key)
        if value is None and key in record.attributes:
            value = record.attributes[key].value
        if isinstance(value, str) and value.strip():
            found[key] = _id_key(key, value)
    return {k: v for k, v in found.items() if v}


def _id_key(key: str, value: str) -> str:
    text = value.strip().casefold()
    if key == "website":
        text = re.sub(r"^[a-z]+://", "", text).removeprefix("www.").rstrip("/")
    elif key == "phone":
        text = re.sub(r"\D", "", text)
    return text


def resolve(records: list[Record], settings: DedupeSettings) -> Resolution:
    positions = [_position(r) for r in records]
    names = [[n for n in (r.name, *r.aliases) if n] for r in records]
    ids = [identifiers(r, settings.id_keys) for r in records]

    duplicates: list[Link] = []
    organisations: list[Link] = []
    for i, j in _candidate_pairs(records, positions, settings):
        a, b = records[i], records[j]
        if settings.same_category and a.category and b.category and a.category != b.category:
            continue
        shared = sorted(k for k in ids[i] if ids[j].get(k) == ids[i][k])
        sim = best_similarity(names[i], names[j]) if names[i] and names[j] else 0.0
        pos_i, pos_j = positions[i], positions[j]
        dist = distance_m(pos_i, pos_j) if pos_i is not None and pos_j is not None else None
        near = dist is not None and dist <= settings.max_distance_m
        reasons: list[str] = []
        if shared:
            reasons.append(f"same {', '.join(shared)}")
        if sim:
            reasons.append(f"name similarity {sim:.2f}")
        if dist is not None:
            reasons.append(f"{dist:.0f} m apart")

        same_address = bool(address_key(a.address)) and address_key(a.address) == address_key(
            b.address
        )
        is_duplicate = (shared and (dist is None or near)) or (
            sim >= settings.name_threshold and (near or (dist is None and same_address))
        )
        if same_address and dist is None:
            reasons.append("same address")
        closeness = 0.0 if dist is None else max(0.0, 1.0 - dist / settings.max_distance_m)
        score = round(min(1.0, 0.5 * sim + 0.3 * closeness + (0.2 if shared else 0.0)), 3)
        if is_duplicate:
            duplicates.append(Link(i, j, score, reasons))
        elif shared and sim >= settings.name_threshold and dist is not None:
            organisations.append(Link(i, j, score, reasons))

    clusters = _components(len(records), duplicates)
    by_member = {m: c for c in clusters for m in c}
    links_by_cluster: dict[int, list[Link]] = defaultdict(list)
    for link in duplicates:
        links_by_cluster[min(by_member[link.a])].append(link)

    resolved: list[Record] = []
    merges = 0
    index_of: dict[int, int] = {}
    for cluster in sorted(clusters, key=min):
        members = sorted(cluster)
        if len(members) == 1:
            index_of[members[0]] = len(resolved)
            resolved.append(records[members[0]])
            continue
        merged = _merge([records[m] for m in members], members, links_by_cluster[min(cluster)])
        merges += len(members) - 1
        for m in members:
            index_of[m] = len(resolved)
        resolved.append(merged)

    org_groups = _components(len(records), organisations)
    linked = 0
    for group in org_groups:
        targets = sorted({index_of[m] for m in group})
        if len(targets) < 2:
            continue
        websites = sorted({v for m in group for v in ids[m].values()})
        org_id = resolved[targets[0]].organisation_id or short_id("org", websites)
        for t in targets:
            if resolved[t].organisation_id is None:
                resolved[t] = resolved[t].model_copy(update={"organisation_id": org_id})
        linked += 1

    return Resolution(records=resolved, merges=merges, organisations=linked)


def _position(record: Record) -> Point | None:
    geom = record_shape(record)
    return None if geom is None else Point(anchor(geom))


def _candidate_pairs(
    records: list[Record], positions: list[Point | None], settings: DedupeSettings
) -> list[tuple[int, int]]:
    pairs: set[tuple[int, int]] = set()
    located = [i for i, p in enumerate(positions) if p is not None]
    if located:
        points = [p for p in positions if p is not None]
        tree = STRtree(points)
        for k, i in enumerate(located):
            point = points[k]
            assert point is not None
            reach = metres_to_degrees(settings.max_distance_m, float(point.y))
            for hit in tree.query(point.buffer(reach)):
                j = located[int(hit)]
                if i < j:
                    pairs.add((i, j))
    # Pairs that are far apart, or lack a location, can only be found by name or identifier:
    # block them by each significant word of the name and by each identifier. Very common
    # words carry little signal and would compare everything with everything, so large
    # word blocks are skipped; identifier blocks are always kept.
    blocks: dict[str, list[int]] = defaultdict(list)
    for i, record in enumerate(records):
        for word in set(name_key(record.name or "").split()):
            if len(word) > 2:
                blocks[f"w:{word}"].append(i)
        for key, value in identifiers(record, settings.id_keys).items():
            blocks[f"id:{key}:{value}"].append(i)
    for block, members in blocks.items():
        if block.startswith("w:") and len(members) > MAX_WORD_BLOCK:
            continue
        for x, i in enumerate(members):
            for j in members[x + 1 :]:
                pairs.add((min(i, j), max(i, j)))
    return sorted(pairs)


def _components(size: int, links: list[Link]) -> list[set[int]]:
    parent = list(range(size))

    def root(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for link in links:
        ra, rb = root(link.a), root(link.b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)
    groups: dict[int, set[int]] = defaultdict(set)
    for i in range(size):
        groups[root(i)].add(i)
    return list(groups.values())


def _rank(record: Record) -> tuple[int, int, float, int, int, str]:
    return (
        0 if record.geometry is not None else 1,
        LOCATION_RANK.get(record.location.kind, 9),
        record.location.precision_m if record.location.precision_m is not None else 1e9,
        -len(record.evidence_ids),
        -len(record.attributes),
        record.id,
    )


IDENTIFIER_KEYS = frozenset({"website", "phone", "email"})


def _same(key: str, a: JsonValue, b: JsonValue) -> bool:
    if isinstance(a, str) and isinstance(b, str):
        if key in IDENTIFIER_KEYS:
            return _id_key(key, a) == _id_key(key, b)
        return fold(a) == fold(b)
    return a == b


def _fuller(key: str, kept: str, other: str) -> str:
    """Of two spellings of the same identifier, the one that says more: a website with its
    scheme (``https://…``) rather than without."""
    if key != "website":
        return kept
    return other if "://" in other and "://" not in kept else kept


def _display_name(primary: Record, others: list[Record]) -> str | None:
    """The fullest spelling of the primary record's name, e.g. "Hospital" over "Hosp."."""
    if not primary.name:
        return next((r.name for r in others if r.name), None)
    key = name_key(primary.name)
    spellings = [primary.name, *(r.name for r in others if r.name and name_key(r.name) == key)]
    return max(spellings, key=len)


def _merge(records: list[Record], indexes: list[int], links: list[Link]) -> Record:
    order = sorted(range(len(records)), key=lambda k: _rank(records[k]))
    primary = records[order[0]]
    others = [records[k] for k in order[1:]]
    position_of = {index: k for k, index in enumerate(indexes)}
    linked: dict[str, Link] = {}
    for link in links:
        for end in (link.a, link.b):
            rid = records[position_of[end]].id
            if rid != primary.id and rid not in linked:
                linked[rid] = link

    aliases = list(primary.aliases)
    seen_names = {name_key(n) for n in (primary.name, *primary.aliases) if n}
    external_ids = dict(primary.external_ids)
    attributes = {k: v.model_copy() for k, v in primary.attributes.items()}
    sources_of: dict[str, list[tuple[JsonValue, list[str]]]] = {
        k: [(v.value, list(v.evidence_ids))] for k, v in primary.attributes.items()
    }
    evidence_ids = list(primary.evidence_ids)
    limitations = list(primary.limitations)
    conflicts = list(primary.conflicts)
    merged_notes = list(primary.merged)

    for other in others:
        for name in (other.name, *other.aliases):
            if name and name_key(name) not in seen_names:
                seen_names.add(name_key(name))
                aliases.append(name)
        for key, value in other.external_ids.items():
            if key not in external_ids:
                external_ids[key] = value
            elif _same(key, external_ids[key], value):
                external_ids[key] = _fuller(key, external_ids[key], value)
            else:
                n = 2
                while f"{key}~{n}" in external_ids:
                    n += 1
                external_ids[f"{key}~{n}"] = value
        for key, attr in other.attributes.items():
            sources_of.setdefault(key, []).append((attr.value, list(attr.evidence_ids)))
            if key not in attributes:
                attributes[key] = attr.model_copy()
        evidence_ids += [e for e in other.evidence_ids if e not in evidence_ids]
        limitations += [x for x in other.limitations if x not in limitations]
        conflicts += other.conflicts
        merged_notes += other.merged
        via = linked.get(other.id)
        merged_notes.append(
            Merge(
                record_id=other.id,
                score=via.score if via else 0.0,
                reasons=via.reasons if via and via.reasons else ["linked through the cluster"],
            )
        )

    for key, values in sources_of.items():
        present = [(v, ev) for v, ev in values if v is not None]
        distinct: list[tuple[JsonValue, list[str]]] = []
        for stated, ev in present:
            match = next((d for d in distinct if _same(key, d[0], stated)), None)
            if match is None:
                distinct.append((stated, list(ev)))
            else:
                match[1].extend(e for e in ev if e not in match[1])
        all_evidence = [e for _, ev in distinct for e in ev]
        if len(distinct) > 1:
            conflicts.append(
                Conflict(
                    field=f"attributes.{key}",
                    values=[ConflictingValue(value=v, evidence_ids=ev) for v, ev in distinct],
                    note="sources disagree; the value is cleared until resolved",
                )
            )
            attributes[key] = AttributeValue(
                value=None,
                basis=attributes[key].basis,
                evidence_ids=all_evidence,
                note="sources disagree; see conflicts",
            )
        elif distinct:
            attributes[key] = attributes[key].model_copy(update={"evidence_ids": all_evidence})

    addresses = {address_key(r.address): r.address for r in records if address_key(r.address)}
    if len(addresses) > 1:
        conflicts.append(
            Conflict(
                field="address",
                values=[
                    ConflictingValue(value=a.model_dump(mode="json", exclude_none=True))
                    for a in addresses.values()
                    if a is not None
                ],
                note="sources give different addresses",
            )
        )

    name = _display_name(primary, others)
    if name != primary.name:
        if primary.name and primary.name not in aliases:
            aliases.insert(0, primary.name)
        aliases = [a for a in aliases if a != name]

    status = RecordStatus.conflicting if conflicts else primary.status
    return primary.model_copy(
        update={
            "name": name,
            "aliases": aliases,
            "external_ids": external_ids,
            "attributes": attributes,
            "evidence_ids": evidence_ids,
            "limitations": limitations,
            "conflicts": conflicts,
            "merged": merged_notes,
            "status": status,
            "confidence": None,
        }
    )
