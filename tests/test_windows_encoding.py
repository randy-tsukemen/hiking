import io
import json
import sys
from pathlib import Path

import pytest

from yama import book, cli


@pytest.mark.parametrize("empty_file", [False, True])
def test_setup_writes_utf8_under_cp950(monkeypatch, tmp_path, empty_file):
    guest = tmp_path / "booking_profile.json"
    if empty_file:
        guest.touch()  # A previous encoding failure can leave an empty file.
    monkeypatch.setattr(book, "_GUEST_FILE", guest)
    original = Path.write_text

    def cp950_write(self, data, encoding=None, **kwargs):
        return original(self, data, encoding=encoding or "cp950", **kwargs)

    monkeypatch.setattr(Path, "write_text", cp950_write)

    class StopBeforeBrowser(Exception):
        pass

    def stop():
        raise StopBeforeBrowser

    monkeypatch.setattr(book, "_require_playwright", stop)
    with pytest.raises(StopBeforeBrowser):
        book.setup(echo=lambda *args: None)
    assert json.loads(guest.read_text(encoding="utf-8")) == book._GUEST_TEMPLATE


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig"])
def test_guest_data_accepts_utf8_with_or_without_bom(monkeypatch, tmp_path, encoding):
    guest = tmp_path / "booking_profile.json"
    data = {"セイ": "ユーザー", "メール": "test@example.com"}
    guest.write_text(json.dumps(data, ensure_ascii=False), encoding=encoding)
    monkeypatch.setattr(book, "_GUEST_FILE", guest)
    assert book._load_guest() == data


def test_windows_cli_outputs_japanese_to_cp950_stream(monkeypatch):
    output, errors = io.BytesIO(), io.BytesIO()
    stdout = io.TextIOWrapper(output, encoding="cp950")
    stderr = io.TextIOWrapper(errors, encoding="cp950")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)
    monkeypatch.setattr(sys, "argv", ["yama", "book", "--setup"])

    def app():
        print("ユーザー：涸沢ヒュッテ")
        print("メール", file=sys.stderr)

    monkeypatch.setattr(cli, "app", app)
    cli.run()
    stdout.flush()
    stderr.flush()
    assert output.getvalue().decode("utf-8") == "ユーザー：涸沢ヒュッテ\n"
    assert errors.getvalue().decode("utf-8") == "メール\n"
