# Spatial analysis: proximity, containment and measurement

Questions like "which schools are within 500 m of a bus stop", "which district is each clinic in", "how much park area per neighbourhood". The tools answer these deterministically on datasets you have built. Your job is to frame the question so the answer means what the user thinks it means.

## What the tools compute

| Operation | Tool or stage | Adds to each record |
|---|---|---|
| Nearby | `enrich_records(operation="nearby", reference_dataset_id, field, radius_m, categories)` / `enrich.nearby` | Count of reference features within the radius, nearest distance in metres, and up to `max_ids` nearest ids |
| Within | `enrich_records(operation="within", reference_dataset_id, field)` / `enrich.within` | The reference polygons containing the record (names or ids) |
| Measure | `enrich_records(operation="measure")` / `enrich.measure` | `area_m2` for polygons, `length_m` for lines, `perimeter_m` for polygons |
| Rule checks | `validate_dataset(rules=[near / within / not_within])` | Findings for records that break a relationship |

Distances and areas are metric, computed in a local projection centred on each feature: accurate to well under 1% within a few kilometres. All inputs and outputs are WGS 84 longitude and latitude.

## Steps

1. **Turn the question into operations.** "Schools within 500 m of a bus stop" means schools `nearby` stops with `radius_m` 500, then filtering the schools with a count of 1 or more. Write the question in this form and show the user if the wording was loose.
2. **Build both datasets for an area with a margin.** A record near the edge of the area has neighbours outside it. Collect the reference features for the area plus the largest radius you use, or the counts near the edge will be too low.
3. **Check the inputs** before analysing them: validated, deduplicated, with known location kinds. Analysis on a centroid-located dataset inherits the centroids' error, often tens of metres.
4. **Run the operations,** chaining datasets: each enrichment returns a new `dataset_id`.
5. **Summarise.** The tools enrich records; totals and shares are for you to compute from the exported data or the previews, and to state with the counts behind them.
6. **State the method and its limits** in the answer: straight-line distance, the radius, the area and margin, and the as-of date of each dataset.

## Questions the toolkit does not answer

| Question | Why not | Say or do |
|---|---|---|
| Travel time or network distance | No routing | Straight-line distance is a lower bound; a routing service is needed for the real figure |
| Coverage polygons, buffers or isochrones as outputs | No geometry-producing operations beyond the data | Describe coverage per record ("3 stops within 500 m") instead |
| Population served | No population data built in | Needs a population source; register it like any other |
| Raster analysis (land cover, elevation) | Vector only | Another tool or server is needed |

## Pitfalls

- **Edge effects:** see step 2.
- **Categories in the reference.** `nearby` counts every reference feature unless `categories` filters them. Counting "stops" that include disused ones changes the answer.
- **Polygons versus points.** `within` works on the record's representative point. A large polygon straddling two districts gets the one containing its point; say so when it matters.
- **Implying causation.** "Clinics are far from stops" is a measurement, not an explanation.

## Without the server

Explain the method and what it would compute, and estimate where you can from sources that state the numbers. Don't produce computed distances or areas you did not compute.
