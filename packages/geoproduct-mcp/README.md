# geoproduct-mcp

The MCP server of [GeoProduct Engineer](https://github.com/Ppp2048/geoproduct-engineer-skill). It gives an AI agent structured tools for geographic research:

- register and fetch sources;
- geocode places;
- collect OpenStreetMap features and building footprints;
- build, deduplicate and match datasets;
- validate them, report on their quality and export them;
- run resumable pipelines.

Every location and attribute keeps its evidence.

```bash
uvx geoproduct-mcp            # stdio, for a local MCP client
uvx geoproduct-mcp --http     # Streamable HTTP on 127.0.0.1:8000/mcp (loopback only)
```

A typical client entry:

```json
{
  "mcpServers": {
    "geoproduct": {
      "command": "uvx",
      "args": ["geoproduct-mcp"],
      "env": {
        "GEOPRODUCT_WORKSPACE": "/path/to/my-research/.geoproduct",
        "GEOPRODUCT_USER_AGENT": "my-research/0.1 (you@example.org)"
      }
    }
  }
}
```

The offline `fixture` provider needs no configuration. The public Nominatim and Overpass services need `GEOPRODUCT_USER_AGENT`. The tools read and write only inside the workspace and the folders you allow.

See [configuration](https://github.com/Ppp2048/geoproduct-engineer-skill/blob/main/docs/configuration.md) for every setting. The [repository](https://github.com/Ppp2048/geoproduct-engineer-skill) has the Agent Skill that teaches agents the method.

Licence: Apache-2.0. Datasets you build carry their sources' licences, for example ODbL for OpenStreetMap data.
