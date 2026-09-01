"""데스크톱 런처 생성 — 번들 구조·실행 명령·제거 (앱을 실제로 띄우지는 않는다)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from md4paper import launcher

MACOS_ONLY = pytest.mark.skipif(sys.platform != "darwin", reason="macOS 앱 번들")


def _fake_venv(tmp_path: Path) -> Path:
    """pyvenv.cfg·lib를 갖춘 가짜 가상환경의 인터프리터 경로 (심볼릭 링크 대상이 실제로 있어야 한다)."""
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True, exist_ok=True)
    (venv / "lib").mkdir(exist_ok=True)
    (venv / "pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")
    exe = venv / "bin" / "python"
    exe.write_text("#!/bin/sh\n", encoding="utf-8")
    exe.chmod(0o755)
    return exe


@pytest.fixture
def no_lsregister(monkeypatch):
    """테스트가 LaunchServices에 임시 번들을 등록하지 않도록."""
    monkeypatch.setattr(launcher, "_lsregister", lambda app_dir: None)


def test_venv_root_detected(tmp_path):
    exe = _fake_venv(tmp_path)
    assert launcher.venv_root(exe) == tmp_path / "venv"
    assert launcher.venv_root(tmp_path / "usr" / "bin" / "python") is None  # 가상환경이 아님


def test_launch_command_opens_native_ui():
    assert launcher.launch_command("/x/python") == ["/x/python", "-m", "md4paper", "ui", "--native"]


def test_icon_asset_is_packaged():
    """아이콘 PNG는 패키지에 커밋돼 있어야 한다 (설치 시점에 그리지 않는다)."""
    from PIL import Image

    assert launcher.ICON_PNG.is_file()
    with Image.open(launcher.ICON_PNG) as im:
        assert im.size == (1024, 1024)


def test_write_ico_has_multiple_sizes(tmp_path):
    from PIL import Image

    target = tmp_path / "md4paper.ico"
    launcher.write_ico(target)
    with Image.open(target) as im:
        assert (16, 16) in im.info["sizes"] and (256, 256) in im.info["sizes"]


@MACOS_ONLY
def test_macos_bundle_mirrors_venv(tmp_path, monkeypatch, no_lsregister):
    """번들 안 인터프리터로 실행돼야 macOS가 이 프로세스를 'md4paper' 앱으로 인식한다."""
    exe = _fake_venv(tmp_path)
    monkeypatch.setattr(launcher, "interpreter", lambda: exe)

    app_dir = launcher.install(tmp_path / "md4paper.app")
    contents = app_dir / "Contents"

    assert (contents / "MacOS" / "python").is_symlink()
    assert (contents / "MacOS" / "python").resolve() == exe.resolve()
    assert (contents / "pyvenv.cfg").is_symlink()  # 번들이 가상환경 모양을 갖춰야 import가 된다
    assert (contents / "lib").is_symlink()

    wrapper = contents / "MacOS" / "md4paper"
    assert '"$HERE/python" -m md4paper ui --native' in wrapper.read_text(encoding="utf-8")
    assert os.access(wrapper, os.X_OK)


@MACOS_ONLY
def test_macos_bundle_identity_and_icon(tmp_path, monkeypatch, no_lsregister):
    exe = _fake_venv(tmp_path)
    monkeypatch.setattr(launcher, "interpreter", lambda: exe)
    contents = launcher.install(tmp_path / "md4paper.app") / "Contents"

    from md4paper import __version__

    plist = (contents / "Info.plist").read_text(encoding="utf-8")
    assert "<string>io.github.wooogler.md4paper</string>" in plist
    assert "<string>md4paper.icns</string>" in plist
    assert f"<string>{__version__}</string>" in plist  # Finder '정보 가져오기'의 버전
    assert (contents / "Resources" / "md4paper.icns").stat().st_size > 1000
    assert (contents / "PkgInfo").read_text(encoding="utf-8") == "APPL????"


@MACOS_ONLY
def test_macos_bundle_without_venv_uses_absolute_interpreter(tmp_path, monkeypatch, no_lsregister):
    """가상환경이 아니면(시스템 파이썬 등) 심볼릭 링크 대신 절대 경로로 실행한다."""
    exe = tmp_path / "opt" / "bin" / "python3"
    exe.parent.mkdir(parents=True)
    exe.write_text("", encoding="utf-8")
    monkeypatch.setattr(launcher, "interpreter", lambda: exe)

    contents = launcher.install(tmp_path / "md4paper.app") / "Contents"
    assert not (contents / "MacOS" / "python").exists()
    assert f"{exe} -m md4paper ui --native" in (contents / "MacOS" / "md4paper").read_text(encoding="utf-8")


@MACOS_ONLY
def test_macos_wrapper_reports_failure_instead_of_dying_silently(tmp_path, monkeypatch, no_lsregister):
    """Finder에서 띄우면 출력이 어디에도 안 남으므로, 로그로 남기고 실패는 알림으로 알려야 한다."""
    exe = _fake_venv(tmp_path)
    monkeypatch.setattr(launcher, "interpreter", lambda: exe)
    wrapper = (launcher.install(tmp_path / "md4paper.app")
               / "Contents" / "MacOS" / "md4paper").read_text(encoding="utf-8")
    assert "Library/Logs/md4paper.log" in wrapper
    assert "osascript" in wrapper and "display alert" in wrapper


@MACOS_ONLY
def test_reinstall_replaces_old_bundle(tmp_path, monkeypatch, no_lsregister):
    exe = _fake_venv(tmp_path)
    monkeypatch.setattr(launcher, "interpreter", lambda: exe)
    app_dir = launcher.install(tmp_path / "md4paper.app")
    (app_dir / "Contents" / "낡은파일").write_text("stale", encoding="utf-8")
    launcher.install(tmp_path / "md4paper.app")
    assert not (app_dir / "Contents" / "낡은파일").exists()


@MACOS_ONLY
def test_remove_deletes_bundle(tmp_path, monkeypatch, no_lsregister):
    exe = _fake_venv(tmp_path)
    monkeypatch.setattr(launcher, "interpreter", lambda: exe)
    app_dir = launcher.install(tmp_path / "md4paper.app")
    assert launcher.remove(app_dir) == [app_dir]
    assert not app_dir.exists()
    assert launcher.remove(app_dir) == []  # 두 번 지워도 조용히 빈 목록


def test_linux_desktop_entry(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher.sys, "platform", "linux")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(launcher.shutil, "which", lambda name: None)  # update-desktop-database 없음

    entry = launcher.install(tmp_path / "md4paper.desktop")
    text = entry.read_text(encoding="utf-8")

    assert "Type=Application" in text and "Terminal=false" in text
    assert "-m md4paper ui --native" in text
    icon = next(line[len("Icon="):] for line in text.splitlines() if line.startswith("Icon="))
    assert Path(icon).is_file()  # 아이콘 PNG도 함께 설치돼야 메뉴에 그림이 뜬다


def test_default_location_per_platform(monkeypatch):
    monkeypatch.setattr(launcher.sys, "platform", "darwin")
    assert launcher.default_location().name == "md4paper.app"
    monkeypatch.setattr(launcher.sys, "platform", "linux")
    assert launcher.default_location().name == "md4paper.desktop"
    monkeypatch.setattr(launcher.sys, "platform", "win32")
    assert launcher.default_location().name == "md4paper.lnk"


@MACOS_ONLY
def test_cli_installs_and_removes(tmp_path, monkeypatch, no_lsregister):
    from click.testing import CliRunner

    from md4paper.cli import cli

    exe = _fake_venv(tmp_path)
    monkeypatch.setattr(launcher, "interpreter", lambda: exe)
    dest = tmp_path / "md4paper.app"

    result = CliRunner().invoke(cli, ["app", "--dir", str(dest)])
    assert result.exit_code == 0, result.output
    assert "런처 설치됨" in result.output
    assert dest.is_dir()

    result = CliRunner().invoke(cli, ["app", "--dir", str(dest), "--remove"])
    assert result.exit_code == 0, result.output
    assert not dest.exists()


# --- 앱 창을 못 띄우는 환경에서의 폴백 (배포된 컴퓨터에서 실제로 생기는 상황) ---


def test_native_falls_back_to_browser_without_pywebview(monkeypatch, capsys):
    from md4paper import cli

    monkeypatch.setattr(cli.importlib.util, "find_spec", lambda name: None)
    assert cli._native_available() is False
    assert "브라우저로 엽니다" in capsys.readouterr().err  # 아이콘이 '무반응'이 되지 않게


def test_native_falls_back_when_gui_backend_missing(monkeypatch, capsys):
    """리눅스에서 WebKit2GTK·Qt가 없으면 pywebview는 import까지만 되고 창에서 죽는다."""
    pytest.importorskip("webview", reason="앱 창 의존성(native extra) 미설치")
    import importlib

    from md4paper import cli

    # webview 패키지는 `guilib`라는 이름을 모듈 변수(None)로도 쓴다 — 서브모듈은 import_module로 잡는다.
    guilib = importlib.import_module("webview.guilib")

    def boom(*a, **k):
        raise RuntimeError("You must have either QT or GTK with Python extensions installed")

    monkeypatch.setattr(guilib, "initialize", boom)
    assert cli._native_available() is False
    assert "앱 창을 띄울 수 없어" in capsys.readouterr().err


def test_native_available_when_backend_loads():
    pytest.importorskip("webview", reason="앱 창 의존성(native extra) 미설치")
    from md4paper import cli

    assert cli._native_available() is True
