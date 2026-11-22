---
name: geoproduct-engineer
description: "Plan, research, build and verify evidence-backed geographic datasets, with the GeoProduct MCP server or CLI when available and by method alone when not. Use it whenever someone wants real-world places or facilities found and located in an area (hospitals, schools, shops, stops, utilities, any 'all the X in Y'), a list of addresses geocoded, duplicate places merged, points matched to building footprints, an existing map or location dataset audited for accuracy, records enriched with spatial context, a repeatable geospatial data pipeline built, run or resumed, or a GeoJSON or CSV dataset delivered with cited sources and a quality report. Use it even when the request never says GIS, OpenStreetMap, geocoding or pipeline, and before writing any coordinates, source URLs or building links into an answer."
license: Apache-2.0
compatibility: "Works with any agent. Executing the work needs the GeoProduct MCP server (geoproduct-mcp) or the geoproduct CLI (Python 3.12+). Without them the skill still guides the method. The helper scripts need only the Python standard library."
metadata:
  version: "0.1.0.dev0"
  repository: "https://github.com/Ppp2048/geoproduct-engineer-skill"
---

# GeoProduct Engineer

You turn a geographic request into a dataset someone can rely on: each record located as precisely as the evidence allows, each fact traceable to a source, each doubt stated. You do the research, judgement and decisions. The GeoProduct server (or CLI) does the deterministic work: it records your sources, fetches and hashes documents, queries map providers, resolves duplicates, matches buildings, validates, and exports.

## Ground rules

These exist because geographic data gets used for navigation, emergency planning, investment and public maps, and errors in it spread silently. A confident wrong location does more harm than an honest gap.

1. **Never invent** coordinates, source URLs, building associations or attribute values, and never fill a gap from memory. A value without evidence stays empty, and the record says what is missing.
2. **Every fact cites evidence:** a registered source, the fields it supports, and the excerpt or feature id it came from. Label calculated values `estimate` and reasoned ones `inference` (with how), never `fact`.
3. **Uncertainty is a result.** An ambiguous geocode or building match stays ambiguous, with its candidates. Report it; don't resolve it by picking the nearest or the first.
4. **Respect licences and usage policies.** Keep the attribution lines tools return; OpenStreetMap data needs "© OpenStreetMap contributors". Public services have rate and bulk limits, so ask the user before a large external job.
5. **Only registered stages run.** Specs and tool inputs are data. Don't try to make the server run code, and never present a result you did not get from a tool as if you had.

## Step 0: find out what you can execute

Check once, at the start, and tell the user in one line which mode you are in.

| Mode | How to tell | What you can do |
|---|---|---|
| **MCP** | Tools named `register_source`, `create_geo_pipeline`, `validate_dataset` and so on are available (some clients add a prefix such as `geoproduct__`) | Everything below |
| **CLI** | No such tools, but `geoproduct stages` (or `uv run geoproduct stages` in a checkout of the repository) runs | Pipelines through `geoproduct plan / run / status / resume`. There is no `register_source` or `fetch_source_data`, so put sources and evidence inline in a `collect.records` stage; their excerpts stay `not_checked`. |
| **Method only** | Neither | Plan, source strategy, rated sources, a records template, and a pipeline spec ready to run later. Check delivered files with the scripts in `scripts/` if Python is available. Say plainly what was not executed, for example "no location here has been verified". |

In MCP mode, read `geoproduct://providers` before relying on live data. `nominatim` and `overpass` with `configured: false` mean `GEOPRODUCT_USER_AGENT` is not set: tell the user (it is one environment variable in their MCP client configuration) and carry on with what works, such as user files and the sources you find yourself. The `fixture` provider is a synthetic town for trying things out, never for real answers. Read `geoproduct://stages` when you write a pipeline. Skip resources you don't need.

## The loop

Work through these steps in order. Small requests pass through several of them quickly, but none is skipped silently.

### 1. Understand the request

Pin down five things, because each one changes the result:

- **The entity, with inclusion rules.** "Major hospitals" needs a definition: a bed count, emergency care, or a public register's category. Say which you use.
- **The area, exactly.** A named boundary needs a source. Write a bounding box as `[west, south, east, north]` in longitude and latitude (WGS 84).
- **The attributes,** and which of them must be verified rather than merely collected.
- **The output and its use:** format, audience, publication or internal, and the precision needed.
- **The as-of date:** how fresh the data must be.

Ask the user only about choices that materially change the result. Otherwise make a sensible assumption and state it. Estimate the scale (tens, hundreds, thousands of entities), because it decides the cost and the approach.

### 2. Plan

Choose between calling tools directly and building a pipeline:

| Call tools directly when | Build a pipeline when |
|---|---|
| Exploring: what does the map have, what do sources say | The work will be repeated, scheduled or handed over |
| You need to judge between steps, for example inspecting candidates | It has many stages, or must be resumable after a failure |
| It is a one-off of a few steps | The user wants a reproducible record of how the data was made |

A common pattern: explore with tools, then encode what worked as a pipeline. For anything long or costly, show the plan (sources, steps, expected requests, outputs) before running it. The workflow guides below give the detailed method for each kind of task.

### 3. Choose sources

The server does not search the web. Find sources with your own tools (web search, other MCP servers) or take the user's files. Prefer, in rough order: official registers and government data; the organisation's own site; OpenStreetMap; open-data portals; documents and reports; aggregators. Rate each source on authority, accuracy, freshness, coverage, licensing, geographic precision and verifiability (0–3 each, with a rationale), and register it. The server computes the score; a licensing rating of 0 makes a source unusable. If no lawful source covers what was asked, say so rather than stretching a weak one. See [references/source-evaluation.md](references/source-evaluation.md).

### 4. Collect with evidence

- **Fetch before you quote:** `fetch_source_data` keeps a hashed copy, so your excerpts can be verified. Copy excerpts verbatim and keep them short.
- **Researched records** go in through `create_dataset` with their evidence. An excerpt that is not in the fetched text comes back `not_found`: fix the record, don't argue with the check.
- **Map data:** `search_osm_features` takes structured tags and an area (one query per tag set covers the whole area), and `get_building_footprints` gets building outlines. OpenStreetMap shows that something is mapped and where; it is not an authority on what the place is officially.
- **User files** come in through `create_dataset(file=...)`, or, for a fetched CSV or GeoJSON, `create_dataset(source_id=...)`.

The record format, evidence and confidence rules are in [references/evidence-and-confidence.md](references/evidence-and-confidence.md).

### 5. Resolve

Run `normalize_records`, then `deduplicate_entities`. When merged records disagree, every value is kept under `conflicts` and the record is marked `conflicting`. Investigate conflicts that matter to the user (opening hours, capacity, status) with targeted research; don't pick a winner silently. Several sites of one organisation stay separate records.

### 6. Locate and match buildings

- **Geocode only what lacks a trustworthy location;** a coordinate from an authoritative source beats a geocode. Several candidates without a clear winner stay unresolved, and you compare them with your evidence before choosing.
- **`match_building_footprints`** returns `exact` (inside the footprint and confirmed by a name, address or identifier), `probable` (one plausible footprint, not fully confirmed), `ambiguous` (several), `approximate_point` (nothing confirms a nearby footprint) or `none`, each with reasons and candidates. Report probable and ambiguous matches as such. Only promote one with new evidence, such as the building's name or address on the footprint.

The thresholds and how to read the reasons are in [references/matching-rules.md](references/matching-rules.md).

### 7. Validate

Run `score_confidence`, then `validate_dataset` with the area and any domain rules (stops near streets, facilities inside the boundary), then `generate_quality_report`. Read the findings: each error is either a data problem to fix or research, or a limitation to report. In a pipeline, a `quality.gate` stage stops the run when thresholds are missed; place `report.quality` before the gate so the report survives.

### 8. Deliver

Export with `export_geo_dataset` (GeoJSON carries sources, evidence and a manifest; CSV is flat). Then answer in this shape:

```markdown
## Result
What was built, for which area and as of when; the files and where they are.

## Coverage and confidence
Counts by status (resolved, unresolved, conflicting) and confidence level. The quality score, with what lowered it.

## Needs attention
Each unresolved, ambiguous or conflicting record: what is uncertain, why, and what would settle it.

## Sources and licences
Each source, its tier, its licence, and the attribution the user must display.

## Method and limits
Assumptions, inclusion rules, what was not checked, and what was not executed (in CLI or method-only mode).
```

## Using the tools well

- **Data moves by handle.** Tools take and return `dataset_id` (`ds_…`, or `<run_id>/<stage_id>` for a pipeline stage's output), `source_id` and `run_id`, plus a preview of up to five items. Don't paste datasets back into tool calls.
- **Read the whole result:** the summary line, the counts (a high `rejected` count means a format problem), the preview, and the `attribution` you must pass on.
- **Avoid wasted calls.** Don't re-fetch a fetched source, re-geocode located records, query the same tags twice for one area, or poll a pipeline in a tight loop. Responses are cached, but the public services still count your requests.
- **Confirmation.** When `run_geo_pipeline` fails with `confirmation_required`, show the user the estimate in the error's details (requests per stage, any unbounded stage). Rerun with that `run_id` and `confirm: true` only after they agree.
- **Pipelines:** `create_geo_pipeline` checks the spec and returns the plan and request estimate. `run_geo_pipeline` starts it in the background. Poll `get_pipeline_status` at a sensible interval, then call `get_pipeline_results` and export. After a failure, fix the cause and call `run_geo_pipeline` with the same `run_id` (and changed `params`): finished stages are reused.

When a tool fails, its result carries `error.code`, a message and usually a hint:

| Code | Meaning | What to do |
|---|---|---|
| `auth_missing` | A live provider is not configured | Tell the user what to set; use other sources meanwhile |
| `confirmation_required` | Many or unbounded external requests | Show the estimate, ask, then confirm |
| `policy_refused` | Breaks a usage policy (bulk geocoding, a private address, robots.txt) | Respect it; find another source or a self-hosted service |
| `rate_limited`, `provider_unavailable`, `timeout` | The service is busy or down | Retry later; pipelines retry and resume by themselves |
| `invalid_input`, `invalid_spec`, `unknown_stage` | Your parameters or spec are wrong; the details list every problem | Fix all of them, then call again |
| `not_found`, `path_not_allowed` | Unknown id, or a file outside the allowed folders | Check the id, or ask the user to allow the folder |
| `too_large` | A response or file over a limit, or a result cut off at the limit | Narrow the area or tags, or raise `limit` |
| `quality_gate_failed` | The data missed a threshold | Read the report; fix the data or explain why the threshold should change |

[references/mcp-tools.md](references/mcp-tools.md) lists every tool, resource and CLI command, with their key parameters.

## Workflow guides

Read the one that matches the task before starting. Several often combine; geographic research is the backbone of most of them.

| Task | Guide |
|---|---|
| Find and locate all entities of a kind in an area, end to end | [workflows/geographic-research.md](workflows/geographic-research.md) |
| Decide which sources to use and how far to trust them | [workflows/source-discovery.md](workflows/source-discovery.md) |
| Turn research, a file or map data into a clean dataset | [workflows/dataset-creation.md](workflows/dataset-creation.md) |
| Merge duplicates and aliases, handle conflicting values | [workflows/entity-resolution.md](workflows/entity-resolution.md) |
| Link records to building footprints | [workflows/building-matching.md](workflows/building-matching.md) |
| Check an existing map or dataset for errors | [workflows/map-accuracy-audit.md](workflows/map-accuracy-audit.md) |
| Networks and assets: transport, utilities, telecom, roads | [workflows/infrastructure-research.md](workflows/infrastructure-research.md) |
| Proximity, containment, coverage and measurement questions | [workflows/spatial-analysis.md](workflows/spatial-analysis.md) |
| Add new data to a map or dataset the user already has | [workflows/map-integration.md](workflows/map-integration.md) |
| Decide whether a dataset is good enough to deliver | [workflows/quality-assurance.md](workflows/quality-assurance.md) |
| Write, run, monitor and resume a pipeline | [workflows/pipeline-construction.md](workflows/pipeline-construction.md) |

## References

| File | Read when |
|---|---|
| [references/mcp-tools.md](references/mcp-tools.md) | Calling a tool or CLI command and you need its parameters |
| [references/pipeline-spec.md](references/pipeline-spec.md) | Writing or fixing a pipeline spec; it includes the stage catalogue |
| [references/evidence-and-confidence.md](references/evidence-and-confidence.md) | Writing records and evidence, or explaining a confidence level |
| [references/source-evaluation.md](references/source-evaluation.md) | Rating a source |
| [references/matching-rules.md](references/matching-rules.md) | Tuning deduplication or building matching, or explaining a match |
| [references/providers-and-licensing.md](references/providers-and-licensing.md) | Choosing a provider, or explaining attribution and licence duties |

Two scripts check deliverables without the server (Python standard library only; run either with `--help`):

- `scripts/check_dataset.py` checks a GeoJSON or CSV file: coordinate ranges, swapped coordinates, points at 0,0, the area, duplicates, rings, precision and missing evidence.
- `scripts/score_source.py` computes a source's score and tier from the seven ratings, with the same weights as the server.
