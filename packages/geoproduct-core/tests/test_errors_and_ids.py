import re

import pytest
from pydantic import TypeAdapter

from geoproduct.errors import GeoProductError
from geoproduct.ids import PREFIXES, new_id
from geoproduct.schemas import ErrorCode, GeoError
from geoproduct.schemas._base import Identifier


class TestGeoProductError:
    @pytest.mark.parametrize(
        ("code", "retryable"),
        [
            (ErrorCode.rate_limited, True),
            (ErrorCode.provider_unavailable, True),
            (ErrorCode.timeout, True),
            (ErrorCode.invalid_input, False),
            (ErrorCode.policy_refused, False),
        ],
    )
    def test_retryable_follows_the_code(self, code: ErrorCode, retryable: bool) -> None:
        assert GeoProductError(code, "x").error.retryable is retryable

    def test_retryable_can_be_overridden(self) -> None:
        assert GeoProductError(ErrorCode.timeout, "x", retryable=False).error.retryable is False

    def test_carries_a_structured_error(self) -> None:
        exc = GeoProductError(
            ErrorCode.not_found, "No such dataset.", details={"dataset_id": "ds_1"}, hint="List."
        )
        assert str(exc) == "No such dataset."
        assert exc.code == ErrorCode.not_found
        dumped = exc.error.model_dump(mode="json")
        assert dumped["code"] == "not_found"
        assert GeoError.model_validate(dumped) == exc.error

    def test_unexpected_exceptions_do_not_leak_their_arguments(self) -> None:
        wrapped = GeoProductError.from_exception(RuntimeError("token=secret-value"), stage="s")
        assert wrapped.code == ErrorCode.internal
        assert "secret" not in wrapped.error.model_dump_json()
        assert "RuntimeError" in wrapped.error.message

    def test_expected_errors_pass_through(self) -> None:
        original = GeoProductError(ErrorCode.cancelled, "Stopped.")
        assert GeoProductError.from_exception(original) is original


class TestIds:
    PATTERN = re.compile(r"^[a-z]+_[0-9a-hjkmnp-tv-z]{26}$")

    def test_format(self) -> None:
        for prefix in PREFIXES.values():
            value = new_id(prefix)
            assert self.PATTERN.match(value), value
            assert value.startswith(prefix + "_")

    def test_ids_are_valid_identifiers(self) -> None:
        TypeAdapter(Identifier).validate_python(new_id("run"))

    def test_ids_sort_by_creation_time(self) -> None:
        ids = [new_id("run", timestamp_ms=t) for t in (1, 2, 1_000, 2**40, 2**48 - 1)]
        assert ids == sorted(ids)

    def test_same_millisecond_ids_differ(self) -> None:
        assert len({new_id("ev", timestamp_ms=42) for _ in range(5_000)}) == 5_000

    @pytest.mark.parametrize("prefix", ["", "Run", "run_", "toolongprefix", "r1"])
    def test_bad_prefixes(self, prefix: str) -> None:
        with pytest.raises(ValueError, match="prefix"):
            new_id(prefix)

    @pytest.mark.parametrize("timestamp", [-1, 2**48])
    def test_timestamp_range(self, timestamp: int) -> None:
        with pytest.raises(ValueError, match="timestamp"):
            new_id("run", timestamp_ms=timestamp)
