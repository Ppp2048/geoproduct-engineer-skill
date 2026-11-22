"""Pipeline runs in the background, one thread per run.

A tool call starts a run and returns its ``run_id`` at once; the agent polls
``get_pipeline_status``. Runs live in the workspace, so their records outlive the server.
A run whose server stopped mid-way still reads ``running``; :meth:`Jobs.active` tells the
two apart, and running it again resumes it from its last finished stage.
"""

import logging
import threading
from collections.abc import Mapping

from pydantic import JsonValue

from geoproduct.engine import Runner
from geoproduct.errors import GeoProductError
from geoproduct.schemas import ErrorCode

log = logging.getLogger("geoproduct_mcp.jobs")


class Jobs:
    def __init__(self, runner: Runner) -> None:
        self.runner = runner
        self._threads: dict[str, threading.Thread] = {}
        self._lock = threading.Lock()

    def start(self, run_id: str, params: Mapping[str, JsonValue] | None = None) -> None:
        """Run or resume ``run_id`` in a background thread.

        Parameters must already be checked: errors raised in the thread are only logged
        (the run record shows them).
        """
        with self._lock:
            if self.active(run_id):
                raise GeoProductError(
                    ErrorCode.invalid_input,
                    "This run is already executing.",
                    details={"run_id": run_id},
                    hint="Poll get_pipeline_status, or cancel it first.",
                )
            thread = threading.Thread(
                target=self._work,
                args=(run_id, dict(params or {})),
                name=f"geoproduct-{run_id}",
                daemon=True,
            )
            self._threads[run_id] = thread
            thread.start()

    def _work(self, run_id: str, params: dict[str, JsonValue]) -> None:
        try:
            self.runner.resume(run_id, params=params or None)
        except GeoProductError as exc:
            log.warning("Run %s did not start: %s", run_id, exc.error.message)
        except Exception:
            log.exception("Run %s stopped with an unexpected error", run_id)

    def active(self, run_id: str) -> bool:
        thread = self._threads.get(run_id)
        return thread is not None and thread.is_alive()

    def wait(self, run_id: str, timeout: float | None = None) -> bool:
        """Wait for a background run to finish; True if it did. For tests and shutdown."""
        thread = self._threads.get(run_id)
        if thread is None:
            return True
        thread.join(timeout)
        return not thread.is_alive()
