from __future__ import annotations

import argparse
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from pastebin.storage import FileTooLargeError, Paste, Storage

STATIC_DIR = Path(__file__).parent / "static"
MAX_TEXT_BYTES = 1_000_000
MAX_UPLOAD_BYTES = 100 * 1024 * 1024
# Multipart framing is not part of the uploaded file. Allow a bounded amount
# for it while still stopping oversized bodies before the multipart parser can
# spool an unbounded request.
MAX_UPLOAD_BODY_BYTES = MAX_UPLOAD_BYTES + 1024 * 1024


class UploadBodyLimitMiddleware:
    """Cap request bodies for the upload endpoint before form parsing."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "POST" or scope["path"] != "/api/pastes":
            await self.app(scope, receive, send)
            return

        headers = dict(scope["headers"])
        try:
            content_length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            content_length = 0
        if content_length > self.max_bytes:
            response = JSONResponse({"detail": "File too large"}, status_code=413)
            await response(scope, receive, send)
            return

        received = 0

        async def receive_limited() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise HTTPException(413, "File too large")
            return message

        await self.app(scope, receive_limited, send)


def default_storage_dir() -> Path:
    return Path(os.environ.get("PASTEBIN_STORAGE_DIR", "storage"))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    if not hasattr(app.state, "storage"):  # tests preset this with a temp dir
        app.state.storage = Storage(default_storage_dir())
    yield


app = FastAPI(title="Pastebin", lifespan=lifespan)
app.add_middleware(UploadBodyLimitMiddleware, max_bytes=MAX_UPLOAD_BODY_BYTES)


def get_storage(request: Request) -> Storage:
    return request.app.state.storage


StorageDep = Annotated[Storage, Depends(get_storage)]


def paste_to_json(paste: Paste) -> dict[str, Any]:
    item: dict[str, Any] = {
        "name": paste.name,
        "created_at": paste.created_at.isoformat().replace("+00:00", "Z"),
        "type": paste.kind,
    }
    if paste.kind == "text":
        item["text"] = paste.text
    else:
        item["url"] = f"/files/{quote(paste.name)}"
    return item


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html")


@app.get("/api/pastes")
def list_pastes(storage: StorageDep) -> list[dict[str, Any]]:
    return [paste_to_json(p) for p in storage.list_pastes()]


@app.post("/api/pastes", status_code=201)
async def create_paste(
    storage: StorageDep,
    text: Annotated[str | None, Form()] = None,
    file: Annotated[UploadFile | None, File()] = None,
) -> dict[str, Any]:
    text = text or ""
    has_text = bool(text.strip())
    # Browsers send an empty part with filename="" when no file is chosen.
    has_file = file is not None and bool(file.filename)
    if has_text and has_file:
        raise HTTPException(400, "Provide text or a file, not both")
    if has_text:
        # multipart/form-data encoding turns every newline into CRLF; undo that.
        text = text.replace("\r\n", "\n")
        if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
            raise HTTPException(413, "Text too large")
        return paste_to_json(storage.save_text(text))
    if has_file:
        assert file is not None and file.filename
        if file.size is not None and file.size > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "File too large")
        try:
            paste = await run_in_threadpool(
                storage.save_file_stream, file.filename, file.file, MAX_UPLOAD_BYTES
            )
        except FileTooLargeError as exc:
            raise HTTPException(413, "File too large") from exc
        return paste_to_json(paste)
    raise HTTPException(400, "Provide either text or a file")


@app.get("/files/{name}", include_in_schema=False)
def download(name: str, storage: StorageDep) -> FileResponse:
    path = storage.get_file(name)
    if path is None:
        raise HTTPException(404, "Not found")
    return FileResponse(path, filename=name, media_type="application/octet-stream")


def dev() -> None:
    """Entry point for `uv run dev`: serve the app locally with auto-reload."""
    import uvicorn

    parser = argparse.ArgumentParser(prog="dev", description="Run the pastebin dev server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    uvicorn.run("pastebin.main:app", host=args.host, port=args.port, reload=True)
