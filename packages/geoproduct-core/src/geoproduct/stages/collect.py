"""Collection stages: records supplied directly, local files, and provider data."""

import csv
import json
import math
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field, JsonValue, TypeAdapter, ValidationError

from geoproduct.engine.data import BuildingSet, DataKind, RecordSet
from geoproduct.engine.fingerprint import sha256_file, short_id
from geoproduct.engine.stage import Reject, StageContext, StageResult, stage
from geoproduct.errors import GeoProductError
from geoproduct.providers.base import BuildingQuery, Capability, FeatureQuery, TagValue
from geoproduct.schemas import (
    Area,
    Contract,
    ErrorCode,
    Evidence,
    Geometry,
    Record,
    Source,
    SourceType,
)
from geoproduct.schemas._base import ShortText, Slug
from geoproduct.stages._common import PathText, cite, provider_source, provider_use, slug_id

_GEOMETRY: TypeAdapter[Any] = TypeAdapter(Geometry)


# collect.records ---------------------------------------------------------------------------


class RecordsParams(Contract):
    records: Annotated[list[dict[str, JsonValue]], Field(max_length=10_000)]
    sources: list[Source] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)


@stage(
    name="collect.records",
    params=RecordsParams,
    input=None,
    output=DataKind.records,
    summary="Records supplied directly, e.g. researched by the agent, with their sources and "
    "evidence. Each record is validated; invalid ones are rejected with the reason.",
)
def collect_records(ctx: StageContext, data: None, params: RecordsParams) -> StageResult:
    records, rejects = _validated(params.records)
    return StageResult(
        RecordSet(records=records, sources=params.sources, evidence=params.evidence),
        rejects=rejects,
        input_count=len(params.records),
    )


def _validated(items: list[dict[str, JsonValue]]) -> tuple[list[Record], list[Reject]]:
    records: list[Record] = []
    rejects: list[Reject] = []
    seen: set[str] = set()
    for index, item in enumerate(items):
        try:
            record = Record.model_validate(item)
        except ValidationError as exc:
            first = exc.errors()[0]
            where = ".".join(str(p) for p in first["loc"]) or "record"
            rid = item.get("id")
            rejects.append(
                Reject(
                    index=index,
                    record_id=str(rid)[:200] if isinstance(rid, str) and rid else None,
                    reason=f"{where}: {first['msg']}"[:2000],
                )
            )
            continue
        if record.id in seen:
            rejects.append(Reject(index=index, record_id=record.id, reason="duplicate id"))
            continue
        seen.add(record.id)
        records.append(record)
    return records, rejects


# collect.file ------------------------------------------------------------------------------

LON_NAMES = ("lon", "lng", "long", "longitude", "x")
LAT_NAMES = ("lat", "latitude", "y")


class CsvColumns(Contract):
    """Which CSV columns hold what. Unset columns are found by common header names."""

    id: ShortText | None = None
    name: ShortText | None = None
    category: ShortText | None = None
    lon: ShortText | None = None
    lat: ShortText | None = None
    address: ShortText | None = None
    house_number: ShortText | None = None
    street: ShortText | None = None
    locality: ShortText | None = None
    postcode: ShortText | None = None
    country_code: ShortText | None = None


class FileParams(Contract):
    path: PathText
    """Inside the workspace or a folder listed in GEOPRODUCT_ALLOWED_DIRS."""
    format: Literal["auto", "csv", "geojson", "json"] = "auto"
    title: ShortText | None = None
    licence: ShortText | None = None
    """The data's licence, if known. Recorded on the source; never guessed."""
    category: ShortText | None = None
    """Category for records whose file gives none."""
    columns: CsvColumns = Field(default_factory=CsvColumns)
    max_bytes: Annotated[int, Field(ge=1, le=500_000_000)] = 50_000_000
    max_rows: Annotated[int, Field(ge=1, le=1_000_000)] = 100_000


@stage(
    name="collect.file",
    params=FileParams,
    input=None,
    output=DataKind.records,
    summary="Records from a local CSV, GeoJSON or JSON file. Rows with unusable coordinates "
    "are rejected with the reason; rows without coordinates are kept, unresolved.",
)
def collect_file(ctx: StageContext, data: None, params: FileParams) -> StageResult:
    path = ctx.workspace.user_path(params.path)
    size = path.stat().st_size
    if size > params.max_bytes:
        raise GeoProductError(
            ErrorCode.too_large,
            f"The file is {size} bytes, more than max_bytes ({params.max_bytes}).",
            hint="Raise max_bytes if the file is expected to be this large.",
        )
    fmt: str = params.format
    if fmt == "auto":
        fmt = {".csv": "csv", ".geojson": "geojson", ".json": "json"}.get(path.suffix.lower(), "")
        if not fmt:
            raise GeoProductError(
                ErrorCode.invalid_input,
                f"Cannot tell the format of '{path.name}' from its extension.",
                hint="Set format to csv, geojson or json.",
            )
    content_hash = sha256_file(path)
    source = Source(
        id=short_id("src", "file", content_hash),
        title=params.title or path.name,
        type=SourceType.user_file,
        url_or_ref=path.name,
        licence=params.licence,
        content_sha256=content_hash,
        coverage_note=f"{fmt} file supplied by the user",
    )
    stem = slug_id(path.stem) or "file"
    if fmt == "csv":
        return _read_csv(ctx, path, params, source, stem)
    return _read_json(ctx, path, params, source, stem, geojson=fmt == "geojson")


def _read_csv(
    ctx: StageContext, path: Path, params: FileParams, source: Source, stem: str
) -> StageResult:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        headers = [h for h in reader.fieldnames or [] if h]
        if not headers:
            raise GeoProductError(ErrorCode.invalid_input, "The CSV file has no header row.")
        lookup = {h.strip().casefold(): h for h in headers}

        def column(explicit: str | None, *names: str) -> str | None:
            if explicit:
                if explicit not in headers:
                    raise GeoProductError(
                        ErrorCode.invalid_input,
                        f"The CSV has no column '{explicit}'.",
                        details={"columns": list(headers)},
                    )
                return explicit
            return next((lookup[n] for n in names if n in lookup), None)

        cols = params.columns
        mapping = {
            "id": column(cols.id, "id"),
            "name": column(cols.name, "name", "title"),
            "category": column(cols.category, "category", "type"),
            "lon": column(cols.lon, *LON_NAMES),
            "lat": column(cols.lat, *LAT_NAMES),
            "address": column(cols.address, "address", "full_address"),
            "house_number": column(cols.house_number, "house_number", "housenumber"),
            "street": column(cols.street, "street"),
            "locality": column(cols.locality, "locality", "city", "town"),
            "postcode": column(cols.postcode, "postcode", "zip", "postal_code"),
            "country_code": column(cols.country_code, "country_code"),
        }
        used = {c for c in mapping.values() if c}

        records: list[Record] = []
        evidence: list[Evidence] = []
        rejects: list[Reject] = []
        seen: set[str] = set()
        rows = 0
        for rows, row in enumerate(reader, start=1):
            if rows > params.max_rows:
                raise GeoProductError(
                    ErrorCode.too_large,
                    f"The file has more than max_rows ({params.max_rows}) rows.",
                )
            if rows % 500 == 0:
                ctx.check()
                ctx.progress(rows, None, "reading rows")
            values = {k: (v or "").strip() for k, v in row.items() if k}

            def get(field: str, values: dict[str, str] = values) -> str:
                col = mapping[field]
                return values.get(col, "") if col else ""

            index = rows - 1
            rid = slug_id(get("id")) or f"{stem}:row-{rows}"
            geometry, problem = _point(get("lon"), get("lat"))
            if problem:
                rejects.append(Reject(index=index, record_id=rid, reason=problem))
                continue
            address = {
                key: get(key)
                for key in ("house_number", "street", "locality", "postcode")
                if get(key)
            }
            if get("address"):
                address["full"] = get("address")
            if get("country_code"):
                address["country_code"] = get("country_code").upper()
            if not (get("name") or address or geometry):
                rejects.append(
                    Reject(index=index, record_id=rid, reason="no name, address or location")
                )
                continue
            if rid in seen:
                rejects.append(Reject(index=index, record_id=rid, reason="duplicate id"))
                continue
            attributes = {
                key: {"value": value, "basis": "fact"}
                for key, value in values.items()
                if key not in used and value
            }
            try:
                record = Record.model_validate(
                    {
                        "id": rid,
                        "name": get("name") or None,
                        "category": get("category") or params.category,
                        "address": address or None,
                        "attributes": attributes,
                        "geometry": geometry,
                        "location": {"method": "file"} if geometry else {},
                    }
                )
            except ValidationError as exc:
                first = exc.errors()[0]
                reason = f"{'.'.join(str(p) for p in first['loc'])}: {first['msg']}"
                rejects.append(Reject(index=index, record_id=rid, reason=reason))
                continue
            seen.add(rid)
            record, ev = cite(record, source, f"row {rows}")
            records.append(record)
            evidence.append(ev)

    return StageResult(
        RecordSet(records=records, sources=[source], evidence=evidence),
        rejects=rejects,
        input_count=rows,
    )


def _point(lon_text: str, lat_text: str) -> tuple[dict[str, Any] | None, str | None]:
    if not lon_text and not lat_text:
        return None, None
    if not lon_text or not lat_text:
        return None, "only one of longitude and latitude is given"
    try:
        lon, lat = float(lon_text), float(lat_text)
    except ValueError:
        return None, f"coordinates are not numbers: '{lon_text}', '{lat_text}'"
    if not (math.isfinite(lon) and math.isfinite(lat)):
        return None, "coordinates are not finite numbers"
    if not -180 <= lon <= 180:
        return None, f"longitude {lon_text} is outside -180..180"
    if not -90 <= lat <= 90:
        return None, f"latitude {lat_text} is outside -90..90"
    return {"type": "Point", "coordinates": [lon, lat]}, None


def _read_json(
    ctx: StageContext, path: Path, params: FileParams, source: Source, stem: str, *, geojson: bool
) -> StageResult:
    try:
        content = json.loads(path.read_text(encoding="utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GeoProductError(
            ErrorCode.invalid_input, f"'{path.name}' is not valid JSON: {exc}"
        ) from None

    if isinstance(content, dict) and content.get("type") in ("FeatureCollection", "Feature"):
        return _read_features(ctx, content, params, source, stem)
    if geojson:
        raise GeoProductError(
            ErrorCode.invalid_input, "A GeoJSON file must hold a Feature or FeatureCollection."
        )
    if isinstance(content, list):
        items, sources, evidence = content, [], []
    elif isinstance(content, dict) and isinstance(content.get("records"), list):
        items = content["records"]
        sources = [Source.model_validate(s) for s in content.get("sources", [])]
        evidence = [Evidence.model_validate(e) for e in content.get("evidence", [])]
    else:
        raise GeoProductError(
            ErrorCode.invalid_input,
            "A JSON file must hold a list of records, or an object with a 'records' list.",
        )
    if len(items) > params.max_rows:
        raise GeoProductError(ErrorCode.too_large, "The file has more than max_rows records.")
    records, rejects = _validated([i if isinstance(i, dict) else {} for i in items])
    return StageResult(
        RecordSet(records=records, sources=[source, *sources], evidence=evidence),
        rejects=rejects,
        input_count=len(items),
    )


def _read_features(
    ctx: StageContext, content: dict[str, Any], params: FileParams, source: Source, stem: str
) -> StageResult:
    crs = (content.get("crs") or {}).get("properties", {}).get("name", "")
    if crs and not any(code in str(crs) for code in ("CRS84", "4326")):
        raise GeoProductError(
            ErrorCode.invalid_input,
            f"The file declares the coordinate system '{crs}'. GeoJSON must be WGS 84.",
            hint="Reproject the file to WGS 84 (EPSG:4326) longitude/latitude first.",
        )
    features = content["features"] if content["type"] == "FeatureCollection" else [content]
    if len(features) > params.max_rows:
        raise GeoProductError(ErrorCode.too_large, "The file has more than max_rows features.")
    carried_sources = [Source.model_validate(s) for s in content.get("sources", [])]
    carried_evidence = [Evidence.model_validate(e) for e in content.get("evidence", [])]

    records: list[Record] = []
    evidence: list[Evidence] = []
    rejects: list[Reject] = []
    seen: set[str] = set()
    for index, feature in enumerate(features):
        if index % 500 == 0:
            ctx.check()
        properties = dict(feature.get("properties") or {})
        try:
            if properties.get("schema_version") == "geoproduct/v1":
                record = Record.from_feature(feature)
                own_evidence = True
            else:
                record = _generic_record(feature, properties, params, stem, index)
                own_evidence = False
        except (ValidationError, ValueError) as exc:
            rid = feature.get("id") or properties.get("id")
            rejects.append(
                Reject(
                    index=index,
                    record_id=str(rid)[:200] if rid else None,
                    reason=_first_error(exc),
                )
            )
            continue
        if record.id in seen:
            rejects.append(Reject(index=index, record_id=record.id, reason="duplicate id"))
            continue
        seen.add(record.id)
        if not own_evidence:
            record, ev = cite(record, source, f"feature {index + 1}")
            evidence.append(ev)
        records.append(record)

    return StageResult(
        RecordSet(
            records=records,
            sources=[source, *carried_sources],
            evidence=carried_evidence + evidence,
        ),
        rejects=rejects,
        input_count=len(features),
    )


def _generic_record(
    feature: dict[str, Any], properties: dict[str, Any], params: FileParams, stem: str, index: int
) -> Record:
    raw_id = feature.get("id", properties.pop("id", None))
    rid = slug_id(str(raw_id)) if raw_id is not None else ""
    geometry = feature.get("geometry")
    if geometry is not None:
        _GEOMETRY.validate_python(geometry)
    name = properties.pop("name", None)
    category = properties.pop("category", None) or params.category
    attributes = {
        key: {"value": value, "basis": "fact"}
        for key, value in sorted(properties.items())
        if value is not None and value != ""
    }
    return Record.model_validate(
        {
            "id": rid or f"{stem}:feature-{index + 1}",
            "name": str(name) if name else None,
            "category": str(category) if category else None,
            "attributes": attributes,
            "geometry": geometry,
            "location": {"method": "file"} if geometry else {},
        }
    )


def _first_error(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        first = exc.errors()[0]
        return f"{'.'.join(str(p) for p in first['loc']) or 'feature'}: {first['msg']}"[:2000]
    return str(exc)[:2000] or type(exc).__name__


# collect.features / collect.buildings ------------------------------------------------------


class FeaturesParams(Contract):
    provider: Slug
    area: Area
    tags: Annotated[dict[ShortText, TagValue], Field(min_length=1)]
    """Key to value, list of values, or null for any value; every key must match."""
    limit: Annotated[int, Field(ge=1, le=10_000)] = 1_000
    allow_truncated: bool = False
    """Accept a result cut off at ``limit``; otherwise that is an error."""


@stage(
    name="collect.features",
    params=FeaturesParams,
    input=None,
    output=DataKind.records,
    summary="Features matching tags in an area, from a provider. Each record cites the "
    "provider as its source.",
)
def collect_features(ctx: StageContext, data: None, params: FeaturesParams) -> StageResult:
    provider = ctx.providers.require(params.provider, Capability.features)
    ctx.check()
    query = FeatureQuery(area=params.area, tags=params.tags, limit=params.limit)
    result = provider.features(query)
    notes = _check_truncated(result.truncated, params.limit, params.allow_truncated)
    source = provider_source(provider.info, "features", query, result)
    records: list[Record] = []
    evidence: list[Evidence] = []
    seen: set[str] = set()
    for record in result.items:
        if record.id in seen:
            continue
        seen.add(record.id)
        locator = record.external_ids.get(provider.info.name, record.id)
        record, ev = cite(record, source, locator)
        records.append(record)
        evidence.append(ev)
    # An empty answer cites nothing, so it adds no source and no licence duty. The query
    # itself stays on record under `providers`.
    return StageResult(
        RecordSet(records=records, sources=[source] if records else [], evidence=evidence),
        input_count=len(result.items),
        providers=[provider_use(provider.info, [result])],
        notes=notes,
    )


class BuildingsParams(Contract):
    provider: Slug
    area: Area
    limit: Annotated[int, Field(ge=1, le=50_000)] = 10_000
    allow_truncated: bool = False


@stage(
    name="collect.buildings",
    params=BuildingsParams,
    input=None,
    output=DataKind.buildings,
    summary="Building footprints in an area, from a provider.",
)
def collect_buildings(ctx: StageContext, data: None, params: BuildingsParams) -> StageResult:
    provider = ctx.providers.require(params.provider, Capability.buildings)
    ctx.check()
    query = BuildingQuery(area=params.area, limit=params.limit)
    result = provider.buildings(query)
    notes = _check_truncated(result.truncated, params.limit, params.allow_truncated)
    source = provider_source(provider.info, "buildings", query, result)
    return StageResult(
        BuildingSet(buildings=result.items, sources=[source] if result.items else []),
        input_count=len(result.items),
        providers=[provider_use(provider.info, [result])],
        notes=notes,
    )


def _check_truncated(truncated: bool, limit: int, allowed: bool) -> list[str]:
    if not truncated:
        return []
    if not allowed:
        raise GeoProductError(
            ErrorCode.too_large,
            f"More than {limit} items match, so the result would be incomplete.",
            hint="Narrow the area or tags, raise limit, or set allow_truncated: true to "
            "accept a partial result knowingly.",
        )
    return [f"result truncated at {limit} items; more exist"]
