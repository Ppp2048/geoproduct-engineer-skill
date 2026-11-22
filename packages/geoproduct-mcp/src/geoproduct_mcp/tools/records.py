"""Records: creating datasets and running the record stages on them.

Each tool runs one registered stage on stored datasets and stores the result as a new
dataset, so earlier results stay available and every step can be inspected.
"""

from collections.abc import Callable
from typing import Any, Literal

from pydantic import JsonValue

from geoproduct.engine.data import RecordSet, StageData
from geoproduct.schemas import Evidence, ExcerptCheck, Source
from geoproduct_mcp.tools.common import (
    Done,
    Job,
    ServerState,
    dataset_result,
    describe,
    invalid,
    ref,
    run_stage,
)
from geoproduct_mcp.tools.sources import EXTENSIONS, TABULAR, check_excerpts


def _record_stage(
    state: ServerState,
    job: Job,
    stage: str,
    tool: str,
    dataset_id: str,
    params: dict[str, Any],
    *,
    refs: dict[str, str] | None = None,
    name: str | None = None,
) -> Done:
    info, data, _ = run_stage(
        state,
        job,
        stage,
        params=params,
        input_id=dataset_id,
        refs=refs,
        name=name,
        created_by=tool,
    )
    return Done(dataset_result(info, data), describe(info))


def _adopt(source: Source) -> Callable[[StageData], StageData]:
    """Credit records loaded from a fetched source to that registered source."""

    def adjust(data: StageData) -> StageData:
        assert isinstance(data, RecordSet)
        replaced = {s.id for s in data.sources}
        evidence = [
            e.model_copy(update={"source_id": source.id}) if e.source_id in replaced else e
            for e in data.evidence
        ]
        return data.model_copy(update={"sources": [source], "evidence": evidence})

    return adjust


def create_dataset(
    state: ServerState,
    job: Job,
    *,
    name: str | None = None,
    records: list[dict[str, JsonValue]] | None = None,
    evidence: list[Evidence] | None = None,
    source_ids: list[str] | None = None,
    file: str | None = None,
    source_id: str | None = None,
    dataset_ids: list[str] | None = None,
    format: Literal["auto", "csv", "geojson", "json"] = "auto",
    columns: dict[str, str] | None = None,
    category: str | None = None,
    licence: str | None = None,
) -> Done:
    if sum(x is not None for x in (records, file, source_id, dataset_ids)) != 1:
        raise invalid(
            "Give exactly one of records, file, source_id or dataset_ids.",
            hint="records: inline records you researched; file: a local CSV, GeoJSON or "
            "JSON file; source_id: a fetched source holding such a file; dataset_ids: "
            "records datasets to combine.",
        )
    if dataset_ids is not None:
        return _combined(state, job, name, dataset_ids)
    if records is not None:
        return _from_records(state, job, name, records, evidence or [], source_ids or [])
    params: dict[str, Any] = {"format": format, "columns": columns or {}}
    if category:
        params["category"] = category
    adjust = None
    if source_id is not None:
        source = state.sources.get(source_id)
        folder = state.sources.folder(source_id)
        content = next(
            (
                folder / f"content{EXTENSIONS[k]}"
                for k in TABULAR
                if (folder / f"content{EXTENSIONS[k]}").is_file()
            ),
            None,
        )
        if content is None:
            raise invalid(
                f"Source {source_id} has no fetched CSV, JSON or GeoJSON content.",
                hint="Call fetch_source_data first; only tabular content can be loaded.",
            )
        params |= {"path": str(content), "title": source.title}
        if source.licence or licence:
            params["licence"] = source.licence or licence
        adjust = _adopt(source)
    else:
        assert file is not None
        params |= {"path": file, "title": name or None}
        if licence:
            params["licence"] = licence
    info, data, _ = run_stage(
        state,
        job,
        "collect.file",
        params={k: v for k, v in params.items() if v is not None},
        name=name,
        created_by="create_dataset",
        adjust=adjust,
    )
    return Done(dataset_result(info, data), describe(info))


def _combined(state: ServerState, job: Job, name: str | None, dataset_ids: list[str]) -> Done:
    """Append the records of several datasets, with their sources and evidence."""
    if len(dataset_ids) < 2 or len(set(dataset_ids)) != len(dataset_ids):
        raise invalid("dataset_ids needs two or more different datasets.")
    first, *others = dataset_ids
    aliases = {f"part-{i}": dataset_id for i, dataset_id in enumerate(others, start=2)}
    info, data, _ = run_stage(
        state,
        job,
        "combine.records",
        params={"add": [ref(alias) for alias in aliases]},
        input_id=first,
        refs=aliases,
        name=name,
        created_by="create_dataset",
    )
    return Done(dataset_result(info, data), describe(info))


def _from_records(
    state: ServerState,
    job: Job,
    name: str | None,
    records: list[dict[str, JsonValue]],
    evidence: list[Evidence],
    source_ids: list[str],
) -> Done:
    checked = check_excerpts(state, evidence)
    cited = sorted({e.source_id for e in checked} | set(source_ids))
    sources = [state.sources.get(sid) for sid in cited]
    params = {
        "records": records,
        "sources": [s.model_dump(mode="json") for s in sources],
        "evidence": [e.model_dump(mode="json") for e in checked],
    }
    info, data, _ = run_stage(
        state, job, "collect.records", params=params, name=name, created_by="create_dataset"
    )
    summary = describe(info)
    tally = {status: 0 for status in ExcerptCheck}
    for item in checked:
        if item.excerpt:
            tally[item.excerpt_check] += 1
    if any(tally.values()):
        summary += (
            f" Excerpts: {tally[ExcerptCheck.verified]} verified, "
            f"{tally[ExcerptCheck.not_found]} not found in their source, "
            f"{tally[ExcerptCheck.not_checked]} not checked (source not fetched)."
        )
    if tally[ExcerptCheck.not_found]:
        summary += " Re-read the sources: an excerpt that is not in its source is not evidence."
    return Done(dataset_result(info, data), summary)


def normalize_records(
    state: ServerState,
    job: Job,
    *,
    dataset_id: str,
    categories: dict[str, str] | None = None,
    aliases: dict[str, list[str]] | None = None,
    coordinate_decimals: int = 7,
    name: str | None = None,
) -> Done:
    params = {
        "categories": categories or {},
        "aliases": aliases or {},
        "coordinate_decimals": coordinate_decimals,
    }
    return _record_stage(
        state, job, "normalize.records", "normalize_records", dataset_id, params, name=name
    )


def deduplicate_entities(
    state: ServerState,
    job: Job,
    *,
    dataset_id: str,
    name_threshold: float = 0.88,
    max_distance_m: float = 150.0,
    id_keys: list[str] | None = None,
    same_category: bool = True,
    name: str | None = None,
) -> Done:
    params: dict[str, Any] = {
        "name_threshold": name_threshold,
        "max_distance_m": max_distance_m,
        "same_category": same_category,
    }
    if id_keys is not None:
        params["id_keys"] = id_keys
    return _record_stage(
        state, job, "resolve.deduplicate", "deduplicate_entities", dataset_id, params, name=name
    )


def match_building_footprints(
    state: ServerState,
    job: Job,
    *,
    dataset_id: str,
    buildings_dataset_id: str,
    max_distance_m: float = 30.0,
    assign_geometry: Literal["none", "exact", "exact_or_probable"] = "none",
    options: dict[str, JsonValue] | None = None,
    name: str | None = None,
) -> Done:
    params = {
        **(options or {}),
        "buildings": ref("buildings"),
        "max_distance_m": max_distance_m,
        "assign_geometry": assign_geometry,
    }
    return _record_stage(
        state,
        job,
        "match.buildings",
        "match_building_footprints",
        dataset_id,
        params,
        refs={"buildings": buildings_dataset_id},
        name=name,
    )


def enrich_records(
    state: ServerState,
    job: Job,
    *,
    dataset_id: str,
    operation: Literal["nearby", "within", "measure"],
    reference_dataset_id: str | None = None,
    field: str | None = None,
    radius_m: float | None = None,
    categories: list[str] | None = None,
    options: dict[str, JsonValue] | None = None,
    name: str | None = None,
) -> Done:
    params: dict[str, Any] = dict(options or {})
    refs = None
    if operation in ("nearby", "within"):
        if reference_dataset_id is None or field is None:
            raise invalid(
                f"The '{operation}' operation needs reference_dataset_id and field.",
                hint="field names the attribute to add, e.g. 'district' or 'nearest_station'.",
            )
        params |= {"reference": ref("reference"), "name": field}
        if categories is not None:
            params["categories"] = categories
        if radius_m is not None:
            if operation != "nearby":
                raise invalid("radius_m applies only to the 'nearby' operation.")
            params["radius_m"] = radius_m
        refs = {"reference": reference_dataset_id}
    elif reference_dataset_id is not None or field is not None:
        raise invalid("The 'measure' operation takes no reference dataset or field.")
    return _record_stage(
        state,
        job,
        f"enrich.{operation}",
        "enrich_records",
        dataset_id,
        params,
        refs=refs,
        name=name,
    )


def score_confidence(
    state: ServerState,
    job: Job,
    *,
    dataset_id: str,
    high: float = 0.75,
    medium: float = 0.5,
    low: float = 0.25,
    name: str | None = None,
) -> Done:
    params = {"high": high, "medium": medium, "low": low}
    return _record_stage(
        state, job, "quality.confidence", "score_confidence", dataset_id, params, name=name
    )
