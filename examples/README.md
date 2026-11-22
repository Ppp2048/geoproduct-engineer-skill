# Example pipelines

Four pipelines that run offline against **Sample Town**, the synthetic data of the built-in
`fixture` provider. Sample Town is generated in code near 0°, 0°. It is not a real place,
and every result says so.

| Pipeline | What it shows |
|---|---|
| [`hospitals.yaml`](pipelines/hospitals.yaml) | Collect hospitals and clinics, merge a duplicate (its bed counts disagree, so both values are kept as a conflict), match building footprints, score confidence, validate, gate, export GeoJSON |
| [`commercial-buildings.yaml`](pipelines/commercial-buildings.yaml) | Combine several feature queries, link two sites of one organisation, replace points with matched footprints, measure floor area, add the district, export CSV and GeoJSON |
| [`transport.yaml`](pipelines/transport.yaml) | Check stops against spatial rules: near a street, not in a park, inside a district. One stop is deliberately 100 m from any street. |
| [`csv-enrich.yaml`](pipelines/csv-enrich.yaml) | Import [`data/sample-facilities.csv`](data/sample-facilities.csv). Rows without coordinates are geocoded; one row is rejected; an address that does not exist stays unresolved, with the reason recorded. |

## Running them

From the repository root, after `uv sync`:

```bash
uv run geoproduct plan examples/pipelines/hospitals.yaml
uv run geoproduct run examples/pipelines/hospitals.yaml
uv run geoproduct --allow examples/data run examples/pipelines/csv-enrich.yaml
```

Runs are written to `.geoproduct/runs/<run_id>/` (set another folder with `--workspace` or
`GEOPRODUCT_WORKSPACE`). Each stage has its own folder with its output, a `manifest.json`
and any `rejects.jsonl`. Exports and reports are in the folders of the export and report
stages.

Parameters declared in a spec can be changed without editing it:

```bash
uv run geoproduct run examples/pipelines/transport.yaml --param max_stop_distance_m=5
uv run geoproduct resume RUN_ID --param max_stop_distance_m=30
```

With a 5 m limit, four of the five records break a rule, so the first command fails at the
quality gate, which allows errors on at most a quarter of the records. The quality report is
still written, because it comes before the gate. Resuming with the corrected parameter
reuses every stage before validation and runs only the rest.
