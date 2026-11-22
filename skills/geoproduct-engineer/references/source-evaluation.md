# Source evaluation

Rate every source on seven criteria, each 0 to 3 with a one-line rationale based on what you observed. The server (or `scripts/score_source.py`) computes the score as a weighted mean scaled to 0–1. You never type the score yourself, so a source can't be talked up.

## Criteria and weights

| Criterion | Weight | 3 | 2 | 1 | 0 |
|---|---|---|---|---|---|
| `authority` | 0.20 | The body legally or operationally responsible for the data | A recognised organisation describing itself, or a curated public dataset | A secondary compiler or aggregator | Unknown origin |
| `accuracy` | 0.20 | Verified against other sources, or known to be maintained carefully | Mostly consistent with other sources | Visible errors or inconsistencies | Contradicted by better sources |
| `freshness` | 0.10 | Updated recently enough for this data (days for hours, a year for buildings) | A little old for the purpose | Clearly dated | No date, or obsolete |
| `coverage` | 0.10 | The whole area and all the entities and attributes needed | Most of them | A small part | Not the area or entities needed |
| `licensing` | 0.15 | An open licence that allows the intended use | Usable with conditions you can meet (attribution, share-alike) | Unclear: can be cited, but reuse is doubtful | Forbids the intended use. **Makes the source unusable whatever else it scores.** |
| `geographic_precision` | 0.15 | Building, entrance or surveyed coordinates | Address points | Street, neighbourhood or centroid level | No location information |
| `verifiability` | 0.10 | A stable URL or identifier, and machine-readable | A stable page anyone can check | Hard to re-find, such as a changing page or a document without an identifier | Can't be checked by anyone else |

## Tiers

The score is the sum over criteria of weight × rating, divided by 3.

| Tier | Score |
|---|---|
| `high` | ≥ 0.75 |
| `medium` | ≥ 0.5 |
| `low` | below 0.5 |
| `unusable` | licensing rated 0 |

Confidence scoring uses the best cited source's score; validation and the report list the tiers.

## Typical ratings

These are starting points; rate what you actually see.

| Source | Authority | Accuracy | Freshness | Coverage | Licensing | Precision | Verifiability |
|---|---|---|---|---|---|---|---|
| A ministry's register of licensed facilities, as an open CSV | 3 | 2–3 | 1–3 | 3 | 3 | 1–2 | 3 |
| A hospital's own "contact us" page | 2–3 | 2 | 2–3 | 1 | 1–2 | 2 | 2 |
| OpenStreetMap features | 1–2 | 2 | 2 | 1–3 | 2 (ODbL) | 2–3 | 3 |
| A news article mentioning a new clinic | 1 | 1–2 | 2–3 | 1 | 1 | 1 | 2 |
| A user's spreadsheet of unknown origin | 1 | 1 | 0–1 | 2 | 2 | 1–2 | 1 |

## Rationales

One concrete line each, about this source. For example:
- "Register maintained by the health ministry under the facilities act" (authority);
- "Updated 2026-09-30; the user needs current data" (freshness);
- "No licence on the portal page; data can be cited but republishing is unclear" (licensing).

Avoid generic praise like "reliable source".

## Independence

Two sources count as independent only if neither copies the other. An aggregator republishing the register adds no independent evidence; note it in the coverage note and cite the register itself where you can.

## Running the calculation without the server

```bash
python scripts/score_source.py --authority 3 --accuracy 2 --freshness 1 --coverage 3 --licensing 3 --geographic-precision 2 --verifiability 3
```

It prints the score and tier as JSON, with the same weights as the server.
