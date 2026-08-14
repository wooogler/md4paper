"""OS 기본 '폴더 선택' 대화상자 — 로컬 앱이라 서버 프로세스 = 사용자 컴퓨터다.

브라우저에는 실제 경로를 주는 폴더 선택 수단이 없다(File System Access API는 샌드박스 핸들만
주고 Chrome 전용). md4paper UI는 127.0.0.1에만 붙는 로컬 앱이므로, 서버 프로세스에서 OS
대화상자를 띄우면 사용자 화면에 그대로 뜬다 — macOS는 osascript, Windows는 PowerShell의
FolderBrowserDialog, Linux는 zenity/kdialog.

대화상자를 못 쓰는 환경(SSH·헤드리스·원격 브라우저)에서는 `available()`이 False가 되고
`choose_folder()`는 None을 반환한다. UI는 항상 경로를 직접 입력하는 칸을 함께 둔다.

이벤트 루프를 막으므로 NiceGUI에서는 `run.io_bound`로 호출할 것.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

# 사용자가 폴더를 고르는 데 걸리는 시간. 넘으면 포기하고 직접 입력으로 유도한다.
TIMEOUT_SEC = 300


def _disabled() -> bool:
    """테스트·헤드리스에서 대화상자를 강제로 끄는 탈출구."""
    return bool(os.environ.get("MD4PAPER_NO_NATIVE_DIALOG"))


def available() -> bool:
    """이 환경에서 폴더 선택 대화상자를 띄울 수 있는지."""
    if _disabled():
        return False
    if sys.platform == "darwin":
        return shutil.which("osascript") is not None
    if sys.platform.startswith("win"):
        return shutil.which("powershell") is not None or shutil.which("powershell.exe") is not None
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        return False  # X/Wayland 없음 → GUI 대화상자 불가
    return shutil.which("zenity") is not None or shutil.which("kdialog") is not None


def _run(cmd: list[str]) -> str | None:
    """대화상자 명령 실행 → 선택 경로 문자열. 취소·오류·타임아웃이면 None."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_SEC, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:  # 사용자가 취소(맥은 -128) 하거나 도구가 실패
        return None
    out = (proc.stdout or "").strip()
    return out or None


def _applescript(title: str, initial: str | None) -> list[str]:
    # AppleScript 문자열 리터럴 이스케이프 (제목에 따옴표가 들어와도 스크립트가 안 깨지게)
    def lit(s: str) -> str:
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'

    loc = ""
    if initial and Path(initial).is_dir():  # 없는 경로를 주면 choose folder가 오류를 낸다
        loc = f" default location POSIX file {lit(str(Path(initial).resolve()))}"
    return ["osascript", "-e", f"POSIX path of (choose folder with prompt {lit(title)}{loc})"]


def _powershell(title: str, initial: str | None) -> list[str]:
    def lit(s: str) -> str:
        return "'" + s.replace("'", "''") + "'"

    script = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$d = New-Object System.Windows.Forms.FolderBrowserDialog;"
        f"$d.Description = {lit(title)};"
        + (f"$d.SelectedPath = {lit(str(Path(initial).resolve()))};" if initial and Path(initial).is_dir() else "")
        + "if ($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) "
          "{ Write-Output $d.SelectedPath } else { exit 1 }"
    )
    exe = shutil.which("powershell") or "powershell.exe"
    return [exe, "-NoProfile", "-STA", "-Command", script]


def choose_folder(title: str = "폴더 선택", initial: str | None = None) -> str | None:
    """OS 폴더 선택 대화상자를 띄우고 고른 폴더의 절대경로를 반환. 취소·미지원이면 None."""
    if not available():
        return None
    if sys.platform == "darwin":
        picked = _run(_applescript(title, initial))
    elif sys.platform.startswith("win"):
        picked = _run(_powershell(title, initial))
    elif shutil.which("zenity"):
        cmd = ["zenity", "--file-selection", "--directory", f"--title={title}"]
        if initial and Path(initial).is_dir():
            cmd.append(f"--filename={Path(initial).resolve()}/")
        picked = _run(cmd)
    else:
        cmd = ["kdialog", "--title", title, "--getexistingdirectory",
               str(Path(initial).resolve()) if initial and Path(initial).is_dir() else str(Path.home())]
        picked = _run(cmd)
    if not picked:
        return None
    # macOS의 POSIX path는 폴더에 슬래시를 붙여 준다 ('/Users/me/Papers/') → 정규화
    picked = picked.splitlines()[0].strip()
    return str(Path(picked)) if picked else None
