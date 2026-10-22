"""에이전트 인용 마커 — 지금 논문 [[rN]], 다른 논문 [[tok:rN]], 메모 [[a…]]."""

from __future__ import annotations

import pytest

pytest.importorskip("markdown_it")

from md4paper.ui.agent.cites import note_tag, render_html, resolve_citations, row_label  # noqa: E402

CUR = "0123456789ab"
OTHER = "fedcba987654"
ATOK = "abcdef012345"   # 'a'로 시작하는 토큰 — 메모 태그로 잘못 읽히면 안 된다


def test_single_paper_numbers_in_order_and_drops_unknown():
    text = "첫째 [[r5]] 둘째 [[ r2 ]] 다시 [[r5]] 그리고 [[r99]]."
    clean, refs = resolve_citations(text, {(CUR, 2), (CUR, 5)}, current=CUR)
    assert refs == [(CUR, 5), (CUR, 2)]
    assert "[[r99]]" not in clean and clean.count("[[r5]]") == 2
    assert clean.endswith("그리고.")      # 지운 마커 앞 공백도 정리


def test_current_paper_with_explicit_token_shares_number():
    clean, refs = resolve_citations(f"a [[r3]] b [[{CUR}:r3]]", {(CUR, 3)}, current=CUR)
    assert refs == [(CUR, 3)] and clean == "a [[r3]] b [[r3]]"


def test_multi_paper_markers():
    text = f"여기 [[r1]] 저기 [[{OTHER}:r7]] 'a' 토큰 [[{ATOK}:r2]] 허용 밖 [[{OTHER}:r8]]"
    allowed = {(CUR, 1), (OTHER, 7), (ATOK, 2)}
    clean, refs = resolve_citations(text, allowed, current=CUR)
    assert refs == [(CUR, 1), (OTHER, 7), (ATOK, 2)]
    assert f"[[{OTHER}:r7]]" in clean and f"[[{OTHER}:r8]]" not in clean
    html = render_html(clean, refs, current=CUR)
    assert f'data-kind="row" data-token="{CUR}" data-row="1" data-n="1"' in html
    assert f'data-token="{OTHER}" data-row="7" data-n="2"' in html
    assert f'data-token="{ATOK}" data-row="2" data-n="3"' in html


def test_bare_row_marker_dropped_without_current_paper():
    """홈 화면(지금 논문 없음)에서 [[rN]]은 어느 논문인지 모르므로 버린다."""
    clean, refs = resolve_citations(f"x [[r1]] y [[{OTHER}:r1]]", {(OTHER, 1)}, current=None)
    assert refs == [(OTHER, 1)] and "[[r1]]" not in clean
    assert row_label(OTHER, 1, None) == f"{OTHER}:r1" and row_label(CUR, 1, CUR) == "r1"


def test_note_chips_share_numbers_and_carry_token():
    text = "문단 [[r2]] 메모 [[a9x]] 다시 [[r2]] 없는 메모 [[aXXX]]."
    clean, refs = resolve_citations(text, {(CUR, 2)}, {"a9x"}, current=CUR)
    assert refs == [(CUR, 2), "a9x"] and "[[aXXX]]" not in clean
    html = render_html(clean, refs, {"a9x": {"id": "a9x", "row": 4, "token": OTHER}},
                       current=CUR)
    assert "chat-cite-note" in html and 'data-id="a9x"' in html
    assert f'data-token="{OTHER}" data-id="a9x" data-row="4" data-n="2"' in html


def test_note_ids_are_matched_by_tag():
    """메모 id가 'a'로 시작하지 않아도 태그(a+id)로 인용·허용된다."""
    tag = note_tag("m1k2")
    assert tag == "am1k2"
    clean, refs = resolve_citations(f"메모 [[{tag}]]", set(), {"m1k2"}, current=CUR)
    assert refs == [tag]
    html = render_html(clean, refs, {tag: {"id": "m1k2", "row": 3, "token": CUR}}, current=CUR)
    assert 'data-id="m1k2"' in html


def test_render_escapes_raw_html():
    clean, refs = resolve_citations("주장 [[r5]] <script>alert(1)</script>", {(CUR, 5)},
                                    current=CUR)
    html = render_html(clean, refs, current=CUR)
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert 'data-row="5"' in html
