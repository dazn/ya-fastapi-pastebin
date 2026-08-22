import time
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

import pastebin.main as main_module
from pastebin.main import app


def test_index_is_single_self_contained_html(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "<style>" in r.text and "<script>" in r.text
    assert "http://" not in r.text and "https://" not in r.text  # no external assets


def test_list_empty(client: TestClient) -> None:
    assert client.get("/api/pastes").json() == []


def test_post_text(client: TestClient, storage_dir: Path) -> None:
    text = "hello <b>world</b>\n  indented\n"
    r = client.post("/api/pastes", data={"text": text})
    assert r.status_code == 201
    item = r.json()
    assert item["type"] == "text"
    assert item["name"] == "paste.txt"
    assert item["text"] == text
    assert item["created_at"].endswith("Z")
    assert "url" not in item
    assert (storage_dir / "paste.txt").read_bytes() == text.encode()
    assert client.get("/api/pastes").json() == [item]


def test_post_text_normalizes_crlf(client: TestClient, storage_dir: Path) -> None:
    r = client.post("/api/pastes", data={"text": "a\r\nb\r\n"})
    assert r.status_code == 201
    assert r.json()["text"] == "a\nb\n"
    assert (storage_dir / "paste.txt").read_bytes() == b"a\nb\n"


def test_post_file_and_download(client: TestClient, storage_dir: Path) -> None:
    r = client.post("/api/pastes", files={"file": ("réport 1.pdf", b"%PDF-1.4", "application/pdf")})
    assert r.status_code == 201
    item = r.json()
    assert item["type"] == "file"
    assert item["name"] == "réport 1.pdf"
    assert item["url"] == "/files/" + quote("réport 1.pdf")
    assert "text" not in item
    assert (storage_dir / "réport 1.pdf").read_bytes() == b"%PDF-1.4"

    d = client.get(item["url"])
    assert d.status_code == 200
    assert d.content == b"%PDF-1.4"
    assert "attachment" in d.headers["content-disposition"]
    assert d.headers["content-type"] == "application/octet-stream"


def test_delete_text_paste(client: TestClient, storage_dir: Path) -> None:
    item = client.post("/api/pastes", data={"text": "delete me"}).json()

    response = client.delete(f"/api/pastes/{item['name']}")

    assert response.status_code == 204
    assert response.content == b""
    assert not (storage_dir / item["name"]).exists()
    assert client.get("/api/pastes").json() == []
    assert client.delete(f"/api/pastes/{item['name']}").status_code == 404


def test_delete_uploaded_file_removes_listing_and_download(
    client: TestClient, storage_dir: Path
) -> None:
    item = client.post(
        "/api/pastes", files={"file": ("réport 1.pdf", b"pdf", "application/pdf")}
    ).json()

    response = client.delete("/api/pastes/" + quote(item["name"]))

    assert response.status_code == 204
    assert response.content == b""
    assert not (storage_dir / item["name"]).exists()
    assert client.get("/api/pastes").json() == []
    assert client.get(item["url"]).status_code == 404


@pytest.mark.parametrize(
    "path",
    [
        "/api/pastes/missing.bin",
        "/api/pastes/.hidden",
        "/api/pastes/..%2Foutside.bin",
    ],
)
def test_delete_invalid_or_missing_paste_returns_404(
    client: TestClient, storage_dir: Path, path: str
) -> None:
    (storage_dir / ".hidden").write_text("keep")

    response = client.delete(path)

    assert response.status_code == 404
    assert (storage_dir / ".hidden").read_text() == "keep"


def test_duplicate_upload_names_get_suffix(client: TestClient) -> None:
    names = [client.post("/api/pastes", files={"file": ("x.bin", b"1")}).json()["name"] for _ in range(2)]
    assert names == ["x.bin", "x-2.bin"]


def test_upload_over_limit_is_rejected_without_storage(
    client: TestClient, storage_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(main_module, "MAX_UPLOAD_BYTES", 3)
    r = client.post("/api/pastes", files={"file": ("large.bin", b"1234")})
    assert r.status_code == 413
    assert r.json() == {"detail": "File too large"}
    assert list(storage_dir.iterdir()) == []


def test_upload_filename_is_sanitized(client: TestClient, storage_dir: Path) -> None:
    r = client.post("/api/pastes", files={"file": ("../../evil.txt", b"x")})
    assert r.status_code == 201
    assert r.json()["name"] == "evil.txt"
    assert [p.name for p in storage_dir.iterdir()] == ["evil.txt"]
    assert not (storage_dir.parent / "evil.txt").exists()


def test_list_newest_first(client: TestClient) -> None:
    for t in "abc":
        client.post("/api/pastes", data={"text": t})
        time.sleep(0.02)
    assert [p["text"] for p in client.get("/api/pastes").json()] == ["c", "b", "a"]


@pytest.mark.parametrize("data", [{}, {"text": ""}, {"text": "   \n"}])
def test_post_neither_400(client: TestClient, data: dict[str, str]) -> None:
    assert client.post("/api/pastes", data=data).status_code == 400


def test_post_both_400(client: TestClient) -> None:
    r = client.post("/api/pastes", data={"text": "x"}, files={"file": ("a.txt", b"1")})
    assert r.status_code == 400


def test_empty_file_part_is_ignored(client: TestClient) -> None:
    """Browsers send a part with filename="" when no file is chosen."""
    boundary = "testboundary"
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="text"\r\n\r\n'
        "x\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename=""\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
        "\r\n"
        f"--{boundary}--\r\n"
    ).encode()
    r = client.post(
        "/api/pastes",
        content=body,
        headers={"content-type": f"multipart/form-data; boundary={boundary}"},
    )
    assert r.status_code == 201
    assert r.json()["type"] == "text"


@pytest.mark.parametrize(
    "path",
    [
        "/files/..%2F..%2Fetc%2Fpasswd",
        "/files/%2e%2e",
        "/files/paste.txt",
        "/files/nope.bin",
        "/files/.hidden",
    ],
)
def test_download_404(client: TestClient, storage_dir: Path, path: str) -> None:
    client.post("/api/pastes", data={"text": "x"})
    (storage_dir / ".hidden").write_text("h")
    assert client.get(path).status_code == 404


def test_env_var_selects_storage_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "from-env"
    monkeypatch.setenv("PASTEBIN_STORAGE_DIR", str(target))
    assert not target.exists()
    with TestClient(app) as c:
        assert c.get("/api/pastes").json() == []
        assert target.is_dir()
    del app.state.storage


def test_index_includes_accessible_delete_behavior(client: TestClient) -> None:
    html = client.get("/").text

    assert "class=\"delete\"" in html
    assert "setAttribute('aria-label', deleteLabel)" in html
    assert "deleteButton.title = deleteLabel" in html
    assert "encodeURIComponent(paste.name)" in html
    assert "method: 'DELETE'" in html
    assert "button.disabled = true" in html
    assert "button.disabled = false" in html
    assert "node.remove()" in html
    assert "Delete failed for" in html
