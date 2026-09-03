"""추가 앱 창 — 논문을 나란히 놓고 보려고 띄우는 형제 창.

`python -m md4paper.ui.window <URL> [제목]`으로 창 하나를 띄우는 작은 실행 모듈이다.
NiceGUI의 네이티브 모드는 웹뷰 프로세스를 하나 띄우고 **그 창 하나**의 메서드만 큐로 중계하므로
서버 프로세스에서 두 번째 창을 만들 방법이 없다(§nicegui/native/native_mode.py). 그래서 같은
인터프리터로 프로세스를 하나 더 띄워 같은 로컬 주소를 가리키게 한다 — 서버는 그대로 하나다.

브라우저 모드에서는 이 모듈이 필요 없다: 새 탭(`ui.navigate.to(..., new_tab=True)`)이 곧 새 창이다.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from md4paper.ui import desktop

# 우리가 띄운 형제 창들: [(url, Popen)] — 서버 종료 시 함께 닫고, 같은 논문을 두 번 띄우지 않는다.
_CHILDREN: list[tuple[str, subprocess.Popen]] = []


def _prune() -> None:
    """끝난 자식은 목록에서 뺀다. poll()이 좀비도 함께 거둔다."""
    _CHILDREN[:] = [(u, p) for u, p in _CHILDREN if p.poll() is None]


def open_new(url: str, title: str = "md4paper") -> subprocess.Popen | None:
    """URL을 새 앱 창으로 띄우고 그 프로세스를 반환. 못 띄우면 None.

    프로세스가 떴다는 것과 창이 실제로 그려졌다는 것은 다르다(pywebview 미설치·백엔드 없음 등).
    그래서 성공 안내는 호출자가 `died()`로 잠깐 지켜본 뒤에 하도록 프로세스를 그대로 돌려준다.
    """
    _prune()
    try:
        proc = subprocess.Popen([sys.executable, "-m", "md4paper.ui.window", url, title])
    except OSError:
        return None
    _CHILDREN.append((url, proc))
    return proc


def already_open(url: str) -> bool:
    """이 주소가 이미 형제 창으로 떠 있는지 — 같은 논문을 두 창에서 고치면 나중 저장이 앞을 덮는다."""
    _prune()
    return any(u == url for u, _ in _CHILDREN)


def died(proc: subprocess.Popen) -> int | None:
    """이미 끝난 프로세스면 종료 코드, 아직 살아 있으면 None."""
    return proc.poll()


def any_open() -> bool:
    """지금 떠 있는 형제 창이 있는지 (죽은 프로세스는 정리하면서)."""
    _prune()
    return bool(_CHILDREN)


def close_all() -> None:
    """서버가 내려갈 때 형제 창도 함께 닫는다 — 서버 없는 창은 빈 페이지만 남는다."""
    for _, proc in _CHILDREN:
        if proc.poll() is None:
            proc.terminate()
    for _, proc in _CHILDREN:
        try:
            proc.wait(timeout=3)  # 거두고 나간다 (좀비를 남기지 않게)
        except subprocess.TimeoutExpired:
            proc.kill()
    _CHILDREN.clear()


def _main(argv: list[str]) -> int:
    if not argv:
        print("사용법: python -m md4paper.ui.window <URL> [제목]", file=sys.stderr)
        return 2
    try:
        import webview
    except ImportError:
        print("pywebview 미설치 — `uv sync --extra native`", file=sys.stderr)
        return 1
    url, title = argv[0], (argv[1] if len(argv) > 1 else "md4paper")
    # 본 창과 같은 조건으로 (본문 드래그 선택 허용 · 최소 크기 · Dock 아이콘).
    webview.settings["ALLOW_DOWNLOADS"] = True
    webview.create_window(title, url, width=1280, height=880, min_size=(960, 640), text_select=True)
    # 아이콘은 PNG다. cocoa·GTK·Qt는 PNG를 받지만 윈도우(winforms)는 System.Drawing.Icon으로
    # 넘겨 .ico가 아니면 예외가 난다 → 윈도우에서는 넘기지 않는다(창이 안 뜨는 것보다 낫다).
    icon = str(desktop.ICON) if not sys.platform.startswith("win") and Path(desktop.ICON).is_file() else None
    webview.start(**({"icon": icon} if icon else {}))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
