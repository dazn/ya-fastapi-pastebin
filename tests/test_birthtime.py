import os
import time
from datetime import timezone
from pathlib import Path

import pytest

from pastebin import birthtime as bt

SLACK_NS = 50_000_000  # kernels stamp files with a coarse (jiffy) clock


def _reports_birthtime(path: Path) -> bool:
    st = os.stat(path)
    return hasattr(st, "st_birthtime_ns") or hasattr(st, "st_birthtime") or bt._statx_btime_ns(path) is not None


def test_birthtime_of_new_file_is_now(tmp_path: Path) -> None:
    before = time.time_ns()
    path = tmp_path / "f"
    path.write_bytes(b"x")
    after = time.time_ns()

    ns = bt.birthtime_ns(path)
    assert before - SLACK_NS <= ns <= after + SLACK_NS
    assert ns <= os.stat(path).st_mtime_ns + SLACK_NS


def test_birthtime_datetime_is_utc_and_matches_ns(tmp_path: Path) -> None:
    path = tmp_path / "f"
    path.write_bytes(b"x")
    dt = bt.birthtime(path)
    assert dt.tzinfo == timezone.utc
    assert round(dt.timestamp() * 1_000_000) == bt.birthtime_ns(path) // 1000


def test_birthtime_survives_utime(tmp_path: Path) -> None:
    path = tmp_path / "f"
    path.write_bytes(b"x")
    if not _reports_birthtime(path):
        pytest.skip("filesystem does not report birth time")
    born = bt.birthtime_ns(path)
    future = time.time() + 3600
    os.utime(path, (future, future))
    assert bt.birthtime_ns(path) == born
    assert os.stat(path).st_mtime_ns != born


@pytest.mark.skipif(
    hasattr(os.stat("."), "st_birthtime_ns") or hasattr(os.stat("."), "st_birthtime"),
    reason="platform exposes birth time directly via os.stat",
)
def test_falls_back_to_mtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "f"
    path.write_bytes(b"x")
    monkeypatch.setattr(bt, "_statx_btime_ns", lambda _path: None)
    assert bt.birthtime_ns(path) == os.stat(path).st_mtime_ns
