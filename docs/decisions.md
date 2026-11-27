# Status and decisions

## Phase status

| Stage | Scope | Status |
|---|---|---|
| A | Research, architecture, MVP boundaries | **Done and approved 2026-11-21.** [research.md](research.md), [architecture.md](architecture.md) |
| B | Workspace, schemas, core contracts | **Done 2026-11-21.** Reviewed; you asked to start C |
| C | Pipeline engine and stages | **Done 2026-11-22.** Reviewed; you asked to start D |
| D | MCP server and providers | **Done 2026-11-22.** Reviewed; you asked to start E |
| E | Agent Skill | **Done 2026-11-22.** Reviewed; you asked to start F |
| F | End-to-end validation | **Done 2026-11-22.** Reviewed; you asked to start G |
| G | Release readiness (README, LICENSE, CI hardening) | **Done 2026-11-22.** Awaiting your review (see below). Publishing is yours: [releasing.md](releasing.md) |

### Stage B record

- **Built.** A uv workspace with one package so far, `geoproduct-core` (import name `geoproduct`, runtime dependency `pydantic` only). It contains:
  - the `geoproduct/v1` contracts: geometry and query areas, sources and evidence, records, dataset manifests, pipeline specs, run records, stage manifests and errors;
  - `GeoProductError`;
  - time-ordered ids;
  - a `Workspace` with path confinement and atomic writes;
  - the provider interface.
- **Published schemas.** Nine JSON Schemas (draft 2020-12) are generated into `schemas/v1/`. A test fails whenever they drift from the models. Regenerate them with `uv run python -m geoproduct.schemas.export schemas/v1`.
- **Verified locally on Python 3.14 (Windows).**
  - ruff format and lint: clean.
  - mypy `--strict`: clean.
  - pytest: 170 passed, 1 skipped. The skipped test checks the symbolic-link escape, and Windows here does not allow creating links. It runs on Linux and macOS CI.
  - The wheel contains only the package and `py.typed`.
- **Not verified locally.** Python 3.12 and 3.13, Linux and macOS. CI (`.github/workflows/ci.yml`) runs all nine combinations once the repository is pushed.

### Stage C record

- **Engine** (`geoproduct.engine`):
  - stages are typed functions registered with `@stage`; specs can only name registered stages;
  - `compile_plan` checks a whole spec in one pass and lists every problem: unknown stages (with suggestions), input count and kind, parameters, and the kinds of `{stage: ...}` references;
  - the runner keeps a run store (`run.json`, `spec.json`, `events.jsonl`, one folder per stage with its output, `rejects.jsonl` and `manifest.json`);
  - checkpoints are content-addressed: a stage is reused when its name, version, parameters and input hashes match and its files are intact;
  - retries apply only to retry-safe stages and retryable errors, with backoff, jitter and `retry_after_s`;
  - also: reject-share limits, cooperative cancellation and timeouts, resume with changed parameters, and a Ctrl+C run recorded as cancelled.
- **Stages (18).**
  - Collect: `collect.records`, `collect.file`, `collect.features`, `collect.buildings`.
  - Prepare: `combine.records`, `normalize.records`, `resolve.deduplicate`, `geocode.records`.
  - Match and enrich: `match.buildings`, `enrich.nearby`, `enrich.within`, `enrich.measure`.
  - Quality: `quality.confidence`, `validate.dataset`, `quality.gate`.
  - Output: `export.geojson`, `export.csv`, `report.quality`.
- **Providers.** A provider registry and the offline `fixture` provider. Sample Town is synthetic and CC0, attributed "Not a real place".
- **CLI.** `geoproduct stages | stage | plan | run | resume | status | cancel | runs`, with `--param`, `--allow` and `--json`.
- **Sample pipelines** in `examples/pipelines/`:
  - hospitals to validated GeoJSON;
  - commercial places matched to footprints;
  - transport stops checked by spatial rules;
  - a user CSV geocoded and enriched (`examples/data/sample-facilities.csv`).
- **Verified locally on Python 3.14 (Windows).**
  - ruff format and lint: clean.
  - mypy `--strict`: clean.
  - pytest: 239 passed, 1 skipped (the symbolic-link test, as in Stage B). New tests:
    - 26 engine tests: retries, `retry_after`, timeouts, cancellation (in-process and through the store), Ctrl+C, the concurrent-execution guard, checkpoint reuse and invalidation, resume with changed parameters, reject and size limits, and spec errors;
    - 25 stage tests;
    - 6 fixture-provider tests;
    - 10 end-to-end tests that run the four examples through the CLI, including a failing gate resumed with a corrected parameter.
  - `schemas/v1` regenerated: 10 schemas, with `quality-report` added.
  - The wheel contains the package and the `geoproduct` console script.
- **Not verified locally.** Python 3.12 and 3.13, Linux and macOS (CI covers them once pushed). No live provider exists yet; the first ones arrive in Stage D.

### Stage E record

- **The skill** (`skills/geoproduct-engineer/`), following the Agent Skills specification with progressive disclosure:
  - `SKILL.md`: about 170 lines and 3,900 tokens. It covers the ground rules, the three execution modes (MCP, CLI, method only), an eight-step loop, tool use and error handling, an answer template, and indexes of the workflows and references;
  - 11 workflows, one per PRD workflow, each with steps, tool calls, pitfalls and what to do without the server;
  - 6 references: tools and CLI, pipeline spec and stage catalogue, evidence and confidence, source evaluation, matching rules, and providers and licensing;
  - 2 standard-library scripts: `check_dataset.py` (GeoJSON or CSV location checks) and `score_source.py` (the server's source score and tier);
  - 3 eval cases in `evals/`, in the layout `claude plugin eval` reads.
- **Gaps found while writing the skill, fixed in the server:**
  - `create_dataset` can now combine datasets (decision 49);
  - the `validate_dataset` checks list is generated from the code (decision 50).
- **Defect found by an eval run, fixed in the core.** A footprint assigned from a *probable* building match counted as a confirmed footprint, raising a record from medium (0.705) to high (0.75) with no new evidence. `quality.confidence` now caps it at the probable weight (decision 54). A regression test covers it.
- **Tests.** `tests/skill/` has 45 tests:
  - the specification's frontmatter rules, the size of `SKILL.md`, every relative link, and no place- or project-specific names;
  - tools, prompts and parameters named in the skill exist on the server;
  - stage names exist in the registry, and the catalogue lists every stage;
  - the check list matches `CHECKS`, and the error codes exist;
  - every YAML example validates, and full specs compile into plans;
  - the scripts use only the standard library, answer `--help`, agree with the server's scores, and find the errors they claim to.
- **Verified locally on Python 3.14 (Windows).**
  - ruff format and lint: clean.
  - mypy `--strict`: clean, 61 source files. The skill's scripts and tests are now included.
  - pytest: 393 passed, 1 skipped (the symbolic-link test, as before).
- **Evaluation (iteration 1).** There were three cases, each run with and without the skill:
  - an offline CLI dataset of Sample Town's hospitals matched to buildings;
  - a library-dataset plan made without tools;
  - an audit of a CSV with seven planted location errors.

  Results:
  - **All 22 assertions passed in both configurations.** With this model the cases do not separate the skill from the baseline. The with-skill outputs followed the toolkit's conventions (evidence, source ratings, the scripts, a draft spec with a request estimate). The baselines were equally careful but organised their own way.
  - Every run hit a usage limit and was resumed, so the timing and token figures cover only part of each run and are not comparable.
  - The harness refused the hospital runs' own `report.md` writes, so their returned text was saved in their place. Each run's folder notes this.
  - Results and the review page are in `skills/geoproduct-engineer-workspace/iteration-1/` (git-ignored): `benchmark.json` and `review.html`.
- **Not verified.**
  - The official `skills-ref validate`: downloading it from GitHub was blocked here. `tests/skill/` checks the same frontmatter rules. To run it yourself: `uvx --from "git+https://github.com/agentskills/agentskills#subdirectory=skills-ref" skills-ref validate skills/geoproduct-engineer`.
  - MCP-mode orchestration by a real client: eval runs could use only the CLI and method-only modes.
  - Activation of the skill from its description in a client that discovers skills.
- **Found and left for Stage F.**
  - `match.buildings` leaves `limitations` empty for ambiguous, none and approximate matches. The skill tells the agent to write these into the report itself.
  - Merging keeps the less tidy form of a website value.
  - An empty query still adds an uncited source to the export.
  - Eval cases that discriminate: ones that tempt invention, MCP-mode orchestration, licence traps and real conflicting sources.

### Stage D record

- **Providers** (`geoproduct.providers`):
  - shared HTTP plumbing: per-provider throttle, on-disk response cache, response size and time limits, short retries, and stable error codes;
  - `nominatim` (forward and reverse geocoding) and `overpass` (features and building footprints), with OpenStreetMap conversion to records and footprints;
  - `builtin_providers()` configures the fixture and both live providers from the environment. The CLI uses it too, so `geoproduct run` can now call live services.
- **Source fetching** (`geoproduct.fetch`): public addresses only (checked when connecting, after DNS, on every redirect), `robots.txt`, a content-type allowlist, 20 MB, 30 seconds and 5 redirects. Page text is extracted so evidence excerpts can be checked.
- **MCP server** (`geoproduct-mcp`):
  - 20 tools, each with an output schema, annotations and structured `GeoError` results;
  - 3 resources and 3 resource templates (guide, stages, providers, schemas, runs, datasets);
  - 2 prompts;
  - stdio, and Streamable HTTP on loopback only;
  - progress, cancellation, background pipeline runs, and confirmation above 25 external requests.
- **Configuration.** [configuration.md](configuration.md) covers every variable and option, MCP client configuration examples, what the server can reach, and the workspace layout. `.env.example` lists the variables.
- **Verified locally on Python 3.14 (Windows).**
  - ruff format and lint: clean.
  - mypy `--strict`: clean (58 source files).
  - pytest: 346 passed, 1 skipped (the symbolic-link test, as before). New in Stage D:
    - 19 HTTP plumbing tests: throttle, cache, retries, error mapping, settings;
    - 27 OpenStreetMap provider tests: query building, conversion, both providers against a mock transport, the engine running them, the bulk refusal, endpoint validation;
    - 28 fetching tests: address checks, robots.txt, redirects, content types and limits, excerpt matching;
    - 12 tool tests, each tool called directly, plus a full collect → match → validate → report → export flow;
    - 11 pipeline tool tests: plans, background runs, confirmation, resume with corrected parameters, cancellation;
    - 10 server tests through the SDK's in-process client: tool list and schemas, structured results and errors, argument validation, resources, prompts, cancellation, loopback-only HTTP.
  - The installed `geoproduct-mcp` entry point was driven over stdio and over HTTP by the SDK client: 20 tools listed, a fixture search succeeded, an unconfigured live provider returned `auth_missing`.
  - Both wheels build; the MCP wheel contains `guide.md` and the console script.
- **Not verified.**
  - No request was sent to the public Nominatim or Overpass services; providers were tested against service-shaped mock responses. The opt-in `live` tests come in Stage F.
  - Not yet tried in a desktop MCP client (Claude Desktop, Cursor and others); only the official SDK client.
  - Python 3.12 and 3.13, Linux and macOS: CI covers them once pushed.
- **Known limits.** PDF text is not extracted, so excerpts from PDFs stay `not_checked`. Exports are GeoJSON and CSV only. Nothing loads provider plug-ins yet. Overture is not built.

### Stage F record

- **End-to-end tests** (`tests/e2e/`, 10 tests, offline):
  - *Research to export* (6 tests), through the MCP client against mock web and geocoding services:
    - register and fetch two sources (a CSV registry and a web page), turn the registry into a dataset and add a researched record with its evidence;
    - search features, combine, normalise, deduplicate, geocode, match footprints, score, validate, report, and export GeoJSON and CSV;
    - reload the GeoJSON export.
  - The export is checked against:
    - the ground truth;
    - the published record and manifest schemas;
    - every citation resolving and every listed source being used;
    - the manifest checksums;
    - the skill's `check_dataset.py`;
    - the CSV layout;
    - a lossless round trip.
  - *Failure and recovery* (4 tests):
    - a run killed mid-stage is resumed by a new process, which reruns only the unfinished stage, after a 429 with `Retry-After` was retried;
    - a missing user agent stops the run before any request;
    - an invalid spec lists every problem and runs nothing;
    - a provider outage fails the run as retryable, and resuming finishes it without repeating completed requests.
- **Live tests** (`tests/live/`, 4 tests, opt-in): geocoding, reverse geocoding, features and buildings against the public Nominatim and Overpass. They skip by default. Run on 2026-11-22 at your request:
  - **Nominatim: both passed.** A named place was found within 1 km and cited under ODbL, a repeated query came from the cache, and a point reverse-geocoded to a GB address.
  - **Overpass: not reachable from this network.** `overpass-api.de` (ports 80 and 443), `overpass.kumi.systems` and `overpass.private.coffee` all time out, while Nominatim, openstreetmap.org and other sites answer. The block is upstream of this machine, so the two Overpass tests still need a run from another network.
  - The run exposed decision 61: an unreachable host waited out the whole query timeout, then advised narrowing the query.
- **Installation checked** in a separate copy of the repository, with the network blocked:
  - `uv sync`, every documented example command, and the documented resume all work.
  - The MCP server, started exactly as [configuration.md](configuration.md) says from an unrelated folder, was driven over stdio. It listed 21 tools, 2 prompts and 3 resources, ran a fixture search, ran a pipeline and polled it to success, and refused a path outside the allowed folders.
  - Both wheels contain what they need (`guide.md`, `py.typed`) and run from site-packages.
  - The skill, copied as documented, keeps its name and links and its scripts run.
  - The skill's installation was not documented (PRD Phase 8, step 3). [configuration.md](configuration.md) now covers it.
- **Found and fixed:**
  - a Windows file-sharing race when a run's files are read while being replaced (decision 59);
  - an error outside any stage left the run `running` (decision 60);
  - exports listed sources nothing cited (decision 57), including Stage E's empty-query case;
  - building matches other than exact gave no reason in `limitations` (decision 56), a Stage E finding;
  - merged duplicates kept the less complete website form, a Stage E finding: the form with a scheme is now kept;
  - spec errors from the spec's own checks came out as one joined line, `(top): Value error, a; b`: there is now one line per problem;
  - geocoding a researched dataset needed a hand-written pipeline (decision 55);
  - an unreachable provider took the full query timeout per attempt (120 s for Overpass) and was reported as a slow answer (decision 61);
  - `check_dataset.py` skipped the duplicate-id check without saying so when the id column was named like `facility_id`. It now recognises `*_id` columns and lists what it could not check under `not_checked`.
- **Verified locally on Python 3.14 (Windows).**
  - ruff format and lint: clean.
  - mypy `--strict`: clean, 64 source files.
  - pytest: 415 passed, 2 skipped (the symbolic-link test and the opt-in live module). There are 22 new tests: 10 end-to-end, 4 stage, 2 tool, 2 HTTP, 1 engine, 1 workspace race and 2 skill script tests.
- **Not verified.**
  - The Overpass live tests: no Overpass instance is reachable from this network.
  - A desktop MCP client, and Claude Code discovering the skill: both would change your own client configuration.
  - A fresh install into an empty environment: with the network blocked, dependencies cannot be downloaded.
  - Further skill evals: not rerun, to save usage. The end-to-end tests cover the workflows deterministically. Stage E's list of discriminating cases is still open.
  - Python 3.12 and 3.13, Linux and macOS: CI covers them once pushed.
- **Known limits.** The guard against running a run twice works within one process. Two processes resuming the same run at the same moment are not prevented.

### Stage G record

- **Licence.**
  - Apache-2.0, with Ppp2048 as the copyright holder in `NOTICE`, as you chose (decision 9).
  - `LICENSE` is the text from apache.org. Its terms were checked word for word against the copies that two Apache-2.0 dependencies ship.
  - The licence is declared with the PEP 639 `license` and `license-files` fields in both packages, and with `license` in the skill's frontmatter.
  - All 30 runtime dependencies are under permissive licences: MIT, BSD-3-Clause, Apache-2.0 and PSF, plus 0BSD, Zlib and CC0 parts in numpy.
- **Documentation.**
  - A root README with every section Phase 10 asks for, and its quick start verified as written.
  - A README for each package, for its PyPI page.
  - `CONTRIBUTING.md`, `SECURITY.md` and `CHANGELOG.md`.
  - [releasing.md](releasing.md), with the versioning policy and the release checklist.
  - [configuration.md](configuration.md) and [architecture.md](architecture.md) updated. The architecture diagram had listed Overture as if it existed; it is now marked planned.
- **CI** (`.github/workflows/ci.yml`):
  - every action is pinned to a commit SHA (decision 63);
  - jobs have timeouts, uv is fixed at 0.11.23, and the workflow can also be started by hand;
  - a new `package` job builds both packages, checks them with `.github/scripts/check_dist.py`, installs the wheels in a fresh environment and runs the example pipeline from outside the source tree (decision 64).
  - Dependabot proposes monthly updates of the actions and the locked dependencies. Bug and feature issue forms route security reports to private reporting.
- **Release checks** (`tests/release/`, 8 tests):
  - versions agree across both packages, the server's pin and the skill;
  - the changelog covers the version;
  - the licence copies are identical;
  - every relative link in the documentation resolves;
  - the README's pipeline example compiles;
  - no private project or place name appears anywhere (decision 65);
  - workflow actions are pinned.
- **Found and fixed:**
  - `astral-sh/setup-uv` publishes no `v10` tag, so the existing CI would have failed on its first run. The workflow is now pinned to the v10.4.0 commit.
  - The skill's name guard spelled out the private project's and the cities' names in plain text, which would have published them. The guard is replaced by a whole-repository check on SHA-256 digests. A scratch check confirmed it still catches every form of each name, and that it no longer flags "architecture".
- **Verified locally on Python 3.14 (Windows).**
  - ruff format and lint: clean.
  - mypy `--strict`: clean, 66 source files.
  - pytest: 422 passed, 2 skipped (the symbolic-link test and the opt-in live module).
  - A copy holding only the files that would be committed (no `prd.txt`, `.env`, caches or environments): the README quick start succeeded exactly as written, and the full suite passed there.
  - Both wheels and sdists built and passed `check_dist.py`. The wheels, installed alone in a fresh environment, started the MCP server and ran the example pipeline.
  - The names `geoproduct-core`, `geoproduct-mcp` and `geoproduct` were unclaimed on PyPI on 2026-11-22.
- **Not verified.**
  - CI had not run before the push of the documentation and CI. Its results are on the repository's Actions tab.
  - The issue forms and Dependabot configuration take effect only on GitHub.
  - The Overpass live tests still need a network that reaches Overpass.
- **Left to you** (see [releasing.md](releasing.md)):
  - enable private vulnerability reporting on GitHub;
  - choose how to publish to PyPI;
  - publish when you are ready.

  Pushed in two commits, at your request: the code, skill and tests first (`e89c2dd`, 2026-11-22), then the documentation, CI and release checks after your review. Nothing was tagged or published.

## Decisions

Status values: **Proposed** needs your approval; **Accepted** is decided; **Open** is not decided yet.

| # | Decision | Why | Status |
|---|---|---|---|
| 1 | Python ≥ 3.12 for everything | Best geospatial libraries; the official MCP SDK 2.3 supports it; pyproj 3.8 needs 3.12 | Accepted |
| 2 | uv workspace with two distributions (`geoproduct-core`, `geoproduct-mcp`), plus the skill as a folder | Independently installable parts without the overhead of five packages | Accepted |
| 3 | Our own lightweight pipeline engine with declarative specs and registered stages | The PRD forbids running model-written code; Prefect, Dagster and Hamilton add servers or code-defined graphs | Accepted |
| 4 | Long pipelines run as background jobs addressed by `run_id`; adopt the MCP Tasks extension later | The 2026-07-28 core is stateless, and the Python SDK doesn't support Tasks yet | Accepted |
| 5 | Data moves through handles (`dataset_id`, `run_id`), not through the model's context | Keeps token use and errors down; matches the specification's handle pattern | Accepted |
| 6 | Explainable entity resolution with RapidFuzz and spatial blocking; Splink is a later optional back end | Most datasets are small; explanations matter more than scale | Accepted |
| 7 | Free default providers: Nominatim, Overpass, files and fixtures, with Overture as an extra. Commercial providers are optional adapters later. | No keys or costs by default; strict usage-policy handling | Accepted |
| 8 | Evidence excerpts are checked against fetched content | A deterministic guard against invented quotes and URLs | Accepted |
| 9 | Licence for our code: Apache-2.0, copyright Ppp2048 (in `NOTICE`) | Permissive, with a patent grant; all 30 runtime dependencies are MIT, BSD, Apache-2.0 or PSF | Accepted 2026-11-22 (your choice) |
| 10 | The private PRD stays out of git (`.git/info/exclude`) | It names earlier private projects | Accepted |
| 11 | `httpx2` is the HTTP client, added in Stage D | The MCP SDK already depends on it, so the server ships one HTTP stack, not two | Accepted |
| 12 | Providers are synchronous; the engine calls them from worker threads | Simpler provider code for contributors; throttling and retries live in one place | Accepted |
| 13 | Contracts reject unknown fields | A typo in agent-written input fails loudly instead of being silently dropped | Accepted |
| 14 | Geometry models check shape only; semantic problems (unclosed rings, self-intersections) are validation findings | Broken data can still be loaded, inspected and repaired, and is never silently "fixed" | Accepted |
| 15 | A source's score and tier are always derived from its seven ratings; a licensing rating of 0 makes it unusable | Values typed into input cannot inflate a source; licence problems cannot be outweighed | Accepted |
| 16 | Pipeline references may only point to earlier stages, and a spec has no field that can carry code | Cycles are impossible by construction; specs stay data | Accepted |
| 17 | The engine writes only inside the workspace (`GEOPRODUCT_WORKSPACE`); user files are read from the workspace or `GEOPRODUCT_ALLOWED_DIRS` only; writes are atomic | Path confinement for tool arguments; no half-written files after a crash | Accepted |
| 18 | JSON Schema `$id`s point at `raw.githubusercontent.com/Ppp2048/geoproduct-engineer-skill/main/schemas/v1/` | Resolvable as soon as the repository is public, without a docs site | Accepted for now; revisit in Stage G (a stable docs URL would outlive a branch rename) |
| 19 | CI uses major-version action tags, read-only token permissions and no persisted checkout credentials | Simple and safe for now | Accepted for now; Stage G considers pinning actions to commit SHAs with Dependabot updates |
| 20 | Core dependencies added in Stage C: `shapely` (geometry, spatial index), `rapidfuzz` (name similarity), `pyyaml` (safe loader only), `numpy` (used directly, and already required by shapely). `pyproj` is deferred: metric calculations use a local equirectangular projection centred on each feature. | Each one has a stage that needs it. Within a few kilometres of a feature, the local projection is accurate to well under 1%, without a PROJ install. | Accepted; add `pyproj` when a stage must output a projected CRS |
| 21 | Provider-neutral `collect.features` and `collect.buildings` take a `provider` parameter, instead of `collect.osm_features` and `collect.building_footprints` | One stage per capability; new providers plug in without new stages | Accepted |
| 22 | `quality.confidence` replaces `quality.score`. The per-record score combines four parts: evidence (0.35, counting only sources that describe the record, not a geocoder that merely placed it), source quality (0.2), location (0.3), and consistency (0.15). Levels: high ≥ 0.75, medium ≥ 0.5, low ≥ 0.25. | A transparent formula with reasons on every record. The dataset score comes from `validate.dataset`. | Accepted; recalibrate the weights after the Stage F evaluation |
| 23 | Source and evidence ids are derived from their content | Re-running gives identical outputs, so checkpoint fingerprints stay stable | Accepted |
| 24 | When merged records disagree, every value is kept in `conflicts`, the field is cleared, and the record becomes `conflicting` | Never pick a winner silently | Accepted |
| 25 | Cancellation and stage timeouts are cooperative: stages call `ctx.check()` between items. Another process cancels by writing a marker file. Ctrl+C records the run as cancelled. | Portable and safe; no thread killing. A stage blocked inside one provider call is bounded by that provider's request timeout (Stage D), not by `timeout_s`. | Accepted |
| 26 | Run locking is in-process only: one process cannot execute the same run twice | The MCP server is the one long-lived executor; a cross-process file lock adds platform-specific code | Accepted for now; revisit if two executors become possible |
| 27 | The fixture provider ("Sample Town", synthetic, CC0) was built in Stage C instead of D | The engine, the stages and the examples need provider data that works offline | Accepted |
| 28 | Exports are written into the run folder. A copy elsewhere is allowed only inside the workspace or an allowed folder. Replacing a different existing file needs `overwrite: true` (otherwise `confirmation_required`). Export stages are never retried automatically. | No silent data loss outside the run | Accepted |
| 29 | CSV export escapes text that a spreadsheet would run as a formula: text starting with `=`, `+`, `-`, `@`, a tab or a carriage return gets a leading apostrophe. A `+` or `-` followed only by digits and number punctuation is left unchanged. | Blocks formula injection without corrupting phone numbers or negative values | Accepted |
| 30 | `combine.records` appends records from other stages | A feature query matches one tag set, so pipelines with several categories need to combine queries | Accepted |
| 31 | Intermediate stage outputs are stored as GeoJSON plus `provenance.json` | Any GIS tool can open an intermediate result | Accepted |
| 32 | The `geoproduct` CLI uses `argparse`. Exit codes: 0 succeeded, 1 failed or error, 2 usage, 3 cancelled, 130 interrupted. | No new dependency; scripts can branch on the result | Accepted |
| 33 | Core adds `httpx2` and `httpcore2`. `httpcore2` supplies the network backend that checks each address before connecting (decision 45). | The MCP SDK already ships both, so the server still has one HTTP stack | Accepted |
| 34 | Nominatim candidates carry no score. When a record gets two or more candidates, it stays unresolved instead of taking the first. | Nominatim's `importance` measures prominence, not how well a result matches the query | Accepted |
| 35 | At most 100 geocodes per run on the public Nominatim instance (our own cap). A self-hosted instance has no cap and a configurable rate. | The OSMF policy forbids bulk geocoding but names no number; 100 at 1 per second is under two minutes | Accepted; revisit if the policy names a limit |
| 36 | An Overpass response whose `remark` reports an error or timeout is refused (`timeout` or `provider_error`), not used | Overpass returns HTTP 200 with partial data in that case, which would silently drop features | Accepted |
| 37 | Building footprints from OpenStreetMap use the provider name `osm` in their references, whichever service delivered them | A footprint keeps the same id from Overpass, a self-hosted mirror or a future extract | Accepted |
| 38 | 20 MCP tools: the 19 planned, minus `resume_geo_pipeline` (decision 39), plus `score_confidence` | Confidence is a step the agent needs between matching and validation; the architecture's count of 19 had already merged resume | Accepted |
| 39 | Pipelines are addressed by `run_id` only. `create_geo_pipeline` records a pending run with its plan; `run_geo_pipeline` starts a pending run, resumes a failed or cancelled one, or creates and starts a new one. There is no `pipeline_id`. | One handle per pipeline and one tool to run it, whatever state it is in | Accepted; refined by 74 |
| 40 | Endpoint URLs from the environment must be `http` or `https` with a host and no user name, password, query or fragment. The public instances are recognised by host. | Endpoints appear in `geoproduct://providers` and in run manifests; a trailing slash must not lift public limits | Accepted |
| 41 | The throttle is per process. Cached responses are kept 7 days by default (`GEOPRODUCT_CACHE_TTL_HOURS`, 0 turns it off). | A cross-process throttle needs a lock file or a service; one server at a time is the expected setup | Accepted; revisit with decision 26 |
| 42 | Streamable HTTP is served on loopback addresses only; any other `--host` is refused | The server has no authentication. Remote use needs the MCP authorization flow, per-user workspaces and quotas, and has no use case yet. | Accepted; remote serving deferred; refined by 75 |
| 43 | Confirmation: a run estimated above 25 external requests, or with any unbounded stage, needs `confirm: true`. One request per `collect.features` or `collect.buildings` stage; the provider's largest batch for `geocode.records`; anything else is unbounded. | Puts a person in the loop before a large job against a shared public service | Accepted; the threshold may change after Stage F |
| 44 | The public OpenStreetMap services need `GEOPRODUCT_USER_AGENT`. Without it, they are listed as not configured and fail with `auth_missing` before any request. `fetch_source_data` falls back to a default User-Agent naming this project. | Both services' policies require an identifying client; there is no safe default contact for the user's own application | Accepted |
| 45 | `fetch_source_data` checks every address when connecting (after DNS, on each redirect) through a custom `httpcore2` network backend, ignores proxy variables, and honours `robots.txt`. The backend is installed through `httpx2`'s private `_pool` attribute. | Checking at connection time also blocks DNS rebinding. The private attribute is the one fragile point: `httpx2` is pinned below 3, and a test fails if the hook stops working. | Accepted; replace if `httpx2` gains a public hook; refined by 76 |
| 46 | Data stays in the workspace behind handles: `ds_…` ids for tool outputs, `<run_id>/<stage_id>` for pipeline outputs read in place. Results carry at most five preview items. | Keeps large data out of the model's context (decision 5) without copying pipeline outputs | Accepted |
| 47 | Background runs use daemon threads in the server process. If the server stops, the run still says `running` and the status tool reports `active: false`; `run_geo_pipeline` resumes it. Cancelling a tool call sets the work's cancel flag. | No job service to install; checkpoints make an interrupted run cheap to resume | Accepted; refined by 77 |
| 48 | `geoproduct-mcp` requires the same version of `geoproduct-core` | The two are developed and released together, and the server uses core internals | Accepted |
| 49 | `create_dataset` also takes `dataset_ids`: two to twenty records datasets, combined with `combine.records` (the first is the base, the others are added). Sources and evidence are kept. | Combining a researched list with map features is the core research step, and before this it was possible only in a pipeline | Accepted |
| 50 | The `validate_dataset` description lists its checks from `CHECKS` in the code | The hand-written list had drifted; a test now guards it | Accepted |
| 51 | The skill works in three modes: MCP tools, the `geoproduct` CLI, or method only. It never requires the server; without tools it gives the method and names the steps it could not run. | PRD Phase 7: still useful without MCP, and not tied to one LLM provider or client | Accepted |
| 52 | `tests/skill/` checks the skill against the code: tools, prompts and their parameters, stages, checks, error codes, source scores and the YAML examples. Mypy also covers the skill's scripts. | Prose drifts silently; this turns drift into a failing test | Accepted |
| 53 | Skill test cases live in `skills/geoproduct-engineer/evals/`, in the layout `claude plugin eval` reads. Run outputs go in `skills/*-workspace/`, which git ignores. | Keeps the tests with the skill and runnable by the CLI; run outputs are local and large | Accepted |
| 54 | `quality.confidence` gives a footprint assigned from a probable building match the probable weight (0.85), not the footprint weight (1.0) | The footprint comes from the same unconfirmed match, so it adds no certainty; found by a Stage E eval run | Accepted |
| 55 | The `geocode_records` tool geocodes a records dataset in one call. With an online geocoder, more than 25 lookups need `confirm=true`, as pipelines do. | Geocoding researched records otherwise needed a hand-written pipeline; found by the Stage F research workflow | Accepted |
| 56 | `match.buildings` adds one `Building match: …` line to `limitations` for every match that is not exact, saying why | Reports and exports then explain each missing or uncertain building without the agent writing it; a Stage E finding | Accepted |
| 57 | A dataset lists a source only when something in it relies on that source. An empty provider answer adds none, and footprints are credited only when a record names one of their buildings. The query itself stays recorded under `providers`. | An export must not cite sources it does not use; found by the end-to-end source checks | Accepted |
| 58 | End-to-end tests (`tests/e2e/`) run offline against mock services and run by default. Live tests (`tests/live/`) run only with `GEOPRODUCT_LIVE_TESTS=1` and `GEOPRODUCT_USER_AGENT`. | PRD Phase 9: deterministic by default, live tests optional and separate | Accepted |
| 59 | On Windows, workspace reads and atomic replacements retry for up to 2 seconds when another thread has the file open | Polling a running run's `run.json` failed with access errors; Windows will not replace a file that is open | Accepted |
| 60 | The runner records an error outside any stage as a failed run with a structured error, and the run can be resumed | The exception escaped and left the run `running` | Accepted |
| 61 | Connecting to a provider has its own 10-second limit, separate from the time an answer may take. A host that never accepts the connection is `provider_unavailable` ("could not be reached"), not `timeout`. | Found by the live tests: a blocked Overpass host waited out the 120-second query timeout on each attempt, then advised narrowing the query | Accepted |
| 62 | Both packages and the skill share one version, and the server pins the exact core. The contract version (`geoproduct/v1`) changes separately: within v1, changes are additive only. | One tested combination per release; files written by older releases keep loading ([releasing.md](releasing.md)) | Accepted |
| 63 | CI actions are pinned to full commit SHAs, with the tag in a comment, and Dependabot proposes updates | A tag can be moved and a commit cannot; pinning also exposed a tag that did not exist | Accepted |
| 64 | CI builds and checks the packages and runs the installed wheels, but nothing publishes. Releasing is a manual checklist. | PRD: never publish automatically. The checklist keeps every release a deliberate step. | Accepted |
| 65 | A repository-wide test refuses the private project's and specific places' names. It holds them as SHA-256 digests and matches whole words. | PRD: no project- or city-specific material. The former regex published the names it guarded against and also matched inside "architecture". | Accepted |
| 66 | Each package carries its own copy of `LICENSE` and `NOTICE`, and a test keeps them identical to the root | PEP 639 accepts only licence files inside the package's own folder | Accepted |
| 67 | The README's graphics are hand-written SVGs in `docs/assets/`: system fonts, SMIL animation only, no scripts or external files. The terminal replays a real run of the hospitals example. A test checks the counts the README states and that it lists every tool, prompt and stage. | GitHub shows README SVGs as images, which load no fonts or scripts. The README's numbers and badges must not drift from the code. | Accepted |
| 68 | Deduplication merges a pair only when a shared identifier or an agreeing address corroborates it, the two are near, and no identifier or address differs. It merges groups in which every pair matches. Unconfirmed look-alikes stay separate, marked `Possible duplicate of <id>` in `limitations`. | Audit GP-02: a similar name nearby merged a chain's branches, and union-find chained records through a third. A wrong merge loses a record; a kept pair can still be reviewed. The mark uses `limitations` because a new field would change the v1 contract. | Accepted; identifiers refined by 70 and 71 |
| 69 | The HTTP client takes a `check` that refuses an answer before it is returned or cached; Overpass uses it for partial answers | Audit GP-01: a timed-out query was cached as a result and replayed on every retry for the cache's lifetime | Accepted |
| 70 | Phone numbers match only when every digit does, after dropping extensions; numbers under 7 digits are ignored; an international against a national form, or a national number with and without its area code, is unclear and neither confirms nor contradicts. No numbering-plan library is added. | Audit GP-17: comparing the last 8 digits matched numbers in different countries, and an extension made one number look like another. Without the numbering plan the forms cannot be settled, so the comparison says so instead of guessing. | Accepted |
| 71 | Identifiers are site-level (`site_keys`, default `wikidata`) or organisation-level (`organisation_keys`, default website, phone, email). Only site-level evidence, or an agreeing street address with a house number, can merge records; organisation-level evidence links them by `organisation_id`. A site-level value on records farther apart than `max_distance_m` counts as organisation-level. `id_keys` stays as an alias. | Audit GP-16: a brand website shared by a chain's branches merged branches 80 m apart. The fixture hospital's two records share only a website, so they now stay apart as possible duplicates; the fixture is unchanged. The parameters are additive within v1 (decision 62). | Accepted; refined by 73 |
| 72 | Skill evaluations run by hand follow [evaluations.md](evaluations.md): run agents return reports as text, the orchestrator saves them with `provenance.json`, and a refused action is reported, never worked around. The skill's ground rules carry the same rule. | Audit GP-19: a run agent that was refused a report write wrote the file through a shell command instead. | Accepted |
| 73 | Identifier levels are decided per value. `wikidata` items are site-level only when listed in `wikidata_sites`, organisation-level when listed in `wikidata_organisations` or used by a record as its `brand:wikidata` or `operator:wikidata`, and of unknown meaning otherwise; `wikidata` and the brand keys cannot be site keys, and `site_keys` defaults to none. A site-level or unknown value carried by records shown to be distinct sites (beyond `max_distance_m`, at different addresses, or with different site identifiers) is organisation-level. Only site-level differences (site keys, site-level or unknown Wikidata items, street addresses) block a merge; organisation-level differences are kept as second values or conflicts. Multi-valued fields are split (`;`, and `,` `/` `or` `and` for phones when every part is a whole number) and compared value by value. No lookup service or numbering-plan library is added. | Audit GP-16 follow-up: the `wikidata` tag names the establishment on some features and the brand on others, so a shared item merged a chain's branches 60 m apart; a central phone differing from a direct line kept one register-confirmed site apart; `A; B` was read as one number. Classifying an item needs Wikidata's P31 and P625, which the agent looks up and passes in; offline, an unknown item links but never merges. | Accepted |
| 74 | `run_geo_pipeline` with a spec checks its providers before it records a run. It and `create_geo_pipeline` reuse the newest run of exactly the same spec (same `spec_sha256`, parameters included) that is pending, never started and not executing, looking at the 20 newest runs, and record a new one only when there is none. | Audit GP-06: each call refused for want of `confirm`, or for a provider that is not configured, left another pending run. A retry finds the run its earlier attempt recorded among the newest; two such runs would be identical anyway. | Accepted |
| 75 | The HTTP transport always gets explicit DNS-rebinding settings for the loopback address it serves: `Host` and `Origin` must name that address, on any port (bare names as well on port 80). `127.0.0.1`, `localhost` and `::1` accept one another, as in the SDK's own defaults. | Audit GP-05: the SDK turns the protection on by itself only for those three hosts, so `--host 127.0.0.2` answered a request for any host name. Refines decision 42. | Accepted |
| 76 | An IPv6 address carrying an IPv4 address is judged by the IPv4 address when fetching: IPv4-mapped, the NAT64 prefix `64:ff9b::/96`, and IPv4-compatible `::/96`. Other translation prefixes (the local-use `64:ff9b:1::/48`, 6to4, Teredo) are already not global, so they stay refused. | Audit GP-10: `ipaddress` calls `64:ff9b::7f00:1` global, and through a NAT64 gateway it reaches 127.0.0.1. Public NAT64 addresses still work on IPv6-only networks. Refines decision 45. | Accepted |
| 77 | A cancelled MCP tool call stores nothing: tools check for cancellation just before they commit a dataset or a fetched page. An error answer from a provider is read only as far as its excerpt needs (4,000 bytes). | Audit GP-07: the worker thread of a cancelled call is abandoned, not stopped, and could commit its result afterwards; a 4xx body was read whole. Stopping the thread itself is not possible from Python, so it may still run until its provider request returns. | Accepted |
| 78 | Reading `events.jsonl` skips a line that is not a JSON object, and writing an event first ends a line that a crash left torn. Events are written as UTF-8 with `\n` line ends on every platform. | Audit GP-04: one torn line made every read of the log fail, and the next event was appended to it. The log is a diagnostic aid; the run's state is in `run.json`. | Accepted |
| 79 | `get_pipeline_results` previews records and buildings by reading each stage's GeoJSON as a stream, up to the fifth feature. This relies on the compact layout the engine writes (`{"type":"FeatureCollection","features":[`); a file laid out otherwise is loaded whole. Reports and file lists are small and are always loaded whole. | Audit GP-09: every stage's full output was loaded, up to 100,000 records each, to show five items. No streaming-JSON dependency is added. | Accepted |
