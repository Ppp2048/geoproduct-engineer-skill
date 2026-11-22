#!/usr/bin/env python3
"""Check a GeoJSON or CSV location dataset for common errors, without the GeoProduct server.

Standard library only (Python 3.12+). Prints a JSON report to stdout.
Exit codes: 0 no errors, 1 errors found, 2 the file could not be read or the options are wrong.

Checks:
  structure      GeoJSON types, coordinate nesting, LineString and Polygon sizes, closed rings
  range          longitude in [-180, 180], latitude in [-90, 90]; values that look projected
  null_island    points at exactly 0, 0
  swapped        longitude and latitude probably swapped (needs --bbox, or an out-of-range
                 latitude that is valid as a longitude)
  bbox           positions outside --bbox
  missing        features or rows without a location
  duplicate_id   the same id twice
  duplicate      the same position, or the same name within --duplicate-distance-m
  precision      fewer decimals than --min-decimals (4 decimals is about 11 m)
  evidence       GeoProduct records with facts that cite no evidence
  crs            a GeoJSON "crs" member (removed in RFC 7946; coordinates must be WGS 84)

Examples:
  python check_dataset.py clinics.geojson --bbox -0.01,-0.01,0.02,0.02
  python check_dataset.py facilities.csv --lon-column x --lat-column y --id-column code
"""

import argparse
import csv
import json
import math
import re
import sys
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

EARTH_RADIUS_M = 6_371_008.8
LON_NAMES = ("lon", "lng", "long", "longitude", "x")
LAT_NAMES = ("lat", "latitude", "y")
ID_NAMES = ("id", "identifier", "code", "ref")
NAME_NAMES = ("name", "title", "label")
GEOMETRY_TYPES = {
    "Point",
    "MultiPoint",
    "LineString",
    "MultiLineString",
    "Polygon",
    "MultiPolygon",
    "GeometryCollection",
}


@dataclass
class Item:
    """One feature or row: an id, a name, and its positions with their written decimals."""

    index: int
    where: str
    """Where the item is in the file: "row 3" in a CSV, "feature 2" in GeoJSON."""
    id: str | None
    name: str | None
    geometry_type: str | None
    positions: list[tuple[float, float]] = field(default_factory=list)
    decimals: list[int] = field(default_factory=list)
    properties: dict[str, Any] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return self.id if self.id is not None else self.where


@dataclass
class Report:
    max_findings: int
    findings: list[dict[str, Any]] = field(default_factory=list)
    counts: dict[str, dict[str, int]] = field(
        default_factory=lambda: defaultdict(lambda: {"error": 0, "warning": 0})
    )

    def add(self, severity: str, check: str, item: str | None, message: str) -> None:
        self.counts[check][severity] += 1
        if len(self.findings) < self.max_findings:
            self.findings.append(
                {"severity": severity, "check": check, "item": item, "message": message}
            )

    def total(self, severity: str) -> int:
        return sum(c[severity] for c in self.counts.values())


def decimals_of(text: str) -> int:
    text = text.strip().lower()
    if "e" in text:
        return 15
    return len(text.split(".", 1)[1]) if "." in text else 0


def haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    lon1, lat1, lon2, lat2 = map(math.radians, (*a, *b))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(h)))


def name_key(name: str | None) -> str:
    return re.sub(r"[^0-9a-z]+", "", (name or "").casefold())


# Reading -----------------------------------------------------------------------------------


class Written(float):
    """A float that remembers how it was written, so precision is judged from the file."""

    text: str

    def __new__(cls, text: str) -> "Written":
        number = super().__new__(cls, float(text))
        number.text = text
        return number


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _walk(coords: Any, depth: int, item: Item, report: Report) -> Iterator[tuple[float, float]]:
    """Yield positions from a coordinates array that should be nested ``depth`` deep."""
    if depth == 0:
        if not isinstance(coords, list) or len(coords) < 2:
            report.add("error", "structure", item.label, "a position needs two numbers")
            return
        lon, lat = _number(coords[0]), _number(coords[1])
        if lon is None or lat is None:
            report.add("error", "structure", item.label, "a coordinate is not a number")
            return
        item.decimals += [decimals_of(getattr(v, "text", str(v))) for v in coords[:2]]
        yield lon, lat
        return
    if not isinstance(coords, list):
        report.add("error", "structure", item.label, "coordinates are not nested as expected")
        return
    for part in coords:
        yield from _walk(part, depth - 1, item, report)


def _check_shape(geometry: dict[str, Any], item: Item, report: Report) -> None:
    kind, coords = geometry.get("type"), geometry.get("coordinates")
    lines: list[Any] = []
    if kind == "LineString":
        lines = [coords]
    elif kind == "MultiLineString":
        lines = coords or []
    for line in lines:
        if not isinstance(line, list) or len(line) < 2:
            report.add("error", "structure", item.label, "a line needs at least two positions")
    rings: list[Any] = []
    if kind == "Polygon":
        rings = coords or []
    elif kind == "MultiPolygon":
        rings = [ring for polygon in coords or [] for ring in polygon or []]
    for ring in rings:
        if not isinstance(ring, list) or len(ring) < 4:
            report.add("error", "structure", item.label, "a ring needs at least four positions")
        elif ring[0] != ring[-1]:
            report.add("error", "structure", item.label, "a polygon ring is not closed")


DEPTH = {
    "Point": 0,
    "MultiPoint": 1,
    "LineString": 1,
    "MultiLineString": 2,
    "Polygon": 2,
    "MultiPolygon": 3,
}


def _read_geometry(geometry: Any, item: Item, report: Report) -> None:
    if geometry is None:
        return
    if not isinstance(geometry, dict) or geometry.get("type") not in GEOMETRY_TYPES:
        report.add("error", "structure", item.label, "the geometry type is not GeoJSON")
        return
    item.geometry_type = geometry["type"]
    if geometry["type"] == "GeometryCollection":
        for part in geometry.get("geometries") or []:
            _read_geometry(part, item, report)
        return
    _check_shape(geometry, item, report)
    depth = DEPTH[geometry["type"]]
    item.positions.extend(_walk(geometry.get("coordinates"), depth, item, report))


def read_geojson(path: Path, report: Report) -> tuple[list[Item], dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8-sig"), parse_float=Written)
    meta: dict[str, Any] = {}
    if isinstance(data, dict) and data.get("type") == "Feature":
        features: Any = [data]
    elif isinstance(data, dict) and data.get("type") == "FeatureCollection":
        features = data.get("features")
        if not isinstance(features, list):
            raise ValueError("a FeatureCollection needs a features array")
        if "crs" in data:
            meta["crs"] = data["crs"]
    else:
        raise ValueError("expected a GeoJSON Feature or FeatureCollection")
    items = []
    for index, feature in enumerate(features):
        if not isinstance(feature, dict) or feature.get("type") != "Feature":
            report.add("error", "structure", f"feature {index}", "not a GeoJSON Feature")
            continue
        props = feature.get("properties") or {}
        if not isinstance(props, dict):
            props = {}
        fid = feature.get("id", props.get("id"))
        item = Item(
            index=index,
            where=f"feature {index}",
            id=None if fid is None else str(fid),
            name=next((str(props[k]) for k in NAME_NAMES if props.get(k)), None),
            geometry_type=None,
            properties=props,
        )
        _read_geometry(feature.get("geometry"), item, report)
        items.append(item)
    return items, meta


def _column(header: list[str], wanted: str | None, names: tuple[str, ...]) -> str | None:
    if wanted:
        if wanted not in header:
            raise ValueError(f"column '{wanted}' is not in the file")
        return wanted
    lowered = {h.strip().casefold(): h for h in header}
    return next((lowered[n] for n in names if n in lowered), None)


def read_csv(path: Path, report: Report, args: argparse.Namespace) -> list[Item]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        header = list(reader.fieldnames or [])
        lon_col = _column(header, args.lon_column, LON_NAMES)
        lat_col = _column(header, args.lat_column, LAT_NAMES)
        if lon_col is None or lat_col is None:
            raise ValueError(
                "no longitude and latitude columns; name them with --lon-column and --lat-column"
            )
        id_col = _column(header, args.id_column, ID_NAMES) or next(
            (h for h in header if h.strip().casefold().endswith("_id")), None
        )
        name_col = _column(header, None, NAME_NAMES)
        items = []
        for index, row in enumerate(reader, start=2):  # row 1 is the header
            item = Item(
                index=index,
                where=f"row {index}",
                id=(row.get(id_col) or None) if id_col else None,
                name=(row.get(name_col) or None) if name_col else None,
                geometry_type=None,
                properties=dict(row),
            )
            lon_text, lat_text = (row.get(lon_col) or "").strip(), (row.get(lat_col) or "").strip()
            if lon_text or lat_text:
                try:
                    item.positions = [(float(lon_text), float(lat_text))]
                    item.decimals = [decimals_of(lon_text), decimals_of(lat_text)]
                    item.geometry_type = "Point"
                except ValueError:
                    report.add("error", "structure", item.label, "a coordinate is not a number")
            items.append(item)
    return items


# Checks ------------------------------------------------------------------------------------


def in_bbox(position: tuple[float, float], bbox: tuple[float, float, float, float]) -> bool:
    west, south, east, north = bbox
    lon, lat = position
    inside_lon = west <= lon <= east if west <= east else (lon >= west or lon <= east)
    return inside_lon and south <= lat <= north


def check(
    items: list[Item], meta: dict[str, Any], args: argparse.Namespace, report: Report
) -> None:
    bbox = args.bbox
    if "crs" in meta:
        text = json.dumps(meta["crs"]).casefold()
        wgs84 = any(s in text for s in ("4326", "crs84", "wgs84", "wgs 84"))
        report.add(
            "warning" if wgs84 else "error",
            "crs",
            None,
            "the file has a 'crs' member; RFC 7946 GeoJSON is always WGS 84"
            + ("" if wgs84 else ", and this one names another system"),
        )

    out_of_range = 0
    seen_ids: dict[str, str] = {}
    for item in items:
        if item.id is not None:
            if item.id in seen_ids:
                report.add(
                    "error",
                    "duplicate_id",
                    item.label,
                    f"{item.where} reuses the id of {seen_ids[item.id]}",
                )
            else:
                seen_ids[item.id] = item.where
        if not item.positions:
            report.add("warning", "missing", item.label, "no location")
            continue
        misplaced = False
        for position in item.positions:
            lon, lat = position
            misplaced = misplaced or not in_bbox(position, (-180, -90, 180, 90))
            if not (-180 <= lon <= 180 and -90 <= lat <= 90):
                out_of_range += 1
                if -90 <= lon <= 90 and -180 <= lat <= 180:
                    report.add(
                        "error",
                        "swapped",
                        item.label,
                        f"({lon}, {lat}) is out of range; swapped it would be valid",
                    )
                else:
                    report.add("error", "range", item.label, f"({lon}, {lat}) is out of range")
                continue
            if lon == 0 and lat == 0:
                misplaced = True
                report.add("error", "null_island", item.label, "position at 0, 0")
                continue
            if bbox and not in_bbox(position, bbox):
                if in_bbox((lat, lon), bbox):
                    report.add(
                        "error",
                        "swapped",
                        item.label,
                        f"({lon}, {lat}) is outside the area, but inside it if swapped",
                    )
                else:
                    distance = haversine_m(
                        position, ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)
                    )
                    report.add(
                        "error",
                        "bbox",
                        item.label,
                        f"({lon}, {lat}) is outside the area, "
                        f"{distance / 1000:.1f} km from its centre",
                    )
        if not misplaced and item.decimals and min(item.decimals) < args.min_decimals:
            report.add(
                "warning",
                "precision",
                item.label,
                f"only {min(item.decimals)} decimal(s); {args.min_decimals} or more expected",
            )
        attributes = item.properties.get("attributes")
        if isinstance(attributes, dict):
            bare = [
                name
                for name, value in attributes.items()
                if isinstance(value, dict)
                and value.get("basis", "fact") == "fact"
                and value.get("value") is not None
                and not value.get("evidence_ids")
            ]
            if bare:
                report.add(
                    "warning",
                    "evidence",
                    item.label,
                    "facts without evidence: " + ", ".join(sorted(bare)),
                )

    if items and out_of_range > len(items) / 2:
        report.add(
            "error",
            "range",
            None,
            "most coordinates are out of range: they may be projected (metres), not WGS 84",
        )
    _duplicates(items, args.duplicate_distance_m, report)


def _duplicates(items: list[Item], distance_m: float, report: Report) -> None:
    """Same position (to about 1 cm), or the same name within ``distance_m``."""
    by_position: dict[tuple[float, float], list[Item]] = defaultdict(list)
    cell = max(distance_m, 1.0) / 111_320
    grid: dict[tuple[int, int], list[tuple[Item, tuple[float, float]]]] = defaultdict(list)
    for item in items:
        if item.geometry_type != "Point" or not item.positions:
            continue
        position = item.positions[0]
        if not (-180 <= position[0] <= 180 and -90 <= position[1] <= 90):
            continue
        by_position[(round(position[0], 7), round(position[1], 7))].append(item)
        grid[(math.floor(position[0] / cell), math.floor(position[1] / cell))].append(
            (item, position)
        )
    for group in by_position.values():
        if len(group) > 1:
            labels = ", ".join(i.label for i in group)
            report.add("warning", "duplicate", group[0].label, f"same position: {labels}")
    reported: set[tuple[int, int]] = set()
    for (gx, gy), members in grid.items():
        for item, position in members:
            key = name_key(item.name)
            if not key:
                continue
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for other, other_position in grid.get((gx + dx, gy + dy), []):
                        pair = (min(item.index, other.index), max(item.index, other.index))
                        if other is item or pair in reported or name_key(other.name) != key:
                            continue
                        if position == other_position:
                            continue  # already reported as the same position
                        gap = haversine_m(position, other_position)
                        if gap <= distance_m:
                            reported.add(pair)
                            report.add(
                                "warning",
                                "duplicate",
                                item.label,
                                f"same name as {other.label}, {gap:.0f} m apart",
                            )


# Command line ------------------------------------------------------------------------------


def parse_bbox(text: str) -> tuple[float, float, float, float]:
    parts = [float(p) for p in text.split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("give west,south,east,north")
    west, south, east, north = parts
    if not (-180 <= west <= 180 and -180 <= east <= 180 and -90 <= south < north <= 90):
        raise argparse.ArgumentTypeError("west,south,east,north out of range or inverted")
    return west, south, east, north


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check a GeoJSON or CSV location dataset for common errors.",
        epilog="Exit codes: 0 no errors, 1 errors found, 2 unreadable file or bad options.",
    )
    parser.add_argument("file", type=Path)
    parser.add_argument("--bbox", type=parse_bbox, help="expected area: west,south,east,north")
    parser.add_argument(
        "--lon-column", help="CSV longitude column (default: lon, longitude, x, ...)"
    )
    parser.add_argument("--lat-column", help="CSV latitude column (default: lat, latitude, y)")
    parser.add_argument(
        "--id-column", help="CSV id column (default: id, code, ref, or one ending in _id)"
    )
    parser.add_argument("--duplicate-distance-m", type=float, default=25.0)
    parser.add_argument("--min-decimals", type=int, default=4)
    parser.add_argument("--max-findings", type=int, default=200)
    args = parser.parse_args(argv)

    report = Report(max_findings=args.max_findings)
    meta: dict[str, Any] = {}
    try:
        if args.file.suffix.lower() == ".csv":
            items = read_csv(args.file, report, args)
        else:
            items, meta = read_geojson(args.file, report)
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        print(json.dumps({"file": str(args.file), "error": str(exc)}), file=sys.stderr)
        return 2
    check(items, meta, args, report)
    errors, warnings = report.total("error"), report.total("warning")
    # Say what could not be checked, so a clean result is not read as more than it is.
    not_checked = []
    if args.bbox is None:
        not_checked.append("bbox: no --bbox given")
    if not any(i.id for i in items):
        not_checked.append(
            "duplicate_id: no ids found (for a CSV, name the column with --id-column)"
        )
    result = {
        "file": str(args.file),
        "items": len(items),
        "with_location": sum(1 for i in items if i.positions),
        "errors": errors,
        "warnings": warnings,
        "by_check": {k: v for k, v in sorted(report.counts.items())},
        "findings": report.findings,
        "findings_shown": f"{len(report.findings)} of {errors + warnings}",
        "not_checked": not_checked,
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
