# GeoProduct MCP tools, resources and CLI

The server's full input and output schemas are published with each tool. This page is the map: what each tool is for, its main parameters, and how the pieces connect. Required parameters are in **bold**.

## Contents

- [Shared shapes](#shared-shapes)
- [Sources](#sources)
- [Geography](#geography)
- [Records](#records)
- [Quality and export](#quality-and-export)
- [Pipelines](#pipelines)
- [Resources and prompts](#resources-and-prompts)
- [CLI equivalents](#cli-equivalents)

## Shared shapes

- **Area.** Exactly one of:
  - `{"bbox": [west, south, east, north]}`;
  - `{"polygon": <GeoJSON Polygon or MultiPolygon>}`;
  - `{"point": [lon, lat], "radius_m": 500}`.

  Optionally add a `"name"`. Coordinates are WGS 84 longitude and latitude.
- **Dataset ids.** `ds_…` from a tool, or `<run_id>/<stage_id>` for a finished pipeline stage. Each dataset has a kind: `records`, `buildings`, `report` or `files`. Tools refuse the wrong kind and say which they need.
- **Tool results.** Every result is structured content with a one-line summary. Dataset results carry `dataset` (id, kind, counts, `rejected`, files), a `preview` of up to five items, and `attribution`.
- **Errors.** An error result has `isError: true` and `error`, with `code`, `message`, `hint` and `details`. The codes and what to do about them are in SKILL.md.

## Sources

| Tool | Purpose | Main parameters |
|---|---|---|
| `register_source` | Record a source you found, with your ratings. The score and tier are computed. The same type and URL again updates it. | **`title`**, **`type`** (`official_government`, `official_organisation`, `osm`, `open_data_portal`, `public_api`, `commercial_api`, `document`, `user_file`, `other`), **`url_or_ref`** (exactly as found), `publisher`, `licence`, `attribution`, `data_as_of`, `coverage_note`, `evaluation` (seven ratings, each `{score: 0-3, rationale}`) |
| `fetch_source_data` | Fetch a registered URL or read its file. Keeps the content, its SHA-256 and its text. | **`source_id`**. Returns the kind, an excerpt, whether it can be loaded as records (`loadable`), and the stored files. |

## Geography

| Tool | Purpose | Main parameters |
|---|---|---|
| `geocode` | Candidates for an address or place name. Does not create a dataset. | **`text`**, `provider` (default `nominatim`), `limit` (5), `country_codes` (lower case), `bias` (an Area), `language` |
| `reverse_geocode` | The address or place at a point | **`lon`**, **`lat`**, `provider`, `language` |
| `geocode_records` | Locate the records of a dataset that have an address or name but no geometry, as a new dataset. Each location cites the geocoder. One request per record; more than 25 to an online provider needs `confirm: true` | **`dataset_id`**, `provider` (default `nominatim`), `country_codes`, `bias`, `use_names` (true), `overwrite` (false), `options` (`min_score` 0.6, `ambiguity_margin` 0.05), `confirm`, `name` |
| `search_osm_features` | Map features with tags in an area, as a records dataset | **`area`**, **`tags`** (`{"key": "value" \| ["v1", "v2"] \| null}`, all keys must match), `provider` (default `overpass`), `limit` (1000), `allow_truncated`, `name` |
| `get_building_footprints` | Building outlines in an area, as a buildings dataset | **`area`**, `provider` (default `overpass`), `limit` (10000), `allow_truncated`, `name` |

## Records

| Tool | Purpose | Main parameters |
|---|---|---|
| `create_dataset` | A records dataset from exactly one input | `records` with `evidence` (and `source_ids`), or `file` (with `format`, `columns`, `category`, `licence`), or `source_id` (a fetched CSV, JSON or GeoJSON), or `dataset_ids` (two or more records datasets to combine); `name` |
| `normalize_records` | Tidy names, categories, addresses and coordinates | **`dataset_id`**, `categories` (old to new), `aliases` (canonical name to alternatives), `coordinate_decimals` (7) |
| `deduplicate_entities` | Merge records describing the same entity; keep conflicts | **`dataset_id`**, `name_threshold` (0.88), `max_distance_m` (150), `id_keys` (website, phone, email, wikidata), `same_category` (true) |
| `match_building_footprints` | Link records to footprints, with a status and reasons | **`dataset_id`**, **`buildings_dataset_id`**, `max_distance_m` (30), `assign_geometry` (`none`, `exact`, `exact_or_probable`), `options` (`min_radius_m` 5, `name_threshold` 0.85, `ambiguity_margin` 0.1, `max_candidates` 5) |
| `enrich_records` | Spatial context | **`dataset_id`**, **`operation`**: `nearby` (needs `reference_dataset_id` and `field`; optional `radius_m` (500), `categories`, `options.max_ids` (10)), `within` (needs `reference_dataset_id`, `field`; `options.label` `name` or `id`), `measure` (`options.measures`: `area` and/or `length`, both by default) |
| `score_confidence` | Per-record confidence level and reasons | **`dataset_id`**, `high` (0.75), `medium` (0.5), `low` (0.25) |

## Quality and export

| Tool | Purpose | Main parameters |
|---|---|---|
| `validate_dataset` | Checks and rules, producing a quality report dataset and a score | **`dataset_id`**, `checks` (default all: `coordinates`, `crs`, `geometry`, `missing_geometry`, `duplicates`, `within_area`, `outliers`, `evidence`, `conflicts`, `rules`), `area`, `rules` (each `{name, kind: near / within / not_within, reference_dataset_id, max_distance_m (near only), categories, reference_categories, severity}`), `outlier_distance_m`, `duplicate_distance_m` |
| `generate_quality_report` | Markdown and JSON report from a validation | **`report_dataset_id`**, `name`, `max_findings` (50), `copy_to`, `overwrite` |
| `export_geo_dataset` | GeoJSON (with sources, evidence and a manifest) or CSV | **`dataset_id`**, `format` (`geojson`, `csv`), `name`, `copy_to` (inside the workspace or an allowed folder), `overwrite`, `options` (CSV: `attributes`, `geometry` `point` / `wkt` / `none`; GeoJSON: `include_provenance`, `description`) |

## Pipelines

| Tool | Purpose | Main parameters |
|---|---|---|
| `create_geo_pipeline` | Check a spec and record it as a pending run. Returns the plan, the external-request estimate and warnings. | `spec` (object, YAML or JSON text) or `spec_path`; `params` |
| `run_geo_pipeline` | Start a pending run, resume a failed or cancelled one, or create and start a new one. Returns at once. | `run_id`, or `spec` / `spec_path`; `params`; `confirm` (only after the user agrees to the estimate) |
| `get_pipeline_status` | Status per stage, progress, error, and whether a worker is active | **`run_id`** |
| `get_pipeline_results` | Finished stages' outputs as dataset ids with previews | **`run_id`** |
| `cancel_geo_pipeline` | Ask a run to stop between items | **`run_id`** |

A run that says `running` with `active: false` was interrupted (the server stopped). Resume it with `run_geo_pipeline(run_id=...)`.

## Resources and prompts

| URI | Contents |
|---|---|
| `geoproduct://guide` | The working method, for clients without this skill |
| `geoproduct://stages` | Every registered stage with its parameter schema |
| `geoproduct://providers` | Providers, capabilities, whether configured, usage policy, licence, attribution |
| `geoproduct://schemas/v1/{name}` | JSON Schemas, for example `record`, `evidence`, `source`, `pipeline-spec` |
| `geoproduct://runs/{run_id}` | A run record and its event log |
| `geoproduct://datasets/{dataset_id}` | A dataset's description and files |

The prompts `plan_geographic_dataset(goal, area, constraints)` and `audit_dataset_quality(dataset_id)` are templates for clients without skills; with this skill you don't need them.

## CLI equivalents

The `geoproduct` command (from `geoproduct-core`) runs pipelines without MCP:

| Command | Does |
|---|---|
| `geoproduct stages`, `geoproduct stage <name>` | List the stages, or describe one with its parameter schema |
| `geoproduct plan <spec> [--param k=v]` | Check a spec without running it |
| `geoproduct run <spec> [--param k=v]` | Run in the foreground and print the result |
| `geoproduct status <run_id>`, `geoproduct runs` | Show one run, or list them |
| `geoproduct resume <run_id> [--param k=v]` | Continue a failed or cancelled run |
| `geoproduct cancel <run_id>` | Ask a run to stop |

Global options: `--workspace DIR`, `--allow DIR` (read and export there), `--json`. Exit codes: 0 succeeded, 1 failed, 2 usage, 3 cancelled, 130 interrupted.

The single-step tools (`register_source`, `fetch_source_data`, `geocode` and so on) have no CLI form. In CLI mode, write their work into the spec: sources and evidence go inside `collect.records`, and geocoding is the `geocode.records` stage.
