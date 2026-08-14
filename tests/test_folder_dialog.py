"""OS 폴더 선택 대화상자 — 명령 구성·취소 처리 (실제 대화상자는 띄우지 않는다)."""

import subprocess

from md4paper.ui import folder_dialog


def test_disabled_by_env(monkeypatch):
    monkeypatch.setenv("MD4PAPER_NO_NATIVE_DIALOG", "1")
    assert folder_dialog.available() is False
    assert folder_dialog.choose_folder() is None  # 헤드리스에선 조용히 None → UI는 직접 입력으로


def test_applescript_escapes_and_skips_missing_location(tmp_path):
    script = folder_dialog._applescript('폴더 "선택"', str(tmp_path / "없는폴더"))[-1]
    assert '\\"선택\\"' in script  # 제목의 따옴표가 스크립트를 깨뜨리지 않게 이스케이프
    assert "default location" not in script  # 없는 경로를 주면 choose folder가 오류를 낸다

    script2 = folder_dialog._applescript("폴더 선택", str(tmp_path))[-1]
    assert f'POSIX file "{tmp_path.resolve()}"' in script2


def test_cancel_returns_none(monkeypatch):
    monkeypatch.delenv("MD4PAPER_NO_NATIVE_DIALOG", raising=False)
    monkeypatch.setattr(folder_dialog, "available", lambda: True)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a, 1, "", "User canceled. (-128)"))
    assert folder_dialog.choose_folder() is None


def test_picked_path_normalized(monkeypatch, tmp_path):
    """맥 osascript는 폴더 경로 끝에 슬래시를 붙여 준다 → 정규화해서 돌려준다."""
    monkeypatch.delenv("MD4PAPER_NO_NATIVE_DIALOG", raising=False)
    monkeypatch.setattr(folder_dialog, "available", lambda: True)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a, 0, f"{tmp_path}/\n", ""))
    assert folder_dialog.choose_folder() == str(tmp_path)


def test_timeout_returns_none(monkeypatch):
    monkeypatch.delenv("MD4PAPER_NO_NATIVE_DIALOG", raising=False)
    monkeypatch.setattr(folder_dialog, "available", lambda: True)

    def timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd="osascript", timeout=1)

    monkeypatch.setattr(subprocess, "run", timeout)
    assert folder_dialog.choose_folder() is None
