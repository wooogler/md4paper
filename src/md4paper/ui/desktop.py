"""네이티브 창(pywebview) 모드 — 브라우저 탭 대신 앱 창으로 띄울 때 달라지는 것들.

앱 창에는 브라우저의 다운로드 UI가 없다. NiceGUI의 `ui.download`는 blob URL을 만들어
`<a download>`를 클릭하는 방식인데, 이걸 웹뷰(WKWebView·WebView2·WebKitGTK)가 어떻게 처리할지는
백엔드 설정에 달려 있어 **아무 일도 일어나지 않을 수** 있다. 그래서 네이티브 모드에서는 서버가
파일을 직접 쓴다 — 로컬 앱이라 서버 프로세스 = 사용자 컴퓨터이므로 가능한 방법이다.
저장 위치는 OS 저장 대화상자로 고르고, 대화상자를 못 띄우면 다운로드 폴더에 떨어뜨린 뒤
파일 탐색기로 그 자리를 열어 준다(어디로 갔는지 모르는 상태를 만들지 않는다).

`window()`가 None이면 브라우저 모드다 — 이 모듈의 함수들은 그때 기존 동작을 그대로 쓴다.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

# webview.FileDialog.SAVE. pywebview는 선택 의존성이라 import 없이 값만 가져다 쓴다
# (브라우저 모드에서 이 모듈을 import하는 것만으로 pywebview를 요구하면 안 된다).
SAVE_DIALOG = 30

ICON = Path(__file__).resolve().parent / "assets" / "icon.png"


def window():  # noqa: ANN201 — nicegui의 WindowProxy (pywebview 미설치 시 타입이 없다)
    """네이티브 창 프록시. 브라우저 모드이거나 UI 밖(CLI·테스트)이면 None."""
    try:
        from nicegui import app
    except ImportError:
        return None
    return getattr(getattr(app, "native", None), "main_window", None)


def active() -> bool:
    """네이티브 창 모드로 돌고 있는지."""
    return window() is not None


def downloads_dir() -> Path:
    """OS 다운로드 폴더 (없으면 홈)."""
    d = Path.home() / "Downloads"
    return d if d.is_dir() else Path.home()


def unique_path(path: Path) -> Path:
    """이미 있는 이름이면 `name (2).zip`으로 비켜 쓴다 — 브라우저 다운로드와 같은 규칙."""
    if not path.exists():
        return path
    i = 2
    while (candidate := path.with_name(f"{path.stem} ({i}){path.suffix}")).exists():
        i += 1
    return candidate


def _first_path(picked: object) -> str | None:
    """저장 대화상자는 문자열, 열기·폴더 대화상자는 튜플을 준다 — 둘 다 받아 첫 경로로."""
    if not picked:
        return None
    if isinstance(picked, (str, Path)):
        return str(picked)
    items = list(picked)  # type: ignore[call-overload]
    return str(items[0]) if items else None


def reveal(path: Path) -> None:
    """파일 탐색기에서 그 파일이 있는 자리를 연다. 실패해도 조용히 넘어간다(부가 기능)."""
    try:
        if sys.platform == "darwin":
            subprocess.run(["open", "-R", str(path)], check=False, timeout=10)
        elif sys.platform.startswith("win"):
            subprocess.run(["explorer", f"/select,{path}"], check=False, timeout=10)
        elif shutil.which("xdg-open"):
            subprocess.run(["xdg-open", str(path.parent)], check=False, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        pass


async def deliver(name: str, data: bytes, media_type: str = "application/zip") -> None:
    """파일을 사용자에게 건넨다 — 브라우저면 다운로드, 네이티브 창이면 직접 저장.

    네이티브 모드에서 사용자가 저장 대화상자를 취소하면 아무것도 하지 않는다(취소했다는 안내만).
    """
    from nicegui import ui

    win = window()
    if win is None:
        ui.download.content(data, name, media_type=media_type)
        return

    fallback = False
    try:
        picked = await win.create_file_dialog(
            dialog_type=SAVE_DIALOG, directory=str(downloads_dir()), save_filename=name)
    except Exception:  # noqa: BLE001 — 대화상자를 못 띄우는 백엔드: 다운로드 폴더로 떨어뜨린다
        picked, fallback = None, True

    if fallback:
        target = unique_path(downloads_dir() / name)
    else:
        chosen = _first_path(picked)
        if chosen is None:  # 사용자가 취소 — 아무 일도 일어나지 않았다는 걸 알려 준다
            ui.notify("저장을 취소했습니다.", type="info")
            return
        target = Path(chosen)

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    except OSError as e:
        ui.notify(f"저장 실패: {e}", type="negative")
        return
    if fallback:  # 사용자가 위치를 고르지 않았으니 어디에 놓였는지 보여 준다
        reveal(target)
    ui.notify(f"저장됨: {target}", type="positive")


async def choose_folder(title: str, initial: str | None = None) -> str | None:
    """폴더 선택 — 네이티브 창이면 앱 자신의 대화상자로, 아니면 OS 대화상자(osascript 등)로.

    네이티브 창 모드에서 굳이 앱 대화상자를 쓰는 이유는 창 소유자가 우리 앱이라 앱 앞에 뜨기
    때문이다. 별도 프로세스(osascript)로 띄우면 앱 창 뒤에 숨을 수 있다.
    """
    from nicegui import run

    from md4paper.ui import folder_dialog

    win = window()
    if win is None:
        return await run.io_bound(folder_dialog.choose_folder, title, initial)
    try:
        picked = await win.create_file_dialog(
            dialog_type=20,  # webview.FileDialog.FOLDER
            directory=str(initial) if initial and Path(initial).is_dir() else "")
    except Exception:  # noqa: BLE001 — 웹뷰 대화상자 실패 시 OS 대화상자로 물러선다
        return await run.io_bound(folder_dialog.choose_folder, title, initial)
    return _first_path(picked)


def configure() -> None:
    """`ui.run(native=True)` 전에 웹뷰 쪽 설정을 얹는다 (창 크기·아이콘·다운로드·텍스트 선택).

    start_args/window_args/settings는 spawn으로 웹뷰 프로세스에 전달되므로 값은 전부 picklable해야
    한다(문자열·숫자·튜플만 넣는다).
    """
    from nicegui import app

    app.native.window_args["min_size"] = (960, 640)
    # pywebview는 text_select 기본값이 False라 `body { user-select: none; cursor: default }`를
    # 주입한다 → 앱 창에서는 마크다운 본문을 드래그로 선택·복사할 수 없다(브라우저 모드에선 됐다).
    # 논문을 읽고 인용을 퍼 가는 도구라 선택은 기본 기능이다 → 켠다.
    app.native.window_args["text_select"] = True
    # 웹뷰가 자체 저장 패널을 띄울 수 있게 — 우리가 처리하지 못한 다운로드가 조용히 사라지지 않도록.
    app.native.settings["ALLOW_DOWNLOADS"] = True
    if ICON.is_file():
        # macOS Dock 아이콘 (setApplicationIconImage_). 번들 없이 띄워도 파이썬 아이콘이 뜨지 않는다.
        app.native.start_args["icon"] = str(ICON)
