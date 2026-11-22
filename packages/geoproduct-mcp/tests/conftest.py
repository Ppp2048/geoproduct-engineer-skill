"""Server state over a temporary workspace. Nothing here touches the network.

Live providers are built with a mock transport that answers like the services would,
with invented places around 0°, 0°.
"""

from collections.abc import Callable
from pathlib import Path

import httpx2
import pytest

from geoproduct.providers.registry import builtin_providers
from geoproduct.schemas import Rating, SourceEvaluation
from geoproduct.workspace import Workspace
from geoproduct_mcp.tools.common import Job, ServerState

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLES = REPO_ROOT / "examples"
AGENT = {"GEOPRODUCT_USER_AGENT": "test-suite/1.0 (tests@example.org)"}

GEOCODE_ANSWER = [
    {
        "osm_type": "node",
        "osm_id": 7,
        "lat": "0.0031",
        "lon": "0.0042",
        "category": "amenity",
        "type": "clinic",
        "place_rank": 30,
        "display_name": "Example Clinic, Exampleton",
        "address": {"town": "Exampleton", "country_code": "zz"},
        "boundingbox": ["0.0030", "0.0032", "0.0041", "0.0043"],
    }
]


def mock_services(request: httpx2.Request) -> httpx2.Response:
    if "/search" in request.url.path:
        return httpx2.Response(200, json=GEOCODE_ANSWER)
    return httpx2.Response(404)


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return Workspace(tmp_path / "workspace", [EXAMPLES, tmp_path / "exports"]).ensure()


@pytest.fixture
def state(workspace: Workspace) -> ServerState:
    """Fixture provider plus live providers that answer from :func:`mock_services`."""
    providers = builtin_providers(workspace, AGENT, transport=httpx2.MockTransport(mock_services))
    return ServerState(workspace, providers=providers, env=AGENT)


@pytest.fixture
def job() -> Job:
    return Job()


@pytest.fixture
def evaluation() -> Callable[..., SourceEvaluation]:
    def build(default: int = 2, **scores: int) -> SourceEvaluation:
        names = (
            "authority",
            "accuracy",
            "freshness",
            "coverage",
            "licensing",
            "geographic_precision",
            "verifiability",
        )
        return SourceEvaluation(
            **{n: Rating(score=scores.get(n, default), rationale=f"rated {n}") for n in names}
        )

    return build
