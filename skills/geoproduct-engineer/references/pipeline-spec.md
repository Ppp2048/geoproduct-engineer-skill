# Pipeline spec reference (`geoproduct/v1`)

A pipeline spec is YAML or JSON data. It names registered stages and their parameters; there is no templating and no code. The full JSON Schema is the resource `geoproduct://schemas/v1/pipeline-spec`, and `geoproduct://stages` (or `geoproduct stage <name>`) gives each stage's parameter schema.

## Contents

- [Top level](#top-level)
- [Stages](#stages)
- [References](#references)
- [Stage catalogue](#stage-catalogue)
- [Patterns](#patterns)

## Top level

| Field | Required | Meaning |
|---|---|---|
| `apiVersion` | yes | `geoproduct/v1` |
| `kind` | yes | `Pipeline` |
| `name` | yes | Lower-case slug, for example `clinics-sample-town` |
| `description` | no | What it produces and from what |
| `params` | no | Declared parameters and their defaults. Only declared names can be set at run time. |
| `stages` | yes | 1 to 100 stages, in execution order |

## Stages

| Field | Meaning |
|---|---|
| `id` | Lower-case slug, unique in the spec |
| `uses` | A registered stage name |
| `input` | The id of an earlier stage whose output is this stage's input. Collect stages have none. |
| `with` | The stage's parameters |
| `timeout_s` | Cooperative time limit for the stage |
| `retry` | `{max_attempts: 3, backoff_s: 1, max_backoff_s: 30}`; only retry-safe stages and transient errors are retried |
| `max_reject_share` | Fail the stage if more than this share of items is rejected (for example 0.2) |
| `description` | Why the stage is there |

## References

- `{param: name}` anywhere in `with` takes the value of a declared parameter.
- `{stage: id}` passes an earlier stage's output as a parameter, for example the buildings for `match.buildings`, or the reference layer for `enrich.nearby`.
- References only point to earlier stages, so a spec cannot contain a cycle.

## Stage catalogue

Kinds: `records`, `buildings`, `report`, `files`. A parameter in **bold** is required; defaults are in brackets.

| Stage | In → out | Parameters |
|---|---|---|
| `collect.records` | – → records | **`records`**, `sources`, `evidence`. Records you researched, with their sources and evidence. |
| `collect.file` | – → records | **`path`**, `format` [auto], `title`, `licence`, `category`, `columns`, `max_bytes` [50 MB], `max_rows` [100000] |
| `collect.features` | – → records | **`provider`**, **`area`**, **`tags`**, `limit` [1000], `allow_truncated` [false] |
| `collect.buildings` | – → buildings | **`provider`**, **`area`**, `limit` [10000], `allow_truncated` [false] |
| `combine.records` | records → records | **`add`**: a list of `{stage: id}`. A repeated record id is rejected. |
| `normalize.records` | records → records | `categories`, `aliases`, `coordinate_decimals` [7] |
| `resolve.deduplicate` | records → records | `name_threshold` [0.88], `max_distance_m` [150], `site_keys` [none], `organisation_keys` [website, phone, email, brand:wikidata, operator:wikidata], `wikidata_sites`, `wikidata_organisations` (Wikidata items classified as one establishment or an organisation), `id_keys` (older single list), `same_category` [true] |
| `geocode.records` | records → records | **`provider`**, `country_codes`, `bias` (an area), `min_score` [0.6], `ambiguity_margin` [0.05], `use_names` [true], `overwrite` [false] |
| `match.buildings` | records → records | **`buildings`** (`{stage: id}`), `max_distance_m` [30], `min_radius_m` [5], `name_threshold` [0.85], `ambiguity_margin` [0.1], `max_candidates` [5], `assign_geometry` [none] |
| `enrich.nearby` | records → records | **`reference`** (`{stage: id}`), **`name`** (the attribute to add), `radius_m` [500], `categories`, `max_ids` [10] |
| `enrich.within` | records → records | **`reference`**, **`name`**, `categories`, `label` [name; or id] |
| `enrich.measure` | records → records | `measures` [area, length] |
| `quality.confidence` | records → records | `high` [0.75], `medium` [0.5], `low` [0.25] |
| `validate.dataset` | records → report | `name`, `checks` [all], `area`, `rules`, `outlier_distance_m` [5000], `duplicate_distance_m` [25], `max_findings` [1000] |
| `quality.gate` | report → report | `min_score`, `max_error_share`, `max_unresolved_share`, `max_conflicting_share`, `min_evidence_coverage`, `fail_on_checks` |
| `report.quality` | report → files | `name`, `copy_to`, `overwrite` [false], `max_findings` [50] |
| `export.geojson` | records → files | `name`, `copy_to`, `overwrite` [false], `include_provenance` [true], `description` |
| `export.csv` | records → files | `name`, `copy_to`, `overwrite` [false], `attributes` [all], `geometry` [point; or wkt, none] |

Validation rules in `validate.dataset` take the form `{name, kind: near | within | not_within, reference: {stage: id}, max_distance_m (near only), categories, reference_categories, severity: error | warning}`.

Export stages are never retried automatically. They write into the run folder, and `copy_to` must be inside the workspace or an allowed folder.

## Patterns

**A researched list, combined with map features:**

```yaml
apiVersion: geoproduct/v1
kind: Pipeline
name: libraries-combined
params:
  provider: fixture
  area: {bbox: [0.0015, 0.0015, 0.0097, 0.0097]}
stages:
  - id: listed
    uses: collect.records
    with:
      sources:
        - {id: src-register, title: Sample library register, type: official_government, url_or_ref: "https://data.example.org/libraries.csv", licence: CC-BY-4.0}
      evidence:
        - {id: ev-1, source_id: src-register, supports: [name, address], excerpt: "Community Library, 14 Second Avenue"}
      records:
        - {id: lib-1, name: Community Library, category: amenity=library, address: {full: 14 Second Avenue}, evidence_ids: [ev-1]}
  - id: mapped
    uses: collect.features
    with: {provider: {param: provider}, area: {param: area}, tags: {amenity: library}}
  - id: together
    uses: combine.records
    input: listed
    with: {add: [{stage: mapped}]}
  - id: tidy
    uses: normalize.records
    input: together
  - id: merged
    uses: resolve.deduplicate
    input: tidy
  - id: located
    uses: geocode.records
    input: merged
    with: {provider: {param: provider}, bias: {param: area}}
```

**A gate with a report that survives it:** `validate.dataset`, then `report.quality` (input: the validation), then `quality.gate` (input: the validation), then the exports (input: the scored records). The gate stops the exports, not the report.

**A file from outside the workspace:** pass its folder with `--allow` (CLI), set `GEOPRODUCT_ALLOWED_DIRS` (server), or copy the file into the workspace.

The repository's `examples/pipelines/` has four complete specs: `hospitals.yaml`, `commercial-buildings.yaml`, `transport.yaml` and `csv-enrich.yaml`.
