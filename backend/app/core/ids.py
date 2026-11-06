"""UUIDv7 generation (RFC 9562) without an extra dependency.

Time-ordered primary keys keep B-tree inserts sequential and give us a
sortable id for cursor pagination.
"""

from __future__ import annotations

import os
import time
import uuid


def uuid7() -> uuid.UUID:
    """Return a RFC 9562 UUIDv7."""
    timestamp_ms = int(time.time() * 1000)
    random_bits = int.from_bytes(os.urandom(10), "big")

    # 48-bit timestamp | 4-bit version | 12-bit rand_a | 2-bit variant | 62-bit rand_b
    value = (timestamp_ms & 0xFFFFFFFFFFFF) << 80
    value |= 0x7 << 76
    value |= ((random_bits >> 62) & 0xFFF) << 64
    value |= 0b10 << 62
    value |= random_bits & ((1 << 62) - 1)
    return uuid.UUID(int=value)
