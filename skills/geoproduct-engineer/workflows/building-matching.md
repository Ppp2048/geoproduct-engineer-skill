# Building matching: linking records to footprints

Users often want "the building" a place is in: to draw it, measure it or count floors. A point inside or near a footprint is not proof that it is that building, so every link must say how sure it is.

## Prerequisites

- **Located records.** Records without geometry get `none` ("no location to match").
- **Footprints for the same area,** with a margin of about 200 m around it, so records near the edge still have candidates: `get_building_footprints(area)`. Use a bounding box slightly larger than the area of interest.
- **The location's precision.** The search radius grows with each record's `precision_m`, up to `max_distance_m`, so an address point is matched more tightly than a rough geocode.

## Steps

1. **Run** `match_building_footprints(dataset_id, buildings_dataset_id)` with the defaults first (`max_distance_m` 30, `assign_geometry` "none").
2. **Read the distribution** of statuses in the result:

   | Status | Meaning | Report it as |
   |---|---|---|
   | `exact` | The point is inside the footprint, and a name, address or identifier confirms it | Matched |
   | `probable` | Inside one footprint with nothing to confirm it, or outside but next to one that a name, address or identifier confirms | "Probably in building X", with the reason |
   | `ambiguous` | Inside several overlapping footprints, or several nearby footprints score within the margin; none chosen | Unresolved, listing the candidates |
   | `approximate_point` | Footprints nearby, but nothing confirms any of them | The point stands on its own; no building |
   | `none` | No footprint within the radius, or no location | No building found |

3. **Investigate the ones that matter.** For important `probable` or `ambiguous` records, look for evidence that names the building:
   - the address on the footprint;
   - the building's name in OpenStreetMap;
   - an operator's page describing which building or entrance;
   - a site plan in an official document.

   Add it as evidence (for example the footprint id in `external_ids` as `"osm": "way/123"`, with a source) and rerun the match. A record that cites its footprint becomes `exact` when its point lies inside it, and `probable` when the point is only nearby. Seeing the place yourself is not available to you; don't claim visual confirmation.
4. **Decide on geometry.** Keep `assign_geometry: "none"` when the user wants points with a building reference. Use `"exact"` to replace points with footprints only where confirmed. Use `"exact_or_probable"` only if the user accepts probable outlines, and say so in the report.
5. **Report** the counts by status, the records you investigated, and the candidates for each ambiguous one. Every match short of `exact` also leaves one line in the record's `limitations`, starting "Building match: ", which the quality report and exports carry; a rerun replaces it.

## Tuning

| Situation | Adjust |
|---|---|
| Large sites (campuses, stations) whose point is a centroid in open ground | Raise `max_distance_m` (for example to 100) for that run, and expect more `ambiguous` |
| Dense terraces where neighbours share walls | Keep the defaults; ambiguity is the honest answer without an address match |
| Names on footprints differ slightly from records ("St Mary's" vs "Saint Mary") | Add aliases during normalisation rather than lowering `name_threshold` (option, default 0.85) |
| Too many ambiguous results between two near-identical candidates | Look at the candidates' reasons; raising `ambiguity_margin` (option) makes more records ambiguous, never fewer, so fix the evidence instead |

Pass `name_threshold`, `ambiguity_margin`, `min_radius_m` and `max_candidates` through `options`. See [../references/matching-rules.md](../references/matching-rules.md) for how candidates are scored.

## Pitfalls

- **Nearest is not matched.** Snapping every point to the nearest building is the most common way maps go wrong. The tool refuses to do it; don't reproduce it by hand.
- **Footprints change.** Use footprints from the same period as the records. A demolished or new building creates false `none` or `ambiguous` results.
- **Multi-building facilities.** A hospital may span several footprints. One record matches one footprint. If the user needs all the buildings of a site, say so: that needs a site boundary, which is a different source.

## Without the server

You can describe the method and the statuses, but don't produce building links without a footprint dataset and a matching run. Deliver the records with `building_match` left empty and a note.
