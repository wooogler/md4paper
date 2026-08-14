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

from md4paper import pipeline
from md4paper.workdir import WorkDir

CORPUS = Path(__file__).parent / "corpus"

pytest.importorskip("nicegui", reason="웹 UI 의존성(ui extra) 미설치")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _serve_and_get(args: list[str], port: int, path: str = "/") -> str:
    """서버를 띄우고 path를 GET해 본문 반환 (정리 포함)."""
    # pytest/nicegui 테스트 모드 env 제거 (하위 서버가 스크린테스트 모드로 오인하지 않도록)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST", "NICEGUI"))}
    proc = subprocess.Popen(
        [sys.executable, "-m", "md4paper.cli", "ui", *args, "--no-show", "--port", str(port)],
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
                r = urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=2)
                assert r.status == 200, "서버가 200을 반환하지 않음"
                return r.read().decode("utf-8", "replace")
            except AssertionError:
                raise
            except Exception:  # noqa: BLE001 — 부팅 대기
                time.sleep(0.5)
        pytest.fail("서버가 시간 내에 응답하지 않음")
        return ""
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
    assert "마크다운" in body  # 변환 탭 우측 토글 칩 (이전: '마크다운 프리뷰' 탭)
    assert "용어집" in body
    # 마크다운 헤더 ⚙ 점프 버튼 + 섹션 트리 행 id (마크다운→트리 이동 기능의 연결부)
    assert "md-sec-jump" in body
    assert "sectree-" in body
    # 레이아웃 자동 수정 — 변환 탭 버튼 + 모달(커스텀 프롬프트 입력창)
    assert "레이아웃 자동 수정" in body
    assert "추가 지시 (선택)" in body
    assert "직전 상태로 되돌리기" in body


def test_ui_home_upload_page(tmp_path):
    # 목록 카드 렌더까지 함께 보기 위해 논문 하나를 변환해 둔다
    pipeline.convert(CORPUS / "sample_arxiv.md", WorkDir(tmp_path / "p" / "p.md4"))
    # 워크디렉토리 없이 실행 → 업로드 홈
    body = _serve_and_get(["--upload-dir", str(tmp_path)], _free_port())
    assert "논문 PDF" in body
    assert "끌어다" in body  # 업로드 드롭존 (docling 단일 백엔드라 변환 방식 선택 없음)
    assert "AI 서비스" in body  # 프로바이더/키 설정 섹션
    # 저장 위치 패널 — 영어/한국어/PDF를 각각 다른 폴더로, 이름 규칙·작업 폴더도 여기서 변경
    assert "저장 위치" in body
    assert "영어 마크다운" in body and "한국어 마크다운" in body and "PDF 원본" in body
    assert "이름 규칙" in body and "기존 논문·PDF 이름 정리" in body
    assert "작업 폴더" in body
    # 목록 카드의 제목 말줄임 — .nicegui-column은 자식을 컬럼 폭으로 stretch하지 않으므로
    # 제목 라벨에 truncate+min-w-0, 그 부모 행에 w-full이 없으면 긴 제목이 화면 밖으로 흘러넘친다.
    assert '"truncate","min-w-0"' in body  # 제목 라벨
    assert '"no-wrap","w-full","min-w-0"' in body  # 제목을 감싸는 행
