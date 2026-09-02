"""네이티브 창 모드에서의 파일 전달 — 저장 대화상자·취소·폴백 (실제 창은 띄우지 않는다).

앱 창에는 브라우저 다운로드가 없으므로 서버가 직접 파일을 쓴다. 그 분기가 조용히 실패하면
사용자는 '눌렀는데 아무 일도 안 일어난다'를 겪게 되므로 경로마다 검증한다.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

pytest.importorskip("nicegui", reason="웹 UI 의존성(ui extra) 미설치")

from nicegui import ui  # noqa: E402

from md4paper.ui import desktop  # noqa: E402


class FakeWindow:
    """nicegui WindowProxy 흉내 — 저장/폴더 대화상자만."""

    def __init__(self, result=None, error: bool = False) -> None:  # noqa: ANN001
        self.result, self.error, self.calls = result, error, []

    async def create_file_dialog(self, **kwargs):  # noqa: ANN003, ANN201
        self.calls.append(kwargs)
        if self.error:
            raise RuntimeError("이 백엔드는 대화상자를 못 띄운다")
        return self.result


@pytest.fixture
def notices(monkeypatch):
    """ui.notify 가로채기 — (메시지, 종류) 목록."""
    seen: list[tuple[str, str | None]] = []
    monkeypatch.setattr(ui, "notify", lambda msg, **kw: seen.append((str(msg), kw.get("type"))))
    return seen


def _deliver(name: str = "paper.zip", data: bytes = b"zipdata") -> None:
    asyncio.run(desktop.deliver(name, data))


def test_browser_mode_uses_download(monkeypatch, notices):
    """브라우저 모드에서는 기존대로 ui.download — 서버가 파일을 쓰지 않는다."""
    sent = []
    monkeypatch.setattr(desktop, "window", lambda: None)
    monkeypatch.setattr(ui.download, "content",
                        lambda data, name, media_type="": sent.append((name, data, media_type)))
    _deliver()
    assert sent == [("paper.zip", b"zipdata", "application/zip")]
    assert notices == []


def test_native_writes_to_chosen_path(tmp_path, monkeypatch, notices):
    target = tmp_path / "골라둔 자리.zip"
    win = FakeWindow(result=str(target))
    monkeypatch.setattr(desktop, "window", lambda: win)
    revealed: list[Path] = []
    monkeypatch.setattr(desktop, "reveal", revealed.append)

    _deliver()

    assert target.read_bytes() == b"zipdata"
    assert win.calls[0]["save_filename"] == "paper.zip"  # 기본 파일 이름을 채워 준다
    assert not revealed  # 직접 고른 자리는 굳이 열어 보여주지 않는다
    assert any("저장됨" in msg for msg, _ in notices)


def test_native_cancel_writes_nothing(tmp_path, monkeypatch, notices):
    monkeypatch.setattr(desktop, "window", lambda: FakeWindow(result=None))
    monkeypatch.setattr(desktop, "downloads_dir", lambda: tmp_path)

    _deliver()

    assert list(tmp_path.iterdir()) == []
    assert notices and notices[-1][1] == "info"  # 취소도 알려 준다 (먹통과 구별되도록)


def test_native_falls_back_to_downloads_when_dialog_fails(tmp_path, monkeypatch, notices):
    monkeypatch.setattr(desktop, "window", lambda: FakeWindow(error=True))
    monkeypatch.setattr(desktop, "downloads_dir", lambda: tmp_path)
    revealed: list[Path] = []
    monkeypatch.setattr(desktop, "reveal", revealed.append)

    _deliver()

    saved = tmp_path / "paper.zip"
    assert saved.read_bytes() == b"zipdata"
    assert revealed == [saved]  # 사용자가 위치를 못 골랐으니 어디에 놓였는지 열어 준다


def test_native_write_error_is_reported(tmp_path, monkeypatch, notices):
    """쓸 수 없는 자리를 고르면 조용히 넘어가지 않는다."""
    monkeypatch.setattr(desktop, "window", lambda: FakeWindow(result=str(tmp_path / "없는폴더" / "x.zip")))
    monkeypatch.setattr(Path, "mkdir", lambda *a, **k: (_ for _ in ()).throw(OSError("권한 없음")))

    _deliver()

    assert any("저장 실패" in msg for msg, _ in notices)


def test_unique_path_avoids_overwrite(tmp_path):
    first = tmp_path / "a.zip"
    first.write_bytes(b"x")
    assert desktop.unique_path(first).name == "a (2).zip"
    (tmp_path / "a (2).zip").write_bytes(b"x")
    assert desktop.unique_path(first).name == "a (3).zip"
    assert desktop.unique_path(tmp_path / "b.zip").name == "b.zip"


def test_first_path_accepts_str_and_sequence():
    """저장 대화상자는 문자열을, 폴더 대화상자는 튜플을 돌려준다."""
    assert desktop._first_path("/a/b.zip") == "/a/b.zip"
    assert desktop._first_path(("/a", "/b")) == "/a"
    assert desktop._first_path(None) is None
    assert desktop._first_path(()) is None


def test_choose_folder_uses_window_dialog_in_native_mode(tmp_path, monkeypatch):
    win = FakeWindow(result=(str(tmp_path),))
    monkeypatch.setattr(desktop, "window", lambda: win)

    picked = asyncio.run(desktop.choose_folder("폴더 선택", str(tmp_path)))

    assert picked == str(tmp_path)
    assert win.calls[0]["dialog_type"] == 20  # FOLDER


def test_choose_folder_falls_back_to_os_dialog(tmp_path, monkeypatch):
    """웹뷰 대화상자가 안 되면 osascript 등 OS 대화상자로 물러선다."""
    from md4paper.ui import folder_dialog

    monkeypatch.setattr(desktop, "window", lambda: FakeWindow(error=True))
    monkeypatch.setattr(folder_dialog, "choose_folder", lambda title, initial: str(tmp_path))

    assert asyncio.run(desktop.choose_folder("폴더 선택", None)) == str(tmp_path)


def test_configure_sets_window_icon_and_downloads():
    from nicegui import app

    for cfg in (app.native.window_args, app.native.settings, app.native.start_args):
        cfg.clear()
    try:
        desktop.configure()
        assert app.native.window_args["min_size"] == (960, 640)
        assert app.native.settings["ALLOW_DOWNLOADS"] is True  # 우리가 못 잡은 다운로드도 사라지지 않게
        # pywebview 기본값(False)이면 body에 user-select:none이 주입돼 본문을 드래그로 못 고른다
        assert app.native.window_args["text_select"] is True
        assert Path(app.native.start_args["icon"]).is_file()  # macOS Dock 아이콘
    finally:
        for cfg in (app.native.window_args, app.native.settings, app.native.start_args):
            cfg.clear()
