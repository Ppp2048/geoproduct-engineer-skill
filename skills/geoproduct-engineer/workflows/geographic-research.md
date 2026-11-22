# Geographic research: find and locate every X in an area

The end-to-end method for requests like "find all major hospitals in this region, locate them exactly, match them to buildings, verify, and export GeoJSON". Most other workflows are parts of this one.

## Pin down first

| Question | Why it matters | If the user hasn't said |
|---|---|---|
| What counts as an X? | "Major hospital", "school" and "store" all have edge cases: clinics, private tutors, kiosks | Use an authoritative definition (a register's category) and state it |
| Exactly which area? | A city's administrative boundary, its built-up area and its metro region give different answers | Find the boundary's source; if only a name is given, say which boundary you used |
| Which attributes, and which must be verified? | Collecting phone numbers is cheap; verifying bed counts needs a document | Collect what sources state; verify only what the user needs |
| What output, for whom? | A public map needs higher confidence than an internal draft | GeoJSON plus a quality report |
| As of when? | Facilities open, close and move | Today, and record each source's date |

## Steps

1. **Estimate the size.** Before collecting, guess how many entities exist, from a register's count, the population, or a quick OSM query. It sets the cost of geocoding and tells you later whether coverage is plausible.
2. **Build the source plan.** Follow [source-discovery.md](source-discovery.md). You usually need:
   - one authoritative list (which entities exist);
   - one location source (where they are: the register's coordinates, the organisations' addresses, or OSM);
   - building footprints, if the user wants buildings.

   Register each source with your ratings.
3. **Collect the authoritative list.**
   - With a fetchable file (CSV, GeoJSON), call `fetch_source_data` then `create_dataset(source_id=...)`.
   - From web pages, fetch each page, write the records yourself with an excerpt for each fact, and call `create_dataset(records=..., evidence=...)`.
4. **Collect the map view.** Run `search_osm_features` over the same area with the tags for the entity (for example `{"amenity": ["hospital", "clinic"]}`). This finds entities the list missed and gives geometry.
5. **Normalise and combine.** Normalise each dataset with the same category mapping and aliases, then put them into one dataset with `create_dataset(dataset_ids=[list_id, map_id])`, which keeps both datasets' sources and evidence. In a pipeline, `combine.records` does the same.
6. **Deduplicate.** `deduplicate_entities` merges the list entry and the map feature for the same hospital and keeps both sources' evidence. Read the merges: each has a score and reasons. Follow [entity-resolution.md](entity-resolution.md) for conflicts.
7. **Locate what is still unplaced.** Call `geocode_records(dataset_id=..., bias=..., country_codes=...)` with the area as `bias`: it looks up only the records without a location. It makes one request per record, so above 25 it stops with `confirmation_required`; tell the user how many requests it needs before passing `confirm: true`. Check each result's location kind: an `approximate` result for a large facility is a lead, not an answer.
8. **Match buildings** if asked: `get_building_footprints` for the area plus a margin of about 200 m, then `match_building_footprints`. See [building-matching.md](building-matching.md).
9. **Score and validate.** Run `score_confidence`, then `validate_dataset` with the area. Then `generate_quality_report`.
10. **Second pass.** List the records that are unresolved, conflicting, ambiguous or low confidence. For each one worth settling, do targeted research: the organisation's contact page for an address, or a recent official document for whether it is still open. Add the evidence and rerun from the affected step. Stop when further research would not change the answer, and report the rest.
11. **Check coverage.** Compare your count with the register's count or your estimate. A large gap is a finding to report, whichever side it is on: missing entities, or extra ones that don't meet the definition.
12. **Export and report** in the shape given in SKILL.md.

## As a pipeline

Steps 4 to 11 map onto stages: `collect.records` or `collect.file`, then `collect.features`, `combine.records`, `normalize.records`, `resolve.deduplicate`, `geocode.records`, `collect.buildings` with `match.buildings`, `quality.confidence`, `validate.dataset`, `report.quality`, `quality.gate`, and finally `export.geojson`. Write the spec once the sources are settled; see [pipeline-construction.md](pipeline-construction.md). Steps 1–3 and 10 are your research, which no stage can do.

## Pitfalls

- **Trusting OpenStreetMap as the list.** It shows what mappers have recorded. Coverage varies by place and category, and tags are applied inconsistently. Use it for discovery and geometry, and confirm existence from an authoritative source when it matters.
- **Geocoding names alone.** A name-only geocode returns the most prominent match anywhere; always pass the area as `bias` and the country codes. A single unscored result is accepted, so check its kind and address.
- **Coordinates from memory.** If you "know" where a well-known place is, that is still not evidence. Find it in a source.
- **Ignoring the edge.** Entities just outside the area appear in map queries with a margin. Validation with `area` flags them, and they should be dropped or reported, not silently kept.
- **One source for everything.** A record supported by two independent sources scores higher, and deserves to.

## Without the server

Do steps 1–3, 10 and 11 as usual. Write the records in the format of [../references/evidence-and-confidence.md](../references/evidence-and-confidence.md), leaving geometry empty where no source gives it. Mark the result as unvalidated. Hand over a pipeline spec that would do the rest, and check any file you produce with `scripts/check_dataset.py`.
