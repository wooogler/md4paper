"""인라인 수식 복원 — PDF 글꼴·기준선으로 첨자를 되살려 $LaTeX$로."""

from md4paper.extract import inline_math as im
from md4paper.extract.inline_math import Glyph, Island

MI, MI7, RM, SY = "LibertineMathMI", "LibertineMathMI7", "LinLibertineT", "txsys"


def _g(idx, ch, font, x0, y=100.0, size=9.0, w=4.0):
    return Glyph(idx, ch, font, size, x0, x0 + w, y)


def _line(*specs):
    return [_g(i, *s) for i, s in enumerate(specs)]


def test_sub_and_superscript_are_rebuilt_from_baselines():
    # x^{(k)}_i ∈ [0,1]^{d_k} — 위첨자 (k)와 아래첨자 i가 같은 x 자리에 겹쳐 있다
    line = _line(("𝑥", MI, 10), ("𝑖", MI7, 14.2, 97.0, 6.5, 2), ("(", SY, 14.5, 103.4, 6.5, 2),
                 ("𝑘", MI7, 16.5, 103.4, 6.5, 2.5), (")", SY, 19, 103.4, 6.5, 2),
                 ("∈", SY, 26), ("[", SY, 33, 100, 9, 2), ("0", RM, 35.5), (",", MI, 39.5, 100, 9, 1),
                 ("1", RM, 43), ("]", SY, 47.5, 100, 9, 2), ("𝑑", MI7, 50, 103.2, 6.5, 3),
                 ("𝑘", "LibertineMathMI5", 53, 101.8, 5.5, 2.5))
    assert im._simplify(im.render(line, 9.0, 100.0)) == r"x_i^{(k)}\in[0,1]^{d_k}"


def test_greek_accents_and_bold():
    line = _line(("x", "LinLibertineTZ", 10), ("˜", RM, 10.5, 100.1, 9, 2.5), ("≻", SY, 20),
                 ("𝜎", MI, 30), ("2", RM, 34.2, 103.5, 6.5, 2.5), ("𝑓", MI7, 34.2, 97.5, 6.5, 2.5))
    assert im._simplify(im.render(line, 9.0, 100.0)) == r"\tilde{\mathbf{x}}\succ\sigma_f^2"


def test_footnote_marker_after_a_word_is_not_math():
    line = _line(("w", RM, 10), ("o", RM, 14), ("r", RM, 18), ("d", RM, 22),
                 ("1", RM, 26.2, 103.5, 6.5, 2.5), (".", RM, 30, 100, 9, 1))
    assert im._islands(line, RM) == []


def test_prose_word_glued_by_hyphen_stays_out():
    line = _line(("𝑡", MI, 10), ("-", RM, 14.2, 100, 9, 2), ("t", RM, 16.5), ("e", RM, 20.5),
                 ("s", RM, 24.5), ("t", RM, 28.5), ("s", RM, 32.5))
    assert im._islands(line, RM) == []  # 낱말 하나로 붙은 "t-tests"는 본문 그대로
    # 하이픈 뒤 낱말이 섬 끝에 걸리면 잘라 낸다
    assert [g.ch for g in im._trim(line)] == ["𝑡"]


def test_relation_operand_and_wrapping_parens():
    # "(p < .001)" — 끝의 관계 기호 피연산자까지 수식이고, 본문 괄호는 밖에 둔다
    line = _line(("(", RM, 10, 100, 9, 2), ("𝑝", MI, 12.5), ("<", RM, 20), (".", RM, 28, 100, 9, 1),
                 ("0", RM, 29.5), ("0", RM, 33.5), ("1", RM, 37.5), (")", RM, 41.6, 100, 9, 2))
    [isl] = im._islands(line, RM)
    assert im.render(isl, 9.0, 100.0) == "p<.001"


def test_apply_replaces_flattened_text_by_context():
    md = "PrefAct represents every observed outcome o i as a coordinate x ( k ) i . Also o i here.\n"
    isl = Island([_g(0, "𝑜", MI, 0), _g(1, "𝑖", MI7, 4, 97, 6.5)], "o_i", "vedoutcome", "asacoordi")
    out, n = im.apply(md, [isl])
    assert n == 1
    assert out.startswith("PrefAct represents every observed outcome $o_i$ as a coordinate")
    assert "Also o i here" in out  # 문맥이 다른 자리는 그대로


def test_apply_skips_display_math_and_partial_words():
    md = "$$o_i$$\n\nthe ratio at the end\n"
    isls = [Island([_g(0, "𝑜", MI, 0), _g(1, "𝑖", MI7, 4, 97, 6.5)], "o_i", "", ""),
            Island([_g(0, "𝑡", MI, 0)], "t", "ther", "atio")]
    out, n = im.apply(md, isls)
    assert (out, n) == (md, 0)


def test_power_of_ten_but_not_a_lone_arrow():
    power = _line(("1", RM, 10), ("0", RM, 14), ("−", SY, 18.2, 103.5, 6.5, 3), ("4", RM, 21.3, 103.5, 6.5, 2.5))
    [isl] = im._islands(power, RM)
    assert im.render(isl, 9.0, 100.0) == "10^{-4}"
    arrow = _line(("a", RM, 10), ("b", RM, 14), ("→", SY, 22), ("c", RM, 30), ("d", RM, 34))
    assert im._islands(arrow, RM) == []


def test_bold_name_with_affiliation_marks_is_not_math():
    bold = "NimbusRomNo9L-Medi"
    line = _line(("B", bold, 10), ("o", bold, 14), ("e", bold, 18), ("n", bold, 22),
                 ("2", RM, 26.2, 103.5, 6.5, 2.5), ("∗", "CMSY7", 28.8, 103.5, 6.5, 2.5))
    assert im._islands(line, RM) == []


def test_orphan_script_is_dropped_but_stacked_fraction_is_kept():
    # 알고리즘 줄 번호 "2:"가 다음 줄 앞 첨자로 붙은 경우
    line = _line(("2", RM, 5, 103.5, 6.5, 2.5), (":", RM, 7.6, 103.5, 6.5, 1), ("𝑥", MI, 12))
    [isl] = im._islands(line, RM)
    assert im.render(isl, 9.0, 100.0) == "x"
    # 본문 속 ∂L/∂x — 분자·분모가 같은 x 자리에 위아래로
    frac = _line(("∂", MI7, 10, 103.5, 6.5, 3), ("𝐿", MI7, 13, 103.5, 6.5, 3),
                 ("∂", MI7, 10, 97.0, 6.5, 3), ("𝑥", MI7, 13, 97.0, 6.5, 3))
    [isl] = im._islands(frac, RM)
    assert im.render(sorted(isl, key=lambda g: g.x0), 9.0, 100.0) == r"\frac{\partial L}{\partial x}"
