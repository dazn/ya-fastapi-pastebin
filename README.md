# pastebin

A simple pastebin service built with [FastAPI](https://fastapi.tiangolo.com/), managed with [uv](https://docs.astral.sh/uv/).

## Development

```sh
# Install dependencies
uv sync

# Run the dev server (http://127.0.0.1:8000, docs at /docs)
uv run fastapi dev src/pastebin/main.py

# Run tests
uv run pytest
```
