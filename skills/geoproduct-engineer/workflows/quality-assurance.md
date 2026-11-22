# Quality assurance: is this dataset good enough to deliver?

Quality is not one number. It is whether the dataset is fit for the user's purpose, judged from scored records, validation findings and an honest account of what is unknown. This workflow runs at the end of every other one.

## Steps

1. **Score records:** `score_confidence(dataset_id)`. Each record gets a level (`high` ≥ 0.75, `medium` ≥ 0.5, `low` ≥ 0.25, otherwise `unresolved`) with reasons, from four parts:
   - evidence (0.35);
   - source quality (0.2);
   - location precision and building match (0.3);
   - consistency (0.15).

   A record without geometry is `unresolved` whatever its score. Details are in [../references/evidence-and-confidence.md](../references/evidence-and-confidence.md).
2. **Validate:** `validate_dataset(dataset_id, area=..., rules=...)`. The checks are `coordinates`, `crs`, `geometry`, `missing_geometry`, `duplicates`, `within_area` (needs `area`), `outliers`, `evidence`, `conflicts` and `rules` (needs `rules`); all run by default. The result has:
   - **score:** the share of records without errors, where records with only warnings count three quarters. Errors that concern the whole dataset halve it.
   - **metrics:** `error_share`, `unresolved_share`, `conflicting_share`, `evidence_coverage` (the share of stated facts that cite evidence), and counts by status, confidence, location kind and match status.
   - **findings:** each with a check, a severity, the record and a message.
3. **Write the report:** `generate_quality_report(report_dataset_id)` gives Markdown for people and JSON for tools.
4. **Judge fitness for purpose.** Starting points, to adjust with the user:

   | Use | Score | Unresolved | Conflicting | Evidence coverage |
   |---|---|---|---|---|
   | Published map or public dataset | ≥ 0.9 | ≤ 5% | 0 unreviewed | ≥ 0.95 |
   | Internal decision support | ≥ 0.8 | ≤ 15% | reviewed | ≥ 0.8 |
   | Exploration or first draft | any | reported | reported | reported |

5. **Act on the findings,** in order of severity:
   - **Errors:** fix with evidence (re-geocode, correct the source, remove a confirmed duplicate) or drop the record with a reason.
   - **Warnings:** investigate the cheap ones; report the rest.
   - **Unresolved and conflicting records:** do targeted research when the user needs them; otherwise list them.

   Rerun steps 1–3 after changes.
6. **Sign off** only when each item is either fixed or listed in the report with its reason. A dataset with known gaps, honestly reported, is deliverable. One with hidden gaps is not.

## Quality gates in pipelines

A `quality.gate` stage after `validate.dataset` stops the run when thresholds are missed:

```yaml
- id: gate
  uses: quality.gate
  input: validation
  with: {min_score: 0.8, max_unresolved_share: 0.15, min_evidence_coverage: 0.8, fail_on_checks: [coordinates, crs, geometry]}
```

Nothing after the gate runs, so put `report.quality` before it. When the gate fails (`quality_gate_failed`), read the report, fix the data or argue for a different threshold with the user, then resume the run with `run_geo_pipeline(run_id, params=...)`.

## Pitfalls

- **Improving the score by deleting.** Dropping hard records raises the score and lowers the coverage. Report dropped records and why.
- **Treating `probable` as matched** in the counts. Report match statuses as they are.
- **Evidence coverage of 100% with weak sources.** Coverage says facts cite something, not that the something is good. Look at the source tiers too.

## Without the server

Apply the same checklist by hand: coordinates sane (`scripts/check_dataset.py`), every fact with a source, unknowns listed, duplicates reviewed. Say that no automated validation ran.
