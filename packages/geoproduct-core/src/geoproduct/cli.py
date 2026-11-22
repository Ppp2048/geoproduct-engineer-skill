"""The ``geoproduct`` command: list stages, check, run, resume and inspect pipelines.

Exit codes: 0 success, 1 failed run or error, 2 usage error, 3 run cancelled,
130 interrupted (Ctrl+C; the run is recorded as cancelled and can be resumed).
"""

import argparse
import json
import os
import re
import sys
from collections.abc import Sequence
from typing import Any

from pydantic import JsonValue

from geoproduct.engine import Runner, load_spec_file
from geoproduct.engine.spec import with_params
from geoproduct.errors import GeoProductError
from geoproduct.providers.registry import builtin_providers
from geoproduct.schemas import ErrorCode, RunRecord, RunStatus
from geoproduct.workspace import DEFAULT_WORKSPACE, ENV_ALLOWED_DIRS, ENV_WORKSPACE, Workspace

EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_CANCELLED, EXIT_INTERRUPTED = 0, 1, 2, 3, 130


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="geoproduct",
        description="Run evidence-backed geospatial data pipelines.",
        epilog=(
            "examples:\n"
            "  geoproduct stages\n"
            "  geoproduct plan examples/pipelines/hospitals.yaml\n"
            "  geoproduct run examples/pipelines/hospitals.yaml --param output_name=clinics\n"
            "  geoproduct run examples/pipelines/csv-enrich.yaml --allow examples/data\n"
            "  geoproduct status RUN_ID"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--workspace",
        help=f"folder for runs and outputs (default: ${ENV_WORKSPACE} or {DEFAULT_WORKSPACE})",
    )
    parser.add_argument(
        "--allow",
        action="append",
        default=[],
        metavar="DIR",
        help=f"also allow reading and exporting in DIR (repeatable; adds to ${ENV_ALLOWED_DIRS})",
    )
    parser.add_argument("--json", action="store_true", help="print JSON instead of text")
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    commands.add_parser("stages", help="list the registered stages")
    describe = commands.add_parser("stage", help="describe one stage and its parameters")
    describe.add_argument("name")

    for name, text in (("plan", "check a pipeline without running it"), ("run", "run a pipeline")):
        command = commands.add_parser(name, help=text)
        command.add_argument("spec", help="pipeline file (YAML or JSON)")
        _param_option(command)

    resume = commands.add_parser("resume", help="continue a failed or cancelled run")
    resume.add_argument("run_id")
    _param_option(resume)

    for name, text in (("status", "show a run"), ("cancel", "ask a run to stop")):
        commands.add_parser(name, help=text).add_argument("run_id")
    commands.add_parser("runs", help="list runs in the workspace")
    return parser


def _param_option(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "--param",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="set a declared pipeline parameter; VALUE is JSON, or text if not valid JSON",
    )


def parse_params(items: Sequence[str]) -> dict[str, JsonValue]:
    params: dict[str, JsonValue] = {}
    for item in items:
        name, sep, raw = item.partition("=")
        if not sep or not name.strip():
            raise GeoProductError(
                ErrorCode.invalid_input,
                f"--param needs NAME=VALUE, got '{item}'.",
                hint="For example --param area='{\"bbox\": [0, 0, 1, 1]}' or --param name=text.",
            )
        try:
            value: JsonValue = json.loads(raw)
        except json.JSONDecodeError:
            value = raw
        params[name.strip()] = value
    return params


def make_workspace(args: argparse.Namespace) -> Workspace:
    env = Workspace.from_env()
    root = args.workspace or os.environ.get(ENV_WORKSPACE) or DEFAULT_WORKSPACE
    return Workspace(root, [*env.allowed_dirs, *args.allow])


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    args = build_parser().parse_args(argv)
    try:
        return _dispatch(args)
    except GeoProductError as exc:
        _print_error(exc, args.json)
        return EXIT_FAILED
    except KeyboardInterrupt:
        print("\nInterrupted. The run was recorded as cancelled; resume it to continue.")
        return EXIT_INTERRUPTED


def _dispatch(args: argparse.Namespace) -> int:
    workspace = make_workspace(args)
    runner = Runner(workspace, providers=builtin_providers(workspace))
    match args.command:
        case "stages":
            if args.json:
                return _emit([d.describe() for d in runner.registry])
            for d in runner.registry:
                flow = f"{d.input or '-'} -> {d.output}"
                print(f"{d.name:22} {flow:22} {_first_sentence(d.summary)}")
            return EXIT_OK
        case "stage":
            return _emit(runner.registry.get(args.name).describe())
        case "plan":
            spec = with_params(load_spec_file(args.spec), parse_params(args.param))
            plan = runner.plan(spec)
            if args.json:
                return _emit(plan.describe())
            print(f"Pipeline '{spec.name}' is valid: {len(plan.stages)} stage(s).")
            for planned in plan.stages:
                after = ", ".join(planned.dependencies) or "-"
                print(f"  {planned.id:16} {planned.definition.name:22} after: {after}")
            return EXIT_OK
        case "run":
            spec = with_params(load_spec_file(args.spec), parse_params(args.param))
            record = runner.create(spec)
            if not args.json:
                print(f"Run {record.run_id} started ({spec.name}).", flush=True)
            return _report(runner, runner.execute(record.run_id), args.json)
        case "resume":
            record = runner.resume(args.run_id, params=parse_params(args.param) or None)
            return _report(runner, record, args.json)
        case "status":
            return _report(runner, runner.status(args.run_id), args.json)
        case "cancel":
            record = runner.cancel(args.run_id)
            if args.json:
                return _emit(record.model_dump(mode="json"))
            print(f"Cancellation requested for {record.run_id} (status: {record.status.value}).")
            return EXIT_OK
        case "runs":
            records = [runner.status(run_id) for run_id in runner.store.run_ids()]
            if args.json:
                return _emit([_run_summary(r) for r in records])
            for r in records:
                created = f"{r.created_at:%Y-%m-%d %H:%M}"
                print(f"{r.run_id}  {r.status.value:10} {r.pipeline_name:24} {created}")
            return EXIT_OK
    raise AssertionError(args.command)


def _report(runner: Runner, record: RunRecord, as_json: bool) -> int:
    code = {RunStatus.succeeded: EXIT_OK, RunStatus.cancelled: EXIT_CANCELLED}.get(
        record.status, EXIT_FAILED
    )
    if record.status in (RunStatus.pending, RunStatus.running):
        code = EXIT_OK
    if as_json:
        _emit(record.model_dump(mode="json"))
        return code
    print(f"Run {record.run_id} ({record.pipeline_name}): {record.status.value}")
    for state in record.stages:
        counts = state.counts
        line = f"  {state.stage_id:16} {state.status.value:10}"
        if state.status.value in ("succeeded", "skipped"):
            line += f" in {counts.input:>6}  out {counts.output:>6}"
            if counts.rejected:
                line += f"  rejected {counts.rejected}"
        print(line)
        if state.error is not None:
            print(f"      {state.error.code.value}: {state.error.message}")
            for problem in _listed(state.error.details):
                print(f"        - {problem}")
            if state.error.hint:
                print(f"      hint: {state.error.hint}")
    if record.error is not None and not any(s.error for s in record.stages):
        print(f"  {record.error.code.value}: {record.error.message}")
    print(f"  folder: {runner.store.run_dir(record.run_id)}")
    if record.status in (RunStatus.failed, RunStatus.cancelled):
        print(f"  resume: geoproduct resume {record.run_id}")
    return code


_SENTENCE_END = re.compile(r"(?<!\be\.g)(?<!\bi\.e)\.(\s|$)")


def _first_sentence(text: str) -> str:
    return _SENTENCE_END.split(text, maxsplit=1)[0]


def _listed(details: dict[str, Any]) -> list[str]:
    items = details.get("problems") or details.get("violations") or []
    return [str(item) for item in items] if isinstance(items, list) else []


def _run_summary(record: RunRecord) -> dict[str, Any]:
    return {
        "run_id": record.run_id,
        "pipeline": record.pipeline_name,
        "status": record.status.value,
        "created_at": record.created_at.isoformat(),
    }


def _print_error(exc: GeoProductError, as_json: bool) -> None:
    if as_json:
        _emit({"error": exc.error.model_dump(mode="json", exclude_none=True)})
        return
    error = exc.error
    print(f"error [{error.code.value}]: {error.message}", file=sys.stderr)
    for problem in _listed(error.details):
        print(f"  - {problem}", file=sys.stderr)
    if error.hint:
        print(f"hint: {error.hint}", file=sys.stderr)


def _emit(value: Any) -> int:
    print(json.dumps(value, indent=2, ensure_ascii=False, default=str))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
