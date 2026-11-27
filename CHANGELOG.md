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

### Fixed

These were found by the November 2026 audit ([docs/audit-2026-11.md](docs/audit-2026-11.md)), before the first release.

- **Deduplication (GP-02, GP-16, GP-17).**
  - A similar name nearby no longer merges records on its own, and records are never chained together through a third. Every pair in a merged group must match.
  - Identifiers are site-level (`site_keys`, none by default; register codes are named per dataset) or organisation-level (`organisation_keys`: website, phone, email, brand:wikidata, operator:wikidata). A merge needs site-level evidence (a shared site identifier, or an agreeing street address with a house number), compatible names, nearby positions, and no differing site-level identifier or address.
  - A `wikidata` item is no longer a site identifier by default. It is site-level only when listed in the new `wikidata_sites`, organisation-level when listed in `wikidata_organisations` or given by a record as its brand or operator, and of unknown meaning otherwise: a shared unknown item links records and merges nothing, and the stage notes count such items. `wikidata` cannot be a site key. Three branches tagged with their chain's item merged before.
  - A shared website, phone or email no longer merges records, however close and alike: it links them by `organisation_id`. A differing one no longer keeps a confirmed site apart: both values are kept.
  - A site-level value, or a Wikidata item of unknown meaning, carried by records shown to be different sites (farther apart than `max_distance_m`, at different addresses, or with different site identifiers) is treated as organisation-level.
  - Phone numbers match only when every digit does, after dropping extensions. Numbers under 7 digits are ignored. An international against a national form is unclear, and neither confirms nor blocks a merge. Numbers were compared by their last 8 digits, which matched numbers in different countries.
  - A field with several values (`A; B`, or phone numbers separated by `,`, `/`, `or`, `and`) is compared value by value. Two numbers in one field were read as one long number.
  - Look-alikes that do not merge stay separate, each marked `Possible duplicate of <id>` in its `limitations` with the reason.
  - `id_keys` still works: website, phone and email in it count as organisation-level, `wikidata` is classified item by item, other keys count as site-level.
  - The hospitals example keeps Sample Town's two hospital records apart, since they share only a website and nothing shows they are one site: it delivers five records instead of four, with no bed-count conflict. The fixture no longer describes them as one hospital mapped twice.
- **Overpass partial answers (GP-01).**
  - An answer whose `remark` reports a timeout or error, or that has no `elements`, is refused before it is cached, so a retry asks the server again.
  - A partial answer cached by an earlier version is fetched again.
- **Reused pipeline stages (GP-03).**
  - MCP pipeline status and results count stages reused from a checkpoint as finished, and list their outputs.
  - The call estimate for a resume leaves them out.
  - Summaries read "N of M stage(s) done, K reused from a checkpoint".
- **Skill evaluations (GP-19).**
  - [docs/evaluations.md](docs/evaluations.md) sets how the skill's test cases are run by hand. Run agents return reports as text, and the orchestrator saves them and records who produced and saved each file. A refused action is reported, never retried another way.
  - The skill's ground rules say the same: a refusal is an answer.
- **HTTP transport (GP-05).** The server now protects every loopback address it serves against DNS rebinding: a request whose `Host` header names another address is refused (421), and so is one whose `Origin` does (403). The MCP SDK did this by itself only for `127.0.0.1`, `localhost` and `::1`, so `--host 127.0.0.2` answered any `Host`.
- **Fetching (GP-10).** An IPv6 address that carries an IPv4 one is checked as that IPv4 address: the NAT64 prefix `64:ff9b::/96` and IPv4-compatible `::/96`, as well as IPv4-mapped addresses as before. `64:ff9b::7f00:1` (127.0.0.1 through a NAT64 gateway) counted as public.
- **Pending runs (GP-06).** `run_geo_pipeline` with a spec checks its providers before it records a run. A call refused for want of `confirm`, repeated, reuses the run it recorded the first time, and so does checking the same spec again with `create_geo_pipeline`: only a pending run that never started, of exactly the same spec and parameters, is reused. Each such call used to leave another pending run behind.
- **Cancelled tool calls (GP-07).** A tool call cancelled while its work was finishing could still store a dataset, or a fetched page, from its abandoned worker thread. Tools now check for cancellation before storing anything, and keep nothing.
- **Provider error answers (GP-07).** Only the first 4,000 bytes of an error answer are read, for its excerpt. The whole body was read, with no size limit.
- **Run event log (GP-04).** A line torn when a process died mid-write (including a character cut in half) is skipped when the log is read, so the `geoproduct://runs/{run_id}` resource still shows the run's events. The next event starts on a new line, where it used to be appended to the torn one.
- **Result previews (GP-09).** `get_pipeline_results` reads only the first five records or buildings of each stage's output, where it loaded every output whole.
