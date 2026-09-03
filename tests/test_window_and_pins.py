"""고정(pin) · 형제 앱 창 · 찾기 바 · 읽던 자리 — UI 없이 검증 가능한 부분.

리뷰 화면을 실제로 띄우지 않고도 확인할 수 있는 것들만 본다: status.json에 남는 고정 표시,
형제 창 프로세스 관리(진짜 창은 띄우지 않는다 — Popen을 가로챈다), 창이 여러 개일 때의 저장
대화상자 분기, 그리고 브라우저로 내려가는 스크립트가 온전히 만들어지는지.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

from md4paper import pipeline
from md4paper.workdir import (
    WorkDir,
    is_pinned,
    pinned_workdirs,
    recent_workdirs,
    set_hidden,
    set_pinned,
)

CORPUS = Path(__file__).parent / "corpus" / "sample_arxiv.md"


def _paper(ws: Path, name: str) -> WorkDir:
    """작업 폴더 안에 논문 하나를 변환해 둔다 (LLM·네트워크 없음)."""
    wd = WorkDir(ws / name / f"{name}.md4")
    pipeline.convert(CORPUS, wd)
    return wd


# ===== 고정 (status.json) =====

def test_pin_marks_and_unmarks(tmp_path):
    """고정은 status.json의 pinned_at 표시일 뿐 — 파일은 건드리지 않는다."""
    wd = _paper(tmp_path, "p1")
    assert is_pinned(wd.root) is False

    assert set_pinned(wd.root) is True
    assert is_pinned(wd.root) is True
    assert wd.en_md.exists()  # 결과물은 그대로

    assert set_pinned(wd.root, False) is True
    assert is_pinned(wd.root) is False
    assert "pinned_at" not in wd.load_status()


def test_pin_keeps_original_order_when_pinned_again(tmp_path):
    """이미 고정된 논문을 다시 고정해도 '고정한 시각'은 그대로 — 탭 자리가 튀지 않게."""
    wd = _paper(tmp_path, "p1")
    set_pinned(wd.root)
    first = wd.load_status()["pinned_at"]
    set_pinned(wd.root)
    assert wd.load_status()["pinned_at"] == first


def test_pin_rejects_missing_workdir(tmp_path):
    assert set_pinned(tmp_path / "없는논문.md4") is False
    assert is_pinned(tmp_path / "없는논문.md4") is False


def test_pinned_workdirs_is_ordered_by_pin_time(tmp_path):
    """탭 순서 = 고정한 순서 (제목·수정시각과 무관하게 안정적이어야 한다)."""
    a, b, c = (_paper(tmp_path, n) for n in ("a", "b", "c"))
    for wd, at in ((b, 300.0), (a, 100.0), (c, 200.0)):  # 고정 시각을 직접 박아 순서를 못 박는다
        set_pinned(wd.root)
        st = wd.load_status()
        st["pinned_at"] = at
        wd.save_status(st)

    assert [p["root"].stem for p in pinned_workdirs(tmp_path)] == ["a", "c", "b"]
    assert [p["title"] for p in pinned_workdirs(tmp_path)]  # 제목이 채워진다


def test_pinned_workdirs_uses_paper_meta_title(tmp_path):
    """제목은 recent_workdirs와 같은 규칙 — paper_meta.json이 있으면 그것."""
    wd = _paper(tmp_path, "p1")
    set_pinned(wd.root)
    wd.paper_meta_json.write_text(json.dumps({"title": "정정된 제목"}), encoding="utf-8")

    assert pinned_workdirs(tmp_path)[0]["title"] == "정정된 제목"
    assert recent_workdirs(tmp_path)[0]["title"] == "정정된 제목"


def test_pinned_workdirs_skips_hidden_and_broken(tmp_path):
    """숨긴 논문은 탭에 올리지 않고, 손상된 status.json이 목록을 깨뜨리지 않는다."""
    keep, hide, broken = (_paper(tmp_path, n) for n in ("keep", "hide", "broken"))
    for wd in (keep, hide, broken):
        set_pinned(wd.root)
    set_hidden(hide.root)
    broken.status_json.write_text("{이건 JSON이 아니다", encoding="utf-8")

    assert [p["root"].stem for p in pinned_workdirs(tmp_path)] == ["keep"]

    broken.status_json.write_text(json.dumps({"pinned_at": "어제"}), encoding="utf-8")
    assert [p["root"].stem for p in pinned_workdirs(tmp_path)] == ["keep"]  # 숫자가 아니면 건너뛴다


def test_recent_workdirs_reports_pin_and_keeps_it_past_the_limit(tmp_path):
    """고정한 논문은 최근 목록의 limit 밖으로 밀려도 남는다 — 고정의 뜻이 그것이다."""
    old = _paper(tmp_path, "오래된")
    new = _paper(tmp_path, "새것")
    import os
    import time

    os.utime(old.root, (time.time() - 9999, time.time() - 9999))
    set_pinned(old.root)

    rows = recent_workdirs(tmp_path, limit=1)
    names = [r["name"] for r in rows]
    assert names[0] == "새것"          # 최근 것이 먼저
    assert "오래된" in names           # 고정한 것은 잘려 나가지 않는다
    assert {r["name"]: r["pinned"] for r in rows} == {"새것": False, "오래된": True}
    assert new.root.is_dir()


def test_save_status_is_atomic_and_leaves_no_temp(tmp_path):
    """창이 여러 개면 같은 status.json을 두 프로세스가 쓴다 → 반쯤 쓴 파일이 남지 않아야 한다."""
    wd = _paper(tmp_path, "p1")
    set_pinned(wd.root)
    wd.save_status({**wd.load_status(), "note": "다시 쓰기"})

    assert json.loads(wd.status_json.read_text(encoding="utf-8"))["note"] == "다시 쓰기"
    assert not list(wd.root.glob("status.json.tmp*"))  # 임시 파일을 남기지 않는다


# ===== 형제 앱 창 (§ui/window.py) =====

class FakeProc:
    """subprocess.Popen 흉내 — 살아 있음/끝남만 흉내 낸다."""

    def __init__(self, code: int | None = None) -> None:
        self.code, self.terminated, self.killed, self.waited = code, False, False, False

    def poll(self) -> int | None:
        return self.code

    def terminate(self) -> None:
        self.terminated = True
        self.code = -15  # 종료 요청을 받으면 죽은 것으로 (close_all의 wait가 걸리지 않게)

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout=None):  # noqa: ANN001, ANN201
        self.waited = True
        return self.code


@pytest.fixture
def win_mod(monkeypatch):
    """window 모듈 — 프로세스 목록을 비우고 Popen을 가로챈 상태로."""
    from md4paper.ui import window

    window._CHILDREN.clear()
    spawned: list[list[str]] = []

    def fake_popen(argv, *a, **kw):  # noqa: ANN001, ANN002, ANN003, ANN202
        spawned.append(list(argv))
        return FakeProc()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    yield window, spawned
    window._CHILDREN.clear()


def test_open_new_spawns_our_own_module(win_mod):
    """형제 창 = 같은 인터프리터로 이 모듈을 실행하는 프로세스 (셸을 거치지 않는다)."""
    window, spawned = win_mod
    proc = window.open_new("http://127.0.0.1:9999/review?wd=/tmp/a.md4", "제목")

    assert proc is not None
    assert spawned == [[sys.executable, "-m", "md4paper.ui.window",
                        "http://127.0.0.1:9999/review?wd=/tmp/a.md4", "제목"]]
    assert window.any_open() is True
    assert window.died(proc) is None


def test_open_new_reports_failure(win_mod, monkeypatch):
    """프로세스를 못 띄우면 None — 호출자가 '열었습니다'라고 하지 않도록."""
    window, _ = win_mod
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: (_ for _ in ()).throw(OSError("no exec")))
    assert window.open_new("http://127.0.0.1:9999/x") is None
    assert window.any_open() is False


def test_already_open_blocks_the_same_paper_twice(win_mod):
    """같은 논문을 두 창에서 고치면 하이라이트·메모는 나중 저장이 앞을 덮는다 → 두 번 열지 않는다."""
    window, spawned = win_mod
    url = "http://127.0.0.1:9999/review?wd=/tmp/a.md4"
    window.open_new(url)

    assert window.already_open(url) is True
    assert window.already_open(url + "&other=1") is False
    assert len(spawned) == 1


def test_closed_children_drop_out_of_the_list(win_mod):
    """창을 사용자가 닫으면 목록에서 빠진다 (좀비도 poll로 거둔다)."""
    window, _ = win_mod
    url = "http://127.0.0.1:9999/review?wd=/tmp/a.md4"
    proc = window.open_new(url)
    proc.code = 0  # 사용자가 창을 닫았다

    assert window.any_open() is False
    assert window.already_open(url) is False
    assert window.died(proc) == 0


def test_close_all_terminates_and_reaps(win_mod):
    """서버가 내려가면 형제 창도 함께 닫고 거둔다."""
    window, _ = win_mod
    procs = [window.open_new(f"http://127.0.0.1:9999/{i}") for i in range(3)]
    window.close_all()

    assert all(p.terminated and p.waited for p in procs)
    assert window.any_open() is False


# ===== 창이 여러 개일 때의 저장·폴더 대화상자 (§ui/desktop.py) =====

def test_extra_windows_switch_download_to_the_downloads_folder(tmp_path, monkeypatch):
    """형제 창이 있으면 저장 대화상자를 띄우지 않는다 — 본 창에 붙어 '먹통'처럼 보이기 때문."""
    pytest.importorskip("nicegui", reason="웹 UI 의존성(ui extra) 미설치")
    from nicegui import ui

    from md4paper.ui import desktop

    class Win:
        def __init__(self) -> None:
            self.calls = 0

        async def create_file_dialog(self, **kw):  # noqa: ANN003, ANN201
            self.calls += 1
            return None

    win = Win()
    notices: list[str] = []
    monkeypatch.setattr(desktop, "window", lambda: win)
    monkeypatch.setattr(desktop, "extra_windows_open", lambda: True)
    monkeypatch.setattr(desktop, "downloads_dir", lambda: tmp_path)
    monkeypatch.setattr(desktop, "reveal", lambda p: None)
    monkeypatch.setattr(ui, "notify", lambda msg, **kw: notices.append(str(msg)))

    asyncio.run(desktop.deliver("paper.zip", b"zipdata"))

    assert win.calls == 0                                   # 대화상자를 부르지 않았다
    assert (tmp_path / "paper.zip").read_bytes() == b"zipdata"
    assert any("저장됨" in m for m in notices)               # 어디에 놓였는지는 알려 준다


def test_folder_dialog_stays_on_main_window_without_os_dialog(monkeypatch):
    """OS 폴더 대화상자를 못 쓰는 환경이면, 창이 여러 개여도 앱 창 대화상자를 쓴다.

    (엉뚱한 창에 뜨는 것보다 '고를 방법이 아예 없음'이 더 나쁘다.)
    """
    pytest.importorskip("nicegui", reason="웹 UI 의존성(ui extra) 미설치")
    from md4paper.ui import desktop, folder_dialog

    class Win:
        def __init__(self) -> None:
            self.calls = 0

        async def create_file_dialog(self, **kw):  # noqa: ANN003, ANN201
            self.calls += 1
            return ("/골라둔/폴더",)

    win = Win()
    monkeypatch.setattr(desktop, "window", lambda: win)
    monkeypatch.setattr(desktop, "extra_windows_open", lambda: True)
    monkeypatch.setattr(folder_dialog, "available", lambda: False)
    monkeypatch.setattr(folder_dialog, "choose_folder", lambda *a, **kw: pytest.fail("OS 대화상자를 쓸 수 없다"))

    got = asyncio.run(desktop.choose_folder("폴더 고르기"))

    assert got == "/골라둔/폴더"
    assert win.calls == 1


def test_desktop_asks_the_window_module_about_extra_windows(win_mod):
    """desktop은 window 모듈에 물어본다 (순환 import 없이 지연 import로)."""
    window, _ = win_mod
    from md4paper.ui import desktop

    assert desktop.extra_windows_open() is False
    window.open_new("http://127.0.0.1:9999/review?wd=/tmp/a.md4")
    assert desktop.extra_windows_open() is True


# ===== 브라우저로 내려가는 스크립트 (§ui/find_bar.py, §ui/scroll_memory.py) =====

def test_find_bar_script_is_complete():
    """찾기 바 스크립트가 온전한지 — 파이썬 문자열 안의 JS라 이스케이프가 깨지기 쉽다."""
    from md4paper.ui import find_bar

    assert find_bar.HTML.count("<script>") == find_bar.HTML.count("</script>") == 1
    assert 'id="md4-find"' in find_bar.HTML
    assert find_bar.HTML.count("{") == find_bar.HTML.count("}")
    assert "%s" not in find_bar.HTML and "{}" not in find_bar.HTML  # 미완성 포맷 자리 없음
    assert "\\n" in find_bar.HTML  # 문단 경계 구분자가 JS의 개행 리터럴로 내려간다
    assert "::highlight(md4-find)" in find_bar.CSS and "CSS.highlights" in find_bar.HTML
    assert "window.find(" in find_bar.HTML  # 하이라이트 API가 없는 웹뷰용 폴백


def test_scroll_memory_script_carries_the_paper_key():
    """읽던 자리 스크립트는 논문 열쇠와 셀렉터 목록을 JSON으로 받아 간다."""
    from md4paper.ui import scroll_memory

    js = scroll_memory.init_js("abc'\"123")
    assert json.dumps("abc'\"123") in js          # 따옴표가 섞여도 스크립트가 깨지지 않는다
    assert json.dumps(list(scroll_memory.SELECTORS)) in js
    assert js.count("{") == js.count("}")
    assert "%s" not in js
    assert "sessionStorage" in js and "md4:pos:" in js
    for sel in (".conv-md", ".sbs-grid", ".vtoc", ".md4-scroll"):
        assert sel in js
