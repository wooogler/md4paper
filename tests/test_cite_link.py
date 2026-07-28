"""본문 인용 탐지·치환 테스트 — 마스킹, 범위 바운드, 스타일."""

from md4paper.cite import link
from md4paper.ir import RefEntry

REFS = [
    RefEntry(label="1", authors=["Ashish Vaswani", "Noam Shazeer", "Niki Parmar"], year=2017,
             title="Attention", short_name="Transformer"),
    RefEntry(label="2", authors=["Dzmitry Bahdanau"], year=2015, title="NMT"),
    RefEntry(label="3", authors=["Kyunghyun Cho", "Yoshua Bengio"], year=2014, title="RNN Enc"),
]


def _rewrite(text, parts=("number",)):
    lines, stats = link.rewrite_lines([text], REFS, list(parts))
    return lines[0], stats


def test_keep_single():
    out, stats = _rewrite("Transformers [1] are great.")
    assert out == "Transformers [[1](#ref-1)] are great."
    assert stats.rewritten == 1


def test_keep_multi():
    out, _ = _rewrite("Prior work [2, 3] used RNNs.")
    assert out == "Prior work [[2](#ref-2), [3](#ref-3)] used RNNs."


def test_keep_range():
    out, _ = _rewrite("See [1-3] for details.")
    assert out == "See [[1](#ref-1), [2](#ref-2), [3](#ref-3)] for details."


def test_authoryear_style():
    out, _ = _rewrite("Transformers [1] and [3].", parts=["authoryear"])
    assert "[Vaswani et al. 2017](#ref-1)" in out
    assert "[Cho & Bengio 2014](#ref-3)" in out


def test_short_style_prefers_nickname():
    out, _ = _rewrite("The [1] architecture.", parts=["short"])
    assert out == "The [[Transformer](#ref-1)] architecture."
    # short_name 없으면 author-year로 폴백
    out2, _ = _rewrite("See [2].", parts=["short"])
    assert "[Bahdanau 2015](#ref-2)" in out2


def test_combined_number_and_short():
    # 번호 + 약칭 동시 표시
    out, _ = _rewrite("Transformers [1] rock.", parts=["number", "short"])
    assert out == "Transformers [[1, Transformer](#ref-1)] rock."
    # 약칭 없는 항목은 번호 + author-year 폴백
    out2, _ = _rewrite("See [2].", parts=["number", "short"])
    assert "[2, Bahdanau 2015](#ref-2)" in out2


def test_combined_number_and_authoryear():
    out, _ = _rewrite("Transformers [1].", parts=["number", "authoryear"])
    assert "[1, Vaswani et al. 2017](#ref-1)" in out


def test_out_of_range_rejected():
    # [0,1] 은 0이 범위 밖 → 인용 아님 (수학 구간)
    out, stats = _rewrite("The interval [0,1] is closed.")
    assert out == "The interval [0,1] is closed."
    assert stats.skipped_out_of_range == 1

    # [10] 은 참고문헌이 3개뿐 → 무시
    out2, _ = _rewrite("Reference [10] does not exist.")
    assert out2 == "Reference [10] does not exist."


def test_inline_math_protected():
    out, _ = _rewrite("Matrix element $a[1]$ is fixed.")
    assert out == "Matrix element $a[1]$ is fixed."


def test_inline_code_protected():
    out, _ = _rewrite("The array `x[1]` in code.")
    assert out == "The array `x[1]` in code."


def test_markdown_link_not_touched():
    out, _ = _rewrite("A link [1](http://x.com) here.")
    assert out == "A link [1](http://x.com) here."


def test_fenced_code_skipped():
    src = ["Body [1] cite.", "```", "arr[1] = 2", "```", "After [2] cite."]
    out, stats = link.rewrite_lines(src, REFS, "keep")
    assert out[0] == "Body [[1](#ref-1)] cite."
    assert out[2] == "arr[1] = 2"  # 코드 블록 내부 그대로
    assert out[4] == "After [[2](#ref-2)] cite."
    assert stats.rewritten == 2


def test_skip_range_excludes_references():
    src = ["Body [1] cite.", "# References", "[1] Vaswani. Attention. 2017."]
    out, _ = link.rewrite_lines(src, REFS, "keep", skip_range=range(1, 3))
    assert out[0] == "Body [[1](#ref-1)] cite."
    assert out[2] == "[1] Vaswani. Attention. 2017."  # 참고문헌 영역은 안 건드림


# --- 저자-연도형 본문 인용 (ACL/arXiv 계열) ---
AY_REFS = [
    RefEntry(label="1", authors=["Amos Azaria", "Tom Mitchell"], year=2023, title="Internal state"),
    RefEntry(label="2", authors=["Yuntao Bai", "Andy Jones"], year=2022, title="Helpful harmless"),
    RefEntry(label="3", authors=["Yuntao Bai", "Saurav Kadavath"], year=2022, title="Constitutional AI"),
    RefEntry(label="4", authors=["Jason Wei", "Xuezhi Wang"], year=2022, title="Chain of thought"),
]


def _rewrite_ay(text):
    lines, stats = link.rewrite_lines([text], AY_REFS, ["number"])
    return lines[0], stats


def test_authoryear_parenthetical_linked():
    out, stats = _rewrite_ay("We use CoT (Wei et al., 2022) for reasoning.")
    assert out == "We use CoT ([Wei et al., 2022](#ref-4)) for reasoning."
    assert stats.rewritten == 1


def test_authoryear_multi_and_suffix_disambiguation():
    # 세미콜론 다중 + 2022a/2022b 접미사 순번 구분 (label 2, 3)
    out, _ = _rewrite_ay("Prior work (Bai et al., 2022a; Bai et al., 2022b) explored this.")
    assert "[Bai et al., 2022a](#ref-2)" in out
    assert "[Bai et al., 2022b](#ref-3)" in out


def test_authoryear_narrative_linked():
    out, _ = _rewrite_ay("As shown by Azaria and Mitchell (2023), models know.")
    assert "[Azaria and Mitchell (2023)](#ref-1)" in out


def test_authoryear_ignores_non_citation_parens_and_abbreviations():
    # 약어(LLMs)·연도만·미매칭 저자는 링크 안 함
    out, stats = _rewrite_ay("Large Language Models (LLMs) appeared around (Unknown, 2019).")
    assert "#ref-" not in out
    assert stats.rewritten == 0


def test_numbered_wins_when_more_matches():
    # 번호형이 더 많으면 번호형 채택 (자동 판별)
    out, _ = _rewrite_ay("Results [1] and [2] and [4].")
    assert out == "Results [[1](#ref-1)] and [[2](#ref-2)] and [[4](#ref-4)]."


def test_authoryear_skips_code_and_fence():
    out, stats = _rewrite_ay("Inline `foo(Wei et al., 2022)bar` stays literal.")
    assert out == "Inline `foo(Wei et al., 2022)bar` stays literal."
    assert stats.rewritten == 0
