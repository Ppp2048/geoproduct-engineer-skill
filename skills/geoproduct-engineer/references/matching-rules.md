# Matching rules: deduplication, geocoding and buildings

How the deterministic matching decides, so you can explain a result, tune a threshold for a reason, and recognise when a result needs your research rather than a different setting.

## Deduplication (`deduplicate_entities`, `resolve.deduplicate`)

1. **Keys.** Names are normalised: case, accents and punctuation removed, common abbreviations and legal suffixes folded, and aliases included. Addresses get a normalised key too.
2. **Blocking.** Only nearby records (spatial index, within `max_distance_m`) and records sharing a name word are compared, which keeps large datasets fast.
3. **A pair is a duplicate when:**
   - it shares an identifier from `id_keys` (default website, phone, email, wikidata), with the defaults' meaning of each, and the two are not far apart; or
   - the name similarity (RapidFuzz token measures over names and aliases) is at least `name_threshold` (0.88) and they are within `max_distance_m` (150 m); or
   - neither has a location, and the names are similar and the addresses are the same.

   With `same_category` (on by default), only records of the same category can match.
4. **Shared identifier, far apart:** one organisation with several sites. The records are linked by `organisation_id` and stay separate.
5. **Merging.** Clusters merge into one record that:
   - keeps every source and piece of evidence;
   - takes the most precise location (footprint before entrance, then address point, centroid, approximate);
   - lists each absorbed record under `merged`, with a score and reasons;
   - keeps each attribute with differing values as a conflict, with that attribute cleared and the status `conflicting`.

## Geocoding (`geocode`, `geocode.records`)

- **Which records.** Only records without geometry are geocoded, unless `overwrite` is set. The text is the address (full, or built from its parts), or the name when `use_names` is on and there is no address.
- **Choosing a candidate.** Candidates scoring below `min_score` (0.6) are dropped; unscored candidates are kept.
  - One usable candidate is accepted.
  - Two or more, unless the top one leads by `ambiguity_margin` (0.05), leave the record unresolved, with the candidates in its limitations.
  - Nominatim gives no scores, so with Nominatim any second candidate means unresolved.
- **What is recorded.** The location kind comes from the result: `address_point` with a house number, `centroid` for a precise place, otherwise `approximate`. The precision is half the result's bounding-box diagonal. The geocoder is cited as evidence for `geometry`.
- **Your part.** A single accepted candidate can still be wrong, especially for a name-only query. Pass `bias` (the area) and `country_codes`, then check the kind and the returned address against your sources.

## Building matching (`match_building_footprints`, `match.buildings`)

1. **Search radius.** The record's `precision_m`, clamped between `min_radius_m` (5) and `max_distance_m` (30). Without a precision, `max_distance_m` is used.
2. **Candidates** are footprints containing the point, or within the radius. Each is scored as 0.6 × closeness (1 when containing) + 0.4 × name similarity, plus 0.2 if confirmed, capped at 1.
3. **Confirmation** is independent of position:
   - the record's name or an alias is at least `name_threshold` (0.85) similar to the footprint's name;
   - the addresses agree;
   - the record cites the footprint (`external_ids` has the provider's id for it, or `building: "<provider>:<id>"`).
4. **Decision.**
   - One containing footprint: `exact` if confirmed, `probable` if not.
   - Several overlapping containing footprints: `ambiguous`, unless exactly one is confirmed.
   - No containing footprint:
     - nothing within the radius: `none`;
     - the top two candidates within `ambiguity_margin` (0.1): `ambiguous`;
     - a confirmed top candidate: `probable`;
     - otherwise `approximate_point`. The point stands on its own, because nearness alone proves nothing.
5. **Output.** Every match lists its reasons and up to `max_candidates` (5) candidates, with distance, containment, name similarity and score, so the decision can be audited.

## When to tune, and when to research

Tune a threshold when the data has a known systematic property. For example, campus centroids are routinely 80 m from buildings, or a register always abbreviates "Saint". Research when individual records are uncertain: tuning to make an ambiguous record resolve is guessing with extra steps. Record every non-default setting, with its reason, in the report.
