# Research: GeoProduct Engineer

Stage A, 2026-11-21.

How each fact was checked:
- **[V]** read on the official page or on PyPI that day;
- **[S]** secondary source, so re-check it before relying on it.

## 1. Summary

- **Nothing on the market does the whole job.** There are 118 geospatial MCP servers in the most complete tracked list [S]. They are capability wrappers: geocoding, routing, GIS operations, catalogues and imagery. None combines all of:
  - collected records that carry their evidence;
  - entity resolution;
  - matching records to building footprints;
  - quality reports;
  - resumable pipelines.
- **Build three parts:** a Python core library, a thin MCP server and an Agent Skill. Reuse mature libraries: Shapely, pyproj, RapidFuzz, httpx and the official MCP SDK. Don't rebuild generic GIS operations, routing or imagery access; other servers already cover them, and agents can use them alongside ours.
- **The differentiation is the workflow, not any one tool:** turning researched evidence into a verified, reproducible geographic dataset.

## 2. Competitor landscape

| Project | What it does | Strengths | What it lacks for our purpose | Licence, status | Our stance |
|---|---|---|---|---|---|
| **gis-mcp** (mahdin75) | Python MCP server: about 92 functions over Shapely, PyProj, GeoPandas, Rasterio and PySAL; stdio or HTTP | Breadth of GIS operations | Stateless single calls; no provenance, entity resolution, footprint matching or pipelines | MIT; 0.15.0 beta, active [S] | Complement. Don't duplicate generic geometry tools. |
| **openstreetmap-mcp-server** (cyanheads) | TypeScript; 6 tools for Nominatim search, reverse and lookup, plus Overpass nearby, bbox and raw queries | Model usage-policy handling: requests 1.05 s apart, identifying User-Agent, caching, Overpass concurrency of 1, failover | Raw data only; no records, matching or validation | Apache-2.0; 0.7.2, active [S] | Follow its rate-limit pattern. Interoperable. |
| **Mapbox MCP** (official), **ArcGIS Location Services MCP** (beta), **Google Maps Grounding Lite**, Geoapify, OpenCage, TomTom | Commercial geocoding, routing, places, static maps | Coverage and quality | API keys and costs. Google's terms restrict storing content. No dataset building. | Proprietary [S] | Possible optional provider adapters later; never required |
| **Overture Maps MCP**, **PlaceRoot** (community) | DuckDB queries over Overture GeoParquet: buildings and places with GERS IDs | Open global buildings and places | No matching or entity resolution | Not stated [S] | Use the official `overturemaps` client directly |
| **MapSmith** | Geoprocessing in which every output has a provenance manifest | Closest to us on provenance | Provenance covers derived outputs, not researched records | Not stated [S] | Overlapping idea; nothing to reuse |
| **qgis-mcp, gdal-mcp, postgis-mcp, Wherobots** | Desktop GIS, GDAL, database and Sedona access | Mature back ends | Not about dataset building | Mixed [S] | Out of scope; interoperable |
| **muend/geoai-skills** | 18 vendor-neutral methodology skills: preconditions, checks, the evidence needed for a geospatial claim | Close in spirit: evidence, and knowing when data can't support a conclusion | Aimed at analysis (Earth Engine, change detection); no MCP or pipelines | Licence not checked; low activity [S] | Related work; the two can be used together |
| **GIS Copilot, LLM-Geo, GISclaw** (research agents) | The LLM writes and runs GIS code in a sandbox. GISclaw reports up to 96% task success. | Flexible analysis | They execute code the model generates, which the PRD forbids; their target is analysis answers, not verified datasets | Open source [S] | Different design. We run only registered, typed stages. |

**Libraries considered:**

| Library | Version and licence | Decision |
|---|---|---|
| **Splink** | 5.0.0, MIT [V] | Probabilistic record linkage on DuckDB, Spark, Postgres or SQLite. Too heavy for the MVP, and its scores are harder to explain. It's an optional back end for large datasets later. |
| **RapidFuzz** | 3.14.6, MIT [V] | Use it for name similarity, inside our own explainable matching rules. |
| **Prefect, Dagster** | — | Need a server or an asset platform, and run arbitrary decorated Python. Rejected. |
| **Hamilton** | — | Lightweight, but its DAGs are defined in code. Rejected. |

Our engine needs declarative specs, registered stages, per-stage checkpoints and provenance. That is small enough to own, and an orchestrator would bring code-defined graphs we'd then have to forbid.

**Current versions [V, PyPI, 2026-11-21]:**

| Package | Version | Python | Licence |
|---|---|---|---|
| mcp | 2.3.0 | ≥ 3.10 | MIT |
| shapely | 2.2.0 | ≥ 3.11 | BSD-3 |
| pyproj | 3.8.0 | **≥ 3.12** | MIT |
| geopandas | 1.2.0 | ≥ 3.11 | BSD-3 |
| pyogrio | 0.13.0 | ≥ 3.10 | MIT |
| pydantic | 2.14.4 | ≥ 3.10 | MIT |
| httpx | 0.28.1 | ≥ 3.8 | BSD-3 |
| rapidfuzz | 3.14.6 | ≥ 3.11 | MIT |
| splink | 5.0.0 | ≥ 3.10 | MIT |
| duckdb | 2.0.1 | ≥ 3.11 | MIT |
| overturemaps | 2.1.1 | ≥ 3.11 | MIT |
| osmnx | 2.2.0 | ≥ 3.12 | MIT |

pyproj sets the floor, so the project needs **Python ≥ 3.12**.

## 3. Platform facts that shape the design

**MCP specification 2026-07-28 [V, changelog]**
- **Stateless core.** `initialize` and sessions are gone. State across calls goes through **handles the server issues and the client passes back as tool arguments**.
- **New methods.** Servers must implement `server/discover`. Every result carries a `resultType`.
- **Schemas.** `inputSchema` and `outputSchema` accept any JSON Schema 2020-12 keywords.
- **Tasks** moved into the `io.modelcontextprotocol/tasks` extension, which works by polling `tasks/get`.
- **Server-initiated requests** for elicitation and sampling are replaced by multi-round-trip results.
- **Deprecated:** Roots, Sampling and Logging.

**Python SDK v2 [V]**
- **Server class.** FastMCP is now `MCPServer`.
- **Structured output.** A tool's typed return value becomes its `outputSchema`, and the returned structured content is validated against it.
- **Progress** is reported with `ctx.report_progress`.
- **Transports:** stdio and Streamable HTTP.
- **Licence:** MIT.

**Two SDK gaps [S]**
- The Tasks extension is not implemented in the Python SDK yet.
- The tool `Context` has no public cancellation event (issue #3389).

**Consequence:** a pipeline runs as a background job inside the server, addressed by a `run_id` handle: start it, poll its status, then fetch the results. Its state lives on disk, so a stateless HTTP instance can still answer. We can adopt the Tasks extension once the SDK supports it.

**Python or TypeScript?** The TypeScript SDK is mature too, but Python's geometry, projection and entity-resolution libraries are far ahead of TypeScript's, and `uvx` gives a one-command install. **Python.**

**Agent Skills specification [V, agentskills.io]**
- **Frontmatter:**
  - `name` (required): 1–64 characters, lowercase letters, digits and hyphens, and it **must match its folder name**;
  - `description` (required): up to 1,024 characters;
  - optional `license` and `metadata`;
  - optional `compatibility`, up to 500 characters;
  - optional `allowed-tools`, which is experimental.
- **Size and layout:** SKILL.md stays under 500 lines (about 5,000 tokens), and referenced files sit one level below it.
- **Validation:** `skills-ref validate`.
- **Installing:** copy the folder into an agent's skills folder, or run `npx skills add <owner>/<repo>` [S].

## 4. Data providers and licensing

| Provider | Used for | Key | Terms that shape the design | Licence of results |
|---|---|---|---|---|
| **Nominatim**, public OSM instance | Geocoding and reverse geocoding | No | At most 1 request per second per application, an identifying User-Agent, caching, attribution; bulk geocoding discouraged; self-host for volume [V] | ODbL |
| **Overpass**, public OSM instances | Features and building footprints | No | Fair use: concurrency 1, timeouts, caching, configurable endpoint, self-host for volume [S] | ODbL |
| **Overture Maps** (`overturemaps` client) | Buildings, and places with stable GERS IDs | No | Large downloads, so always limit to a bbox | Buildings, base, transportation and divisions under ODbL; places under CDLA-Permissive-2.0, some sources Apache-2.0 or CC0 [S]. Confirm on Overture's attribution page. |
| **User files**: CSV, GeoJSON, GeoPackage | Records and boundaries | — | The user states the licence; we carry it in provenance | The user's |
| **Commercial**: Mapbox, OpenCage, Geoapify, TomTom, Esri, Google | Later optional adapters | Yes | Costs. Google Maps Platform terms restrict storing content, so it isn't suitable as a default dataset source. | Provider terms |

**Implications**
- **Outputs inherit their sources' licences.** A dataset built from OSM data is a derivative database under ODbL: share-alike applies if it is distributed publicly.
- **Exports state their licences.** Every export records each record's source licences and includes an attribution block.
- **Code and data licences are separate.** Our code licence is independent of the data licences.

## 5. Licence for our code

| Option | Recommendation |
|---|---|
| **Apache-2.0** | **Recommended.** Permissive, with an explicit patent grant and contribution terms. Compatible with every dependency above (MIT, BSD). Common for MCP and agent tooling. |
| MIT | Simplest, but no patent grant. A fine alternative. |
| MPL-2.0 or GPL family | File-level or strong copyleft would deter adoption by agent hosts. Not recommended. |

**Chosen on 2026-11-22: Apache-2.0** (decision 9). See [LICENSE](../LICENSE) and [NOTICE](../NOTICE).

## 6. Differentiation

**Not unique on their own:** geocoding, Overpass search, geometry validation and fuzzy matching all exist elsewhere.

**Unique as a product:** an **evidence-first dataset-building workflow** for agents.

1. **Records carry evidence.** Researched records enter with evidence that points to registered sources. A quoted excerpt can be **checked against the fetched page**, so invented quotes and URLs are caught.
2. **Decisions are explainable.** Entity resolution and building matching return a decision with reasons:
   - exact building;
   - probable;
   - ambiguous;
   - approximate point;
   - none.

   A record is never attached to a nearby building without evidence.
3. **Runs are reproducible.** Pipelines are declarative, resumable and checkpointed, with quality gates, provenance and run records.
4. **The skill degrades gracefully.** It teaches the research and validation method and still helps when the server is missing.

**Interoperable, not competing.** An agent can use other MCP servers (OSM, Mapbox, gis-mcp) or web search for discovery, then feed what it finds into ours.

## 7. Proposed MVP (v0.1)

1. **Core library.**
   - Versioned schemas (`geoproduct/v1`) for records, evidence, sources and runs.
   - A pipeline engine with checkpoints, resume, retries, cancellation, quality gates and run records.
   - Registered stages covering collection, normalisation, deduplication, geocoding, building matching, enrichment, validation, scoring, export and reporting.
   - A CLI.
2. **Providers.** Built in:
   - offline fixtures;
   - local files;
   - Nominatim;
   - Overpass.

   Overture is an optional extra, the first one added after the core works.
3. **MCP server.** About 19 tools, plus resources and two prompts. stdio by default; Streamable HTTP optional.
4. **Agent Skill.** SKILL.md plus the 11 workflows and focused references. It works without the server, in methodology-only mode.
5. **Exports.** GeoJSON, CSV and JSON. GeoPackage as an optional extra.
6. **Reports.** A Markdown quality report and a run report.
7. **Examples.** The four PRD example pipelines, all on a fictional sample town for tests, plus optional live mode.
8. **Tests.** Unit, integration, end-to-end and reliability tests, offline by default. Live tests are opt-in.
9. **Release files.** CI, README, CONTRIBUTING, SECURITY, CHANGELOG and a release checklist.

## 8. Technical risks

| Risk | Mitigation |
|---|---|
| Public OSM services block bulk or parallel use | Per-provider throttles; caching; batch caps; configurable endpoints for self-hosting; external-call estimates with confirmation before large runs |
| The LLM invents evidence, coordinates or building links | Evidence must point at registered sources; excerpts are checked against fetched content; coordinates come only from providers or files; matching rules refuse to attach on proximity alone |
| False building matches in dense areas, or gaps in footprint coverage | Containment before proximity; an ambiguity margin; precision-aware distance limits; an "approximate point" status; the reasons and candidates are always returned |
| Licence contamination (ODbL share-alike, restrictive provider terms) | Per-record source licences; an attribution block; restrictive providers excluded by default |
| MCP specification and SDK churn (stateless core; Tasks pending in Python) | A thin MCP layer over the core; run handles now, Tasks later; `mcp` pinned to a major version |
| Heavy native dependencies (GDAL) on Windows | Core needs only Shapely and pyproj, which ship wheels; GeoPackage and GeoParquet are extras |
| Scope creep (11 workflows, 25 proposed tools) | Merged overlapping tools; vertical slices; postponements listed below |

## 9. Postponed

- **Analysis features:**
  - routing and network analysis (other MCP servers already do it; OSMnx is heavy);
  - raster processing;
  - change detection.
- **Storage and back ends:**
  - PostGIS and DuckDB imports;
  - GeoParquet;
  - Splink as an entity-resolution back end.
- **Providers:** commercial adapters.
- **Server features:**
  - authentication for remote HTTP deployments;
  - the Tasks extension;
  - a map preview interface.

## 10. Installed skills audit

Skills installed on this machine, and how each relates to the project:

| Skill | Relevance |
|---|---|
| `anthropic-skills:skill-creator` | **Use it** in Stage E to write and evaluate SKILL.md |
| `mattpocock-skills:tdd`, `codebase-design` | **Use them** as development method for the engine (Stages B–C) |
| `google-maps-platform` | Reference only, for a possible commercial adapter later, under its terms on storing content |
| `maplibre-*`, `playwright-cli` | Only for a postponed map preview or screenshot-based map audits |
| `mimic`, `isometric-ops`, `city3d`, `migrating-navsdk-*` | Not relevant |

## Sources

- MCP: [changelog for 2026-07-28](https://modelcontextprotocol.io/specification/2026-07-28/changelog) · [Python SDK README](https://github.com/modelcontextprotocol/python-sdk) · [SDK migration guide](https://py.sdk.modelcontextprotocol.io/migration/) · [SDK structured output](https://py.sdk.modelcontextprotocol.io/servers/structured-output/) · [Tasks extension](https://modelcontextprotocol.io/extensions/tasks/overview) · [SDK issue #3389](https://github.com/modelcontextprotocol/python-sdk/issues/3389)
- Agent Skills: [specification](https://agentskills.io/specification)
- Landscape: [sparkgeo/geo-mcp-servers](https://github.com/sparkgeo/geo-mcp-servers) · [gis-mcp](https://github.com/mahdin75/gis-mcp) · [openstreetmap-mcp-server](https://github.com/cyanheads/openstreetmap-mcp-server) · [ArcGIS MCP beta](https://www.esri.com/arcgis-blog/products/platform/developers/mcp-support-beta-and-arcgis-static-maps-service-in-arcgis-location-platform-release) · [Google-managed MCP servers](https://cloud.google.com/blog/products/ai-machine-learning/google-managed-mcp-servers-are-available-for-everyone) · [muend/geoai-skills](https://www.skills.sh/muend/geoai-skills) · [GISclaw](https://arxiv.org/abs/2603.26845) · [GIS Copilot](https://giscience.psu.edu/gis-copilot-towards-an-autonomous-gis-agent-for-spatial-analysis)
- Data: [Nominatim usage policy](https://operations.osmfoundation.org/policies/nominatim/) · [Overture release notes 2026-11-18](https://docs.overturemaps.org/blog/2026/11/18/release-notes/) · [overturemaps-py](https://github.com/OvertureMaps/overturemaps-py) · [Overture on AWS](https://registry.opendata.aws/overture/) · [POI conflation review](https://arxiv.org/pdf/2310.15320)
- Libraries: PyPI JSON for each package (2026-11-21) · [Splink 5](https://mojdigital.blog.gov.uk/2026/10/15/from-messy-data-to-meaningful-connections-introducing-splink-5/)
