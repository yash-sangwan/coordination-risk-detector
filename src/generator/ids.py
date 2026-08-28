"""Razorpay-shaped identifiers.

The probe (docs/api-probe.md section 6) observed prefix + 14 base62 characters,
with IDs created within the same minute sharing a leading prefix. That means the
generator is time-ordered, so IDs must be monotonic with created_at. Random IDs
would be unrealistic and, worse, an ordering leak if one population were minted
from a separate counter.

Every id in this generator comes from one monotonic sequence keyed on the event
timestamp, so no population can occupy its own block of the id space.
"""

# Ordered so lexicographic sort equals numeric sort on zero-padded output.
ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
BASE = len(ALPHABET)
WIDTH = 14

# Arbitrary fixed origin. Only the difference matters, and keeping it fixed keeps
# output byte-identical across runs.
EPOCH_ORIGIN = 1_600_000_000
SUBSECOND_SLOTS = 4096


def encode(value: int, width: int = WIDTH) -> str:
    if value < 0:
        raise ValueError("id value must be non-negative")
    out = []
    while value:
        value, rem = divmod(value, BASE)
        out.append(ALPHABET[rem])
    s = "".join(reversed(out)) or ALPHABET[0]
    if len(s) > width:
        raise ValueError("id overflowed width")
    return s.rjust(width, ALPHABET[0])


class IdMinter:
    """One monotonic sequence for all entity types.

    Encoding is (seconds since origin) * SUBSECOND_SLOTS + intra-second counter,
    so ids sort by time and share prefixes within a second, matching the probe.
    """

    def __init__(self) -> None:
        self._last_second = -1
        self._slot = 0

    def mint(self, prefix: str, created_at: int) -> str:
        if created_at != self._last_second:
            self._last_second = created_at
            self._slot = 0
        else:
            self._slot += 1
            if self._slot >= SUBSECOND_SLOTS:
                raise RuntimeError("too many ids in one second")
        value = (created_at - EPOCH_ORIGIN) * SUBSECOND_SLOTS + self._slot
        return f"{prefix}_{encode(value)}"
