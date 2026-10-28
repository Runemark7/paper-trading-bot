"""Give freed heap pages back to the OS.

glibc keeps per-thread arenas. A process that builds a large summary and
drops it can still climb in RSS. ``malloc_trim`` returns the unused top
of the heap. It is a no-op where libc does not provide it.
"""
from __future__ import annotations


def release_heap() -> None:
    try:
        import ctypes

        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (OSError, AttributeError):
        return
