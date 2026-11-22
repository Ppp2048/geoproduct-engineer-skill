# Dataset creation: from research, files or map data to a clean dataset

Every dataset the server holds is a set of records in the `geoproduct/v1` Record format, with its sources and evidence. There are three ways in, and they can be combined.

## 1. Records you researched

Use this when the facts come from pages or documents you read.

1. Register and fetch each source (see [source-discovery.md](source-discovery.md)).
2. Write one record per entity, and one evidence item per source passage. Copy excerpts verbatim from the fetched text: short and specific, just enough to support the field.

   ```json
   {
     "records": [
       {
         "id": "clinic-riverside",
         "name": "Riverside Clinic",
         "category": "healthcare=clinic",
         "address": {"street": "Mill Road", "house_number": "12", "locality": "Exampleton", "country_code": "ZZ"},
         "attributes": {
           "opening_hours": {"value": "Mo-Fr 08:00-18:00", "basis": "fact", "evidence_ids": ["ev-hours"]},
           "beds": {"value": 12, "basis": "fact", "evidence_ids": ["ev-beds"]}
         },
         "evidence_ids": ["ev-hours"]
       }
     ],
     "evidence": [
       {"id": "ev-hours", "source_id": "src_…", "supports": ["name", "address", "attributes.opening_hours"],
        "excerpt": "Riverside Clinic, 12 Mill Road. Open Monday to Friday, 8am to 6pm."},
       {"id": "ev-beds", "source_id": "src_…", "supports": ["attributes.beds"],
        "excerpt": "the clinic's 12 inpatient beds", "locator": "Annual report 2025, p. 4"}
     ]
   }
   ```
3. Call `create_dataset(records=..., evidence=...)`. Read the result:
   - `rejected` records failed the schema, and the reason is stored with them;
   - the summary says how many excerpts were verified and how many were not found;
   - a `not_found` excerpt means the quote isn't in the fetched text. Fix the quote or drop the claim.

Leave `geometry` out unless a source gives coordinates. Geocoding or matching adds it later, with its own evidence.

## 2. A file

Use this for the user's CSV, GeoJSON or JSON, or an open-data download.

- **A local file:** `create_dataset(file=..., columns=..., licence=..., category=...)`. The file must be inside the workspace or an allowed folder; if it isn't, the error says so, and the user can add the folder. For CSV, map the column names with `columns` (`id`, `name`, `category`, `lon`, `lat`, `address`, `house_number`, `street`, `locality`, `postcode`, `country_code`). Other columns become attributes.
- **A fetched URL:** register it, `fetch_source_data`, then `create_dataset(source_id=...)`. The records are then credited to that source automatically.
- **Check the result:** look at the rejects (bad coordinates, missing ids) and the preview. A file with coordinates in a projected system, such as metres, fails the coordinate checks: ask the user for the coordinate system. The server only reads WGS 84 longitude and latitude.

## 3. Map data

`search_osm_features(area, tags)` returns a records dataset with geometry, tags kept as facts, and OpenStreetMap evidence. `get_building_footprints(area)` returns a buildings dataset, used only for matching and spatial rules. Choose tags deliberately:

- `{"amenity": ["hospital", "clinic"]}` matches either value; `{"amenity": null}` matches any amenity.
- Several keys must all match: `{"amenity": "school", "operator:type": "public"}`.
- Set `limit` above the expected count. A result cut off at the limit is an error unless you pass `allow_truncated`, because a silently partial dataset looks complete.

## Conventions

- **Categories.** Use one scheme across sources. OSM-style `key=value` (`amenity=hospital`) or your own `domain.type` (`health.hospital`) both work. Map the others onto it with `normalize_records(categories=...)`.
- **Ids.** Stable and meaningful: a register's id, or `osm:way/123` for map features. Never reuse an id for a different entity.
- **Attributes.** Use `basis: "fact"` with evidence for what a source states, `"estimate"` for calculations (with a `note` on how), and `"inference"` for conclusions (with a `note` on the reasoning).
- **Names.** Keep the official name in `name` and other spellings in `aliases`, which deduplication uses.

## Without the server

Write the same JSON. It is the input format of a `collect.records` pipeline stage, so the work isn't lost: the user can run it later. Validate any GeoJSON you produce with `scripts/check_dataset.py`.
