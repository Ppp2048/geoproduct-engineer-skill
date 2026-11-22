"""The structured error returned by every tool and stage."""

from enum import StrEnum
from typing import Final

from pydantic import Field, JsonValue

from geoproduct.schemas._base import Contract, Text


class ErrorCode(StrEnum):
    invalid_input = "invalid_input"
    invalid_spec = "invalid_spec"
    unknown_stage = "unknown_stage"
    not_found = "not_found"
    path_not_allowed = "path_not_allowed"
    too_large = "too_large"
    timeout = "timeout"
    cancelled = "cancelled"
    rate_limited = "rate_limited"
    provider_unavailable = "provider_unavailable"
    provider_error = "provider_error"
    policy_refused = "policy_refused"
    """The request would break a provider's usage policy, e.g. bulk geocoding."""
    auth_missing = "auth_missing"
    capability_unsupported = "capability_unsupported"
    confirmation_required = "confirmation_required"
    quality_gate_failed = "quality_gate_failed"
    stage_failed = "stage_failed"
    internal = "internal"


RETRYABLE_CODES: Final = frozenset(
    {ErrorCode.rate_limited, ErrorCode.provider_unavailable, ErrorCode.timeout}
)


class GeoError(Contract):
    code: ErrorCode
    message: Text
    stage: str | None = None
    retryable: bool = False
    details: dict[str, JsonValue] = Field(default_factory=dict)
    hint: Text | None = None
    """What the caller can do about it."""
