"""The exception carried through the core, and its conversion to a structured error."""

from collections.abc import Mapping

from geoproduct.schemas.errors import RETRYABLE_CODES, ErrorCode, GeoError


class GeoProductError(Exception):
    """An expected failure with a stable code, a message for people and a hint for agents.

    Unexpected exceptions are wrapped with :meth:`from_exception` at tool and stage
    boundaries, so callers always receive a :class:`GeoError`.
    """

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        stage: str | None = None,
        retryable: bool | None = None,
        details: Mapping[str, object] | None = None,
        hint: str | None = None,
    ) -> None:
        super().__init__(message)
        self.error = GeoError.model_validate(
            {
                "code": code,
                "message": message,
                "stage": stage,
                "retryable": code in RETRYABLE_CODES if retryable is None else retryable,
                "details": dict(details or {}),
                "hint": hint,
            }
        )

    @property
    def code(self) -> ErrorCode:
        return self.error.code

    @classmethod
    def from_exception(cls, exc: BaseException, *, stage: str | None = None) -> "GeoProductError":
        """Wrap an unexpected exception without leaking its arguments, which may hold secrets."""
        if isinstance(exc, GeoProductError):
            return exc
        return cls(
            ErrorCode.internal,
            f"Unexpected {type(exc).__name__}.",
            stage=stage,
            hint="This is a bug or an unhandled case; the run log has the details.",
        )
