"""데스크톱 런처 설치 — 더블클릭으로 앱 창을 여는 아이콘을 OS에 등록한다 (`md4paper app`).

셋 다 결국 같은 명령을 실행한다: `<파이썬> -m md4paper ui --native`.

| OS | 만드는 것 |
|---|---|
| macOS | `~/Applications/md4paper.app` (Launchpad·Spotlight·Dock) |
| Windows | 시작 메뉴 `md4paper.lnk` (`--desktop`을 주면 바탕화면에도) |
| Linux | `~/.local/share/applications/md4paper.desktop` |

macOS 번들은 조금 특이하다. 실행 파일 옆에 인터프리터를 심볼릭 링크로 두고 venv 구조
(`pyvenv.cfg`·`lib`)를 번들 안에 미러링한다. 그래야 **프로세스의 실행 경로가 번들 안**이 되어
macOS가 이 프로세스를 '이 앱'으로 인식한다 — Dock·Cmd+Tab에 md4paper 이름과 아이콘이 뜨고,
아이콘을 다시 눌러도 두 번 뜨지 않는다. 셸 스크립트가 번들 바깥의 파이썬을 실행하면 실행 중에는
'Python'이라는 이름의 앱이 된다(아이콘만 pywebview가 바꿔 준다).

설치 시점의 인터프리터 경로를 박아 넣으므로, 저장소를 옮기거나 가상환경을 갈아엎으면 런처를
다시 설치해야 한다 — 그때는 앱이 조용히 죽는 대신 로그 위치를 알려주는 알림을 띄운다.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from md4paper import __version__

APP_NAME = "md4paper"
BUNDLE_ID = "io.github.wooogler.md4paper"
SUMMARY = "논문 PDF를 마크다운과 한국어 번역으로"
ICON_PNG = Path(__file__).resolve().parent / "ui" / "assets" / "icon.png"
MAC_LOG = "$HOME/Library/Logs/md4paper.log"


class LauncherError(RuntimeError):
    """런처를 만들 수 없을 때 (지원하지 않는 OS, 아이콘 변환 실패 등)."""


# --- 실행 명령 -------------------------------------------------------------


def interpreter() -> Path:
    """런처가 실행할 파이썬. sys.executable을 그대로 쓴다 (resolve 금지 — 가상환경 신원이 사라진다)."""
    return Path(sys.executable)


def venv_root(exe: Path | None = None) -> Path | None:
    """이 인터프리터가 속한 가상환경 루트. 가상환경이 아니면 None."""
    exe = exe or interpreter()
    root = exe.parent.parent
    return root if (root / "pyvenv.cfg").exists() and (root / "lib").is_dir() else None


def launch_command(python: str | Path | None = None) -> list[str]:
    """런처가 실행하는 명령."""
    return [str(python or interpreter()), "-m", "md4paper", "ui", "--native"]


def default_location() -> Path:
    """런처가 설치될 기본 위치 (파일 자체의 경로)."""
    if sys.platform == "darwin":
        return Path.home() / "Applications" / f"{APP_NAME}.app"
    if sys.platform.startswith("win"):
        return _win_start_menu() / f"{APP_NAME}.lnk"
    return Path.home() / ".local" / "share" / "applications" / f"{APP_NAME}.desktop"


# --- 아이콘 변환 -----------------------------------------------------------


def _source_icon():  # noqa: ANN202 — PIL.Image.Image
    from PIL import Image

    if not ICON_PNG.is_file():
        raise LauncherError(f"아이콘 파일이 없습니다: {ICON_PNG}")
    return Image.open(ICON_PNG).convert("RGBA")


def write_icns(target: Path) -> None:
    """PNG → .icns. iconutil(맥 기본 도구)로 만들고, 없으면 Pillow 저장으로 물러선다."""
    from PIL import Image

    src = _source_icon()
    with tempfile.TemporaryDirectory() as td:
        iconset = Path(td) / f"{APP_NAME}.iconset"
        iconset.mkdir()
        for size in (16, 32, 128, 256, 512):
            src.resize((size, size), Image.LANCZOS).save(iconset / f"icon_{size}x{size}.png")
            src.resize((size * 2, size * 2), Image.LANCZOS).save(iconset / f"icon_{size}x{size}@2x.png")
        if shutil.which("iconutil"):
            proc = subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(target)],
                                  capture_output=True, check=False, timeout=120)
            if proc.returncode == 0 and target.exists():
                return
    src.save(target)  # Pillow의 ICNS 저장 (해상도는 적지만 아이콘은 나온다)


def write_ico(target: Path) -> None:
    """PNG → .ico (윈도우 바로가기 아이콘)."""
    _source_icon().save(target, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


# --- macOS -----------------------------------------------------------------


def _mac_alert(repo_hint: str) -> str:
    """실행이 실패했을 때 띄울 알림 (Finder에서 띄우면 터미널 출력이 어디에도 안 남으므로)."""
    message = (f"로그: ~/Library/Logs/md4paper.log\n\n"
               f"의존성이 빠졌을 수 있습니다. 터미널에서:\n{repo_hint}")
    return (f'display alert "md4paper를 열지 못했습니다" message "{message}" as critical')


def _mac_wrapper(python_cmd: str, repo_hint: str) -> str:
    return f"""#!/bin/sh
# md4paper 런처 — `md4paper app`이 자동 생성합니다. 직접 고치면 다음 설치 때 덮어씁니다.
# Finder에서 띄우면 표준 출력이 어디에도 남지 않으므로 로그 파일로 모으고, 실패하면 알림을 띄운다.
HERE=$(cd "$(dirname "$0")" && pwd)
LOG="{MAC_LOG}"
mkdir -p "$(dirname "$LOG")"
printf '\\n=== %s ===\\n' "$(date)" >>"$LOG"
{python_cmd} -m md4paper ui --native >>"$LOG" 2>&1
status=$?
if [ $status -ne 0 ]; then
  osascript -e '{_mac_alert(repo_hint)}' >/dev/null 2>&1
fi
exit $status
"""


def _plist() -> str:
    keys = {
        "CFBundleName": APP_NAME,
        "CFBundleDisplayName": APP_NAME,
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleExecutable": APP_NAME,
        "CFBundleIconFile": f"{APP_NAME}.icns",
        "CFBundlePackageType": "APPL",
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleShortVersionString": __version__,
        "CFBundleVersion": __version__,
        "LSMinimumSystemVersion": "11.0",
        "LSApplicationCategoryType": "public.app-category.productivity",
    }
    body = "".join(f"\t<key>{k}</key>\n\t<string>{v}</string>\n" for k, v in keys.items())
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
            '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            '<plist version="1.0">\n<dict>\n'
            f"{body}"
            "\t<key>NSHighResolutionCapable</key>\n\t<true/>\n"
            "</dict>\n</plist>\n")


def _repo_hint() -> str:
    """의존성이 빠졌을 때 안내할 명령 (저장소에서 실행 중이면 그 경로까지)."""
    from md4paper.config import _project_root

    root = _project_root()
    return (f"cd {root} && uv sync --extra ui --extra native" if root
            else "pip install 'md4paper[ui,native]'")


def _install_macos(app_dir: Path) -> Path:
    contents = app_dir / "Contents"
    macos, resources = contents / "MacOS", contents / "Resources"
    if app_dir.exists():
        shutil.rmtree(app_dir)  # 다시 설치하면 통째로 새로 만든다 (낡은 심볼릭 링크가 남지 않게)
    macos.mkdir(parents=True)
    resources.mkdir(parents=True)

    exe = interpreter()
    venv = venv_root(exe)
    if venv:
        # 번들 안에 가상환경 구조를 비춰 둔다 — 실행 경로가 번들 안이어야 macOS가 이 앱으로 인식한다.
        (macos / "python").symlink_to(exe)
        (contents / "pyvenv.cfg").symlink_to(venv / "pyvenv.cfg")
        (contents / "lib").symlink_to(venv / "lib")
        python_cmd = '"$HERE/python"'
    else:
        python_cmd = shlex.quote(str(exe))

    launcher = macos / APP_NAME
    launcher.write_text(_mac_wrapper(python_cmd, _repo_hint()), encoding="utf-8")
    launcher.chmod(0o755)
    (contents / "Info.plist").write_text(_plist(), encoding="utf-8")
    (contents / "PkgInfo").write_text("APPL????", encoding="utf-8")
    write_icns(resources / f"{APP_NAME}.icns")
    _lsregister(app_dir)
    return app_dir


def _lsregister(app_dir: Path) -> None:
    """LaunchServices에 즉시 등록 — Spotlight·Launchpad가 바로 잡도록. 실패해도 무시(부가)."""
    tool = ("/System/Library/Frameworks/CoreServices.framework/Frameworks/"
            "LaunchServices.framework/Support/lsregister")
    if not Path(tool).exists():
        return
    try:
        subprocess.run([tool, "-f", str(app_dir)], capture_output=True, check=False, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        pass


# --- Windows ---------------------------------------------------------------


def _win_start_menu() -> Path:
    appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def _win_pythonw(exe: Path) -> Path:
    """콘솔 창이 함께 뜨지 않게 pythonw.exe를 쓴다 (없으면 python.exe)."""
    candidate = exe.with_name("pythonw.exe")
    return candidate if candidate.exists() else exe


def _install_windows(link: Path, also_desktop: bool = False) -> Path:
    icon = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "md4paper" / "md4paper.ico"
    icon.parent.mkdir(parents=True, exist_ok=True)
    write_ico(icon)

    targets = [link] + ([Path.home() / "Desktop" / link.name] if also_desktop else [])
    exe = _win_pythonw(interpreter())
    for path in targets:
        path.parent.mkdir(parents=True, exist_ok=True)
        script = (
            "$s = (New-Object -ComObject WScript.Shell).CreateShortcut({lnk});"
            "$s.TargetPath = {exe};"
            "$s.Arguments = '-m md4paper ui --native';"
            "$s.WorkingDirectory = {cwd};"
            "$s.IconLocation = {icon};"
            "$s.Description = {desc};"
            "$s.Save()"
        ).format(lnk=_ps_str(str(path)), exe=_ps_str(str(exe)), cwd=_ps_str(str(Path.home())),
                 icon=_ps_str(str(icon)), desc=_ps_str(SUMMARY))
        powershell = shutil.which("powershell") or shutil.which("powershell.exe")
        if not powershell:
            raise LauncherError("PowerShell을 찾지 못해 바로가기를 만들지 못했습니다.")
        proc = subprocess.run([powershell, "-NoProfile", "-Command", script],
                              capture_output=True, text=True, check=False, timeout=120)
        if proc.returncode != 0:
            raise LauncherError(f"바로가기 생성 실패: {proc.stderr.strip()}")
    return link


def _ps_str(value: str) -> str:
    """PowerShell 작은따옴표 문자열 리터럴."""
    return "'" + value.replace("'", "''") + "'"


# --- Linux -----------------------------------------------------------------


def _install_linux(entry: Path) -> Path:
    icon = Path.home() / ".local" / "share" / "icons" / "hicolor" / "512x512" / "apps" / f"{APP_NAME}.png"
    icon.parent.mkdir(parents=True, exist_ok=True)
    from PIL import Image

    _source_icon().resize((512, 512), Image.LANCZOS).save(icon)

    entry.parent.mkdir(parents=True, exist_ok=True)
    exec_line = " ".join(shlex.quote(p) for p in launch_command())
    entry.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={APP_NAME}\n"
        f"Comment={SUMMARY}\n"
        f"Exec={exec_line}\n"
        f"Icon={icon}\n"
        "Terminal=false\n"
        "Categories=Office;Science;Utility;\n"
        f"StartupWMClass={APP_NAME}\n",
        encoding="utf-8")
    entry.chmod(0o755)
    if shutil.which("update-desktop-database"):
        try:
            subprocess.run(["update-desktop-database", str(entry.parent)],
                           capture_output=True, check=False, timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            pass  # 메뉴 갱신은 부가 기능 — 실패해도 런처는 만들어졌다
    return entry


# --- 공개 API --------------------------------------------------------------


def install(dest: Path | None = None, also_desktop: bool = False) -> Path:
    """런처를 만들고 그 경로를 반환. dest는 파일/번들 자체의 경로(기본: `default_location()`)."""
    target = Path(dest).expanduser() if dest else default_location()
    if sys.platform == "darwin":
        return _install_macos(target)
    if sys.platform.startswith("win"):
        return _install_windows(target, also_desktop)
    return _install_linux(target)


def remove(dest: Path | None = None) -> list[Path]:
    """설치한 런처를 지우고 지운 경로 목록을 반환 (없으면 빈 목록)."""
    target = Path(dest).expanduser() if dest else default_location()
    candidates = [target]
    if sys.platform.startswith("win"):
        candidates.append(Path.home() / "Desktop" / target.name)
    removed = []
    for path in candidates:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
            removed.append(path)
        elif path.exists() or path.is_symlink():
            path.unlink()
            removed.append(path)
    return removed
