# Matching rules: deduplication, geocoding and buildings

How the deterministic matching decides, so you can explain a result, tune a threshold for a reason, and recognise when a result needs your research rather than a different setting.

## Deduplication (`deduplicate_entities`, `resolve.deduplicate`)

1. **Keys.** Names are normalised: case, accents and punctuation removed, common abbreviations and legal suffixes folded, and aliases included. Addresses get a normalised key too.
2. **Blocking.** Only nearby records (spatial index, within `max_distance_m`) and records sharing a name word or an identifier are compared, which keeps large datasets fast. Phone numbers block on their last 7 digits so that the forms of one number meet; blocking only proposes a pair, and the comparison uses every digit.
3. **Levels of identifier.** Each identifier value is site-level, organisation-level or of unknown meaning.
   - **Site-level** identifiers name one physical establishment: register codes (a health facility register, a school census number) and the like. Name the keys your records carry in `site_keys` (default: none), for example `site_keys: ["register"]`. A street address with a house number counts as site-level evidence too.
   - **Organisation-level** identifiers are shared by every site of an organisation: a corporate website, a central booking phone, an organisation email, the brand's or operator's Wikidata item. They are the keys in `organisation_keys` (default `website`, `phone`, `email`, `brand:wikidata`, `operator:wikidata`).
   - **Wikidata items** (`wikidata`) are classified item by item, because the one key holds both kinds: OpenStreetMap's `wikidata` tag names the establishment on some features and the brand on others. An item is site-level only when it is listed in `wikidata_sites`. It is organisation-level when it is listed in `wikidata_organisations`, when any record gives it as its `brand:wikidata` or `operator:wikidata`, or when `wikidata` is in `organisation_keys`. Any other item is of **unknown meaning**: shared, it links records like an organisation-level identifier and merges nothing; differing, it keeps records apart like a site-level one, because the two items may name two establishments. The stage notes count the shared items of unknown meaning. `wikidata`, `brand:wikidata` and `operator:wikidata` cannot be site keys.
   - **Classifying an item.** Look it up on Wikidata. An instance (P31) of a facility type, such as a hospital, a school or a shop, with coordinates (P625) at the site names one establishment: list it in `wikidata_sites`. An instance of a company, brand, chain, franchise or other organisation, or an item whose coordinates are elsewhere, does not: list it in `wikidata_organisations`. Say in the report which items you classified and why. The toolkit has no offline way to tell.
   - The older `id_keys` list still works: `website`, `phone` and `email` in it are organisation-level, `wikidata` is classified item by item as above, and every other key is site-level.
   - **Identifiers repeated at distinct sites.** A site-level value, or a Wikidata item of unknown meaning, carried by records that are shown to be different sites names more than one site, so it is treated as organisation-level. Records are shown to be different sites when any two of those carrying the value are farther apart than `max_distance_m`, give street addresses that differ, or carry different values of another site-level key. The stage notes say how many values were treated this way. Items listed in `wikidata_sites` are exempt, because you checked them. A site key repeated on records that are close together, with no addresses and nothing else to tell them apart, still merges them: naming a key in `site_keys` says that a value names one site.
4. **Comparing values.** Websites are compared without scheme, `www.` or trailing slash. Phone numbers lose their extension (`ext. 12`, `x12`, `#12`) and any `(0)` inside an international number, and numbers under 7 digits are ignored. Two numbers are the same only when every digit matches. An international and a national form (`+44 20 7946 0958` and `020 7946 0958`), or a national number with and without its area code, might be one number but cannot be settled without the country's numbering plan: the comparison is **unclear**, and the reason reads `phone may match`. An unclear comparison neither confirms nor contradicts.

   A field can hold several values. Every key is split on `;`, and a phone field also on `,`, `/`, `or` and `and` when every part is a number of 7 digits or more, so `030/1234567` stays one number and `+44 20 7946 0958, ext. 12` stays one number with its extension. The values are compared one by one: two fields are the same when they share a value, unclear when the closest result is unclear, and different otherwise.
5. **A pair is a duplicate, and merges, only when all of these hold:**
   - **Site evidence.** It shares a site-level identifier, or its names are at least `name_threshold` (0.88) similar (RapidFuzz token measures over names and aliases) and its street addresses agree, house number included.
   - **Compatible names.** The names are similar, or one record has no name and a site-level identifier links them.
   - **Placed.** The two are within `max_distance_m` (150 m), or one has no location.
   - **Not contradicted by site-level evidence.** No site-level identifier present in both has a different value, nor does a Wikidata item of unknown meaning, and the addresses do not name different places (another house number, street or postcode). An address that only says less, such as no house number, neither confirms nor contradicts.

   **Organisation-level evidence never merges on its own**, however close and alike the records: a shared website, phone and email with the same name 20 m apart still leaves two records. A chain's branches can stand a street apart, and a hospital's outpatient building can share its website. **Nor does it keep one site apart.** One site can have its own line and the organisation's central number, or pages on two websites, so when site-level evidence confirms one site, a differing website, phone, email, brand or operator item, or organisation Wikidata item does not block the merge. The difference is listed in the reasons (`different phone`), and both values are kept: the second identifier as `phone~2` in `external_ids`, a differing attribute as a conflict. With `same_category` (on by default), only records of the same category are compared.

   Which differences block a merge:

   | Difference between the two records | Blocks a merge | Why |
   |---|---|---|
   | A site key's value (two register codes) | Yes | Two site identifiers name two sites |
   | A Wikidata item listed in `wikidata_sites`, or of unknown meaning | Yes | The items may name two establishments |
   | The street address: house number, street, or postcode on the same street | Yes | Two addresses are two places |
   | Website, phone, email | No | One site can have several; they describe the organisation |
   | `brand:wikidata`, `operator:wikidata`, an item in `wikidata_organisations` | No | They name the organisation, not the site |
   | A phone comparison that is unclear (international against national form) | No | It cannot be settled without the numbering plan |
   | An address that says less (no house number, no postcode) | No | It is not a different place |
6. **Organisations.** Records with similar names that share an organisation-level identifier are linked by `organisation_id`, at any distance, and stay separate records.
7. **Possible duplicates.** Some pairs look alike but do not merge: a similar name within `max_distance_m` or on the same street, or site evidence held back by a disagreement or a name that does not match. These stay separate records. Each gets a limitation `Possible duplicate of <id> (<reasons>; not merged because <why>).`, at most five per record, and the stage notes count the pairs. The reasons say which:
   - `they differ in <field>`: a site-level identifier, a Wikidata item of unknown meaning, or the address disagrees;
   - `their names do not match`: a site identifier links records whose names differ;
   - `they share only organisation-level details (<keys>), which do not identify a site`;
   - `they share only a Wikidata item that is not known to name a site`: classify the item (step 3);
   - `no site-level identifier or matching address confirms it`.

   Research these pairs: add the site identifier or address that settles a real duplicate, or leave both and report them.
8. **Groups.** Records merge in groups in which every pair is a duplicate. Links are taken strongest first, and a record joins a group only if it is a duplicate of every member, so A and C never merge just because each matches B. A link left out this way is reported as a possible duplicate.
9. **Merging.** Each group merges into one record that:
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
