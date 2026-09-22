"""변환 탭 프리뷰 렌더러 — 소스 줄 앵커와 '이 문서 형식이 요구하는 것'을 지킨다.

이 문서들은 순수 마크다운이 아니다. 인용·각주 인프라가 raw HTML(`<a id="ref-N"></a>`,
`<sup class="md-fn">`)로 짜여 있어서, 렌더러가 그걸 이스케이프하면 본문 인용이 통째로 글자가 된다.
그리고 분할 편집이 에디터 줄과 프리뷰 위치를 맞추므로 data-line이 없으면 싱크가 성립하지 않는다.
"""

from __future__ import annotations

import re

from md4paper.ui import mdrender


def test_raw_html_anchors_survive():
    """참고문헌 앵커와 각주 위첨자는 HTML 그대로 나와야 한다 (이스케이프되면 인용이 다 죽는다)."""
    src = ('<a id="ref-1"></a>**[1]** Someone. Title. 2020\n\n'
           'Body text <sup class="md-fn"><a href="#fn-2">2</a></sup> continues.\n')
    html = mdrender.render(src)
    assert '<a id="ref-1"></a>' in html
    assert '<sup class="md-fn"><a href="#fn-2">2</a></sup>' in html
    assert "&lt;a id=" not in html


def test_currency_is_not_eaten_by_math():
    """`$0.08 per Find, $1.41` 같은 금액이 수식으로 삼켜지면 안 된다.

    코퍼스의 단일 `$...$` 33건이 전부 금액·JS 템플릿이었고, 이전 렌더러(markdown2 latex extra)는
    이걸 MathML로 바꿔 `$`와 문장을 함께 없앴다. dollarmath를 allow_digits=False로 쓰는 이유다.
    """
    html = mdrender.render("The cost was $0.08 per Find, $1.41 total.")
    assert "$0.08 per Find, $1.41" in html
    assert "<math" not in html


def test_real_math_still_renders():
    """진짜 수식은 MathML로 (LLM 수식 인식이 붙으면 이 경로로 들어온다)."""
    html = mdrender.render(r"Mass is $E = mc^2$ here.")
    assert "<math" in html


def test_intraword_underscore_is_not_emphasis():
    """CommonMark는 단어 안 `_`를 강조로 보지 않는다 — snake_case가 깨지면 안 된다."""
    html = mdrender.render("Use snake_case_name for this.")
    assert "snake_case_name" in html
    assert "<em>" not in html


def test_data_line_anchors_point_at_source_lines():
    """블록마다 data-line이 붙고, 그 값이 원문의 실제 줄 번호(0-based)여야 한다."""
    src = "# Title\n\nFirst para.\n\nSecond para.\n"
    html = mdrender.render(src)
    lines = [int(x) for x in re.findall(r'data-line="(\d+)"', html)]
    assert lines == [0, 2, 4]


def test_link_destination_spaces_are_tightened():
    """PDF 추출이 URL 안에 넣은 공백을 지운다 — 안 지우면 링크로 인정되지 않는다."""
    html = mdrender.render("See [paper](https://doi.org/10. 1145/1234).")
    assert "https://doi.org/10.1145/1234" in html
    assert "<a href=" in html


def test_blocks_cover_the_whole_document_in_order():
    """blocks()를 이어 붙이면 render()와 같아야 한다 — 라이브 갱신이 이 등가성에 기댄다."""
    src = "# A\n\npara one\n\n- x\n- y\n\n| h |\n|---|\n| c |\n\n```py\nz = 1\n```\n"
    joined = "".join(h for _, h in mdrender.blocks(src))
    assert joined == mdrender.render(src)
    starts = [ln for ln, _ in mdrender.blocks(src)]
    assert starts == sorted(starts)


def test_blocks_isolate_a_single_edit():
    """한 문단을 고치면 그 블록 하나만 달라져야 한다 (전체 재전송을 피하는 근거)."""
    src = "# A\n\npara one\n\npara two\n\npara three\n"
    before = mdrender.blocks(src)
    after = mdrender.blocks(src.replace("para two", "para two edited"))
    assert len(before) == len(after)
    changed = [i for i, (a, b) in enumerate(zip(before, after)) if a[1] != b[1]]
    assert changed == [2]
