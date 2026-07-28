"""numbering.py 단위 테스트 — 핵심 IP라 광범위하게 커버한다."""

from md4paper.ir import Scheme
from md4paper.structure import numbering


def test_dotted_arabic():
    d = numbering.classify("1 Introduction")
    assert d.scheme is Scheme.DOTTED_ARABIC and d.depth == 1 and d.title == "Introduction"

    d = numbering.classify("3.1 Encoder and Decoder Stacks")
    assert d.scheme is Scheme.DOTTED_ARABIC and d.depth == 2

    d = numbering.classify("3.2.1 Scaled Dot-Product Attention")
    assert d.scheme is Scheme.DOTTED_ARABIC and d.depth == 3

    d = numbering.classify("2. Background")  # 후행 점
    assert d.scheme is Scheme.DOTTED_ARABIC and d.depth == 1 and d.title == "Background"


def test_roman():
    for txt in ("I. Introduction", "II. Related Work", "IV. Method", "VII) Results"):
        d = numbering.classify(txt)
        assert d.scheme is Scheme.ROMAN, txt

    # 잘못된 로마 숫자는 로마가 아님
    assert numbering.classify("VV. Bad").scheme is not Scheme.ROMAN


def test_letter_appendix():
    d = numbering.classify("A. Implementation Details")
    assert d.scheme is Scheme.LETTER and d.depth == 1

    d = numbering.classify("A.1 Hyperparameters")
    assert d.scheme is Scheme.LETTER and d.depth == 2

    d = numbering.classify("B.2.3 Extra")
    assert d.scheme is Scheme.LETTER and d.depth == 3


def test_article_not_a_letter_heading():
    # 관사 "A"로 시작하는 제목이 부록 문자로 오탐되면 안 됨
    d = numbering.classify("A Survey of Methods")
    assert d.scheme is not Scheme.LETTER
    assert d.scheme is Scheme.NONE


def test_unnumbered_keywords():
    for txt in ("Abstract", "References", "Acknowledgments", "Bibliography", "Appendix"):
        assert numbering.classify(txt).scheme is Scheme.UNNUMBERED, txt
    # 후행 콜론 허용
    assert numbering.classify("Keywords:").scheme is Scheme.UNNUMBERED


def test_none():
    assert numbering.classify("The quick brown fox").scheme is Scheme.NONE


def test_relevel_arabic_mode():
    texts = [
        "Abstract",
        "1 Introduction",
        "3 Model Architecture",
        "3.1 Encoder",
        "3.2.1 Scaled Dot-Product Attention",
        "References",
    ]
    result = numbering.relevel(texts)
    levels = [lvl for _, lvl, _ in result]
    assert levels == [1, 1, 1, 2, 3, 1]
    # arabic 모드에서 needs_review 없음
    assert not any(nr for _, _, nr in result)


def test_relevel_roman_mixed_mode():
    # IEEE식: 로마(외) → 문자(중) → 아라비아(내)
    texts = ["I. Introduction", "II. Method", "A. Setup", "B. Model", "1) Detail"]
    result = numbering.relevel(texts)
    levels = [lvl for _, lvl, _ in result]
    assert levels[0] == 1 and levels[1] == 1  # 로마 → L1
    assert levels[2] == 2 and levels[3] == 2  # 문자 → L2
    assert levels[4] == 3  # 아라비아 → L3


def test_relevel_flags_undetected():
    texts = ["Some Freeform Heading", "1 Real Section"]
    result = numbering.relevel(texts)
    # 첫 헤더는 감지 실패 → needs_review
    assert result[0][2] is True
    assert result[1][2] is False


def test_level_capped_at_6():
    d = numbering.classify("1.1.1.1.1.1.1 Deep")
    assert d.depth == 7
    _, level, _ = numbering.relevel(["1.1.1.1.1.1.1 Deep"])[0]
    assert level == 6
