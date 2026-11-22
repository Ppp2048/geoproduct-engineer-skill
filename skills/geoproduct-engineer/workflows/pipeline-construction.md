# Pipeline construction and execution

A pipeline is a declarative spec of registered stages. It runs in the background, checkpoints every stage, retries what is safe to retry, resumes after failures, and leaves a run record that shows how the data was made. Write one when work will be repeated, is long, or must be reproducible.

## Steps

1. **Settle the method first.** Explore with direct tool calls until you know the sources, tags, thresholds and rules. A pipeline encodes decisions; it doesn't make them.
2. **Write the spec.** The format and the stage catalogue are in [../references/pipeline-spec.md](../references/pipeline-spec.md). In outline:

   ```yaml
   apiVersion: geoproduct/v1
   kind: Pipeline
   name: clinics-sample-town
   description: Clinics in Sample Town, matched to buildings, validated and exported.
   params:
     provider: fixture
     area: {name: Sample Town, bbox: [0.0015, 0.0015, 0.0097, 0.0097]}
     output_name: clinics
   stages:
     - id: clinics
       uses: collect.features
       with: {provider: {param: provider}, area: {param: area}, tags: {amenity: [clinic, hospital]}}
     - id: tidy
       uses: normalize.records
       input: clinics
     - id: merged
       uses: resolve.deduplicate
       input: tidy
     - id: buildings
       uses: collect.buildings
       with: {provider: {param: provider}, area: {param: area}}
     - id: matched
       uses: match.buildings
       input: merged
       with: {buildings: {stage: buildings}}
     - id: scored
       uses: quality.confidence
       input: matched
     - id: validation
       uses: validate.dataset
       input: scored
       with: {area: {param: area}, name: {param: output_name}}
     - id: report
       uses: report.quality
       input: validation
       with: {name: {param: output_name}}
     - id: export
       uses: export.geojson
       input: scored
       with: {name: {param: output_name}}
   ```

   Put everything a user might change (area, provider, thresholds, output names) in `params` and reference it with `{param: name}`. Reference earlier stages' outputs with `{stage: id}`; only earlier stages can be referenced.
3. **Check it:** `create_geo_pipeline(spec=...)`. Every problem is reported at once: unknown stages (with suggestions), wrong input kinds, invalid parameters and bad references. The result also gives:
   - the plan, stage by stage;
   - the estimated external requests;
   - warnings, such as a provider that is not configured.

   The check records a pending run, whose `run_id` you can start later.
4. **Confirm costs.** If the estimate needs confirmation (more than 25 requests, or a stage without a limit), show the user the per-stage estimate and wait for their go-ahead. Then call `run_geo_pipeline(run_id=..., confirm=true)`.
5. **Run:** `run_geo_pipeline(run_id=...)` returns immediately. Poll `get_pipeline_status(run_id)` at an interval that suits the work: seconds for the fixture, a minute or more for live geocoding. Report progress to the user if it is long.
6. **Collect the results:** `get_pipeline_results(run_id)` lists each finished stage's output as a dataset id (`<run_id>/<stage_id>`) with a preview. Use those ids with any tool, for example to export a copy elsewhere or to inspect findings.
7. **When it fails,** the status gives the failed stage and the error with its code and hint.
   - Fix the cause: a parameter, a provider setting, or a threshold agreed with the user.
   - Call `run_geo_pipeline(run_id=..., params={...})`. Finished stages whose inputs and parameters are unchanged are reused, not rerun.
   - Transient provider errors are retried automatically, so a failure after retries usually needs a longer wait or a smaller request.
8. **To stop a run,** call `cancel_geo_pipeline(run_id)`. It stops between items. A cancelled run can be resumed later.
9. **Hand over** the spec file with the results. It is the reproducible description of how the data was made. The run folder holds the spec as run, an event log, and each stage's manifest with its parameters, hashes and provider use.

## In CLI mode

The same spec runs with the `geoproduct` command:

```bash
geoproduct plan spec.yaml --param output_name=clinics
geoproduct run spec.yaml --param output_name=clinics
geoproduct status run_…
geoproduct resume run_… --param max_stop_distance_m=40
```

Add `--allow <folder>` when the spec reads a file outside the workspace, and `--json` for machine-readable output. The CLI runs in the foreground, gives no request estimate and asks for no confirmation. Before running against public services, estimate the requests yourself (one per collect stage, one per record to geocode) and ask the user if the number is large.

## Common spec errors

| Error | Cause |
|---|---|
| `unknown_stage` | A misspelt stage name. The message suggests the closest names, and `geoproduct://stages` lists them all. |
| Input kind mismatch | For example, `match.buildings` given a buildings dataset as its input: the input is the records, and the buildings come as `{stage: …}` |
| Parameter not allowed | Specs reject unknown fields, so check the spelling against the stage's parameters |
| Reference to a later stage | Reorder the stages: references only point backwards |
| Undeclared `{param: x}`, or a `params` value the spec doesn't declare | Declare it under `params` with a default |

## Without the server

Write and hand over the spec anyway; it is the most useful thing to leave behind. Say it has not been checked, and name the command that checks it: `geoproduct plan spec.yaml`.
