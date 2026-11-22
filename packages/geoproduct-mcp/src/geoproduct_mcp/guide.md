# GeoProduct Engineer: working method

This server builds geographic datasets from evidence. It does not research on its own:
you (the agent) find and judge sources with your own tools; the server records your
judgement, fetches and hashes sources, calls map providers, runs registered pipeline
stages, and checks the results.

## Rules

1. Never invent coordinates, source URLs, building associations or missing attributes.
   A value without evidence stays empty, and the record says what is missing.
2. Every fact on a record cites evidence: a source, the fields it supports, and the
   excerpt or feature id it came from.
3. Report uncertainty. Ambiguous geocodes and building matches are left unresolved, with
   their candidates, rather than resolved by guessing.
4. Respect licences. OpenStreetMap data is ODbL and must be credited as
   "© OpenStreetMap contributors". Keep the attribution lines tools return.

## The loop

1. **Understand** the request: the entities, the area, the attributes, the output.
2. **Plan** the sources and steps. For repeatable work, write a pipeline spec
   (`geoproduct://stages` lists the stages and their parameters).
3. **Choose sources.** Register each with `register_source`, rating it on authority,
   accuracy, freshness, coverage, licensing, geographic precision and verifiability
   (0 to 3, each with a rationale). The server computes the score. A licensing rating of 0
   makes a source unusable.
4. **Collect with evidence.** `fetch_source_data` keeps a copy and its hash, so
   excerpts can be verified. `create_dataset` takes your records with their evidence and
   checks each excerpt against the fetched text. Map data comes from
   `search_osm_features`, `get_building_footprints` and `geocode`; `geocode_records`
   locates the records of a dataset that have an address but no geometry.
5. **Resolve.** `normalize_records`, then `deduplicate_entities`. Disagreeing values
   are kept side by side as conflicts; never pick one silently.
6. **Match and enrich.** `match_building_footprints` reports exact, probable, ambiguous
   or no match, with reasons. `enrich_records` adds nearby features, containing areas,
   or measurements.
7. **Validate.** `score_confidence`, then `validate_dataset` (with spatial rules where
   the domain has them), then `generate_quality_report`.
8. **Report.** Export with `export_geo_dataset`. Tell the user the quality score, what
   is unresolved or conflicting and why, the sources used, and the attribution required.

## Handles

Tools pass data by id. Datasets are `ds_...` ids, or `<run_id>/<stage_id>` for a
pipeline stage's output. Sources are `src_...` ids. Pipelines are addressed by `run_id`.

## Costs and confirmation

Live providers follow their usage policies: Nominatim at most one request per second
and no bulk geocoding on the public instance; Overpass one query at a time. Responses are
cached in the workspace. Pipelines that may make many external requests need
`confirm: true`; show the user the estimate before confirming.
