"""Ids issued by the server: a type prefix, then a time-ordered random part.

``run_01kaxq7b8m3p9r2v5w0yz4c6dt`` sorts by creation time and carries 80 random bits,
so ids from separate processes do not collide and need no coordination.
"""

import os
import re
import time
from typing import Final

_ALPHABET: Final = "0123456789abcdefghjkmnpqrstvwxyz"  # Crockford base32, lower case
_PREFIX: Final = re.compile(r"^[a-z]{1,8}$")

PREFIXES: Final = {
    "dataset": "ds",
    "evidence": "ev",
    "pipeline": "pl",
    "record": "rec",
    "run": "run",
    "source": "src",
}


def _base32(value: int, length: int) -> str:
    chars = []
    for _ in range(length):
        value, index = divmod(value, 32)
        chars.append(_ALPHABET[index])
    return "".join(reversed(chars))


def new_id(prefix: str, *, timestamp_ms: int | None = None) -> str:
    """A new id such as ``src_01kaxq7b8m3p9r2v5w0yz4c6dt`` (10 time + 16 random characters)."""
    if not _PREFIX.match(prefix):
        raise ValueError("prefix must be 1-8 lower-case letters")
    millis = time.time_ns() // 1_000_000 if timestamp_ms is None else timestamp_ms
    if not 0 <= millis < 2**48:
        raise ValueError("timestamp out of range")
    random_part = int.from_bytes(os.urandom(10), "big")
    return f"{prefix}_{_base32(millis, 10)}{_base32(random_part, 16)}"
