"""Best-effort file creation ("birth") time.

``os.stat`` only exposes ``st_birthtime`` on macOS and Windows. On Linux the
kernel reports it through ``statx(2)``, which we call via ctypes. Filesystems
that don't record a birth time (some tmpfs/NFS setups) fall back to mtime,
which for write-once paste files is the moment they were written anyway.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import sys
from datetime import datetime, timedelta, timezone

_AT_FDCWD = -100
_STATX_BTIME = 0x800


class _StatxTimestamp(ctypes.Structure):
    _fields_ = [
        ("tv_sec", ctypes.c_int64),
        ("tv_nsec", ctypes.c_uint32),
        ("_reserved", ctypes.c_int32),
    ]


class _Statx(ctypes.Structure):
    # Layout of ``struct statx`` from <linux/stat.h>; the kernel writes 256
    # bytes, so the trailing spare keeps us safely over-allocated.
    _fields_ = [
        ("stx_mask", ctypes.c_uint32),
        ("stx_blksize", ctypes.c_uint32),
        ("stx_attributes", ctypes.c_uint64),
        ("stx_nlink", ctypes.c_uint32),
        ("stx_uid", ctypes.c_uint32),
        ("stx_gid", ctypes.c_uint32),
        ("stx_mode", ctypes.c_uint16),
        ("_spare0", ctypes.c_uint16),
        ("stx_ino", ctypes.c_uint64),
        ("stx_size", ctypes.c_uint64),
        ("stx_blocks", ctypes.c_uint64),
        ("stx_attributes_mask", ctypes.c_uint64),
        ("stx_atime", _StatxTimestamp),
        ("stx_btime", _StatxTimestamp),
        ("stx_ctime", _StatxTimestamp),
        ("stx_mtime", _StatxTimestamp),
        ("_spare1", ctypes.c_uint8 * 256),
    ]


_statx: object = None  # None = not yet resolved, False = unavailable


def _load_statx():
    global _statx
    if _statx is None:
        _statx = False
        if sys.platform == "linux":
            try:
                libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
                fn = libc.statx
            except (OSError, AttributeError):
                fn = None
            if fn is not None:
                fn.argtypes = [
                    ctypes.c_int,
                    ctypes.c_char_p,
                    ctypes.c_int,
                    ctypes.c_uint,
                    ctypes.POINTER(_Statx),
                ]
                fn.restype = ctypes.c_int
                _statx = fn
    return _statx or None


def _statx_btime_ns(path: str | os.PathLike[str]) -> int | None:
    """Birth time in ns via Linux statx(), or None if unavailable."""
    statx = _load_statx()
    if statx is None:
        return None
    buf = _Statx()
    rc = statx(_AT_FDCWD, os.fsencode(path), 0, _STATX_BTIME, ctypes.byref(buf))
    if rc != 0 or not buf.stx_mask & _STATX_BTIME:
        return None
    return buf.stx_btime.tv_sec * 1_000_000_000 + buf.stx_btime.tv_nsec


def birthtime_ns(path: str | os.PathLike[str]) -> int:
    """Creation time of *path* in nanoseconds since the epoch (UTC)."""
    st = os.stat(path)
    ns = getattr(st, "st_birthtime_ns", None)
    if ns is not None:
        return ns
    secs = getattr(st, "st_birthtime", None)
    if secs is not None:
        return round(secs * 1_000_000_000)
    ns = _statx_btime_ns(path)
    if ns is not None:
        return ns
    return st.st_mtime_ns


def birthtime(path: str | os.PathLike[str]) -> datetime:
    """Creation time of *path* as a timezone-aware UTC datetime."""
    ns = birthtime_ns(path)
    secs, rem = divmod(ns, 1_000_000_000)
    return datetime.fromtimestamp(secs, tz=timezone.utc) + timedelta(microseconds=rem // 1000)
