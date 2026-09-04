"""Flat-directory paste storage.

Every paste is one plain file in the storage directory; there is no metadata.

* uploads keep their (sanitized) original filename, e.g. ``report.pdf``
* text pastes are stored as ``paste.txt``
* a name that is already taken gets ``-2``, ``-3``, ... before the extension
* the paste's date is the file's creation time (see :mod:`pastebin.birthtime`)
"""

from __future__ import annotations

import errno
import os
import re
import stat
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import BinaryIO

from pastebin.birthtime import birthtime

TEXT_NAME = "paste.txt"
TEXT_RE = re.compile(r"^paste(-\d+)?\.txt$")
MAX_NAME_BYTES = 200
FALLBACK_NAME = "upload"
COPY_CHUNK_BYTES = 1024 * 1024
WINDOWS_INVALID_CHARS = frozenset('<>:"/\\|?*')
WINDOWS_RESERVED_NAMES = re.compile(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", re.IGNORECASE)


class FileTooLargeError(ValueError):
    """Raised when a streamed upload exceeds its configured size limit."""


@dataclass(frozen=True)
class Paste:
    name: str
    created_at: datetime
    kind: str  # "text" | "file"
    path: Path
    text: str | None = None


def _truncate_component(name: str, max_bytes: int) -> str:
    """Truncate without splitting a character in the filesystem encoding."""
    if len(os.fsencode(name)) <= max_bytes:
        return name

    result: list[str] = []
    used = 0
    for char in name:
        char_bytes = len(os.fsencode(char))
        if used + char_bytes > max_bytes:
            break
        result.append(char)
        used += char_bytes
    return "".join(result)


def _truncate_filename(name: str, max_bytes: int) -> str:
    """Truncate a component while preserving a usable extension when possible."""
    if len(os.fsencode(name)) <= max_bytes:
        return name

    stem, ext = os.path.splitext(name)
    if not ext:
        return _truncate_component(name, max_bytes)

    # A pathological extension must not consume the entire component. Reserve
    # enough bytes for the first complete stem character.
    minimum_stem_bytes = len(os.fsencode(stem[0])) if stem else 1
    ext = _truncate_component(ext, max_bytes - minimum_stem_bytes)
    stem = _truncate_component(stem, max_bytes - len(os.fsencode(ext)))
    if not stem:
        stem = _truncate_component(FALLBACK_NAME, max_bytes - len(os.fsencode(ext)))
    return stem + ext


def sanitize_filename(name: str, max_bytes: int = MAX_NAME_BYTES) -> str:
    """Reduce an untrusted filename to a single safe path segment."""
    name = os.path.basename(name.replace("\\", "/"))
    name = "".join(
        c for c in name if c.isprintable() and ord(c) >= 32 and c not in WINDOWS_INVALID_CHARS
    )
    name = _truncate_filename(name.strip(" ."), max_bytes).rstrip(" .")
    if not name:
        name = FALLBACK_NAME
    if TEXT_RE.match(name) or WINDOWS_RESERVED_NAMES.match(name):
        # Do not masquerade as a text paste or use a reserved Windows device.
        name = _truncate_filename("upload-" + name, max_bytes).rstrip(" .")
    return name


def _numbered_name(name: str, number: int, max_bytes: int) -> str:
    """Insert a numeric suffix while keeping the complete component in bounds."""
    if number == 1:
        return name

    stem, ext = os.path.splitext(name)
    suffix = f"-{number}"
    suffix_bytes = len(os.fsencode(suffix))
    # Keep at least one byte for the stem. Extremely long extensions are
    # shortened first so that the collision suffix can never be truncated.
    ext = _truncate_component(ext, max_bytes - suffix_bytes - 1)
    stem = _truncate_component(stem, max_bytes - suffix_bytes - len(os.fsencode(ext)))
    if not stem:
        stem = _truncate_component(FALLBACK_NAME, max_bytes - suffix_bytes - len(os.fsencode(ext)))
    return f"{stem}{suffix}{ext}"


class Storage:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            component_limit = os.pathconf(self.root, "PC_NAME_MAX")
        except (AttributeError, OSError, ValueError):
            component_limit = MAX_NAME_BYTES
        if component_limit <= 0:
            component_limit = MAX_NAME_BYTES
        self.max_name_bytes = min(MAX_NAME_BYTES, component_limit)

    def _create(self, name: str, write: Callable[[BinaryIO], None]) -> Path:
        """Write privately, then atomically publish the first free name."""
        taken = set(os.listdir(self.root))
        fd, temporary_name = tempfile.mkstemp(prefix=".paste-", dir=self.root)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "wb") as destination:
                fd = -1
                write(destination)

            number = 1
            while True:
                candidate = _numbered_name(name, number, self.max_name_bytes)
                if candidate in taken:
                    number += 1
                    continue
                path = self.root / candidate
                try:
                    # A hard link publishes the fully written file without an
                    # overwrite race. The private temporary name is removed below.
                    os.link(temporary, path, follow_symlinks=False)
                except FileExistsError:
                    number += 1
                    continue
                return path
        finally:
            if fd >= 0:
                os.close(fd)
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _copy(source: BinaryIO, destination: BinaryIO, max_bytes: int | None) -> None:
        total = 0
        while True:
            read_size = COPY_CHUNK_BYTES
            if max_bytes is not None:
                # Read one byte beyond the remaining allowance to detect an
                # oversized source without ever holding more than one chunk.
                read_size = min(read_size, max_bytes - total + 1)
            chunk = source.read(read_size)
            if not chunk:
                return
            total += len(chunk)
            if max_bytes is not None and total > max_bytes:
                raise FileTooLargeError(f"file exceeds {max_bytes} bytes")
            destination.write(chunk)

    def _read(self, path: Path) -> Paste:
        if TEXT_RE.match(path.name):
            text = path.read_text(encoding="utf-8", errors="replace")
            return Paste(path.name, birthtime(path), "text", path, text)
        return Paste(path.name, birthtime(path), "file", path)

    def save_text(self, text: str) -> Paste:
        data = text.encode("utf-8")
        return self._read(self._create(TEXT_NAME, lambda destination: destination.write(data)))

    def save_file(self, filename: str, data: bytes) -> Paste:
        return self.save_file_stream(filename, BytesIO(data))

    def save_file_stream(
        self, filename: str, source: BinaryIO, max_bytes: int | None = None
    ) -> Paste:
        """Save a binary stream in bounded chunks, optionally enforcing a limit."""
        name = sanitize_filename(filename, self.max_name_bytes)
        path = self._create(name, lambda destination: self._copy(source, destination, max_bytes))
        return self._read(path)

    def list_pastes(self) -> list[Paste]:
        """All pastes, newest first."""
        pastes = []
        with os.scandir(self.root) as it:
            for entry in it:
                if entry.name.startswith(".") or not entry.is_file(follow_symlinks=False):
                    continue
                pastes.append(self._read(Path(entry.path)))
        pastes.sort(key=lambda p: (p.created_at, p.name), reverse=True)
        return pastes

    def get_file(self, name: str) -> Path | None:
        """Path of an uploaded file by name, or None if it's not a valid file paste."""
        if name != sanitize_filename(name, self.max_name_bytes) or TEXT_RE.match(name):
            return None
        path = self.root / name
        try:
            mode = os.stat(path, follow_symlinks=False).st_mode
        except (FileNotFoundError, NotADirectoryError):
            return None
        return path if stat.S_ISREG(mode) else None

    def delete_paste(self, name: str) -> bool:
        """Permanently delete a visible regular file directly inside storage."""
        if not name or name.startswith(".") or Path(name).name != name:
            return False

        path = self.root / name
        try:
            mode = os.stat(path, follow_symlinks=False).st_mode
        except (FileNotFoundError, NotADirectoryError, ValueError):
            return False
        except OSError as exc:
            if exc.errno == errno.ENAMETOOLONG:
                return False
            raise
        if not stat.S_ISREG(mode):
            return False

        try:
            path.unlink()
        except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
            return False
        return True
