"""뷰어(원문|번역) 섹션·문단 정렬 헬퍼 — 순수 함수 단위 테스트."""

import pytest

pytest.importorskip("nicegui", reason="웹 UI 의존성(ui extra) 미설치")

from md4paper.ui.app import (  # noqa: E402
    _heading_of,
    _split_paragraphs,
    _split_sections,
    align_rows,
)


def test_split_sections_by_heading_with_preamble():
    md = "서문 문단.\n\n# 제목\n\n본문\n\n## 소절\n\n내용"
    secs = _split_sections(md)
    assert len(secs) == 3
    assert secs[0].startswith("서문")  # 첫 헤더 앞 서문이 첫 블록
    assert secs[1].startswith("# 제목")
    assert secs[2].startswith("## 소절")


def test_split_sections_ignores_hash_in_code_fence():
    md = "# H\n\n```py\n# 주석처럼 보이는 헤더\nx = 1\n```\n\n다음"
    assert len(_split_sections(md)) == 1  # 펜스 안 # 는 섹션 경계가 아님


def test_split_paragraphs_keeps_fence_intact():
    md = "p1\n\n```\nline a\n\nline b\n```\n\np2"
    paras = _split_paragraphs(md)
    assert len(paras) == 3
    assert "line a\n\nline b" in paras[1]  # 펜스 내 빈 줄은 문단을 나누지 않음


def test_heading_of():
    assert _heading_of("## 1 Introduction\n\nbody") == (2, "1 Introduction")
    assert _heading_of("just a paragraph") is None
    assert _heading_of("\n\n### Deep") == (3, "Deep")


def test_align_rows_paragraph_level_when_counts_match():
    en = "# T\n\n## A\n\np1\n\np2"
    ko = "# T\n\n## A\n\nㄱ1\n\nㄱ2"
    rows = align_rows(en, ko)
    # 제목/헤더/문단이 각각 한 행씩 정렬
    assert [h for *_e, h in rows if h] == [(1, "T"), (2, "A")]
    bodies = [(e, k) for e, k, h in rows if h is None]
    assert ("p1", "ㄱ1") in bodies and ("p2", "ㄱ2") in bodies


def test_align_rows_falls_back_to_section_when_paragraphs_differ():
    en = "# T\n\n## B\n\nx1\n\nx2\n\nx3"       # 본문 3문단
    ko = "# T\n\n## B\n\nㅁ1과2\n\nㅁ3"          # 본문 2문단 → 섹션 통째 정렬
    rows = align_rows(en, ko)
    assert len(rows) == 2  # 제목 행 + B섹션(통째) 행
    assert rows[1][2] == (2, "B")
    assert "x1\n\nx2\n\nx3" in rows[1][0]  # 섹션 본문이 한 셀에 통째로


def test_align_rows_none_when_section_counts_differ():
    assert align_rows("# A\n\n## B\n\nx", "# A\n\n본문만") is None
