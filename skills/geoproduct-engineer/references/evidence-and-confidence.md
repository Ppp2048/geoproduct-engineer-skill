# Records, evidence and confidence

The `geoproduct/v1` contracts separate what sources state from what was calculated or concluded, and they record how sure each record is. Full schemas: `geoproduct://schemas/v1/record`, `…/evidence`, `…/source`.

## A record

| Field | Meaning |
|---|---|
| `id` | Stable identifier, unique in the dataset |
| `name`, `aliases` | Official name, and other names used for matching |
| `category` | One scheme per dataset, for example `amenity=hospital` or `health.hospital` |
| `organisation_id` | Links several sites of one organisation |
| `external_ids` | Identifiers elsewhere: `{"osm": "way/123", "register": "H-0042", "website": "…"}` |
| `address` | `full`, `house_number`, `street`, `locality`, `region`, `postcode`, `country_code` (upper case) |
| `attributes` | Name → `{value, basis, evidence_ids, note}` |
| `geometry` | GeoJSON geometry in WGS 84 longitude and latitude, or absent |
| `location` | `kind`, `precision_m`, `method` (for example `geocode:nominatim`), `evidence_ids` |
| `building_match` | Status, building reference, method, score, reasons and candidates |
| `evidence_ids` | Evidence that the entity exists and is what the record says |
| `confidence` | Level, score and reasons |
| `status` | `resolved`, `unresolved`, `conflicting` or `rejected` |
| `conflicts` | Per field, every disagreeing value with its evidence |
| `merged` | Records merged into this one, with score and reasons |
| `limitations` | Plain statements of what is missing or uncertain |

## Basis of an attribute

| `basis` | Use for | Needs |
|---|---|---|
| `fact` | What a source states | `evidence_ids` pointing at evidence that supports `attributes.<name>` |
| `estimate` | A calculation or model, such as floors × 3 m for a height, or an area from a footprint | A `note` saying how |
| `inference` | A conclusion from other facts ("likely closed: no listing since 2023 and the site is for sale") | A `note` with the reasoning |

Never label an estimate or an inference as a fact: a reader treats facts as checked. A value with no basis at all is left out.

## Evidence

```json
{"id": "ev-7", "source_id": "src_…", "supports": ["name", "address", "attributes.beds"],
 "excerpt": "St Example's Hospital, 3 Harbour Road, has 240 beds", "locator": "Annual report 2025, p. 12"}
```

- **`supports`** names record fields: `name`, `address`, `geometry`, `category`, `attributes.<name>`.
- **`excerpt`** is copied verbatim from the source. Keep it short: the sentence or table row that states the fact.
- **`locator`** says where in the source: a page, section, table row or feature id. Use it when the excerpt alone is hard to find.
- **`excerpt_check`** is set by the server against the fetched text:
  - `verified`: found, ignoring case, spacing, curly quotes and dashes;
  - `not_found`: the fetched text does not contain it;
  - `not_checked`: the source wasn't fetched, or is a PDF, whose text isn't extracted.

  A value you type into this field is replaced.

## Location kinds

Ordered from most to least precise. The weight is the location part of the confidence score.

| `kind` | Meaning | Weight |
|---|---|---|
| `building_footprint` | The geometry is the building outline | 1.0 |
| `entrance` | A surveyed or published entrance point | 1.0 |
| `address_point` | An address-level point, for example a geocode with a house number | 0.85 |
| `centroid` | The centre of a place, street or area | 0.6 |
| `approximate` | Somewhere near, for example a town-level geocode | 0.35 |
| `unknown` | No information | 0.25 |

An `exact` building match raises the location part to 1.0, and a `probable` one to 0.85. A footprint assigned from a `probable` match (`assign_geometry: exact_or_probable`) also counts 0.85, not 1.0: it proves no more than the match. A `precision_m` worse than 100 m halves it.

## Confidence score

`score_confidence` and `quality.confidence` add four parts, each with a reason on the record:

| Part | Weight | Full marks when |
|---|---|---|
| Evidence | 0.35 | Two or more independent sources describe the record. One source gives 0.2 of the 0.35. A geocoder or footprint that only placed the record counts under location, not here. |
| Source quality | 0.2 | 0.2 × the best cited source's score (0–1). Cited sources that were never rated give 0.1. |
| Location | 0.3 | Weight from the table above, times 0.3 |
| Consistency | 0.15 | No conflicts, and every fact cites evidence. With conflicts this part is 0; with unsupported facts it is 0.05. |

The levels are: `high` ≥ 0.75, `medium` ≥ 0.5, `low` ≥ 0.25, otherwise `unresolved`. A record without geometry is `unresolved` whatever its score. The thresholds can be changed per call; say so if you change them.

## Status

- **`resolved`:** located, with enough confidence.
- **`unresolved`:** not located, or the confidence is too low. Its `limitations` say why.
- **`conflicting`:** sources disagree, and the `conflicts` list each value with its evidence. The disputed attribute is cleared until resolved.
- **`rejected`:** failed validation or the inclusion rules. Kept in the rejects with the reason, not in the dataset.

## Writing limitations

One plain sentence per gap, saying what is missing and why. For example: "No source gives opening hours", "Geocoded from the street name only; the position may be off by 200 m", or "Two footprints are equally plausible: way/41 and way/42". These lines go straight into the user's report.
