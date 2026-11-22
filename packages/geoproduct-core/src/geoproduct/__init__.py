"""GeoProduct Engineer core: schemas, pipeline engine and providers for evidence-backed
geospatial datasets.

The core has no dependency on MCP or on any LLM. It can be used from Python, from the
``geoproduct`` command line, or through the separate ``geoproduct-mcp`` server.
"""

from importlib.metadata import PackageNotFoundError, version

from geoproduct.schemas._base import SCHEMA_VERSION

try:
    __version__ = version("geoproduct-core")
except PackageNotFoundError:  # running from a source tree without installation
    __version__ = "0.0.0"

__all__ = ["SCHEMA_VERSION", "__version__"]
