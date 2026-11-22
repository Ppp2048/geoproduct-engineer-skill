# Versioning and releases

## What is versioned

| Part | Version | Where it is set |
|---|---|---|
| `geoproduct-core` and `geoproduct-mcp` | One shared version, released together | `version` in both `packages/*/pyproject.toml`. The server pins the exact core (`geoproduct-core==X`) because it is tested against that core only. |
| The Agent Skill | The same version | `metadata.version` in `skills/geoproduct-engineer/SKILL.md` |
| Data contracts | `geoproduct/v1` | `schema_version` in every dataset, spec and run, and the schemas in `schemas/v1/` |

`tests/release/` fails if these versions disagree.

**Package versions** follow [Semantic Versioning](https://semver.org/):
- **Before 1.0**, a minor release (`0.2.0`) may contain breaking changes, and the changelog names each one. Patch releases (`0.1.1`) only fix things.
- **From 1.0**, a breaking change needs a new major version.

The following count as breaking:
- removing or renaming a tool, parameter, stage, stage option, CLI command or environment variable;
- changing what a successful call returns, in a way that breaks a client;
- tightening a default limit that ends up refusing input it used to accept.

**The contract version changes on its own schedule.** Within `geoproduct/v1`, changes are additive: new optional fields and new enum values that older readers can ignore. A change that makes old files invalid, or changes what a field means, needs `geoproduct/v2`. Readers then have to accept both for at least one minor release. Specs, datasets and runs written by an older release must keep loading.

Development versions carry a `.devN` suffix, and the changelog's top section is then `[Unreleased]`.

## Release checklist

Releasing is done by hand. Nothing in CI publishes.

**Once, before the first release:**

- [ ] On GitHub, enable **private vulnerability reporting** in the repository's security settings, so `SECURITY.md`'s reporting route works.
- [ ] On PyPI, set up the account that will own `geoproduct-core` and `geoproduct-mcp`, with two-factor authentication. Both names were unclaimed on 2026-11-22; the first upload claims them.
- [ ] Decide whether to publish from your machine with an API token scoped to these projects, or to add a trusted-publishing workflow later.

**Every release:**

1. **Choose the version** by the rules above. Set it in both `packages/*/pyproject.toml`, in the core pin of `packages/geoproduct-mcp/pyproject.toml`, and in `SKILL.md`. Then run `uv lock`.
2. **Update `CHANGELOG.md`.** Rename `[Unreleased]` to `[X.Y.Z] - YYYY-MM-DD`, and check that every breaking change is listed.
3. **Run the checks locally:**
   ```bash
   uv sync --locked
   uv run ruff format --check . && uv run ruff check . && uv run mypy && uv run pytest
   ```
4. **Run the live tests** from a network that reaches the public services:
   ```bash
   GEOPRODUCT_LIVE_TESTS=1 GEOPRODUCT_USER_AGENT="my-project/0.1 (you@example.org)" uv run pytest tests/live -m live
   ```
5. **Build and check the packages:**
   ```bash
   uv build --all-packages --out-dir dist
   python .github/scripts/check_dist.py dist
   ```
6. **Try the wheels in a clean environment.** Run them from outside the repository, then follow the README's quick start against the installed packages.
7. **Commit**, push, and wait for CI to pass on `main`. The `package` job builds the same artefacts and uploads them as `dist`.
8. **Tag the release:** `git tag -a vX.Y.Z -m "vX.Y.Z"` (or `-s` to sign it), then `git push origin vX.Y.Z`.
9. **Publish:**
   - `uv publish dist/*`, using the token or trusted publisher chosen above.
   - Upload `geoproduct-core` first. `geoproduct-mcp` depends on it, so publishing the server first would leave it uninstallable.
10. **Create the GitHub release** from the tag. Paste the changelog section, and attach a zip of `skills/geoproduct-engineer/` for people who install the skill by hand.
11. **Start the next cycle.** Bump to the next `.dev0` version and add a new `[Unreleased]` section.

After publishing, check that `uvx geoproduct-mcp --help` works in a fresh environment. Then update the README and [configuration.md](configuration.md): the `uv run --directory …` instructions become `uvx geoproduct-mcp`.
