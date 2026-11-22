# Infrastructure research: networks and assets

Transport, power, water, telecom and roads differ from facility research in three ways:
- the features are lines and networks as well as points;
- correctness is often a spatial relationship (a stop must be on a street);
- some details are sensitive.

## Pin down first

- **Asset type and level of detail.** Stations, or every stop? Substations, or every pole? Trunk lines only?
- **The network relationships that must hold,** and the attributes that matter: operator, voltage, line or route, status, capacity.
- **Whether the user needs geometry** (lines with lengths) or an inventory (counts and locations).

## Steps

1. **Sources.**
   - Operators and regulators publish asset registers, timetables and network maps; open transit feeds publish stops and routes.
   - OpenStreetMap covers much of the visible infrastructure. Typical tags:
     - transport: `highway=bus_stop`, `railway=station|halt`, `public_transport=platform`;
     - power: `power=substation|line|tower`;
     - water: `man_made=water_tower|water_works`;
     - telecom: `man_made=mast` or `tower:type=communication`;
     - roads: `highway=primary|secondary|…`.

     Check tag usage for the region before relying on one tag.
   - Rate the sources as usual (see [source-discovery.md](source-discovery.md)).
2. **Collect** assets and the network they belong to separately: the stops, and the streets; the substations, and the lines. Each is its own `search_osm_features` call, or `collect.features` stage.
3. **Resolve** the operator's list against the map with deduplication (see [entity-resolution.md](entity-resolution.md)). Stop names are often reused across a network, so keep `max_distance_m` small (for example 50 m) and rely on stop codes as identifiers where they exist.
4. **Add network context** with `enrich_records`:
   - `nearby` with the street or line network: distance to the nearest segment and how many lie within a radius;
   - `within` service areas or districts;
   - `measure` for line length and polygon area.
5. **Validate with rules** that encode the network's logic:

   ```json
   [
     {"name": "stop-on-street", "kind": "near", "reference_dataset_id": "ds_streets", "max_distance_m": 30, "categories": ["highway=bus_stop"]},
     {"name": "not-in-park", "kind": "not_within", "reference_dataset_id": "ds_parks"},
     {"name": "in-service-area", "kind": "within", "reference_dataset_id": "ds_districts", "severity": "warning"}
   ]
   ```

   Each violation is a finding with the record and the distance or relation that broke the rule.
6. **Report** the inventory by type and status, the rule violations, the operator conflicts, and coverage gaps (parts of the area with no data, rather than no infrastructure).

## Sensitivity

Stick to what operators and the public map already publish. Don't compile non-public security details: protection measures, vulnerabilities, or access arrangements for critical sites. Don't infer such details by combining sources. If a request heads that way, explain the limit and offer the public inventory instead.

## Pitfalls

- **Lines split into many segments.** One OpenStreetMap way is not one route. Lengths add up per segment, and a route is a relation. Report segment counts and total lengths, not "routes", unless a source defines the routes.
- **Planned or disused assets.** Tags and registers include proposed, under-construction and abandoned features. Filter by status, or report the status explicitly.
- **Straight-line distances.** `nearby` distances are straight lines. Walking or driving distance needs a routing service, which this toolkit doesn't include; say so if the user's question depends on it.

## As a pipeline

`examples/pipelines/transport.yaml` in the repository shows the pattern: collect stops, stations, streets, parks and districts; combine; then `enrich.nearby`, `enrich.within`, `validate.dataset` with rules, `report.quality`, and a `quality.gate`. See [pipeline-construction.md](pipeline-construction.md).
