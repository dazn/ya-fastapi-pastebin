from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pastebin.main import app
from pastebin.storage import Storage


@pytest.fixture
def storage_dir(tmp_path: Path) -> Path:
    return tmp_path / "storage"


@pytest.fixture
def client(storage_dir: Path) -> Iterator[TestClient]:
    app.state.storage = Storage(storage_dir)
    with TestClient(app) as c:
        yield c
    del app.state.storage
