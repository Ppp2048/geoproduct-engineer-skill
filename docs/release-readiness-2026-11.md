# GeoProduct Engineer — release readiness audit (Phase F, 2026-11-27)

**Scope.** This repository as it stands on 2026-11-27: commit `86432a7` plus the uncommitted work of Phases A, A2 and B and the GP-16 follow-up (46 modified and 2 deleted files, and 6 new ones). The release under review is the first one, planned as `0.1.0`; every part still says `0.1.0.dev0`.

**Method.** The audit was read-only. No source, test, configuration, dependency or existing document was changed, nothing was installed into the project or the system, and nothing was committed, pushed or published. This report is the only file added. Probes, benchmarks, throwaway environments and the evaluation runs all lived in a scratch folder outside the repository, and the repository's files were fingerprinted (SHA-256) before and after to confirm it. Every result below was produced in this audit unless it says otherwise. **Verified** means a command or probe was run and its output read; **hypothesis** means reasoned from the code but not run.

**Machine.** Intel Core i7-1255U (10 cores, 12 threads), 15.7 GB RAM (1.3 GB free when measuring started; other applications were open), NVMe SSD, Windows 11 Home 10.0.26300 (64-bit), Python 3.14.2, uv 0.11.23. Python 3.12 and 3.13, Linux and macOS were not available locally.

---

## 1. Verdict

**READY WITH LIMITATIONS.** The code has no P0 or P1 defect. The release itself is still gated on three steps that only you can take.

- **The code is in releasable shape for a pre-alpha `0.1.0`.**
  - All automated checks pass, and the packages build and install cleanly.
  - The four example pipelines give the same results as after Phase B.
  - The MCP server negotiates every current protocol version.
  - Five agent evaluation runs passed all 38 of their assertions.
  - No dependency has a known vulnerability.
- **Two P2 defects should be fixed, or at least documented, before publishing.** Both are small.
  - **RR-03:** the fetch "30-second" limit applies per read, not to the whole download, which `SECURITY.md` itself counts as a vulnerability class.
  - **RR-04:** a GeoJSON input with an out-of-range coordinate is accepted, and its GeoJSON export then fails with an `internal` error.
- **The release is gated on three steps** (RR-01, RR-02, RR-05):
  - commit the candidate and see CI pass on every OS and Python version;
  - run the live provider tests from a network that reaches Overpass, or explicitly accept the existing README limitation instead;
  - enable private vulnerability reporting on GitHub.
- **Known limitations remain** (section 9), all P3 or product scope: antimeridian handling, thread growth after cancelled calls (GP-24), validator defaults, and skill evaluations that do not yet separate the skill from a capable agent without it.

## 2. Verification numbers

| Check | Exact command (from the repository root unless noted) | Result |
|---|---|---|
| Test suite | `.venv/Scripts/python.exe -m pytest -p no:cacheprovider -q` | **572 passed, 2 skipped** in 69.2 s. Skipped: the opt-in live tests (`tests/live`) and `test_workspace.py:62` (symbolic links are not permitted on this machine). |
| Test suite, fresh copy | `uv sync --locked --offline` then `uv run --offline pytest -p no:cacheprovider -q`, in a copy of the working tree made from `git ls-files --cached --others --exclude-standard` | **572 passed, 2 skipped** (142 s; it ran alongside other work, so the time is not comparable) |
| Lint | `.venv/Scripts/ruff.exe check --no-cache .` (ruff 0.17.6) | All checks passed |
| Format | `.venv/Scripts/ruff.exe format --check --no-cache .` | 146 files already formatted |
| Types | `.venv/Scripts/python.exe -m mypy` (mypy 2.4.1, `strict = true`, pydantic plugin) | No issues found in 66 source files |
| Example pipelines | `geoproduct --workspace <scratch> --allow examples/data --json run examples/pipelines/<name>.yaml`, for all four (`csv-enrich` with `--param file=examples/data/sample-facilities.csv`) | **4 of 4 succeeded.** Every stage's status and counts are identical to the snapshot taken after Phase B. |
| Skill evaluations | 5 agent runs, three workflows (section 7) | **38 of 38 assertions passed** (8, 8, 8, 7, 7) |
| Release checks | `tests/release` (in the suite): versions agree, licence files, documentation links, README counts, private-name check, actions pinned to commits | Passed (part of the 572) |
| Dependency vulnerabilities | OSV.dev `querybatch`, sending only the names and versions of the 48 PyPI packages pinned in `uv.lock` | **0 known vulnerabilities** (2026-11-27) |
| Secrets | Pattern scan of every file git would ship, for AWS, GitHub, Slack, Google and OpenAI-style keys and private-key blocks | 0 matches |
| Build | `uv build --all-packages --offline --out-dir <scratch>/dist`, then `.venv/Scripts/python.exe .github/scripts/check_dist.py <scratch>/dist` | 2 wheels and 2 sdists. `ok: geoproduct_core 0.1.0.dev0, geoproduct_mcp 0.1.0.dev0` |
| Clean install | `uv venv --offline --python 3.14 <scratch>/venv`, `uv pip install --offline --python <venv> <dist>/*.whl`; then, from a folder outside the repository, `geoproduct-mcp --help` and `geoproduct --workspace <scratch>/ws run examples/pipelines/hospitals.yaml` | Installed (35 distributions, no pip needed). Both commands succeeded. |
| README quick start | `uv sync --locked --offline` and `uv run --offline geoproduct run examples/pipelines/hospitals.yaml` in the fresh copy | Succeeded. The sync took 20 s from the uv cache. |
| MCP protocol | Raw JSON-RPC over stdio against `geoproduct-mcp` (section 4) | 5 negotiations, every listing, 7 error cases: as expected, apart from RR-08 and RR-09 |
| HTTP transport | The Phase B real-socket probe: server on `127.0.0.2`, forged `Host` and `Origin` headers | 421 (foreign Host), 403 (foreign Origin), 200, 200, as accepted in Phase B |
| Fingerprint | SHA-256 of every file git can see, before and after the audit | Only this report differs (section 11) |

**Not run, and why:**
- **The live provider tests** (`GEOPRODUCT_LIVE_TESTS=1`): they need your approval, and no Overpass instance is reachable from this machine's network.
- **Python 3.12 and 3.13, Linux and macOS:** they are not installed here. CI covers them, but its last run was on `86432a7`, which predates all of Phases A and B.
- **The official `skills-ref validate`:** it could not be downloaded here.
- **Native MCP clients** (Claude Desktop, Cursor, MCP Inspector): not available in this environment. The official Python SDK client and raw JSON-RPC stood in for them.
- **A `git clone` from GitHub:** GitHub has only `86432a7`. A copy of the working tree, installed offline from the uv cache, stood in for it.

## 3. Security

### Verified controls

| Area | What was checked | Result |
|---|---|---|
| SSRF address policy | `is_public` on 39 addresses: IPv4 private, loopback, link-local, CGNAT, documentation, benchmarking, reserved, broadcast and multicast; IPv6 loopback, unspecified, link-local with zone, ULA, documentation, multicast, IPv4-mapped, NAT64 (`64:ff9b::/96` and the local-use `64:ff9b:1::/48`), IPv4-compatible, 6to4 and Teredo | All refused except the public ones (8.8.8.8, 1.1.1.1, `2606:4700:4700::1111`, `::ffff:8.8.8.8`, `64:ff9b::808:808`) and the one gap in RR-07 |
| Numeric host spellings | `127.1`, `2130706433`, `0x7f000001`, `0177.0.0.1`, `0`, `localhost`, `[::1]` and `::1` passed to the guarded connector | `localhost` and both `::1` forms were refused by policy. Windows' resolver cannot resolve the numeric forms, so they fail as unreachable. On Linux, glibc resolves them to 127.0.0.1, which the policy refuses: that is a hypothesis, not run here. |
| URL checks | `file:`, `ftp:`, `gopher:`, `data:`, a URL with no host, and user-info forms (`user:pw@`, `example.org@127.0.0.1`) | All refused |
| DNS rebinding and redirects | Code review of `fetch.py`, plus the existing tests | The host is resolved when the connection is made, every address is checked, and the connection goes to a checked address. Redirects pass the same connector and are checked again. Proxies from the environment are ignored. |
| Time limit | A local server dripping one byte every 0.4 s, with `timeout_s=1.0` and an unguarded transport so loopback was allowed | **The fetch finished after 4.42 s.** The limit applies per read (RR-03). |
| Paths | Code review of `Workspace.path` and `user_path`; an MCP call with `file: "../../../../Windows/win.ini"` | Refused with `path_not_allowed`. `resolve()` follows links before the check. Exports never replace a file with different content unless `overwrite: true`. |
| HTTP transport | Bind to non-loopback hosts (existing test); Host and Origin (probe above) | Exits with code 2 for any non-loopback host. Forged Host gets 421, forged Origin gets 403. |
| Secrets | Variable inventory; where the User-Agent flows; scan | The toolkit needs no secrets. The User-Agent goes only into request headers and the `robots.txt` check, never into records, provenance or logs. The scan found nothing. |
| Error exposure | The failing export in RR-04, and seven error cases over MCP | The client receives `internal: Unexpected ValidationError.` with a hint. The traceback goes only to the server's stderr log. Toolkit errors carry `code`, `message`, `retryable` and `details` (the caller's own values). |
| Dependencies and supply chain | OSV query; `uv.lock`; the CI workflow; package metadata | 0 known vulnerabilities. CI installs with `uv sync --locked` and pins actions to commit SHAs (a release test enforces this). Dependabot is active: one open pull request moves `uv_build` from `<0.12` to `<0.14`. `httpx2` and `httpcore2` are the pydantic-maintained fork that the MCP SDK itself requires. |

### Exploitable issues

- **RR-03, fetch duration is unbounded (P2): availability only.**
  - **The attack:** an attacker who can get the agent to fetch their URL can keep `fetch_source_data` busy for as long as the server keeps sending a byte within each 30 s window. That is plausible through prompt injection in a page the agent reads.
  - **The impact:** the tool call hangs and holds a worker thread. Cancelling the call does not stop that thread (GP-24 and RR-06).
  - **What it cannot do:** it does not read or write data, and the 20 MB size cap still holds.
  - `SECURITY.md` lists "bypassing … the size or time limits" as a vulnerability. `docs/configuration.md` says fetching "stops at … 30 seconds".

### Theoretical issues

- **RR-07, IPv4-translated addresses (P3).** `::ffff:0:a00:1` (the RFC 2765 SIIT form of 10.0.0.1) is judged public. Reaching 10.0.0.1 through it needs two things: a network with a stateless translator for `::ffff:0:0/96`, and an attacker-controlled AAAA record. This was not demonstrated.
- **Decompression before the size check (hypothesis).** `httpx2` decodes each received chunk before the 20 MB check runs. A small, highly compressed chunk could therefore expand beyond the cap in memory once. The expansion is bounded by one chunk times the compression ratio. Not tested.
- **Path check to use (hypothesis).** A path is resolved and checked, then used. Racing that gap needs a local attacker who can already write inside an allowed folder.
- **Untrusted content in the agent's context.** Fetched pages and provider data come back as tool output, which is the usual indirect prompt-injection path. The server never executes such content; the skill's ground rules tell the agent to treat it as data.

### Not tested

- Windows alternate data streams (`file.csv:stream`) and device names (`NUL`, `CON`) inside allowed folders.
- The symbolic-link refusal: its test is skipped on this machine and runs only in CI on Linux and macOS.
- Any fuzzing.

## 4. MCP interoperability

**SDK.** `mcp` 2.3.0 (`mcp-types` 2.3.0). The server is `geoproduct` `0.1.0.dev0`, title "GeoProduct Engineer". It offers tools, prompts and resources, none with `listChanged`, and 486 characters of instructions.

**Protocol versions** (raw `initialize` over stdio):

| Asked | Answered |
|---|---|
| `2024-11-05` | `2024-11-05` |
| `2025-03-26` | `2025-03-26` |
| `2025-06-18` | `2025-06-18` |
| `2025-11-25` | `2025-11-25` |
| `1999-01-01` (unknown) | `2025-11-25` (latest; the client decides) |

**Discovery.**
- `tools/list` returns 21 tools in **102,335 bytes**.
- Every tool has an input schema, an output schema and annotations.
- The largest input schemas are `validate_dataset` (4,882 bytes), `geocode_records` (4,082) and `create_dataset` (4,023). Descriptions total 3,730 characters.
- `prompts/list` returns 2 prompts, `resources/list` 3 resources (`guide`, `stages`, `providers`) and `resources/templates/list` 3 templates (`schema`, `run`, `dataset`).

**Invocation and errors.**

| Case | Answer |
|---|---|
| Unknown tool | `isError` result, "Unknown tool: no_such_tool". This is the SDK's behaviour; the specification also allows a JSON-RPC error. |
| Wrong argument type, missing required argument | `isError` result whose text is a raw pydantic message with an `errors.pydantic.dev` link, and **no `structuredContent`** (RR-09) |
| Unknown dataset, plus an unknown argument `bogus` | A structured `not_found` error. **The unknown argument was silently ignored** (RR-08). |
| Path outside the allowed folders | A structured `path_not_allowed` error |
| Unknown method | JSON-RPC `-32601 Method not found` |
| A malformed JSON line, then `ping` | The server survived and answered the ping |

**Structured results.** All 18 calls made by the evaluation runs over Streamable HTTP succeeded, with structured content (0 server errors).

**Long operations and cancellation.**
- Pipelines run in the background, with status, progress and cancellation (covered by the suite).
- A cancelled tool call ends at once and stores nothing (Phase B, GP-07).
- **Measured in this audit:** the worker thread is abandoned, not stopped. The thread-pool limiter's token is released at once, so new calls are not blocked. Instead, threads accumulate: 40 more for each batch of 40 cancelled calls still blocked, reaching **161 live threads after 4 cycles** (RR-06).
- With 45 blocking calls that are not cancelled, a quick call waited **2.5 s** for a free token, which is the expected behaviour of a 40-token pool.

**Client compatibility.**
- **Verified with:** the official Python SDK `Client` over Streamable HTTP (the evaluation shim and the suite's in-process tests), and raw JSON-RPC over stdio.
- **Not run in this audit:** Claude Desktop, Claude Code, Cursor and MCP Inspector.

**Information exposure.**
- `geoproduct://providers` names settings, never their values.
- Errors echo the caller's own identifiers and paths, never server paths outside the workspace.
- Internal errors carry a generic message.

**Annotations.**
- The read-only tools say so, and the network tools set `openWorldHint`.
- `cancel_geo_pipeline` is the only idempotent one.
- `export_geo_dataset` is marked non-destructive, although `overwrite: true` replaces files (RR-15).

## 5. Installation and documentation

**What was verified.**
- The README's quick start works on a fresh copy of the working tree, using an offline `uv sync --locked`, and the wheels run outside the source tree. This is the CI `package` job's check, repeated locally.
- Both wheels and sdists:
  - carry `LICENSE` and `NOTICE`;
  - declare `License-Expression: Apache-2.0`;
  - ship `py.typed` and no tests.
- `geoproduct-mcp` pins `geoproduct-core==0.1.0.dev0`, and `tests/release` checks that every version agrees.
- The skill's two helper scripts use only the standard library. `check_dataset.py` ran with a bare Python 3.14 that has no GeoProduct installed, and found the 7 issues in the audit test file.

**Clean setup.**

| Path | Status |
|---|---|
| Install from scratch | Works from a checkout (`uv sync`). `uvx geoproduct-mcp` works only after PyPI publishing (GP-15). The package README, which becomes the PyPI page, already uses `uvx` and is correct from then on. |
| Configure providers | Documented in `docs/configuration.md` and `.env.example`. `fixture` needs nothing. Without `GEOPRODUCT_USER_AGENT`, `geoproduct://providers` reports Nominatim and Overpass as `configured: false`, and both evaluation agents noticed this and said so. |
| Start the server, connect an agent | Verified with stdio and HTTP (SDK client). Native desktop clients not tested here. |
| Skill on its own, without MCP | The skill's mode table documents CLI mode (no `register_source` or `fetch_source_data`) and method-only mode. The scripts run without the toolkit. |
| MCP without the skill | Protocol probes and the suite only; no agent ran with the server and without the skill. |

**Gaps.**
- **No troubleshooting section** anywhere (RR-14), for example for an unreachable Overpass, `configured: false`, Windows path quoting, or `uv` not found by a desktop client. The last of these is a common setup problem but was not tested here.
- **Windows notes live only in `docs/configuration.md`.** These are forward slashes in JSON paths, and `;` between allowed folders. The README shows POSIX paths only.
- **`copy_to` is described as a folder in the stage docs.** The skill's tool table says only "inside the workspace or an allowed folder", and an evaluation agent passed a file name (RR-13).
- **Documentation quality is otherwise high:**
  - architecture, decisions, configuration, security policy, releasing, evaluations and the changelog are current with Phase B;
  - the README's tool, prompt and stage lists are checked against the code by a release test;
  - Limitations are stated plainly: Overpass live untested, single user, scale and evaluation limits.

**Versioning and packaging.**
- `0.1.0.dev0` everywhere, and `CHANGELOG.md` has an `[Unreleased]` section for `0.1.0`.
- `docs/releasing.md` defines SemVer before and after 1.0, a separate data-contract version (`geoproduct/v1`) and an 11-step manual release.

## 6. Resource efficiency

**Method.**
- **Data:** a synthetic CSV of N rows in a 20 km × 20 km box (seed 7). A third of the names repeat, and 10% of rows are a near copy (same name, about 10 m away) of an earlier row.
- **Pipeline:** `collect.file` → `normalize.records` → `resolve.deduplicate` → `validate.dataset` → `export.geojson`, run in-process through `geoproduct.cli.main`.
- **Measurements:**
  - stage times come from `run.json`;
  - peak working set comes from Windows `GetProcessMemoryInfo`;
  - `head(5)` is compared with a full load of the deduplicated stage.
- **Runs:** 1k and 10k rows three times each, 50k once. They ran after all agent runs had finished.
- **Caveat:** the first two runs of the session, before the memory counter worked, were up to 2.6 times slower (4.99 s for 1k, 19.3 s for 10k). Treat the figures as indicative for this laptop.

| Rows | Runs | Pipeline (s) | Deduplicate (s) | Validate (s) | Export (s) | Peak working set (MB) | Stage outputs on disk (MB) | `head(5)` (ms) | Full load (ms) |
|---:|---:|---|---|---|---|---|---:|---|---|
| 1,000 | 3 | 1.75–1.89 | 0.42–0.52 | 0.14–0.16 | 0.26–0.28 | 75–77 | 2.7 | 0.5–0.9 | 19–25 |
| 10,000 | 3 | 7.42–8.27 | 3.40–3.65 | 1.04–1.33 | 0.64–0.75 | 175–177 | 25.8 | 0.4–0.5 | 256–263 |
| 50,000 | 1 | 47.89 | 32.45 | 5.86 | 2.56 | 627 | 130.1 | 0.7 | 1,216 |

**Observations (measured):**
- **Deduplication dominates and grows faster than linearly.** From 10k to 50k rows (5×), its time grew about 9×. 100,000 rows, the default stage limit, was not measured.
- **Memory grows roughly linearly,** at about 11 MB per 1,000 records at 50k. That is consistent with the documented in-memory design.
- **Checkpoints write a full copy of each records stage.** For 50k rows, a 2.2 MB CSV became 130 MB of stage outputs, about 58×. Nothing prunes them yet (GP-08).
- **`head(5)` stays under 1 ms at every size,** so GP-09 holds. A full load takes 1.2 s at 50k.
- **Imports:**
  - `import geoproduct_mcp.server` takes 2.41–2.57 s, of which the `mcp` SDK accounts for about 1.4 s (`-X importtime`);
  - a stdio server answers `initialize` 2.4–2.9 s after it starts;
  - `import geoproduct.cli` takes 0.57–1.25 s.
- **HTTP caching and duplicate requests** were not measured: the audit made no live requests. The suite covers the cache and its refusal to cache partial Overpass answers (GP-01).

## 7. Skill only versus Skill + MCP

**Design.**
- **Workflows:** w1, no Skill, MCP or toolkit (general tools and Python's standard library); w2, the Skill plus the toolkit through its CLI, without MCP; w3, the Skill plus the toolkit through the MCP server only.
- **Tasks:** two, with identical inputs per task.
  - **`audit-facility-csv`:** the skill's evaluation case, an 11-row CSV with 8 planted problems. All three workflows ran it.
  - **`sample-town-hospitals`:** a tool-neutral wording of the hospitals case, over the offline demo town. Only w2 and w3 ran it, because w1 has no way to reach the demo data.
- **Runs:**
  - Each run was a fresh Opus 5.5 subagent, briefed from `docs/evaluations.md`'s template with a workflow paragraph added.
  - The w3 runs called a dedicated `geoproduct-mcp --http` server through a small SDK-based command-line client that logged every call.
  - Graders were the cases' own assertions, checked by a script on the GeoJSON and by phrases confirmed in the reports, which were read in full.

| Task | Workflow | Assertions | Tokens | Time (s) | Agent tool uses | MCP operations (errors) | Toolkit external requests |
|---|---|---|---:|---:|---:|---|---|
| Audit CSV | w1 none | **8/8** | 74,594 | 117.5 | 6 | — | — |
| Audit CSV | w2 Skill + CLI | **8/8** | 111,149 | 257.5 | 19 | — | 0 (no User-Agent configured) |
| Audit CSV | w3 Skill + MCP | **8/8** | 117,544 | 292.5 | 21 | 4 (0) | 0 (providers `configured: false`) |
| Hospitals | w2 Skill + CLI | **7/7** | 149,914 | 317.3 | 24 | — | 0 (fixture) |
| Hospitals | w3 Skill + MCP | **7/7** | 161,015 | 484.5 | 32 | 14 (0) | 0 (fixture) |

Tokens, time and tool uses are measured, from the task notifications. MCP operations are measured, from the client's log: 1 listing and 3 tool calls; then 1 listing, 1 resource read and 12 tool calls. The facilities CSV was byte-identical after every run (SHA-256 `0036191b…b30b7`).

**Findings.**
- **Correctness was the same in every workflow.**
  - All three audit runs found the same 8 problems and labelled corrections as unconfirmed.
  - Both hospital runs kept the two "Sample General" records apart, gave the same match statuses (exact 2, probable 1, ambiguous 1, none 1) and the same confidence scores.
  - Their GeoJSON files have the same 5 records, the same content and the same size (8,223 bytes). Across the CLI and MCP interfaces the result was reproducible.
- **Cost rises with the toolkit.**
  - On the audit task, w2 used 49% more tokens and 2.2 times the time of w1. w3 used 58% more tokens and 2.5 times the time.
  - On the hospitals task, w3 used 7% more tokens and 53% more time than w2. Its extra steps were a `register_source` call, two export attempts, and two argument files whose Windows backslashes broke JSON quoting in the client, before the request reached the server.
- **What the toolkit added was traceability, not more findings.**
  - w2 and w3 delivered dataset and run identifiers, a quality score (0.60 on the audit) and stored reports.
  - Both reported toolkit limitations themselves:
    - the default 5 km outlier distance flags 2 points inside the 22 × 17 km district;
    - the validator has no precision or lon/lat-swap check, though the skill's helper script has both (RR-12).
  - w3 recovered from a `copy_to` misunderstanding: it gave a file name and got a folder (RR-13).
- **Error recovery.** w1 and w2 had nothing to recover from. w3's errors were all on the client side or from misused parameters; there were 0 server errors.

**How far the comparison holds.**
- **One run per configuration,** so no variance is known, and a 38/38 pass rate cannot separate the workflows.
- **The assertions do not discriminate.** The no-skill baseline passes the audit case at 64% of the tokens and 46% of the time, which repeats the finding of Stage E and of GP-13. **This audit cannot support a claim that the skill improves correctness.** What it supports is that the skill and toolkit produce traceable, reproducible outputs at a higher cost.
- **Workflows were separated by instruction only.** Agents were told not to read the repository (w1) or use the other interface; the w1 agent reported that it did not.
- **w3 is not a native MCP connection.** Its client fetched tool definitions on demand (and cut them at 40,000 characters, so one agent read `validate_dataset`'s parameters from the skill's reference instead). A native client would put the whole 102 KB `tools/list` into the model's context: roughly 25,000 tokens at 4 characters per token. That figure is an estimate, not measured. w3's token figures therefore understate a native setup.
- **No agent ran "MCP without the Skill" or "the Skill without any toolkit".**

## 8. Prioritised findings and blockers

Severity:
- **P0:** exploitable security flaw or data loss on a normal path.
- **P1:** a correctness defect on a main path, or a release gate that is not met.
- **P2:** a documented guarantee that does not hold, or a failure on a plausible input.
- **P3:** hardening, edge cases, usability.

"Blocks" means it must be resolved, or explicitly accepted by you, before tagging `0.1.0`.

| ID | Sev. | Blocks | Finding |
|---|---|---|---|
| RR-01 | P1 | **Yes** | The release candidate is uncommitted, and CI has never run on it |
| RR-02 | P1 | **Yes, unless waived** | The live provider tests have not run against this candidate; live Overpass has never passed |
| RR-03 | P2 | No; fix or correct the docs first | The fetch time limit is per read, not per download |
| RR-04 | P2 | No; fix recommended | A GeoJSON input with out-of-range coordinates is accepted, and its export fails with `internal` |
| RR-05 | P2 | **Yes** (one-time setting) | Private vulnerability reporting is not confirmed enabled |
| RR-06 | P3 | No | GP-24, measured: cancelled calls grow the thread count, rather than exhaust the pool |
| RR-07 | P3 | No | IPv4-translated `::ffff:0:0/96` addresses are not unwrapped |
| RR-08 | P3 | No | Unknown tool arguments are ignored silently |
| RR-09 | P3 | No | Argument-validation errors are unstructured |
| RR-10 | P3 | No | `tools/list` is 102 KB |
| RR-11 | P3 | No | Antimeridian: deduplication, outliers and manifest bbox (GP-11, widened) |
| RR-12 | P3 | No | Validator defaults and gaps found by the evaluations |
| RR-13 | P3 | No | `copy_to` wording |
| RR-14 | P3 | No | No troubleshooting guide; Windows notes scattered |
| RR-15 | P3 | No | `export_geo_dataset` annotated as non-destructive |
| RR-16 | P3 | No | Skill evaluations still do not discriminate (GP-13) |

### RR-01: the candidate is uncommitted, and CI has never run it (P1, blocks)

- **Evidence:** `git status` shows 46 modified, 2 deleted and 6 untracked files besides this report: Phases A, A2 and B and the GP-16 follow-up. The last CI run (CI #3, `86432a7`) passed on 2026-11-23.
- **Impact:** the code being released has passed only on Windows with Python 3.14. Python 3.12 and 3.13, Linux, macOS and the `package` job have not run on it.
- **Fix:** commit and push when you choose to, then confirm that every CI job passes.

### RR-02: live provider tests not run for this candidate (P1, blocks unless waived)

- **Evidence:**
  - the Nominatim live tests last passed on 2026-11-22;
  - `providers/http.py` and `providers/overpass.py` changed afterwards, in Phases A and B (GP-01, GP-07);
  - the Overpass live tests have never reached the public service;
  - `docs/releasing.md` step 4 requires the live tests.
- **Impact:** the Overpass request path has been checked only against service-shaped mocks. The README states this.
- **Fix:** run `tests/live` from a network that reaches Overpass, or accept the README limitation for `0.1.0` explicitly.

### RR-03: the fetch time limit is per read (P2)

- **Evidence and reproduction:**
  - a local `ThreadingHTTPServer` sent 12 bytes, one every 0.4 s;
  - `Fetcher(timeout_s=1.0, transport=httpx2.HTTPTransport(trust_env=False))` was used, because the guarded transport would refuse loopback;
  - it returned all 12 bytes after **4.42 s**;
  - `httpx2` applies `timeout` to each connect, read and write, not to the whole request.
- **Files:** `packages/geoproduct-core/src/geoproduct/fetch.py` (`Fetcher.fetch`, `_get`). The provider client `providers/http.py` uses the same per-read semantics, but its endpoints are operator-configured.
- **Impact:** a hostile site can hold a tool call and its thread for a long time (section 3). The docs' "stops at … 30 seconds" is not true as written.
- **Recommended fix:** a whole-request deadline, checked in the `iter_bytes` loop of `_get`. Checking `job.cancel` in that loop as well would also let cancellation stop the download (RR-06). Otherwise, reword `docs/configuration.md` and `SECURITY.md`.

### RR-04: an out-of-range GeoJSON coordinate breaks the export (P2)

- **Evidence and reproduction:**
  - a GeoJSON `FeatureCollection` with a Point at `[10, 95]` and an unclosed ring;
  - `collect.file` kept both, with 0 rejected;
  - `validate.dataset` reported them correctly: "outside longitude/latitude range", "a polygon ring needs at least 4 positions";
  - `export.geojson` then failed with `{"code": "internal", "message": "Unexpected ValidationError."}`, because the manifest's computed bbox has a latitude above 90;
  - the same values in a CSV are rejected at import ("latitude 95.0 is outside -90..90").
- **Files:** `stages/collect.py` (the GeoJSON path) and `stages/output.py` (`export_geojson` manifest).
- **Impact:** one bad coordinate in a user's GeoJSON makes the export fail, and the error message calls it a bug. No data is corrupted. The JSON format path was not tested.
- **Recommended fix:** apply the CSV path's coordinate and ring checks to GeoJSON geometries in `collect.file`, rejecting such records with a reason. A regression test should cover both formats.

### RR-05: private vulnerability reporting not confirmed (P2, blocks)

- **Evidence:** the public Security page shows the policy. Its accessibility tree has no "Report a vulnerability" link or button; the only match is the policy's own text. Signed-out pages may hide it, so this is unconfirmed rather than disproved.
- **Impact:** `SECURITY.md`'s only reporting route may not work.
- **Fix:** enable it in the repository's security settings (`docs/releasing.md`, the one-time steps).

### RR-06: GP-24, measured (P3)

- **Evidence:**
  - in `f_gp24_probe`, 60 calls of work that blocks for 3 s and ignores the cancel flag were run through `geoproduct_mcp.calls.call` and cancelled;
  - the limiter's 40 tokens were released at once (0 borrowed), and a quick call then took 2 ms;
  - 4 cycles of 40 cancelled calls raised the live thread count from 1 to 41, 81, 121 and 161.
- **Impact:** the backlog's description ("can use up anyio's 40 worker threads") needs correcting. Cancelled calls do not stall the pool. They leave threads running beyond the limit until their blocking work returns: up to about 120 s for Overpass, and with no bound for a fetch (RR-03).
- **Recommended fix:** as GP-24, plus RR-03's cancel check in the download loop.

### RR-07 to RR-16 (P3)

- **RR-07, IPv4-translated addresses.**
  - **Evidence:** `is_public("::ffff:0:a00:1")` returns `True`.
  - **Fix:** add `IPv6Network("::ffff:0:0/96")` to `CARRIES_IPV4` in `fetch.py`, with a test.
- **RR-08, unknown tool arguments are ignored.**
  - **Evidence:** no tool's input schema sets `additionalProperties: false`. `validate_dataset` with an extra `bogus` argument ran normally. Pipeline stage parameters do refuse unknown keys, so the two interfaces behave differently.
  - **Impact:** a misspelled optional parameter is silently ignored; an earlier phase hit this with `radius_m`.
  - **Recommended fix:** none now, because tool schemas must not change in this audit. Later, either set strict schemas (a schema change) or note ignored arguments in the result's summary.
- **RR-09, unstructured argument-validation errors.**
  - **Evidence:** SDK argument-validation failures return raw pydantic text with an external link and no `structuredContent`, unlike the toolkit's own errors.
  - **Recommended fix:** validate inside the tool wrapper, or document the two shapes in `references/mcp-tools.md`.
- **RR-10, `tools/list` size.**
  - **Evidence:** 102,335 bytes (input and output schemas).
  - **Impact:** context cost for clients that pass every definition to the model. About 25,000 tokens is an estimate.
  - **Recommended fix:** none required. If it matters, measure it in a native client first.
- **RR-11, antimeridian (GP-11, widened).**
  - **Evidence:**
    - two records 111 m apart across 180°, sharing a site-level register code, were neither merged nor flagged, while the same pair at 0° merged;
    - `validate.dataset` reported those points about 9,452 km from the median, as outliers;
    - the export manifest's bbox became `[-179.9995, …, 179.9995, …]`, the whole globe.
    - The area check itself handles a bbox that crosses the antimeridian correctly.
  - **Fix:** as GP-11, extended to distances in deduplication, outliers and the manifest bbox.
- **RR-12, validator defaults and gaps.**
  - **Evidence:** on the audit task, `outlier_distance_m` (5,000 m by default) flagged 2 in-box points of a 22 × 17 km area. `validate.dataset` has no coordinate-precision or lon/lat-swap check, though `scripts/check_dataset.py` has both. Both evaluation agents had to explain the difference.
  - **Recommended fix:** scale the default outlier distance to the area, and add the two checks.
- **RR-13, `copy_to` wording.**
  - **Evidence:** `export_geo_dataset` with `copy_to: ".../name.geojson"` created a folder of that name. The skill's tool table does not say that `copy_to` is a folder.
  - **Fix:** say "a folder" there, or refuse a `copy_to` that ends in a known file suffix.
- **RR-14, documentation.**
  - **Evidence:** no troubleshooting section, and the README shows only POSIX paths.
  - **Fix:** add a short troubleshooting section and a Windows example.
- **RR-15, annotation.**
  - **Evidence:** `export_geo_dataset` has `destructiveHint: false`, but with `overwrite: true` it replaces files.
  - **Fix:** set the hint to true, or keep it and document why.
- **RR-16, evaluations (GP-13).**
  - **Evidence:** section 7.
  - **Fix:** harder cases that tempt invention, licence traps and conflicting sources (already on the roadmap), and at least 3 runs per configuration.

### Backlog items revisited (not implemented)

| ID | Backlog severity | Status now | Evidence in this audit | Blocks |
|---|---|---|---|---|
| GP-24 | P3 | Open. **Mechanism corrected:** threads grow beyond the pool instead of exhausting it | RR-06 probes | No |
| GP-20 | P3 | Open, unchanged since the GP-16 follow-up. A merged record keeps the primary record's attribute values. | The follow-up probe still merges as accepted (cases 8, 9 and 12). Not re-measured beyond that. | No |
| GP-21 | P2 | Open. An unclassified Wikidata item still links and never merges offline. A lookup needs network access and your approval. | Follow-up probe case 1: "they share only a Wikidata item that is not known to name a site" | No |
| GP-22 | P3 | Open, confirmed. `0300 123 4567 / 4568`, two numbers separated only by spaces, and `011 44 …` each stay one value. `A; B` splits correctly. | `phone_keys` gave `['030012345674568']`, `['0300123456703001234568']` and `['011442079460958']` | No |
| GP-23 | P3 | Open, unchanged. The summary shows `info.notes[:3]`. | `tools/common.py:277` | No |
| GP-11 | P3 | Open, **widened** by RR-11 | `f_func_probe` | No |
| GP-15 | P2 | Open. Publishing is your step. | Section 5 | Part of the release |

## 9. Remaining limitations

These are known, documented or measured. None blocks a pre-alpha release.

- **Pre-release, single user.** The HTTP transport has no authentication and serves loopback only, so any local process can use it.
- **Scale.**
  - Datasets are held in memory, and 50k records peaked at 627 MB.
  - Deduplication grows faster than linearly: 32 s for 50k records.
  - Checkpoints use about 58× the input size on disk, with no pruning (GP-08).
- **Providers.**
  - Only OpenStreetMap services are built in.
  - Live Overpass has not been verified (RR-02).
  - Public instances limit the rate.
- **Geography.** Nothing handles the antimeridian beyond the area check (RR-11).
- **Identity.** The GP-16 rules are deliberately conservative:
  - organisation-level evidence links records but never merges them;
  - unclassified Wikidata items never merge (GP-21);
  - some phone notations are not parsed (GP-22).
- **Interfaces.**
  - CLI mode has no source registration or fetching, as the skill documents.
  - Tool arguments are not strict (RR-08), and validation errors have two shapes (RR-09).
- **Robustness.** A cancelled call's thread runs on (RR-06), and fetch duration is unbounded (RR-03, until it is fixed).
- **Evidence.** PDF text is not extracted, so PDF excerpts stay `not_checked`.
- **Evaluation.** The skill has not been shown to beat a capable agent without it (RR-16).

## 10. Next steps

These are recommendations only. Nothing below has been started; each waits for your decision.

1. **Decide on RR-03 and RR-04** (fix now, or document and defer). Both are small, need no new dependencies, and come with clear regression tests. RR-07 and RR-13 are one-line companions if you want them in the same change.
2. **Commit and push the candidate** when you choose to (RR-01), then confirm that every CI job passes.
3. **Run the live tests** from a network that reaches the public services, with a generic User-Agent (RR-02), or record that `0.1.0` ships with the Overpass limitation as stated in the README.
4. **Enable private vulnerability reporting** (RR-05) and decide how you will publish to PyPI (token or trusted publishing).
5. **Follow `docs/releasing.md`** for the release itself (section 11).
6. **After `0.1.0`:** GP-24 together with RR-03's cancel check; RR-11 (antimeridian); RR-12 (validator); stronger evaluations (RR-16, GP-13); GP-18 when phase D comes; then GP-12, GP-14 and GP-08 by priority.

## 11. Release checklist

Status against `docs/releasing.md`, as of this audit.

**Once, before the first release**

| Item | Status |
|---|---|
| Enable private vulnerability reporting | **Not confirmed** (RR-05). Your step. |
| PyPI account with two-factor authentication; names `geoproduct-core` and `geoproduct-mcp` | Not done. Your step. The names were unclaimed on 2026-11-22; not re-checked here. |
| Choose a token or trusted publishing | Not decided |

**Every release**

| Step | Status |
|---|---|
| 1. Set the version in both `pyproject.toml` files, the core pin and `SKILL.md`; run `uv lock` | Not done: still `0.1.0.dev0` (consistent, checked by `tests/release`) |
| 2. `CHANGELOG.md`: rename `[Unreleased]` | Not done. The section is current with Phase B. |
| 3. Local checks: `uv sync --locked`, ruff format and check, mypy, pytest | **Equivalent verified.** `uv sync --locked --offline` on a fresh copy; ruff, format, mypy and the suite in section 2 all pass. |
| 4. Live tests | **Not run** (RR-02) |
| 5. Build and `check_dist.py` | **Verified** (into a scratch folder) |
| 6. Wheels in a clean environment, README quick start | **Verified** (offline, from the uv cache; outside the repository) |
| 7. Commit, push, CI green on `main` | **Not done** (RR-01). Your step. |
| 8. Tag `vX.Y.Z` | Not done. Your step. |
| 9. Publish: core first, then the server | Not done. Your step. |
| 10. GitHub release with the changelog and a zip of the skill | Not done. Your step. |
| 11. Bump to the next `.dev0` | Not done |
| After publishing: `uvx geoproduct-mcp --help` in a fresh environment; switch the README to `uvx` | Not done |

**Phase F's own check.** Every file git can see was fingerprinted before (191 entries) and after the audit:
- this report is the only file added;
- no other file's hash changed;
- ignored folders such as `.mypy_cache` may have been refreshed by mypy, and are not shipped.

The scratch artefacts (probe scripts, evaluation runs with their outputs, `timing.json` and `grading.json`, benchmark results, the built distributions) stay outside the repository.
