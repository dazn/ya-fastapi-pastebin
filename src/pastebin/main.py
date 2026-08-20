from fastapi import FastAPI

app = FastAPI(title="Pastebin")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
