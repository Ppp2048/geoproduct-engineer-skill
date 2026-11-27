# Entity resolution: duplicates, aliases and conflicting values

When records come from several sources, the same place appears more than once under slightly different names, and the sources disagree about details. Resolution merges true duplicates, keeps distinct places apart, and makes every disagreement visible.

## Steps

1. **Normalise first.** Run `normalize_records` on each input with the same settings:
   - `categories` maps every source's categories onto one scheme (otherwise `same_category` blocks real duplicates);
   - `aliases` lists known alternative names (`{"Sample General Hospital": ["SGH", "General Hospital"]}`);
   - `coordinate_decimals` defaults to 7 (about 1 cm); don't round coarser than the data's real precision.
2. **Deduplicate.** Run `deduplicate_entities`. Two records merge only when **site-level** evidence confirms they are one physical place: a shared site identifier (a register code: pass the keys your records carry as `site_keys`; or a Wikidata item you checked names the facility: list it in `wikidata_sites`, see below), or names at least 0.88 similar (RapidFuzz token similarity on normalised names and aliases) at a street address that agrees, house number included. Their names must fit, they must be within 150 m (or one has no location), and no site-level identifier or address may differ. A shared website, phone or email is **organisation-level**: every branch of a chain and every building of a hospital trust can share it, so it links records as one organisation and never merges them on its own; a differing one does not keep a confirmed site apart either. An identifier carried by records shown to be different sites (far apart, at different addresses, or with different site identifiers) counts as organisation-level. Phone numbers match only when every digit does, and a field with several numbers is compared number by number; an international against a national form is unclear and counts for nothing either way. Every pair in a merged group must match, so records are never chained together through a third. The full rule is in [matching rules](../references/matching-rules.md).
3. **Read the merges and the possible duplicates.** Each merged record lists `merged` entries with a score and reasons ("same register", "same address", "name similarity 0.93", "40 m apart"). Look at the borderline ones (scores near the threshold) and at any merge that joined different categories. Look-alikes that are not confirmed stay apart, and both records get a limitation `Possible duplicate of <id> (...; not merged because ...)`: a similar name nearby, records sharing only organisation-level details, a site identifier on records whose names differ, or evidence that disagrees. Research each pair that matters: add the site identifier or street address that confirms a real duplicate and run again, or report the pair as unresolved.
4. **Handle conflicts.** When merged records disagree on an attribute, each value is kept under `conflicts` with its evidence, the attribute is cleared, and the record becomes `conflicting`. For each conflict the user cares about:
   - compare the sources' tiers and dates;
   - look for a fresher or more authoritative source;
   - if one value is now clearly supported, add that evidence and rebuild the record;
   - if not, leave the conflict and report it.

   Never pick a value silently, because the user reads a single value as settled.
5. **Report** the merge count, the borderline merges you checked, the possible duplicates left for review, and the open conflicts.

## Organisations and sites

Records with similar names that share a website, phone or email are **one organisation**, near or far: they stay separate records, linked by `organisation_id`. That is usually right: a hospital trust with three hospitals is three map features, and two branches of a shop a street apart are two shops. When two such records are close, they are also marked as possible duplicates, because they might be one site mapped twice; only a site identifier or a matching street address settles that. If the user wants one record per organisation, say so explicitly and aggregate afterwards; don't merge sites.

If your records carry a website or phone that really is per site (each branch's own page or line), you can say so with `site_keys`, and say in the report that you did.

## Wikidata items

The `wikidata` key holds two kinds of item: the establishment itself (one hospital, one shop) or the organisation behind it (a hospital trust, a chain). The toolkit cannot tell them apart offline, so a shared item merges nothing until you classify it:

1. Find the items that more than one record carries. The stage notes count them (`merged nothing on N shared Wikidata item(s) of unknown meaning`), and the possible-duplicate limitations name the pairs.
2. Look each item up on Wikidata (`https://www.wikidata.org/wiki/<item>`). An instance of (P31) a facility type, such as a hospital, school or shop, with coordinates (P625) at the site names one establishment. An instance of a company, brand, chain, franchise or organisation does not, and neither does an item whose coordinates are elsewhere.
3. Pass the establishments as `wikidata_sites` and the organisations as `wikidata_organisations`, run `deduplicate_entities` again, and say in the report which items you classified and on what evidence.

An item a record gives as its `brand:wikidata` or `operator:wikidata` is already an organisation. An item in `wikidata_sites` is trusted even on records far apart, so list only items you checked.

## Tuning

| Symptom | Adjust |
|---|---|
| Obvious duplicates left as possible duplicates (same place, no site identifier) | Add the register code or street address your research confirms, and name the key in `site_keys`, or list a Wikidata item you checked in `wikidata_sites`; a threshold cannot supply missing evidence |
| Obvious duplicates not compared at all (abbreviations, "St" vs "Saint") | Add aliases first; only then lower `name_threshold` (not below about 0.8) |
| Duplicates far apart (a campus entrance vs its centroid) | Raise `max_distance_m` for that category, for example 300 m for campuses |
| Different places merged (a register code reused across sites, an item wrongly listed in `wikidata_sites`, or one address for a whole campus) | Check the site identifier really names one site; take the item out of `wikidata_sites`; keep `same_category` on; lower `max_distance_m` |
| Merges across categories you consider different | Keep `same_category: true` and check the category mapping |

Change one parameter at a time and compare the merge list. Explain any non-default value in the report.

## Pitfalls

- **Chains and franchises.** "Corner Market" may be ten different shops. Names, distance and a brand's website, phone or email are all shared across branches, so none of them merges records. Branches tagged with the brand's Wikidata item instead of their own merge nothing: an item is site-level only when you list it in `wikidata_sites`, and one a record also gives as its `brand:wikidata` is an organisation. A booking line or website shared by three branches links them as one organisation and never merges them.
- **Renamed places.** An old name in one source and a new one in another won't match by name. Add the old name as an alias, with evidence of the rename.
- **Merging to tidy a count.** A smaller number isn't a better dataset. Unmerged probable duplicates should be reported as such.

## Without the server

Apply the same rules by hand on small lists: merge only when a site identifier (a register code) or a very similar name at the same street address, house number included, confirms it, the two are close together, and no identifier or address differs. A Wikidata item counts as a site identifier only after you check that it names the facility. A shared website, phone or email makes records one organisation, not one site, and a differing one does not keep a confirmed site apart; a differing register code or address does. Compare phone numbers digit for digit, each number in a field on its own. Check every pair in a group. List look-alikes that do not meet the rule as possible duplicates. Keep a merge log with reasons, and keep conflicting values side by side in your output.
