# geoproduct-core

The engine behind [GeoProduct Engineer](https://github.com/Ppp2048/geoproduct-engineer-skill): schemas, providers and a resumable pipeline engine for building geographic datasets in which every location and attribute can be traced to its evidence.

It has no MCP or LLM dependency. Use it from Python or through the `geoproduct` command line:

```bash
geoproduct stages                      # the registered pipeline stages
geoproduct plan pipeline.yaml          # check a spec and show the plan
geoproduct run pipeline.yaml           # run it; stages are checkpointed
geoproduct resume RUN_ID               # continue a failed or cancelled run
```

It contains:

- **Contracts.** The versioned `geoproduct/v1` contracts (records, sources, evidence, datasets, pipeline specs, runs), published as JSON Schemas.
- **Pipeline engine.** It runs declarative YAML or JSON specs built from 18 registered stages: collect, normalise, deduplicate, geocode, match buildings, enrich, score, validate, gate and export. It has checkpoints, retries, cancellation and resume.
- **Providers.** An offline `fixture` provider, plus Nominatim and Overpass with throttling, caching and attribution. The public OpenStreetMap services need `GEOPRODUCT_USER_AGENT` to be set.
- **Exports.** GeoJSON and CSV with a manifest, plus a quality report in Markdown and JSON.

Datasets you build carry their sources' licences, for example ODbL for OpenStreetMap data. That is separate from this package's licence, Apache-2.0.

See the [repository](https://github.com/Ppp2048/geoproduct-engineer-skill) for documentation, examples and the MCP server (`geoproduct-mcp`).
