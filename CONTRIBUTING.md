# Contributing

Thanks for your interest. Bug reports, documentation fixes, new checks, stages and providers are all welcome. For anything larger than a small fix, please open an issue first so the approach can be agreed before you invest time.

## Ground rules

These are what the project is for, so changes that break them can't be accepted:

- **Never invent data.** Code and skill text must not create coordinates, source URLs, building links or attribute values. A value either comes from a source or computation recorded as evidence, or it stays missing or unresolved, with the reason.
- **Keep uncertainty visible.** Estimates, inferences, conflicts and uncertain matches stay labelled as such all the way to the export.
- **No code execution.** Pipelines are built from registered stages. Nothing may run code supplied by a spec, a tool argument or a fetched document.
- **Respect providers.** Use the shared throttle and cache, identify the client, and follow each service's usage policy. Don't add a paid service as a requirement.
- **No secrets in the repository.** Settings come from the environment, and secrets are never written to results, logs or disk.
- **Generic examples.** Examples and tests use the synthetic Sample Town or clearly generic data, not data about real places or organisations. The opt-in live tests are the one exception.

## Development setup

You need [uv](https://docs.astral.sh/uv/) and Git.

```bash
git clone https://github.com/Ppp2048/geoproduct-engineer-skill.git
cd geoproduct-engineer-skill
uv sync
```

The repository is a uv workspace:
- `packages/geoproduct-core` holds the schemas, engine, stages and providers;
- `packages/geoproduct-mcp` holds the MCP server;
- `skills/geoproduct-engineer` holds the Agent Skill.

[docs/architecture.md](docs/architecture.md) explains how they fit together.

## Checks

Every change must pass these, as CI does:

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run pytest
```

- **Tests need no network.**
  - Use the `fixture` provider, or an `httpx2.MockTransport` with service-shaped responses.
  - Tests that call real services go in `tests/live/`. They are marked `live`, and run only with `GEOPRODUCT_LIVE_TESTS=1` and `GEOPRODUCT_USER_AGENT`.
- **Test behaviour, not code that merely compiles.** A fix comes with a test that fails without it.
- **Keep the skill in step with the code.** `tests/skill/` fails when the skill names a tool, parameter, stage, check or error code that doesn't exist, or misses one that does. Update the skill's text with the code.
- **Regenerate the schemas after changing a contract:**
  ```bash
  uv run python -m geoproduct.schemas.export schemas/v1
  ```
  Changes within `geoproduct/v1` must be additive; see [docs/releasing.md](docs/releasing.md).

## Adding things

- **A check.** Add it to `validate.dataset`'s checks. The tool description and the skill are tested against that list.
- **A stage.** Register a typed function with `@stage`, then:
  - give it a parameter model and say whether it is retry-safe;
  - add it to the stage catalogue in `skills/geoproduct-engineer/references/pipeline-spec.md`;
  - test it on the fixture data.
- **A provider.** Implement the `Provider` interface in `geoproduct.providers`, then:
  - declare its capabilities, usage policy, licence and attribution;
  - send its requests through the shared HTTP client;
  - test it against a mock transport.

  Explain the provider's terms in the pull request, especially limits on storing or redistributing results.

## Pull requests

- Keep each pull request to one change, and describe what it does and how you tested it.
- Add a line to `CHANGELOG.md` under `[Unreleased]` for anything a user would notice.
- Record a significant design decision in [docs/decisions.md](docs/decisions.md), with its reason.

By contributing, you agree that your contribution is licensed under the [Apache License 2.0](LICENSE), as section 5 of the licence describes.

## Reporting problems

Use the issue templates for bugs and feature requests. Report security problems privately, as [SECURITY.md](SECURITY.md) describes.
