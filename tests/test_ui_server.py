"""웹 UI 서버 통합 스모크 — 실제 서버를 띄워 렌더/응답을 검증한다.

nicegui 미설치 시 skip. 서버 부팅에 몇 초 걸리므로 넉넉히 재시도한다.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from narani import pipeline
from narani.workdir import WorkDir

CORPUS = Path(__file__).parent / "corpus"

pytest.importorskip("nicegui", reason="웹 UI 의존성(ui extra) 미설치")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _serve_and_get(args: list[str], port: int, path: str = "/") -> str:
    """서버를 띄우고 path를 GET해 본문 반환 (정리 포함)."""
    return _serve_and_get_many(args, port, [path])[0]


def _serve_and_get_many(args: list[str], port: int, paths: list[str]) -> list[str]:
    """서버를 한 번 띄워 paths를 차례로 GET해 본문들을 반환 (정리 포함)."""
    # pytest/nicegui 테스트 모드 env 제거 (하위 서버가 스크린테스트 모드로 오인하지 않도록)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST", "NICEGUI"))}
    proc = subprocess.Popen(
        [sys.executable, "-m", "narani.cli", "ui", *args, "--no-show", "--port", str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
    )
    try:
        for _ in range(40):  # 최대 ~20초
            if proc.poll() is not None:
                out = proc.stdout.read().decode() if proc.stdout else ""
                pytest.fail(f"UI 서버가 조기 종료됨:\n{out}")
            try:
                r = urllib.request.urlopen(f"http://127.0.0.1:{port}{paths[0]}", timeout=2)
                assert r.status == 200, "서버가 200을 반환하지 않음"
                bodies = [r.read().decode("utf-8", "replace")]
                for path in paths[1:]:
                    r = urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=10)
                    assert r.status == 200, f"{path}가 200을 반환하지 않음"
                    bodies.append(r.read().decode("utf-8", "replace"))
                return bodies
            except AssertionError:
                raise
            except Exception:  # noqa: BLE001 — 부팅 대기
                time.sleep(0.5)
        pytest.fail("서버가 시간 내에 응답하지 않음")
        return []
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_ui_server_renders(tmp_path):
    from urllib.parse import quote

    wd = WorkDir(tmp_path / "paper.md4")
    pipeline.convert(CORPUS / "sample_arxiv.md", wd)
    # 리뷰는 이제 워크디렉토리를 URL 쿼리에 담는다 (브라우저 뒤로가기 지원)
    body = _serve_and_get(
        ["--upload-dir", str(tmp_path)], _free_port(),
        path=f"/review?wd={quote(str(wd.root.resolve()))}",
    )
    assert "섹션 트리" in body
    assert "마크다운 편집" in body  # 캔버스 툴바의 편집 스위치
    assert "용어집" in body
    # 마크다운 헤더 ⚙ 점프 버튼 + 섹션 트리 행 id (마크다운→트리 이동 기능의 연결부)
    assert "md-sec-jump" in body
    assert "sectree-" in body
    # 본문 그림 클릭 → 확대 뷰어 (오버레이 + 확대/닫기 도구막대)
    assert "md-img-zoom" in body
    assert "mdz-bar" in body
    # 레이아웃 자동 수정 — 변환 탭 버튼 + 모달(커스텀 프롬프트 입력창)
    assert "레이아웃 자동 수정" in body
    assert "추가 지시 (선택)" in body
    assert "직전 상태로 되돌리기" in body


def test_ui_serves_notes_editor_bundle(tmp_path):
    """메모 패널 위지윅 편집기(Milkdown Crepe) 번들 — 오프라인으로 서버가 직접 내준다."""
    js, css, ver = _serve_and_get_many(
        ["--upload-dir", str(tmp_path)], _free_port(),
        ["/home", "/static/crepe/crepe.min.js", "/static/crepe/crepe.min.css", "/static/crepe/VERSION"],
    )[1:]
    assert "MilkdownCrepe" in js
    assert ".milkdown" in css and "prefers-color-scheme: dark" in css.replace("prefers-color-scheme:dark",
                                                                               "prefers-color-scheme: dark")
    assert "@milkdown/crepe" in ver and "MIT" in ver


def test_ui_home_upload_page(tmp_path):
    # 목록 카드 렌더까지 함께 보기 위해 논문 하나를 변환해 둔다
    pipeline.convert(CORPUS / "sample_arxiv.md", WorkDir(tmp_path / "p" / "p.md4"))
    # 워크디렉토리 없이 실행 → 업로드 홈
    body = _serve_and_get(["--upload-dir", str(tmp_path)], _free_port())
    # 2단 틀 — 왼쪽 컬렉션 사이드바(모든 논문·미분류·새 컬렉션·설정) | 오른쪽 목록. 뷰어와 같은 .md4-body
    assert "md4-hside" in body and "md4-hmain" in body and "md4-body" in body
    assert "모든 논문" in body and "미분류" in body and "새 컬렉션" in body
    assert '"컬렉션"' in body  # 사이드바 캡 — 화면 말은 '컬렉션'(코드 식별자는 project 그대로)
    assert '"설정"' in body  # 사이드바 바닥 ⚙ 설정 (전역 설정 대화상자는 눌렀을 때 짓는다)
    assert "올리기" in body
    # 설정 패널은 첫 화면에서 뺐다 — 대화상자 안의 문구가 홈 첫 렌더에 섞여 나오면 안 된다
    assert "AI 서비스" not in body and "기존 논문·PDF 이름 정리" not in body
    # 창 전체로 끌어 놓기 — 문서 수준 끌기 스크립트와 목록 열을 덮는 안내
    assert "__md4FileDrag" in body and "md4-dropov" in body
    # 목록 카드: 사이드바로 끌 수 있고, 소속 프로젝트 태그를 단다 (미분류면 '미분류' 태그)
    assert "data-md4-root" in body and "md4-ptag" in body
    # 오른쪽 미리보기 열(Zotero식) — 아무것도 고르지 않았으면 범위 요약 + 안내. 카드 한 번 = 선택,
    # 두 번(dblclick)·Enter = 열기. 목록 위 선택 툴바는 없앴다(일괄 동작은 미리보기 자리로).
    assert "md4-hprev" in body and "논문을 고르면 여기에 미리보기" in body
    assert "dblclick" in body
    # 목록 카드의 제목 말줄임 — .nicegui-column은 자식을 컬럼 폭으로 stretch하지 않으므로
    # 제목 라벨에 truncate+min-w-0, 그 부모 행에 w-full이 없으면 긴 제목이 화면 밖으로 흘러넘친다.
    assert '"truncate","min-w-0"' in body  # 제목 라벨
    assert '"no-wrap","w-full","min-w-0"' in body  # 제목을 감싸는 행
    # '올리기' 클릭 — 진짜 <input type=file>이 단추를 덮고 있어야 파일 선택창이 뜬다.
    # 입력을 감춰 두고(display:none) 서버를 거쳐 JS로 input.click()을 부르면 사용자 제스처가 끊겨
    # 웹뷰(앱 창)가 선택창을 막는다 — 드래그&드롭만 되고 클릭은 먹통이 됐다.
    assert ".md4-upload .q-uploader__header { position: absolute; inset: 0;" in body
    assert ".q-uploader__header { display: none" not in body
    assert ".md4-upload .q-uploader__list { pointer-events: none; }" in body  # 목록이 클릭을 가로챈다
    # 미리보기 서지 칸은 눌러서 바로 고친다 — 편집 중 칸은 메모 탭 이름 바꾸기처럼 흰 바탕 + 파란 테두리
    assert ".md4-pv-ed.editing" in body and "md4-pv-kv" in body and "md4-pv-hint" in body


def test_ui_home_empty_workspace_says_where_it_is_looking(tmp_path):
    """논문이 하나도 안 보일 때 — 어느 폴더를 보고 있는지와 바꾸는 길을 같이 알려준다.

    실행 방식(저장소에서 실행/설치본)에 따라 작업 폴더 기본값이 달라서, 다른 폴더에서 변환해 둔
    논문이 안 보이는 걸 '논문이 사라졌다'로 오해하기 쉽다.
    """
    body = _serve_and_get(["--upload-dir", str(tmp_path)], _free_port())
    assert "아직 변환한 논문이 없습니다" in body
    assert "창 위로 끌어다 놓거나" in body  # 빈 목록 = 큰 올리기 안내
    assert "보고 있는 작업 폴더" in body and str(tmp_path) in body
    assert "저장 위치 → 작업 폴더" in body


def test_home_preview_field_parsing():
    """미리보기 서지 칸 — 입력칸 글자 ↔ 저장 값 (서버 없이). 저자는 한 줄에 한 명, 연도는 숫자만."""
    from narani.ui.home import field_text, parse_field

    assert field_text("authors", ["A Kim", " ", "B Lee"]) == ("A Kim, B Lee", "A Kim\nB Lee")
    assert parse_field("authors", "A Kim\n\n  B Lee  \r\n") == ["A Kim", "B Lee"]
    assert field_text("year", None) == ("", "") and field_text("year", 2024) == ("2024", "2024")
    assert parse_field("year", " 2024 ") == 2024 and parse_field("year", "") is None
    with pytest.raises(ValueError):
        parse_field("year", "20x4")
    assert parse_field("title", "  Attention\nIs  All ") == "Attention Is All"  # Shift+Enter 줄바꿈은 공백으로
    assert parse_field("abstract", " a\n\nb ") == "a\n\nb"  # 초록은 문단 줄바꿈을 지킨다


def test_home_naming_hint_ignores_uniquified_folder():
    """같은 이름의 다른 논문 때문에 'X (2)'로 유일화된 폴더는 이미 규칙대로 — 힌트가 영영 남지 않게."""
    from narani.ui.home import _same_base

    assert _same_base("2017_Attention_Vaswani", "2017_Attention_Vaswani")
    assert _same_base("2017_Attention_Vaswani (2)", "2017_Attention_Vaswani")
    assert not _same_base("2017_Attention_Vaswani (x)", "2017_Attention_Vaswani")
    assert not _same_base("paper", "2017_Attention_Vaswani")
