"""The GeoProduct Engineer MCP server.

Run it with ``geoproduct-mcp`` (stdio, for local clients) or ``geoproduct-mcp --http``
(Streamable HTTP on 127.0.0.1). Configuration comes from the environment; see
``docs/configuration.md``. The server has no authentication, so HTTP is bound to the
loopback interface only.
"""

import argparse
import ipaddress
import json
import logging
import sys
from collections.abc import Sequence
from datetime import date
from importlib import resources
from typing import Annotated, Any, Final, Literal

from mcp.server.mcpserver import Context, MCPServer
from mcp_types import CallToolResult, ToolAnnotations
from pydantic import Field, JsonValue

from geoproduct.errors import GeoProductError
from geoproduct.providers.base import GeocodeCandidate, ProviderResult
from geoproduct.schemas import Area, Evidence, SourceEvaluation, SourceType
from geoproduct.schemas.export import PUBLISHED, json_schema
from geoproduct.stages.quality import CHECKS
from geoproduct.workspace import DEFAULT_WORKSPACE, ENV_WORKSPACE, Workspace
from geoproduct_mcp.calls import call
from geoproduct_mcp.tools import geography, pipelines, quality, sources
from geoproduct_mcp.tools import records as record_tools
from geoproduct_mcp.tools.common import CONFIRM_ABOVE_CALLS, DatasetResult, ServerState

try:
    from importlib.metadata import version

    VERSION = version("geoproduct-mcp")
except Exception:  # running from a source tree
    VERSION = "0.0.0"

log = logging.getLogger("geoproduct_mcp")

INSTRUCTIONS: Final = (
    "Tools for building evidence-backed geographic datasets. This server does not research: "
    "find and judge sources with your own tools, then register them, attach evidence to "
    "records, and use these tools to geocode, collect map data, resolve duplicates, match "
    "buildings, validate and export. Never invent coordinates, URLs, building links or "
    "attributes. Read geoproduct://guide for the method and geoproduct://stages for pipeline "
    "stages. Data is passed by id (dataset_id, source_id, run_id)."
)

READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
READ_ONLY_REMOTE = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
WRITES = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)
WRITES_REMOTE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True)

DatasetId = Annotated[
    str,
    Field(
        description="A dataset id returned by a tool (ds_...), or '<run_id>/<stage_id>' "
        "for the output of a finished pipeline stage.",
        max_length=200,
    ),
]
NewName = Annotated[
    str | None, Field(description="An optional label for the new dataset.", max_length=200)
]
ProviderName = Annotated[
    str,
    Field(
        description="Provider name; geoproduct://providers lists them. 'fixture' is an "
        "offline synthetic town for trying things out.",
        pattern=r"^[a-z][a-z0-9_-]{0,63}$",
    ),
]
Options = Annotated[
    dict[str, JsonValue] | None,
    Field(description="Further stage parameters, as listed in geoproduct://stages."),
]
CopyTo = Annotated[
    str | None,
    Field(
        description="Also copy the files to this folder, which must be inside the workspace "
        "or an allowed folder.",
        max_length=1000,
    ),
]


def create_server(state: ServerState) -> MCPServer:
    server: MCPServer = MCPServer(
        name="geoproduct",
        title="GeoProduct Engineer",
        version=VERSION,
        instructions=INSTRUCTIONS,
        website_url="https://github.com/Ppp2048/geoproduct-engineer-skill",
    )
    _sources(server, state)
    _geography(server, state)
    _records(server, state)
    _quality(server, state)
    _pipelines(server, state)
    _resources(server, state)
    _prompts(server)
    return server


# Sources ------------------------------------------------------------------------------------


def _sources(server: MCPServer, state: ServerState) -> None:
    @server.tool(title="Register a source", annotations=WRITES)
    async def register_source(
        title: Annotated[str, Field(min_length=1, max_length=200)],
        type: SourceType,
        url_or_ref: Annotated[
            str,
            Field(
                description="The URL, file path, dataset identifier or citation, exactly as "
                "found. Never a guessed URL.",
                min_length=1,
                max_length=2000,
            ),
        ],
        publisher: Annotated[str | None, Field(max_length=200)] = None,
        licence: Annotated[
            str | None,
            Field(description="SPDX id or licence name; leave empty if unknown.", max_length=200),
        ] = None,
        attribution: Annotated[str | None, Field(max_length=2000)] = None,
        data_as_of: date | None = None,
        coverage_note: Annotated[str | None, Field(max_length=2000)] = None,
        evaluation: Annotated[
            SourceEvaluation | None,
            Field(
                description="Your ratings, 0 (unacceptable or unknown) to 3 (strong), each "
                "with a rationale. The score is computed from them."
            ),
        ] = None,
    ) -> Annotated[CallToolResult, sources.SourceResult]:
        """Record a source you found and your rating of it; the server computes its score.

        Registering the same type and URL again updates the record. The server does not
        search for sources and does not judge them: it records your judgement with fixed,
        published weights."""
        return await call(
            lambda job: sources.register_source(
                state,
                title=title,
                type=type,
                url_or_ref=url_or_ref,
                publisher=publisher,
                licence=licence,
                attribution=attribution,
                data_as_of=data_as_of,
                coverage_note=coverage_note,
                evaluation=evaluation,
            )
        )

    @server.tool(title="Fetch a source", annotations=WRITES_REMOTE)
    async def fetch_source_data(
        source_id: Annotated[str, Field(description="From register_source.", max_length=64)],
    ) -> Annotated[CallToolResult, sources.FetchResult]:
        """Fetch a registered source's URL (or read its file) and keep a hashed copy.

        Only public http(s) addresses, an allowlist of content types (HTML, text, CSV,
        JSON, GeoJSON, XML, PDF), 20 MB and 30 s at most; robots.txt is respected. The text
        is kept so evidence excerpts can be verified, and CSV, JSON or GeoJSON content can
        be loaded with create_dataset(source_id=...)."""
        return await call(lambda job: sources.fetch_source_data(state, job, source_id=source_id))


# Geography ----------------------------------------------------------------------------------


def _geography(server: MCPServer, state: ServerState) -> None:
    @server.tool(title="Geocode an address or place", annotations=READ_ONLY_REMOTE)
    async def geocode(
        text: Annotated[str, Field(min_length=1, max_length=500)],
        provider: ProviderName = "nominatim",
        limit: Annotated[int, Field(ge=1, le=20)] = 5,
        country_codes: Annotated[
            list[Annotated[str, Field(pattern=r"^[a-z]{2}$")]] | None,
            Field(description="Restrict to these ISO 3166-1 alpha-2 codes, lower case."),
        ] = None,
        bias: Annotated[Area | None, Field(description="Prefer results in this area.")] = None,
        language: Annotated[str | None, Field(pattern=r"^[a-z]{2,3}(-[A-Za-z]{2,4})?$")] = None,
    ) -> Annotated[CallToolResult, ProviderResult[GeocodeCandidate]]:
        """Find coordinates for an address or place name. Returns candidates with their
        location kind, precision and the provider's attribution.

        When several candidates come back without scores, compare them with your evidence;
        do not take the first one on trust."""
        return await call(
            lambda job: geography.geocode(
                state,
                text=text,
                provider=provider,
                limit=limit,
                country_codes=country_codes,
                bias=bias,
                language=language,
            )
        )

    @server.tool(title="Reverse geocode a point", annotations=READ_ONLY_REMOTE)
    async def reverse_geocode(
        lon: Annotated[float, Field(ge=-180, le=180)],
        lat: Annotated[float, Field(ge=-90, le=90)],
        provider: ProviderName = "nominatim",
        language: Annotated[str | None, Field(pattern=r"^[a-z]{2,3}(-[A-Za-z]{2,4})?$")] = None,
    ) -> Annotated[CallToolResult, ProviderResult[GeocodeCandidate]]:
        """Find the address or place at a point (longitude, latitude in WGS 84)."""
        return await call(
            lambda job: geography.reverse_geocode(
                state, lon=lon, lat=lat, provider=provider, language=language
            )
        )

    @server.tool(title="Geocode the records of a dataset", annotations=WRITES_REMOTE)
    async def geocode_records(
        dataset_id: DatasetId,
        provider: ProviderName = "nominatim",
        country_codes: Annotated[
            list[Annotated[str, Field(pattern=r"^[a-z]{2}$")]] | None,
            Field(description="Restrict to these ISO 3166-1 alpha-2 codes, lower case."),
        ] = None,
        bias: Annotated[Area | None, Field(description="Prefer results in this area.")] = None,
        use_names: Annotated[
            bool, Field(description="Geocode by name when a record has no address.")
        ] = True,
        overwrite: Annotated[
            bool, Field(description="Also geocode records that already have a location.")
        ] = False,
        options: Options = None,
        confirm: Annotated[
            bool,
            Field(
                description=f"Needed when this would make more than {CONFIRM_ABOVE_CALLS} "
                "requests to an online provider. Ask the user first."
            ),
        ] = False,
        name: NewName = None,
        ctx: Context | None = None,
    ) -> Annotated[CallToolResult, DatasetResult]:
        """Locate the records of a dataset that have an address (or a name) but no geometry,
        one request per record. Each location cites the geocoder as evidence. Ambiguous or
        weak answers leave the record unresolved, with the reason in its limitations."""
        return await call(
            lambda job: geography.geocode_records(
                state,
                job,
                dataset_id=dataset_id,
                provider=provider,
                country_codes=country_codes,
                bias=bias,
                use_names=use_names,
                overwrite=overwrite,
                options=options,
                confirm=confirm,
                name=name,
            ),
            ctx,
        )

    @server.tool(title="Search OpenStreetMap features", annotations=WRITES_REMOTE)
    async def search_osm_features(
        area: Area,
        tags: Annotated[
            dict[str, str | list[str] | None],
            Field(
                description="OSM tags to match, all of them: a value, a list of values, or "
                'null for any value. E.g. {"amenity": ["hospital", "clinic"]}.',
                min_length=1,
            ),
        ],
        provider: ProviderName = "overpass",
        limit: Annotated[int, Field(ge=1, le=10_000)] = 1000,
        allow_truncated: Annotated[
            bool, Field(description="Accept a result cut off at the limit.")
        ] = False,
        name: NewName = None,
        ctx: Context | None = None,
    ) -> Annotated[CallToolResult, DatasetResult]:
        """Collect map features with given tags in an area, as a records dataset with
        provenance. A result cut off at the limit is an error unless allow_truncated."""
        return await call(
            lambda job: geography.search_osm_features(
                state,
                job,
                area=area,
                tags=tags,
                provider=provider,
                limit=limit,
                allow_truncated=allow_truncated,
                name=name,
            ),
            ctx,
        )

    @server.tool(title="Get building footprints", annotations=WRITES_REMOTE)
    async def get_building_footprints(
        area: Area,
        provider: ProviderName = "overpass",
        limit: Annotated[int, Field(ge=1, le=50_000)] = 10_000,
        allow_truncated: bool = False,
        name: NewName = None,
        ctx: Context | None = None,
    ) -> Annotated[CallToolResult, DatasetResult]:
        """Collect building footprints in an area, as a buildings dataset for
        match_building_footprints or validate_dataset rules."""
        return await call(
            lambda job: geography.get_building_footprints(
                state,
                job,
                area=area,
                provider=provider,
                limit=limit,
                allow_truncated=allow_truncated,
                name=name,
            ),
            ctx,
        )


# Records ------------------------------------------------------------------------------------


def _records(server: MCPServer, state: ServerState) -> None:
    @server.tool(title="Create a dataset", annotations=WRITES)
    async def create_dataset(
        name: NewName = None,
        records: Annotated[
            list[dict[str, JsonValue]] | None,
            Field(
                description="Records you researched, in the Record schema "
                "(geoproduct://schemas/v1/record). Invalid ones are rejected with the reason.",
                max_length=10_000,
            ),
        ] = None,
        evidence: Annotated[
            list[Evidence] | None,
            Field(
                description="Evidence the records cite, each pointing at a registered "
                "source. Excerpts are checked against fetched sources."
            ),
        ] = None,
        source_ids: Annotated[
            list[str] | None, Field(description="Further registered sources to include.")
        ] = None,
        file: Annotated[
            str | None,
            Field(
                description="Or: a CSV, GeoJSON or JSON file inside the workspace or an "
                "allowed folder.",
                max_length=1000,
            ),
        ] = None,
        source_id: Annotated[
            str | None,
            Field(description="Or: a fetched source whose content is CSV, JSON or GeoJSON."),
        ] = None,
        dataset_ids: Annotated[
            list[str] | None,
            Field(
                description="Or: two or more records datasets to combine into one, e.g. a "
                "researched list and map features, before deduplicate_entities. A record "
                "id already present is rejected.",
                min_length=2,
                max_length=20,
            ),
        ] = None,
        format: Literal["auto", "csv", "geojson", "json"] = "auto",
        columns: Annotated[
            dict[str, str] | None,
            Field(
                description="For CSV: which column holds id, name, category, lon, lat, "
                "address, house_number, street, locality, postcode or country_code."
            ),
        ] = None,
        category: Annotated[str | None, Field(max_length=200)] = None,
        licence: Annotated[str | None, Field(max_length=200)] = None,
        ctx: Context | None = None,
    ) -> Annotated[CallToolResult, DatasetResult]:
        """Create a records dataset from exactly one of: inline records with their evidence,
        a local file, a fetched source, or existing datasets to combine."""
        return await call(
            lambda job: record_tools.create_dataset(
                state,
                job,
                name=name,
                records=records,
                evidence=evidence,
                source_ids=source_ids,
                file=file,
                source_id=source_id,
                dataset_ids=dataset_ids,
                format=format,
                columns=columns,
                category=category,
                licence=licence,
            ),
            ctx,
        )

    @server.tool(title="Normalize records", annotations=WRITES)
    async def normalize_records(
        dataset_id: DatasetId,
        categories: Annotated[
            dict[str, str] | None,
            Field(description='Rename categories, e.g. {"amenity=clinic": "healthcare=clinic"}.'),
        ] = None,
        aliases: Annotated[
            dict[str, list[str]] | None,
            Field(description="Canonical name to the alternative names that mean it."),
        ] = None,
        coordinate_decimals: Annotated[int, Field(ge=5, le=9)] = 7,
        name: NewName = None,
    ) -> Annotated[CallToolResult, DatasetResult]:
        """Tidy names, categories, addresses and coordinates so records can be compared."""
        return await call(
            lambda job: record_tools.normalize_records(
                state,
                job,
                dataset_id=dataset_id,
                categories=categories,
                aliases=aliases,
                coordinate_decimals=coordinate_decimals,
                name=name,
            )
        )

    @server.tool(title="Deduplicate entities", annotations=WRITES)
    async def deduplicate_entities(
        dataset_id: DatasetId,
        name_threshold: Annotated[float, Field(ge=0.5, le=1.0)] = 0.88,
        max_distance_m: Annotated[float, Field(gt=0, le=5000)] = 150.0,
        id_keys: Annotated[
            list[str] | None,
            Field(
                description="Identifiers that mark the same entity (default: website, "
                "phone, email, wikidata)."
            ),
        ] = None,
        same_category: bool = True,
        name: NewName = None,
    ) -> Annotated[CallToolResult, DatasetResult]:
        """Merge records describing the same entity. Disagreeing values are kept side by
        side as conflicts; far-apart sites sharing an identifier are linked as one
        organisation, not merged."""
        return await call(
            lambda job: record_tools.deduplicate_entities(
                state,
                job,
                dataset_id=dataset_id,
                name_threshold=name_threshold,
                max_distance_m=max_distance_m,
                id_keys=id_keys,
                same_category=same_category,
                name=name,
            )
        )

    @server.tool(title="Match records to building footprints", annotations=WRITES)
    async def match_building_footprints(
        dataset_id: DatasetId,
        buildings_dataset_id: Annotated[
            str, Field(description="A buildings dataset, e.g. from get_building_footprints.")
        ],
        max_distance_m: Annotated[float, Field(gt=0, le=500)] = 30.0,
        assign_geometry: Annotated[
            Literal["none", "exact", "exact_or_probable"],
            Field(description="Replace a record's point with the matched footprint."),
        ] = "none",
        options: Options = None,
        name: NewName = None,
        ctx: Context | None = None,
    ) -> Annotated[CallToolResult, DatasetResult]:
        """Link each record to the building it is in or next to. Each match is exact,
        probable, ambiguous or none, with reasons and candidates; ambiguous cases are never
        resolved by guessing."""
        return await call(
            lambda job: record_tools.match_building_footprints(
                state,
                job,
                dataset_id=dataset_id,
                buildings_dataset_id=buildings_dataset_id,
                max_distance_m=max_distance_m,
                assign_geometry=assign_geometry,
                options=options,
                name=name,
            ),
            ctx,
        )

    @server.tool(title="Enrich records spatially", annotations=WRITES)
    async def enrich_records(
        dataset_id: DatasetId,
        operation: Annotated[
            Literal["nearby", "within", "measure"],
            Field(
                description="nearby: reference features within a radius; within: reference "
                "polygons containing each record; measure: area and length."
            ),
        ],
        reference_dataset_id: Annotated[
            str | None, Field(description="For nearby and within.")
        ] = None,
        field: Annotated[
            str | None,
            Field(
                description="For nearby and within: the attribute to add.",
                pattern=r"^[a-z][a-z0-9_-]{0,63}$",
            ),
        ] = None,
        radius_m: Annotated[float | None, Field(gt=0, le=50_000)] = None,
        categories: Annotated[
            list[str] | None, Field(description="Only consider these reference categories.")
        ] = None,
        options: Options = None,
        name: NewName = None,
    ) -> Annotated[CallToolResult, DatasetResult]:
        """Add spatial context to records: nearby features, containing areas, or measures."""
        return await call(
            lambda job: record_tools.enrich_records(
                state,
                job,
                dataset_id=dataset_id,
                operation=operation,
                reference_dataset_id=reference_dataset_id,
                field=field,
                radius_m=radius_m,
                categories=categories,
                options=options,
                name=name,
            )
        )

    @server.tool(title="Score confidence", annotations=WRITES)
    async def score_confidence(
        dataset_id: DatasetId,
        high: Annotated[float, Field(ge=0, le=1)] = 0.75,
        medium: Annotated[float, Field(ge=0, le=1)] = 0.5,
        low: Annotated[float, Field(ge=0, le=1)] = 0.25,
        name: NewName = None,
    ) -> Annotated[CallToolResult, DatasetResult]:
        """Give each record a confidence level with reasons, from its evidence, its
        sources' scores, how precisely it is located, and any conflicts."""
        return await call(
            lambda job: record_tools.score_confidence(
                state, job, dataset_id=dataset_id, high=high, medium=medium, low=low, name=name
            )
        )


# Quality and export -------------------------------------------------------------------------


def _quality(server: MCPServer, state: ServerState) -> None:
    @server.tool(title="Validate a dataset", annotations=WRITES)
    async def validate_dataset(
        dataset_id: DatasetId,
        checks: Annotated[
            list[str] | None,
            Field(
                description=f"Checks to run (default: all): {', '.join(CHECKS)}. "
                "within_area is skipped without an area, and rules without rules."
            ),
        ] = None,
        area: Annotated[Area | None, Field(description="Records should lie in it.")] = None,
        rules: Annotated[
            list[quality.RuleInput] | None,
            Field(description="Spatial rules, each against a reference dataset."),
        ] = None,
        outlier_distance_m: Annotated[float | None, Field(gt=0)] = None,
        duplicate_distance_m: Annotated[float | None, Field(ge=0, le=1000)] = None,
        name: NewName = None,
    ) -> Annotated[CallToolResult, quality.ValidationResult]:
        """Check coordinates, geometry, duplicates, the area, outliers, evidence and spatial
        rules. Returns a quality score, metrics and findings, stored as a report dataset."""
        return await call(
            lambda job: quality.validate_dataset(
                state,
                job,
                dataset_id=dataset_id,
                checks=checks,
                area=area,
                rules=rules,
                outlier_distance_m=outlier_distance_m,
                duplicate_distance_m=duplicate_distance_m,
                name=name,
            )
        )

    @server.tool(title="Write a quality report", annotations=WRITES)
    async def generate_quality_report(
        report_dataset_id: Annotated[str, Field(description="From validate_dataset.")],
        name: Annotated[
            str | None,
            Field(description="File name stem.", pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$"),
        ] = None,
        max_findings: Annotated[int, Field(ge=0, le=10_000)] = 50,
        copy_to: CopyTo = None,
        overwrite: bool = False,
    ) -> Annotated[CallToolResult, quality.ReportResult]:
        """Write the quality report as Markdown and JSON, for people and for machines."""
        return await call(
            lambda job: quality.generate_quality_report(
                state,
                job,
                report_dataset_id=report_dataset_id,
                name=name,
                max_findings=max_findings,
                copy_to=copy_to,
                overwrite=overwrite,
            )
        )

    @server.tool(title="Export a dataset", annotations=WRITES)
    async def export_geo_dataset(
        dataset_id: DatasetId,
        format: Literal["geojson", "csv"] = "geojson",
        name: Annotated[
            str | None,
            Field(description="File name stem.", pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$"),
        ] = None,
        copy_to: CopyTo = None,
        overwrite: Annotated[
            bool, Field(description="Replace different existing files at copy_to.")
        ] = False,
        options: Options = None,
    ) -> Annotated[CallToolResult, DatasetResult]:
        """Export records as GeoJSON (with sources, evidence and a manifest) or CSV."""
        return await call(
            lambda job: quality.export_geo_dataset(
                state,
                job,
                dataset_id=dataset_id,
                format=format,
                name=name,
                copy_to=copy_to,
                overwrite=overwrite,
                options=options,
            )
        )


# Pipelines ----------------------------------------------------------------------------------

SpecInput = Annotated[
    dict[str, Any] | str | None,
    Field(
        description="A pipeline spec (geoproduct://schemas/v1/pipeline-spec), as an "
        "object or as YAML or JSON text."
    ),
]
SpecPath = Annotated[
    str | None,
    Field(
        description="Or: a spec file inside the workspace or an allowed folder.", max_length=1000
    ),
]
Params = Annotated[
    dict[str, JsonValue] | None,
    Field(description="Values for parameters the spec declares."),
]
RunId = Annotated[str, Field(pattern=r"^run_[0-9a-z]{26}$")]


def _pipelines(server: MCPServer, state: ServerState) -> None:
    @server.tool(title="Check a pipeline", annotations=WRITES)
    async def create_geo_pipeline(
        spec: SpecInput = None, spec_path: SpecPath = None, params: Params = None
    ) -> Annotated[CallToolResult, pipelines.PipelinePlan]:
        """Validate a pipeline spec and record it as a pending run, without running it.
        Returns the plan and an estimate of external requests."""
        return await call(
            lambda job: pipelines.create_geo_pipeline(
                state, spec=spec, spec_path=spec_path, params=params
            )
        )

    @server.tool(title="Run a pipeline", annotations=WRITES_REMOTE)
    async def run_geo_pipeline(
        run_id: Annotated[
            str | None,
            Field(
                description="A pending, failed or cancelled run to start or resume.",
                pattern=r"^run_[0-9a-z]{26}$",
            ),
        ] = None,
        spec: SpecInput = None,
        spec_path: SpecPath = None,
        params: Params = None,
        confirm: Annotated[
            bool,
            Field(
                description="Required when the estimate of external requests is high or "
                "unbounded. Ask the user first."
            ),
        ] = False,
    ) -> Annotated[CallToolResult, pipelines.RunStarted]:
        """Start a pipeline run in the background and return its run_id at once.

        Give a run_id to start a pending run or resume a failed or cancelled one (finished
        stages are reused; params may change declared parameters), or a spec to create and
        start a new run. Poll get_pipeline_status for progress."""
        return await call(
            lambda job: pipelines.run_geo_pipeline(
                state,
                run_id=run_id,
                spec=spec,
                spec_path=spec_path,
                params=params,
                confirm=confirm,
            )
        )

    @server.tool(title="Pipeline status", annotations=READ_ONLY)
    async def get_pipeline_status(
        run_id: RunId,
    ) -> Annotated[CallToolResult, pipelines.RunStatusResult]:
        """A run's status, stage by stage, with progress and any error."""
        return await call(lambda job: pipelines.get_pipeline_status(state, run_id=run_id))

    @server.tool(title="Pipeline results", annotations=READ_ONLY)
    async def get_pipeline_results(
        run_id: RunId,
    ) -> Annotated[CallToolResult, pipelines.RunResults]:
        """The outputs of a run's finished stages, as dataset ids with previews."""
        return await call(lambda job: pipelines.get_pipeline_results(state, run_id=run_id))

    @server.tool(
        title="Cancel a pipeline",
        annotations=ToolAnnotations(
            readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
        ),
    )
    async def cancel_geo_pipeline(
        run_id: RunId,
    ) -> Annotated[CallToolResult, pipelines.RunStatusResult]:
        """Ask a run to stop. Finished stages are kept, so it can be resumed later."""
        return await call(lambda job: pipelines.cancel_geo_pipeline(state, run_id=run_id))


# Resources and prompts ----------------------------------------------------------------------


def _json(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, default=str)


def _resources(server: MCPServer, state: ServerState) -> None:
    @server.resource("geoproduct://guide", mime_type="text/markdown", title="Working method")
    def guide() -> str:
        """The method: evidence rules, the research loop, handles and costs."""
        return resources.files("geoproduct_mcp").joinpath("guide.md").read_text("utf-8")

    @server.resource("geoproduct://stages", mime_type="application/json", title="Stages")
    def stages() -> str:
        """Every registered pipeline stage, with its parameter schema."""
        return _json([definition.describe() for definition in state.registry])

    @server.resource("geoproduct://providers", mime_type="application/json", title="Providers")
    def providers() -> str:
        """Providers, whether each is configured, their usage policies and attribution."""
        return _json([info.model_dump(mode="json") for info in state.providers.infos()])

    @server.resource(
        "geoproduct://schemas/v1/{name}", mime_type="application/json", title="JSON Schemas"
    )
    def schema(name: str) -> str:
        """A published JSON Schema: area, dataset-manifest, evidence, geo-error,
        pipeline-spec, quality-report, record, run-record, source or stage-manifest."""
        if name not in PUBLISHED:
            raise ValueError(f"unknown schema; choose one of {', '.join(PUBLISHED)}")
        return _json(json_schema(name))

    @server.resource("geoproduct://runs/{run_id}", mime_type="application/json", title="Run")
    def run(run_id: str) -> str:
        """A pipeline run record and its latest events."""
        record = state.runner.status(run_id)
        events = state.runner.store.events(run_id)[-20:]
        return _json({"run": record.model_dump(mode="json"), "events": events})

    @server.resource(
        "geoproduct://datasets/{dataset_id}", mime_type="application/json", title="Dataset"
    )
    def dataset(dataset_id: str) -> str:
        """A stored dataset's description and files (ds_... ids)."""
        return _json(state.datasets.info(dataset_id).model_dump(mode="json"))


def _prompts(server: MCPServer) -> None:
    @server.prompt(title="Plan a geographic dataset")
    def plan_geographic_dataset(goal: str, area: str = "", constraints: str = "") -> str:
        """A plan for building a dataset from evidence, for clients without the skill."""
        return (
            f"Plan how to build this geographic dataset: {goal}\n"
            f"Area: {area or 'not stated; ask the user'}\n"
            f"Constraints: {constraints or 'none stated'}\n\n"
            "Read geoproduct://guide first. Then write a plan with: the entities and "
            "attributes; candidate sources and how you will judge each (authority, accuracy, "
            "freshness, coverage, licensing, geographic precision, verifiability); which "
            "tools or pipeline stages collect, resolve, match and validate the data; the "
            "spatial rules that should hold; the expected external requests; and the "
            "deliverables. Mark every assumption. Do not invent coordinates, URLs or "
            "attributes; plan how each will be evidenced instead."
        )

    @server.prompt(title="Audit a dataset's quality")
    def audit_dataset_quality(dataset_id: str) -> str:
        """Steps to audit a stored dataset and explain its quality."""
        return (
            f"Audit the quality of dataset {dataset_id}.\n\n"
            "1. Run score_confidence on it, then validate_dataset on the result, adding "
            "spatial rules that the domain implies (for example, stops near streets).\n"
            "2. Run generate_quality_report on the report dataset.\n"
            "3. Explain to the user: the quality score; records with errors and why; "
            "unresolved, ambiguous or conflicting records; evidence coverage and any excerpts "
            "not found in their sources; and the licences and attribution that apply.\n"
            "4. Propose fixes that need new evidence, and say which cannot be fixed without it."
        )


# Entry point --------------------------------------------------------------------------------


def _loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="geoproduct-mcp",
        description="MCP server for evidence-backed geographic datasets.",
    )
    parser.add_argument("--http", action="store_true", help="serve Streamable HTTP, not stdio")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP host (loopback only)")
    parser.add_argument("--port", type=int, default=8000, help="HTTP port (default 8000)")
    parser.add_argument(
        "--workspace", help=f"workspace folder (default ${ENV_WORKSPACE} or {DEFAULT_WORKSPACE})"
    )
    parser.add_argument(
        "--allow",
        action="append",
        default=[],
        metavar="DIR",
        help="a further folder tools may read from and export to (repeatable)",
    )
    parser.add_argument("--log-level", default="WARNING", choices=["DEBUG", "INFO", "WARNING"])
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=args.log_level, stream=sys.stderr)
    if args.http and not _loopback(args.host):
        print(
            "geoproduct-mcp: --host must be a loopback address. The server has no "
            "authentication, so it is not exposed to the network.",
            file=sys.stderr,
        )
        return 2
    try:
        env = Workspace.from_env()
        workspace = Workspace(args.workspace or env.root, [*env.allowed_dirs, *args.allow])
        server = create_server(ServerState(workspace))
    except GeoProductError as exc:
        print(f"geoproduct-mcp: {exc.error.message}", file=sys.stderr)
        return 2
    if args.http:
        server.run("streamable-http", host=args.host, port=args.port)
    else:
        server.run("stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
