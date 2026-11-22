# Map integration: adding new data to a map the user already has

The user has a layer (their own dataset, a published map, a GIS project) and wants new or updated data merged in without breaking what is there. The risks are duplicates, silently overwritten values, broken identifiers and licence conflicts.

## Pin down first

- **The existing data's format and coordinate system.** The server reads and writes WGS 84 longitude and latitude only. Data in a projected system (metres) must be converted by the user's GIS tool before and after.
- **Which side wins.** For each attribute, decide what happens when the new data disagrees: keep the existing value, take the new one, or flag it. The default here is flag; never overwrite silently.
- **Identifiers.** Does the user's map have stable ids that other systems rely on? Those ids must survive.
- **Licences.** What licence is the existing map under, and can the new sources be combined with it? See [../references/providers-and-licensing.md](../references/providers-and-licensing.md).

## Steps

1. **Load the existing data** with `create_dataset(file=...)`. Keep its ids. Record its source as the user's (`user_file`), with the date it was exported.
2. **Build the new data** as usual: research, files or map data.
3. **Normalise both the same way:** the same category mapping, the same aliases, the same coordinate decimals.
4. **Combine and deduplicate:** `create_dataset(dataset_ids=[existing, new])`, then `deduplicate_entities`. Merged records are entities both sides have. Their `merged` entries show which ids joined, and their `conflicts` show every value that differs.
5. **Classify the outcome per record:**

   | Outcome | Meaning | Action |
   |---|---|---|
   | Matched, no conflicts | Both agree | Keep the existing id; add the new evidence |
   | Matched, conflicts | Both describe it, differently | Apply the user's rule per attribute, or report it for review |
   | Only in the new data | Possibly missing from the map | Add it, with its evidence and confidence |
   | Only in the existing data | Possibly closed, or missing from the new sources | Don't delete; flag it for checking |
   | Moved (matched by id or name, far apart) | Relocated, or one side is wrong | Investigate before updating the geometry |

6. **Validate the integrated dataset** (`validate_dataset` with the area), and match buildings if the map uses them.
7. **Export in the shape the user needs:** GeoJSON with provenance, or CSV with `geometry` as point columns or WKT (`options={"geometry": "wkt"}`). Hand over the change list (added, updated, conflicting, to check) alongside the data, so the user can review before replacing their layer.

## Pitfalls

- **Losing ids.** A merge creates a new record that lists the ids it absorbed. Map the user's id back from `merged`, or from `external_ids`, before export, and state which id is canonical.
- **"Only in existing" is not "closed".** The new sources may simply not cover it.
- **Precision downgrades.** Replacing a surveyed point with a geocoded centroid makes the map worse. Compare location kinds before updating geometry, and keep the more precise one unless there is evidence it is wrong.
- **Share-alike.** Merging OpenStreetMap data into a database that is then published brings ODbL obligations for the derived database. Tell the user before they publish.

## Without the server

Describe the comparison rules above, and apply them by hand to small datasets with an explicit change list. Check the combined file with `scripts/check_dataset.py`.
