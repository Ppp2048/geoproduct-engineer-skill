# Entity resolution: duplicates, aliases and conflicting values

When records come from several sources, the same place appears more than once under slightly different names, and the sources disagree about details. Resolution merges true duplicates, keeps distinct places apart, and makes every disagreement visible.

## Steps

1. **Normalise first.** Run `normalize_records` on each input with the same settings:
   - `categories` maps every source's categories onto one scheme (otherwise `same_category` blocks real duplicates);
   - `aliases` lists known alternative names (`{"Sample General Hospital": ["SGH", "General Hospital"]}`);
   - `coordinate_decimals` defaults to 7 (about 1 cm); don't round coarser than the data's real precision.
2. **Deduplicate.** Run `deduplicate_entities`. The defaults suit most point data: names at least 0.88 similar (RapidFuzz token similarity on normalised names and aliases) and within 150 m, or a shared identifier (`website`, `phone`, `email`, `wikidata`). Records without locations can still match on the same address.
3. **Read the merges.** Each merged record lists `merged` entries with a score and reasons ("names 0.93 similar, 40 m apart", "same phone"). Look at the borderline ones (scores near the threshold) and at any merge that joined different categories.
4. **Handle conflicts.** When merged records disagree on an attribute, each value is kept under `conflicts` with its evidence, the attribute is cleared, and the record becomes `conflicting`. For each conflict the user cares about:
   - compare the sources' tiers and dates;
   - look for a fresher or more authoritative source;
   - if one value is now clearly supported, add that evidence and rebuild the record;
   - if not, leave the conflict and report it.

   Never pick a value silently, because the user reads a single value as settled.
5. **Report** the merge count, the borderline merges you checked, and the open conflicts.

## Organisations and sites

Records far apart that share a website or phone are **one organisation with several sites**. They stay separate records, linked by `organisation_id`. That is usually right: a hospital trust with three hospitals is three map features. If the user wants one record per organisation, say so explicitly and aggregate afterwards; don't merge sites.

## Tuning

| Symptom | Adjust |
|---|---|
| Obvious duplicates not merged (abbreviations, "St" vs "Saint") | Add aliases first; only then lower `name_threshold` (not below about 0.8) |
| Duplicates far apart (a campus entrance vs its centroid) | Raise `max_distance_m` for that category, for example 300 m for campuses |
| Different places merged (a chain's branches with the same name nearby) | Lower `max_distance_m`; keep `same_category` on; add branch names as aliases |
| Merges across categories you consider different | Keep `same_category: true` and check the category mapping |

Change one parameter at a time and compare the merge list. Explain any non-default value in the report.

## Pitfalls

- **Chains and franchises.** "Corner Market" may be ten different shops. Names alone are weak evidence for chains; rely on distance and address.
- **Renamed places.** An old name in one source and a new one in another won't match by name. Add the old name as an alias, with evidence of the rename.
- **Merging to tidy a count.** A smaller number isn't a better dataset. Unmerged probable duplicates should be reported as such.

## Without the server

Apply the same rules by hand on small lists: same identifier, or very similar name and close together. Keep a merge log with reasons, and keep conflicting values side by side in your output.
