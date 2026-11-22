# Source discovery: which sources, and how far to trust them

Finding sources is your job; the server only records and checks them. The aim is a small set of sources that together cover existence, location and the requested attributes, each with a known licence and a reasoned rating.

## Source types, roughly in order of authority

| Type (`type` in `register_source`) | Typical use | Watch for |
|---|---|---|
| `official_government` | Registers of licensed facilities, schools, transport stops, addresses, boundaries | Update cycles that lag reality; categories defined for administration, not for maps |
| `official_organisation` | An operator's own list of sites, addresses and hours | Marketing pages without addresses; branches that closed but still appear |
| `open_data_portal` | Published datasets with metadata and a licence | Stale extracts; check the "last updated" date and the licence on the dataset itself |
| `osm` | Presence and geometry of mapped features | Uneven coverage and tagging; not an authority on official status |
| `public_api` | Services such as geocoders, transit feeds and registries with an API | Terms of use, rate limits, and whether results may be stored |
| `commercial_api` | Licensed data, only when the user has an agreement | Storage and redistribution restrictions; never a mandatory dependency |
| `document` | Reports, plans, annual reports, PDFs | Dated figures; excerpts are needed; PDF text isn't extracted, so excerpts stay `not_checked` |
| `user_file` | Data the user supplies | Unknown provenance; ask where it came from and how old it is |
| `other` | Anything else lawful | Justify the rating carefully |

## Steps

1. **List the facts you need.** Write them out: existence, name, category, location, address, and each requested attribute. For each one, decide what kind of source could state it.
2. **Search for the authoritative list first.** Useful query patterns:
   - "<entity type> register <region>";
   - "<region> open data <entity type>";
   - "list of <entity type> <region> site:<government domain>";
   - the regulator or ministry responsible for the entity type.

   Prefer machine-readable downloads (CSV, GeoJSON, API) over pages to scrape.
3. **Find the location source.** Check whether the authoritative list includes coordinates. If not, look for addresses on it or on the organisations' own sites. Plan OSM as the discovery and geometry layer.
4. **Check the licence and terms** of each source before relying on it. "No licence stated" means you can cite and link it, but republishing its data may not be allowed: rate licensing low and tell the user. Don't scrape against a site's terms or `robots.txt`, and don't bypass logins or paywalls. `fetch_source_data` refuses `robots.txt`-disallowed pages for this reason.
5. **Rate each source** on the seven criteria, with a one-line rationale each (rubric in [../references/source-evaluation.md](../references/source-evaluation.md)). Rate what you observed, not the source's reputation in general: a national register that is two years old is not "fresh".
6. **Register** each with `register_source`: the exact URL as found (never reconstructed or guessed), the publisher, the licence, `data_as_of`, a coverage note and the ratings. Then `fetch_source_data` for those you will quote or load.
7. **Build a coverage matrix** (facts × sources) and look for gaps: an attribute no source states, or an area one source doesn't cover. Fill a gap with another source, or report it.
8. **Stop** when each needed fact has at least one usable source, and the important ones (existence, location) have two independent ones where they exist.

## Judgement calls

- **Independence.** Two sites that copy the same register are one source, not two. Note it in the coverage note.
- **Conflicting sources.** Keep both. The confidence and conflict handling downstream depends on seeing the disagreement.
- **Freshness versus authority.** A recent organisation page can beat an older official register for opening hours, but rarely for official status. Say which you preferred, and why.
- **Nothing usable.** If no lawful source states something, the answer is "unknown". Report it, and suggest how the user could obtain it, for example by asking the operator or through a data request.

## What the tools add

- `register_source` computes the score and tier from your ratings, so a typed-in score can't inflate a source, and licensing 0 makes it `unusable`.
- `fetch_source_data` stores a hashed copy and its text, which makes your evidence verifiable later and detects when a page changes.

## Without the server

Keep the same table of sources, ratings and licences in your answer. `scripts/score_source.py` computes the score and tier exactly as the server would.
