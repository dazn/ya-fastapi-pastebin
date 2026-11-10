# pastebin

A tiny pastebin for the local network, built with [FastAPI](https://fastapi.tiangolo.com/) and managed with [uv](https://docs.astral.sh/uv/).

Open `/` in a browser to get a paste form (plain text **or** one file) followed by every
previous paste, newest first. Text is shown verbatim; files are download links. The whole
page is a single HTML file with inline CSS and vanilla JavaScript.

Drop one file anywhere inside the paste form (including the text box), or use
**Choose File**, then click **Paste** to upload it. Clear any entered text before
selecting a file. Dropping another file replaces the selection; folders and multiple
files are rejected. Files are limited to 100 MiB.

## Running on the LAN

```sh
uv sync
uv run fastapi run src/pastebin/main.py --host 0.0.0.0 --port 8000
```

Then open `http://<this-machine's-ip>:8000/` from any machine on the network.

For local development with auto-reload: `uv run dev` (binds to 127.0.0.1:8000 by
default; pass `--host`/`--port` to change).

## Running with Docker

Build and start the service in the background:

```sh
docker compose up --build -d
```

Open `http://<this-machine's-ip>:8000/`. To publish a different host port, set
`PASTEBIN_PORT` when starting the service:

```sh
PASTEBIN_PORT=8080 docker compose up --build -d
```

The container runs as the unprivileged UID/GID `10001:10001`. Paste data is stored at
`/data` in the container, backed by a Docker volume whose exact name is `pastefiles`.
Compose creates the volume if it does not exist and reuses it on subsequent runs. An
existing populated volume must be readable and writable by UID/GID `10001:10001`.

Useful operating commands:

```sh
docker compose ps
docker compose logs -f pastebin
docker volume inspect pastefiles
docker compose down
```

`docker compose down` preserves the volume. Do not add `--volumes` (or `-v`) unless
you intend to delete every stored paste.

To make a backup in the current directory, stop the application first so that no
pastes can be created or deleted while `tar` enumerates and reads the volume. Restart
the application after the archive has been created:

```sh
docker compose stop pastebin

docker run --rm \
  --user "$(id -u):$(id -g)" \
  --volume pastefiles:/data:ro \
  --volume "$PWD:/backup" \
  alpine tar -czf /backup/pastefiles-backup.tar.gz -C /data .

docker compose start pastebin
```

The service has no authentication. Keep the published port on a trusted network, or
put it behind an authenticated reverse proxy before exposing it more broadly.

## Storage

Pastes live as plain files in one flat directory.

- `PASTEBIN_STORAGE_DIR` selects the directory (default: `./storage`, relative to the
  current working directory; created on startup).
- Uploaded files are limited to 100 MiB and keep their original (sanitized and
  byte-length-limited) filename, e.g. `report.pdf`.
- Text pastes are stored as `paste.txt`.
- If a name is already taken, `-2`, `-3`, … is inserted before the extension
  (`report-2.pdf`, `paste-2.txt`).
- A paste's date is the file's creation time, read with `statx(2)` on Linux (falls back
  to `st_birthtime` on macOS/Windows, and to the modification time on filesystems that
  don't record a birth time).

Anything you copy into the directory by hand shows up in the list as a download.
Pastes can be permanently and immediately deleted from the web page or API.

## API

| Method | Path                   | Description                                             |
|--------|------------------------|---------------------------------------------------------|
| GET    | `/`                    | The web page                                            |
| GET    | `/api/pastes`          | JSON list of pastes, newest first                       |
| POST   | `/api/pastes`          | `multipart/form-data` with either `text` or `file`      |
| DELETE | `/api/pastes/{name}`  | Permanently delete a paste (`204`, or `404` if absent) |
| GET    | `/files/{name}`        | Download an uploaded file                               |
| GET    | `/health`              | `{"status": "ok"}`                                      |

```sh
curl -F 'text=hello from curl' http://HOST:8000/api/pastes
curl -F 'file=@report.pdf'     http://HOST:8000/api/pastes
curl http://HOST:8000/api/pastes
curl -OJ http://HOST:8000/files/report.pdf
curl -X DELETE http://HOST:8000/api/pastes/report.pdf
```

## Tests

```sh
uv run pytest
```
