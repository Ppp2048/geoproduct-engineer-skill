"""Generate the published JSON Schemas (draft 2020-12) from the contract models.

    python -m geoproduct.schemas.export schemas/v1

A test checks that the files in the repository match these models, so the published
schemas cannot drift from the code.
"""

import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel

from geoproduct.schemas.datasets import DatasetManifest
from geoproduct.schemas.errors import GeoError
from geoproduct.schemas.geometry import Area
from geoproduct.schemas.pipelines import PipelineSpec
from geoproduct.schemas.records import Record
from geoproduct.schemas.reports import QualityReport
from geoproduct.schemas.runs import RunRecord, StageManifest
from geoproduct.schemas.sources import Evidence, Source

DIALECT: Final = "https://json-schema.org/draft/2020-12/schema"
BASE_ID: Final = (
    "https://raw.githubusercontent.com/Ppp2048/geoproduct-engineer-skill/main/schemas/v1/"
)

PUBLISHED: Final[dict[str, type[BaseModel]]] = {
    "area": Area,
    "dataset-manifest": DatasetManifest,
    "evidence": Evidence,
    "geo-error": GeoError,
    "pipeline-spec": PipelineSpec,
    "quality-report": QualityReport,
    "record": Record,
    "run-record": RunRecord,
    "source": Source,
    "stage-manifest": StageManifest,
}


def json_schema(name: str) -> dict[str, Any]:
    model = PUBLISHED[name]
    schema = model.model_json_schema(mode="validation", by_alias=True)
    return {"$schema": DIALECT, "$id": f"{BASE_ID}{name}.schema.json", **schema}


def render(name: str) -> str:
    return json.dumps(json_schema(name), indent=2, ensure_ascii=False, sort_keys=False) + "\n"


def write_all(out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name in PUBLISHED:
        path = out_dir / f"{name}.schema.json"
        path.write_text(render(name), encoding="utf-8", newline="\n")
        written.append(path)
    return written


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    out_dir = Path(args[0]) if args else Path("schemas/v1")
    for path in write_all(out_dir):
        print(path.as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
