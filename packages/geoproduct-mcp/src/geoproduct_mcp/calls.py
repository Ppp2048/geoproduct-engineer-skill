"""Running a tool implementation from an MCP request.

Implementations are synchronous (providers block on the network), so they run in a worker
thread. Their result becomes a ``CallToolResult``: structured content plus a short text
summary on success, and a ``GeoError`` as structured content with ``isError`` on failure.

Cancellation: when the client cancels the request, the worker's cancel flag is set and the
request ends at once; the work stops at its next check (stages check between items).
"""

from collections.abc import Callable

import anyio
import anyio.from_thread
import anyio.to_thread
from mcp.server.mcpserver import Context
from mcp_types import CallToolResult, TextContent

from geoproduct.schemas import GeoError
from geoproduct_mcp.tools.common import Done, Job, as_error


def ok(done: Done) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=done.summary)],
        structured_content=done.output.model_dump(mode="json", by_alias=True),
    )


def failed(error: GeoError) -> CallToolResult:
    text = f"{error.code.value}: {error.message}"
    if error.hint:
        text += f" Hint: {error.hint}"
    return CallToolResult(
        content=[TextContent(type="text", text=text)],
        structured_content={"error": error.model_dump(mode="json", exclude_none=True)},
        is_error=True,
    )


def _progress(ctx: Context | None) -> Callable[[int, int | None, str | None], None] | None:
    if ctx is None:
        return None

    def report(done: int, total: int | None, message: str | None) -> None:
        try:
            anyio.from_thread.run(ctx.report_progress, done, total, message)
        except Exception:  # the request may have ended; progress is best effort
            return

    return report


async def call(work: Callable[[Job], Done], ctx: Context | None = None) -> CallToolResult:
    job = Job(progress=_progress(ctx))
    try:
        done = await anyio.to_thread.run_sync(lambda: work(job), abandon_on_cancel=True)
    except anyio.get_cancelled_exc_class():
        job.cancel.set()
        raise
    except Exception as exc:
        return failed(as_error(exc))
    return ok(done)
