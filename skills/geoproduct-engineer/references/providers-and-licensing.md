# Providers, usage policies and licensing

## Built-in providers

`geoproduct://providers` shows the live status: whether each provider is configured, its usage policy and its attribution.

| Provider | Capabilities | Data and licence | Usage policy |
|---|---|---|---|
| `fixture` | geocode, reverse geocode, features, buildings | "Sample Town": synthetic, CC0, around 0.002°, 0.002° in open sea | Offline, deterministic. For demonstrations and tests only; every result says it is not a real place. |
| `nominatim` | geocode, reverse geocode | OpenStreetMap, ODbL | Public instance: 1 request per second, one at a time, no bulk geocoding (at most 100 per run here), an identifying User-Agent required. A self-hosted instance can be faster and has no cap. |
| `overpass` | features, buildings | OpenStreetMap, ODbL | One query at a time, a server-side timeout per query, a User-Agent required on the public instance. Partial answers are refused rather than used. |

The public OpenStreetMap services are shared community infrastructure. Responses are cached in the workspace, so repeating a query costs nothing. Large jobs still deserve a self-hosted instance, or at least the user's agreement first (the confirmation step).

**Configuration.** It is set by the user, in their MCP client or shell, never by you in a tool call:
- `GEOPRODUCT_USER_AGENT` is required for the public services;
- `GEOPRODUCT_NOMINATIM_URL` and `GEOPRODUCT_OVERPASS_URL` point at other instances;
- `GEOPRODUCT_ALLOWED_DIRS` lists folders whose files tools may read.

The repository's `docs/configuration.md` has the details.

**Other data.** The server doesn't search the web or call other services. Use your own tools or other MCP servers to find data, then bring it in as registered sources, fetched documents, files or records. Commercial providers are used only when the user has an agreement, and only through their terms.

## Licence duties to pass on

Keep and report every `attribution` line that tools return; the user must display them.

| Licence | What the user must do |
|---|---|
| **ODbL** (OpenStreetMap) | Credit "© OpenStreetMap contributors" with a link to the copyright page wherever the data or a map made from it is shown. A database derived from OpenStreetMap that is shared publicly must also be offered under ODbL (share-alike). Maps and images made from it ("produced works") need the credit but not share-alike. |
| **CC0 / public domain** | Nothing required; crediting is good practice |
| **CC BY 4.0** | Credit the source, link the licence, and indicate changes |
| **CC BY-SA 4.0** | As CC BY, and share adaptations under the same licence |
| **Open Government Licence or national equivalents** | Attribution statement as specified by the publisher |
| **No licence stated** | You may cite and link it. Reusing or republishing the data is doubtful: rate licensing 1 and tell the user. |
| **Terms that forbid reuse, storage or the intended use** | Licensing 0: the source is unusable for this purpose |

Common traps:
- **Commercial geocoders.** Their terms often forbid storing results, or showing them on another provider's map. Read the terms before using one as a source of stored coordinates.
- **Mixing licences.** Share-alike data (ODbL, CC BY-SA) combined with data under incompatible terms may not be publishable together. Flag it, rather than resolving it for the user.
- **Scraping.** A page you can read is not a licence to republish its data. `robots.txt` and site terms apply, and the fetch tool honours `robots.txt`.

This is practical guidance, not legal advice. When the user plans to publish or sell a dataset, tell them which licences are involved and suggest checking the terms.
