# Evaluating the skill

The skill's test cases are in [`skills/geoproduct-engineer/evals/`](../skills/geoproduct-engineer/evals/), one folder per case, in the layout `claude plugin eval` reads:

- `prompt.md`: the run settings, then the user's task, worded as a user would write it;
- `case.yaml`: the case's name, what a good result looks like, and any scaffold script;
- `graders/`: one assertion per file;
- `files/`: the input files, when the case has any.

There are two ways to run them:

- **`claude plugin eval`** runs each case in its own top-level session. That session writes its deliverables itself.
- **By hand, with an orchestrator.** One session (the orchestrator) starts a run agent per case, collects what each returns, and grades it. This page sets the rules for this way. They came out of audit GP-19 ([audit-2026-11.md](audit-2026-11.md)), where a run agent was refused a report write and wrote the file through a shell command instead.

## Roles

| Role | Who | Does | Does not |
|---|---|---|---|
| Orchestrator | The session running the evaluation | Prepares each run folder, sends the run prompt, saves the files the run agent returns as text, records provenance and timing, grades or starts a grader | Change what a run agent returned, or bypass a tool restriction |
| Run agent | A subagent with the skill | Does the task. Has the toolkit write data files (pipeline runs, exports). Returns every report, audit, plan or note as text. | Write report files itself, or retry a refused action another way |
| Grader | The orchestrator, or a grader subagent | Checks each assertion against the saved outputs. A grader subagent returns its grading as text. | Write `grading.json` itself when it is a subagent |

## The reporting rule

1. **Run agents and grader agents return reports and findings as text** in their final response. They do not write report files: no `report.md`, `audit.md`, `plan.md`, notes or `grading.json`.
2. **The orchestrator saves the returned text** to the file the task names, unchanged, and records that it did (see [Provenance](#provenance)).
3. **A refusal ends the attempt.** If a tool refuses an action (a write, a path, a command), the agent does not retry it with another tool, a shell command or a script. It reports the refusal (the tool, what it tried, the message) and returns the content as text.
4. **Every run prompt states this rule.** Build run prompts from the template below and keep the rule's wording.
5. **Nobody bypasses a tool restriction**, the orchestrator included.

Data files are different: the toolkit writes pipeline runs and exports, and that is the toolkit doing its job. A run agent may also write the working files the toolkit reads, such as pipeline specs, if its tools allow it. If they refuse, rule 3 applies.

## The run prompt

The orchestrator fills in the angle brackets and sends this to the run agent. The task is the body of the case's `prompt.md`, without its settings block.

```text
Execute this evaluation task.

- Skill: <absolute path to skills/geoproduct-engineer>
- Task: <the case's task>
- Input files: <absolute paths, or "none">
- Working folder: <run folder>/outputs/. Run commands here. The toolkit may write its run folders and exports here.
- Deliverables: <what the task asks for, for example "the GeoJSON export and report.md">

Reporting rule (docs/evaluations.md in the repository):
- Return every report, audit, plan or note the task asks for as text in your final response. Put each one in a block that starts with a line "=== FILE: <file name> ===" and ends with a line "=== END FILE ===". Do not write these files yourself: the orchestrator saves them and records that it did.
- If a tool refuses an action (a write, a path, a command), do not retry it with another tool, a shell command or a script. Report the refusal in your final response, with the tool, what you tried and the message, and return the content as text instead.
- End with a list of the files you created or had the toolkit create, and how each was made.
```

A grader subagent gets the same reporting rule, with the grading as its one block.

## Saving and checking a run

When the run agent finishes, the orchestrator:

1. **Checks the outputs folder before saving anything.** A report file that is already there was written by the run agent, against the rule. Record it under `workarounds` in `provenance.json`, and don't grade it as the agent's delivered report.
2. **Saves each `=== FILE ===` block** to `outputs/<file name>` with its own write tool, unchanged.
3. **Saves the run prompt** as `run_prompt.md` in the run folder.
4. **Writes `timing.json`** from the agent's completion notice (`total_tokens`, `duration_ms`).
5. **Writes `provenance.json`** (below).
6. **Grades** each assertion against the saved outputs and saves `grading.json`, with `text`, `passed` and `evidence` for each expectation. Its `notes` say who graded and who saved the file.

A run with a workaround is not clean. Report it, and run the case again once the cause is fixed.

## Provenance

Each run folder holds `provenance.json`:

```json
{
  "case": "sample-town-hospitals-to-buildings",
  "iteration": 3,
  "run_prompt": "run_prompt.md",
  "run_agent": "subagent <agent id>",
  "files": [
    {
      "path": "outputs/report.md",
      "produced_by": "run agent",
      "delivered_as": "text in the final response",
      "saved_by": "orchestrator, with its Write tool"
    },
    {
      "path": "outputs/sample-town-hospitals.geojson",
      "produced_by": "geoproduct CLI export stage, run <run id>",
      "delivered_as": "file",
      "saved_by": "geoproduct CLI"
    }
  ],
  "refusals": [],
  "workarounds": [],
  "graded_by": "orchestrator"
}
```

- `files` lists every deliverable: who produced it, how it reached the run folder, and who saved it.
- `refusals` lists each refusal the run agent reported: the tool, the action and the message.
- `workarounds` lists any refused action that was done another way. It should be empty.

## Layout

Results go in `skills/geoproduct-engineer-workspace/`, which git ignores:

```text
skills/geoproduct-engineer-workspace/
└── iteration-<N>/
    └── <case>/
        ├── eval_metadata.json      # the case name, its task and its assertions
        └── with_skill/
            ├── outputs/            # the deliverables
            ├── run_prompt.md
            ├── provenance.json
            ├── timing.json
            └── grading.json
```

Keep the case folder names from one iteration to the next, so results can be compared.
