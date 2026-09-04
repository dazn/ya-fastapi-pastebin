import errno
import os
import time
from io import BytesIO
from pathlib import Path

import pytest

from pastebin.storage import FileTooLargeError, Storage, sanitize_filename


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("report.pdf", "report.pdf"),
        ("réport 1.pdf", "réport 1.pdf"),
        ("../../etc/passwd", "passwd"),
        ("C:\\Users\\x\\y.txt", "y.txt"),
        ("a\x00b\n.txt", "ab.txt"),
        ("  spaced name.txt  ", "spaced name.txt"),
        (".hidden", "hidden"),
        ("...", "upload"),
        ("", "upload"),
        ("/", "upload"),
        ("paste.txt", "upload-paste.txt"),
        ("paste-7.txt", "upload-paste-7.txt"),
    ],
)
def test_sanitize_filename(raw: str, expected: str) -> None:
    assert sanitize_filename(raw) == expected


def test_sanitize_truncates() -> None:
    assert len(sanitize_filename("a" * 500)) == 200


def test_sanitize_truncates_to_filesystem_bytes() -> None:
    name = sanitize_filename("😀" * 200)
    assert name == "😀" * 50
    assert len(os.fsencode(name)) == 200


def test_sanitize_long_extension_does_not_become_hidden() -> None:
    name = sanitize_filename("😀." + "x" * 500)
    assert name.startswith("😀.")
    assert len(os.fsencode(name)) == 200


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('bad<>:"/\\|?*name.txt', "name.txt"),
        ("CON", "upload-CON"),
        ("nul.txt", "upload-nul.txt"),
    ],
)
def test_sanitize_windows_invalid_names(raw: str, expected: str) -> None:
    assert sanitize_filename(raw) == expected


def test_storage_creates_root(tmp_path: Path) -> None:
    root = tmp_path / "a" / "b"
    Storage(root)
    assert root.is_dir()


def test_save_text_names(tmp_path: Path) -> None:
    s = Storage(tmp_path)
    names = [s.save_text(f"t{i}").name for i in range(3)]
    assert names == ["paste.txt", "paste-2.txt", "paste-3.txt"]
    assert (tmp_path / "paste-2.txt").read_text() == "t1"
    assert sorted(os.listdir(tmp_path)) == sorted(names)


def test_save_file_dedup(tmp_path: Path) -> None:
    s = Storage(tmp_path)
    saved = [
        s.save_file("notes.txt", b"a"),
        s.save_file("notes.txt", b"b"),
        s.save_file("notes", b"c"),
        s.save_file("notes", b"d"),
    ]
    assert [p.name for p in saved] == ["notes.txt", "notes-2.txt", "notes", "notes-2"]
    assert all(p.kind == "file" and p.text is None for p in saved)
    assert (tmp_path / "notes-2.txt").read_bytes() == b"b"


def test_long_duplicate_names_keep_suffix_in_component_limit(tmp_path: Path) -> None:
    s = Storage(tmp_path)
    raw_name = "😀" * 100 + ".txt"
    first = s.save_file(raw_name, b"a")
    second = s.save_file(raw_name, b"b")

    assert first.name != second.name
    assert second.name.endswith("-2.txt")
    assert all(len(os.fsencode(p.name)) <= s.max_name_bytes for p in (first, second))


def test_save_file_sanitizes(tmp_path: Path) -> None:
    s = Storage(tmp_path)
    p = s.save_file("../../evil.txt", b"x")
    assert p.path == tmp_path / "evil.txt"
    assert os.listdir(tmp_path) == ["evil.txt"]


def test_list_pastes_newest_first(tmp_path: Path) -> None:
    s = Storage(tmp_path)
    s.save_text("first")
    time.sleep(0.02)
    s.save_file("a.bin", b"\0")
    time.sleep(0.02)
    s.save_text("third")

    listed = s.list_pastes()
    assert [p.name for p in listed] == ["paste-2.txt", "a.bin", "paste.txt"]
    assert listed[0].kind == "text" and listed[0].text == "third"
    assert listed[1].kind == "file" and listed[1].text is None
    assert listed[0].created_at >= listed[1].created_at >= listed[2].created_at
    assert listed[0].created_at.tzinfo is not None


def test_list_skips_hidden_and_dirs_but_includes_foreign_files(tmp_path: Path) -> None:
    s = Storage(tmp_path)
    (tmp_path / ".hidden").write_text("x")
    (tmp_path / "sub").mkdir()
    (tmp_path / "README").write_text("dropped in by hand")
    assert [(p.name, p.kind) for p in s.list_pastes()] == [("README", "file")]


def test_text_read_replaces_invalid_utf8(tmp_path: Path) -> None:
    (tmp_path / "paste.txt").write_bytes(b"ok \xff")
    assert Storage(tmp_path).list_pastes()[0].text == "ok \ufffd"


@pytest.mark.parametrize(
    "name", ["..", "a/b", ".hidden", "paste.txt", "paste-3.txt", "missing.bin", "", "x\0y", "sub"]
)
def test_get_file_rejects(tmp_path: Path, name: str) -> None:
    s = Storage(tmp_path)
    s.save_text("t")
    s.save_file("real.bin", b"1")
    (tmp_path / ".hidden").write_text("h")
    (tmp_path / "sub").mkdir()
    assert s.get_file(name) is None


def test_get_file_returns_existing_upload(tmp_path: Path) -> None:
    s = Storage(tmp_path)
    p = s.save_file("real.bin", b"1")
    assert s.get_file("real.bin") == p.path


def test_symlinks_are_neither_listed_nor_served(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.bin"
    outside.write_bytes(b"secret")
    (tmp_path / "link.bin").symlink_to(outside)
    s = Storage(tmp_path)

    assert s.list_pastes() == []
    assert s.get_file("link.bin") is None


def test_delete_text_paste(tmp_path: Path) -> None:
    storage = Storage(tmp_path)
    paste = storage.save_text("temporary")

    assert storage.delete_paste(paste.name) is True
    assert not paste.path.exists()
    assert storage.list_pastes() == []
    assert storage.delete_paste(paste.name) is False


def test_delete_uploaded_and_manually_added_files(tmp_path: Path) -> None:
    storage = Storage(tmp_path)
    uploaded = storage.save_file("report.pdf", b"pdf")
    manual = tmp_path / "manually added.data"
    manual.write_bytes(b"data")

    assert storage.delete_paste(uploaded.name) is True
    assert storage.delete_paste(manual.name) is True
    assert not uploaded.path.exists()
    assert not manual.exists()


@pytest.mark.parametrize("name", ["a" * 201, "é" * 101])
def test_delete_manual_name_above_pathconf_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    def unsupported_pathconf(path: os.PathLike[str], setting: str) -> int:
        raise AttributeError

    monkeypatch.setattr(os, "pathconf", unsupported_pathconf, raising=False)
    storage = Storage(tmp_path)
    manual = tmp_path / name
    manual.write_bytes(b"data")

    assert name in {paste.name for paste in storage.list_pastes()}
    assert storage.delete_paste(name) is True
    assert not manual.exists()


def test_delete_treats_name_too_long_as_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = Storage(tmp_path)

    def name_too_long(path: os.PathLike[str], *, follow_symlinks: bool) -> os.stat_result:
        raise OSError(errno.ENAMETOOLONG, "File name too long")

    monkeypatch.setattr(os, "stat", name_too_long)

    assert storage.delete_paste("too-long") is False


@pytest.mark.parametrize("name", ["", "missing", "..", "../outside", "sub/file", "/tmp/file"])
def test_delete_rejects_missing_and_invalid_names(tmp_path: Path, name: str) -> None:
    storage = Storage(tmp_path)

    assert storage.delete_paste(name) is False


def test_delete_rejects_hidden_files_directories_and_symlinks(tmp_path: Path) -> None:
    hidden = tmp_path / ".hidden"
    hidden.write_text("hidden")
    directory = tmp_path / "directory"
    directory.mkdir()
    outside = tmp_path.parent / "delete-outside.bin"
    outside.write_bytes(b"keep")
    symlink = tmp_path / "link.bin"
    symlink.symlink_to(outside)
    storage = Storage(tmp_path)

    assert storage.delete_paste(hidden.name) is False
    assert storage.delete_paste(directory.name) is False
    assert storage.delete_paste(symlink.name) is False
    assert hidden.exists()
    assert directory.is_dir()
    assert symlink.is_symlink()
    assert outside.read_bytes() == b"keep"


def test_stream_limit_removes_private_partial_file(tmp_path: Path) -> None:
    s = Storage(tmp_path)
    with pytest.raises(FileTooLargeError):
        s.save_file_stream("large.bin", BytesIO(b"12345"), max_bytes=4)
    assert list(tmp_path.iterdir()) == []


def test_stream_accepts_exact_limit(tmp_path: Path) -> None:
    paste = Storage(tmp_path).save_file_stream("exact.bin", BytesIO(b"1234"), max_bytes=4)
    assert paste.path.read_bytes() == b"1234"


def test_failed_stream_is_never_published(tmp_path: Path) -> None:
    class FailingStream:
        calls = 0

        def read(self, size: int = -1) -> bytes:
            self.calls += 1
            if self.calls == 1:
                return b"partial"
            raise OSError("read failed")

    s = Storage(tmp_path)
    with pytest.raises(OSError, match="read failed"):
        s.save_file_stream("broken.bin", FailingStream())  # type: ignore[arg-type]
    assert list(tmp_path.iterdir()) == []


def test_create_survives_race(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A file appearing between listdir() and open() is caught by O_EXCL."""
    s = Storage(tmp_path)
    real_listdir = os.listdir

    def sneaky_listdir(path):  # type: ignore[no-untyped-def]
        names = real_listdir(path)
        (tmp_path / "paste.txt").write_text("raced")
        return names

    monkeypatch.setattr(os, "listdir", sneaky_listdir)
    p = s.save_text("mine")
    assert p.name == "paste-2.txt"
    assert (tmp_path / "paste.txt").read_text() == "raced"
    assert (tmp_path / "paste-2.txt").read_text() == "mine"
