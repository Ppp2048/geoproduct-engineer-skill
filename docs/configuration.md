# Configuration

The MCP server (`geoproduct-mcp`) and the CLI (`geoproduct`) read the same environment variables. Nothing needs configuring for the offline `fixture` provider. The live OpenStreetMap providers need one setting: `GEOPRODUCT_USER_AGENT`.

No variable holds a secret: the built-in providers need no keys. A future provider that needs one will read it from the environment, list it under `credentials` in `geoproduct://providers` (the name only, never the value), and never write it to disk, logs or tool results.

## Running the server

The packages are not on PyPI yet ([releasing.md](releasing.md) has the release checklist). Until they are, run the server from a checkout:

```bash
git clone https://github.com/Ppp2048/geoproduct-engineer-skill.git
cd geoproduct-engineer-skill
uv sync
uv run geoproduct-mcp --help
```

| Transport | Command | Use |
|---|---|---|
| stdio (default) | `geoproduct-mcp` | A local client starts the server as a subprocess. This is the normal setup. |
| Streamable HTTP | `geoproduct-mcp --http [--port 8000]` | One long-running server on `http://127.0.0.1:8000/mcp`, shared by local clients |

**HTTP is loopback-only.** The server has no authentication, so `--host` must be a loopback address (`127.0.0.1` or `::1`); any other host exits with code 2. Requests must name the address served in their `Host` header, and in `Origin` if they send one; others are refused (421 or 403), which protects the server from DNS rebinding. `127.0.0.1`, `localhost` and `::1` accept one another. Serving remote users would need the MCP authorization flow (OAuth 2.1), plus per-user workspaces and quotas. None of that exists yet, so remote serving is not offered.

**Command-line options.**

| Option | Default | Meaning |
|---|---|---|
| `--http` | off | Serve Streamable HTTP instead of stdio |
| `--host` | `127.0.0.1` | HTTP host; loopback only |
| `--port` | `8000` | HTTP port |
| `--workspace DIR` | `$GEOPRODUCT_WORKSPACE`, else `.geoproduct` | Workspace folder (overrides the variable) |
| `--allow DIR` | none | A further folder that tools may read from and export to. Repeatable; added to `GEOPRODUCT_ALLOWED_DIRS`. |
| `--log-level` | `WARNING` | `DEBUG`, `INFO` or `WARNING`. Logs go to stderr; with stdio, stdout carries only protocol messages. |

An invalid setting stops the server at start-up with exit code 2 and a message that names the variable. The message never repeats the value.

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `GEOPRODUCT_WORKSPACE` | `.geoproduct` in the current folder | Where sources, datasets, runs and the HTTP cache are stored. **Set an absolute path for MCP clients**: desktop clients often start servers in a folder you did not choose. |
| `GEOPRODUCT_ALLOWED_DIRS` | none | Folders, besides the workspace, that tools may read files from and export to. Separate them with `;` on Windows and `:` elsewhere. Every other path is refused with `path_not_allowed`. |
| `GEOPRODUCT_USER_AGENT` | none | Identifies your application to the public OpenStreetMap services, which refuse anonymous clients. Use 3 to 200 printable ASCII characters, and include a way to reach you, for example `my-project/0.1 (you@example.org)`. `geoproduct-core/<version>` is appended. Without it, `nominatim` and `overpass` are listed but return `auth_missing`. `fetch_source_data` also sends it; without it, a default naming this project is sent. |
| `GEOPRODUCT_CACHE_TTL_HOURS` | `168` (7 days) | How long successful provider responses are reused from the cache, from 0 to 8760. `0` turns the cache off. |
| `GEOPRODUCT_NOMINATIM_URL` | `https://nominatim.openstreetmap.org` | A self-hosted or commercial Nominatim instance |
| `GEOPRODUCT_NOMINATIM_RATE` | `1` | Requests per second, above 0 and at most 100. **Ignored for the public instance**, which stays at 1 per second with at most 100 geocodes per run. |
| `GEOPRODUCT_OVERPASS_URL` | `https://overpass-api.de/api/interpreter` | Another Overpass instance |
| `GEOPRODUCT_OVERPASS_TIMEOUT_S` | `90` | The server-side timeout for one Overpass query, from 10 to 900 seconds |

Endpoint URLs must be `http` or `https` with a host, and carry no user name, password, query or fragment: the URL is shown in `geoproduct://providers` and recorded in run manifests. The public instances are recognised by their host, however the URL is written, so a trailing slash or upper-case letters cannot lift their limits.

`.env.example` in the repository root lists these variables. The server does not read `.env` files itself; set the variables in your MCP client's configuration or your shell.

## Client configuration

Most clients take a JSON block like the one below (Claude Desktop's `claude_desktop_config.json`, Cursor's `.cursor/mcp.json`, and others use the same `mcpServers` shape). Replace the paths and the contact address with your own.

```json
{
  "mcpServers": {
    "geoproduct": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/geoproduct-engineer-skill", "geoproduct-mcp"],
      "env": {
        "GEOPRODUCT_WORKSPACE": "/path/to/my-research/.geoproduct",
        "GEOPRODUCT_ALLOWED_DIRS": "/path/to/my-research/data",
        "GEOPRODUCT_USER_AGENT": "my-research/0.1 (you@example.org)"
      }
    }
  }
}
```

On Windows, write paths with forward slashes or doubled backslashes (`"D:/research/.geoproduct"`), and separate allowed folders with `;`.

**Claude Code:**

```bash
claude mcp add geoproduct -e GEOPRODUCT_USER_AGENT="my-research/0.1 (you@example.org)" -e GEOPRODUCT_WORKSPACE=/path/to/my-research/.geoproduct -- uv run --directory /path/to/geoproduct-engineer-skill geoproduct-mcp
```

**Shared HTTP server.** Start `geoproduct-mcp --http` yourself, with the variables set in that shell, then point clients at the URL. For Claude Code:

```bash
claude mcp add --transport http geoproduct http://127.0.0.1:8000/mcp
```

**After publication**, `uv run --directory …` becomes `uvx geoproduct-mcp`.

## Installing the Agent Skill

The skill is the folder `skills/geoproduct-engineer/`. It is self-contained: every link in it points inside the folder, and its two scripts need only the Python standard library. Copy the folder whole, keeping its name, since a skill's folder name must match the `name` in its `SKILL.md`. The `evals/` subfolder only tests the skill and can be left out.

**Claude Code** looks for skills in two places:

| Scope | Copy the folder to |
|---|---|
| You, in every project | `~/.claude/skills/geoproduct-engineer/` |
| One project, shared through its repository | `<project>/.claude/skills/geoproduct-engineer/` |

```bash
mkdir -p ~/.claude/skills
cp -r /path/to/geoproduct-engineer-skill/skills/geoproduct-engineer ~/.claude/skills/
```

Other agents that support the Agent Skills format take the same folder; put it where that agent looks for skills.

The skill tells the agent how to research, match and validate; the MCP server (above) or the `geoproduct` CLI does the work. Without either, the skill still guides the method and its scripts still check files, but nothing is collected or exported.

## What the server can reach

| Access | Limits |
|---|---|
| Files | Reads and writes inside the workspace; reads from and exports to `GEOPRODUCT_ALLOWED_DIRS` and `--allow` folders. Path traversal and links pointing outside are refused. |
| Provider endpoints | Only the configured Nominatim and Overpass URLs, through the throttle and cache. |
| `fetch_source_data` | `http` and `https` URLs on public addresses only: private, loopback, link-local and other special addresses are refused at connection time, after DNS resolution and on every redirect, as are IPv6 addresses that carry one (IPv4-mapped, NAT64 `64:ff9b::/96`, IPv4-compatible). It honours `robots.txt`, accepts HTML, text, CSV, JSON, GeoJSON, XML and PDF (evidence excerpts can be checked against all of them except PDF, whose text is not extracted), and stops at 20 MB, 30 seconds and 5 redirects. |
| Large workloads | A pipeline estimated at more than 25 external requests, or with no upper bound, needs `confirm: true` (see `run_geo_pipeline`), and so does `geocode_records` on more than 25 records with an online geocoder. |

## Workspace layout

```
<workspace>/
  sources/<source_id>/      source.json, plus content.*, text.txt and fetch.json once fetched
  datasets/<dataset_id>/    dataset.json, the stage output (GeoJSON plus provenance.json, a report or exported files) and rejects.jsonl
  runs/<run_id>/            run.json, spec.json, events.jsonl and one folder per stage
  cache/http/<provider>/    cached provider responses (keyed by a SHA-256 of the request)
```

The workspace is safe to delete when no run is active. Deleting `cache/` only means the next queries are sent again.
