from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from pastebin.main import UploadBodyLimitMiddleware, app

client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_body_limit_rejects_chunked_request_without_content_length() -> None:
    limited_app = FastAPI()
    limited_app.add_middleware(UploadBodyLimitMiddleware, max_bytes=3)

    @limited_app.post("/api/pastes")
    async def consume_body(request: Request) -> dict[str, bool]:
        await request.body()
        return {"accepted": True}

    with TestClient(limited_app) as limited_client:
        response = limited_client.post("/api/pastes", content=iter([b"12", b"34"]))

    assert response.status_code == 413
    assert response.json() == {"detail": "File too large"}
