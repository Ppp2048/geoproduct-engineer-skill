"""The built-in pipeline stages.

Pipelines can only use stages registered here (or in a registry a caller builds itself), so
a pipeline spec is configuration for reviewed code, never code.
"""

from geoproduct.engine.stage import StageRegistry
from geoproduct.stages.collect import (
    collect_buildings,
    collect_features,
    collect_file,
    collect_records,
)
from geoproduct.stages.output import export_csv, export_geojson, report_quality
from geoproduct.stages.prepare import (
    combine_records,
    deduplicate,
    geocode_records,
    normalize_records,
)
from geoproduct.stages.quality import quality_confidence, quality_gate, validate_dataset
from geoproduct.stages.spatial import enrich_measure, enrich_nearby, enrich_within, match_buildings

BUILTIN_STAGES = (
    collect_records,
    collect_file,
    collect_features,
    collect_buildings,
    combine_records,
    normalize_records,
    deduplicate,
    geocode_records,
    match_buildings,
    enrich_nearby,
    enrich_within,
    enrich_measure,
    quality_confidence,
    validate_dataset,
    quality_gate,
    export_geojson,
    export_csv,
    report_quality,
)


def builtin_registry() -> StageRegistry:
    """A new registry holding every built-in stage."""
    registry = StageRegistry()
    for definition in BUILTIN_STAGES:
        registry.add(definition)
    return registry


__all__ = ["BUILTIN_STAGES", "builtin_registry"]
