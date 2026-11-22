# Map accuracy audit: checking an existing map or dataset

The user has a map layer or a location list and wants to know what is wrong with it before publishing or relying on it. You find, classify and explain the problems. You don't silently "fix" them, least of all by moving points to guessed positions.

## Pin down first

- **What the data claims to be:** which entities, which area, as of when, and how it was made (surveyed, geocoded, copied from another map).
- **What it is used for,** which sets the precision that matters. A delivery map needs entrances; a regional overview tolerates 100 m.
- **Whether a reference exists** to compare against: an official register, OpenStreetMap, or the user's own ground truth.

## Steps

1. **Load it.** Use `create_dataset(file=..., columns=...)` for a CSV (map the longitude and latitude columns explicitly), or load GeoJSON directly. Rows that fail to load (coordinates out of range, missing ids) are findings in themselves: read the rejects.
2. **Run the internal checks:** `validate_dataset(dataset_id, area=<the area the data should cover>)`. It covers:
   - coordinates and coordinate system, including points at 0,0 and values outside valid ranges;
   - geometry validity;
   - missing geometry;
   - duplicates within `duplicate_distance_m`;
   - records outside the area;
   - outliers far from the rest (`outlier_distance_m`);
   - facts without evidence.
3. **Compare with a reference** where one exists:
   - collect the reference (`search_osm_features` for the same category, or a register loaded as a dataset);
   - `enrich_records(operation="nearby", reference_dataset_id=..., field="reference", radius_m=...)` gives each record the count and distance of reference features nearby;
   - records with no counterpart within a sensible radius may be misplaced, closed or missing from the reference;
   - run the comparison the other way round as well, to find entities in the reference that the user's data lacks.
4. **Add domain rules** where the data has them, as `rules` in `validate_dataset`: stops `near` streets, facilities `within` the service area, nothing `not_within` water or parks.
5. **Classify each finding:**

   | Class | Examples | Typical cause |
   |---|---|---|
   | Position | Swapped longitude and latitude; 0,0; far outside the area; centroid of the town instead of the site; coordinates rounded to 2 decimals (about 1 km) | Geocoding fallbacks, spreadsheet edits, projection mix-ups |
   | Existence | Duplicates; closed places still listed; real places missing | Merged sources, stale data |
   | Attributes | Wrong category, outdated name or hours, conflicting values | Copying without dates |
   | Provenance | No source or date for a record | Undocumented edits |

6. **Verify a sample.** For the most consequential findings, check against a source and attach the evidence. A finding with evidence ("the operator's site gives 14 Mill Road; the map point is 600 m away") is actionable. A finding without it is a suspicion; label it so.
7. **Recommend fixes** per finding: re-geocode with the full address and the area as bias, take coordinates from the register, remove a duplicate, or confirm closure. Give the evidence-backed correction where you have one; otherwise give the method.
8. **Report** with `generate_quality_report`, plus your classification and the sample you verified. Lead with the counts by class and severity, then the worst findings.

## Pitfalls

- **Assuming the reference is right.** OpenStreetMap or a register can be the one that is wrong. Report disagreements, not verdicts, unless evidence settles them.
- **Over-reading precision.** Seven decimals don't make a geocoded point accurate. Look at how the points were made.
- **Swapped coordinates that look valid.** Swapping longitude and latitude can still give a valid point, just in the wrong place. Compare against the area, not just the valid ranges.

## Without the server

`scripts/check_dataset.py` runs the internal checks on GeoJSON or CSV:

```bash
python scripts/check_dataset.py data.csv --lon-column lon --lat-column lat --bbox -0.01,-0.01,0.02,0.02
```

It reports, as JSON:
- coordinates out of range, at 0,0, outside the bounding box, or likely swapped;
- duplicate ids and duplicate positions;
- unclosed rings;
- low precision;
- facts without evidence.

The reference comparison and the verification sample need your own research; say which parts you could and could not do.
