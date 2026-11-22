# Changelog

Notable changes to `geoproduct-core`, `geoproduct-mcp` and the `geoproduct-engineer` Agent Skill, which are released together under one version. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow the policy in [docs/releasing.md](docs/releasing.md).

## [Unreleased]

The first release, planned as `0.1.0`.

### Added

- **Data contracts.** `geoproduct/v1` covers records, sources, evidence, building matches, confidence, datasets, pipeline specs, runs and errors. They are published as JSON Schemas in `schemas/v1/`.
- **Pipeline engine.**
  - Declarative YAML or JSON specs built from 18 registered stages.
  - A one-pass spec check.
  - Content-addressed checkpoints, resume with changed parameters, retries, cancellation and timeouts.
  - Errors outside any stage are recorded.
- **Stages.**
  - Collecting from providers, files or researched records.
  - Normalising, deduplicating with conflicts kept, and geocoding.
  - Matching building footprints, with a reason recorded for every uncertain match.
  - Enrichment, confidence scoring, validation and quality gates.
  - GeoJSON and CSV export with manifests, and Markdown and JSON quality reports.
- **Providers.**
  - The offline `fixture` provider, over the synthetic Sample Town.
  - `nominatim` and `overpass`, with throttling, caching, retries, a separate connect limit and the public services' usage policies.
- **The `geoproduct` command line**: `stages`, `stage`, `plan`, `run`, `resume`, `status`, `cancel` and `runs`.
- **The MCP server** (`geoproduct-mcp`):
  - 21 tools, 2 prompts and read-only resources;
  - stdio, and Streamable HTTP on loopback;
  - background pipeline runs;
  - confirmation above 25 external requests;
  - source fetching limited to public addresses and checked against `robots.txt`.
- **The Agent Skill** (`skills/geoproduct-engineer`): the method, 11 workflows, 6 references, two standard-library scripts (`check_dataset.py`, `score_source.py`) and evaluation cases.
- **Tests.**
  - Unit, integration, skill-drift and end-to-end tests that need no network: a research-to-export workflow, failure and recovery.
  - Opt-in live tests for the public OpenStreetMap services.
- **Release files.** The Apache-2.0 licence, documentation, contribution and security guides, and CI that builds and checks the packages.
