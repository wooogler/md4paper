"""본문 강조 복원 — PDF 글꼴의 굵게·기울임을 **…** / *…* 로."""

import pytest

from md4paper.extract import emphasis as em
from md4paper.extract.inline_math import font_style

BO, IT = em.BOLD, em.ITALIC


@pytest.mark.parametrize(("name", "weight", "angle", "want"), [
    ("LinLibertineT", 395, 0, 0),
    ("LinLibertineTI", 380, -12, IT),
    ("LinLibertineTB", 650, 0, BO),
    ("LinLibertineTBI", 375, -12, BO | IT),  # 서술자의 굵기가 틀린 글꼴 — 이름이 말해 준다
    ("LinBiolinumTBO", 660, 0, BO | IT),  # 기울기 0인 오블리크 — 이름이 말해 준다
    ("NimbusRomNo9L-Regu", 400, 0, 0),
    ("NimbusRomNo9L-Medi", 0, 0, BO),
    ("NimbusRomNo9L-ReguItal", 0, None, IT),
    ("CMBX10", -1, None, BO),
    ("CMTI10", -1, None, IT),
    ("Arial-BoldItalicMT", 700, -12, BO | IT),
    ("TimesNewRomanPSMT", 400, 0, 0),
    ("SomeFont", 700, 0, BO),  # 이름이 말이 없으면 서술자로
    ("SomeFont", 400, -10, IT),
])
def test_font_style_from_name_or_descriptor(name, weight, angle, want):
    assert font_style(name, weight, angle) == want


def _stream(*parts):
    """[(PDF 글자, 강조 비트)] → 문서 글자열·비트 (추출기와 같은 정규화, 공백 없음)."""
    chars, bits = [], []
    for text, bit in parts:
        for ch in text:
            for c in em._norm_char(ch):
                chars.append(c)
                bits.append(bit)
    return "".join(chars), bits


_HDR = "## 1 Introduction\n\n"


def _apply(line, *parts, head=_HDR):
    out, n = em.apply(head + line + "\n", *_stream(*parts))
    return out[len(head):].rstrip("\n"), n


def test_italic_terms_and_upright_punctuation_stay_outside():
    line = "We call these Decision Points. Users want batching, grounding, and steering."
    out, n = _apply(line, ("We call these ", 0), ("Decision Points", IT), (". Users want ", 0),
                    ("batching", IT), (", ", 0), ("grounding", IT), (", and ", 0), ("steering", IT), (".", 0))
    # 끝의 마침표·쉼표는 PDF에서 똑바로 서 있으니 밖으로. 낱말끼리 맞붙은 두 구간은 하나로 합친다.
    assert out == "We call these *Decision Points*. Users want *batching, grounding*, and *steering*."
    assert n == 3


def test_adjacent_words_merge_and_styled_punctuation_stays_inside():
    line = "3.2.1 Task Design. This work makes a Decision Point taxonomy and more."
    out, _ = _apply(line, ("3.2.1 Task Design.", IT), (" This work makes ", 0),
                    ("a Decision Point taxonomy", BO), (" and more.", 0))
    assert out == "*3.2.1 Task Design.* This work makes **a Decision Point taxonomy** and more."


def test_bold_italic_and_mid_word_change_snaps_to_the_word():
    line = "Expectation A: Scouting matters for everyone in the study today."
    out, _ = _apply(line, ("Expectation A", BO | IT), (": ", 0), ("Scout", BO), ("ing matters for everyone", 0),
                    (" in the study today.", 0))
    # "Scout|ing" — 굵은 글자가 과반이라 낱말 전체를 굵게 (낱말 중간에 표시를 넣지 않는다)
    assert out == "***Expectation A***: **Scouting** matters for everyone in the study today."


def test_letter_fragments_join_their_neighbours_with_the_common_style():
    # 머리글자만 굵은 이름을 Docling이 글꼴 경계에서 띄어 쓴 경우
    line = "We propose Pr ompt O ptimization with Te xtual G radients (ProTeGi), a method."
    out, _ = _apply(line, ("We propose ", 0), ("Pr", BO | IT), ("ompt", IT), ("O", BO | IT),
                    ("ptimization with", IT), ("Te", BO | IT), ("xtual", IT), ("G", BO | IT), ("radients", IT),
                    (" (ProTeGi), a method.", 0))
    assert out == "We propose *Pr ompt O ptimization with Te xtual G radients* (ProTeGi), a method."


def test_unbalanced_brackets_and_quotes_stay_outside():
    line = 'The user typed ("book a flight for the 10th") into the agent box.'
    out, _ = _apply(line, ("The user typed (", 0), ('"book a flight for the 10th"', IT), (") into the agent box.", 0))
    assert out == 'The user typed (*"book a flight for the 10th"*) into the agent box.'


def test_math_and_tags_break_runs_and_are_left_alone():
    line = 'the value $x_{i}$ of variable v is used here<sup class="md-fn"><a href="#fn-1">1</a></sup> today.'
    out, _ = _apply(line, ("the value ", 0), ("xi", 0), (" of variable ", 0), ("v", IT),
                    (" is used here", 0), ("1", 0), (" today.", 0))
    assert out == ('the value $x_{i}$ of variable *v* is used here'
                   '<sup class="md-fn"><a href="#fn-1">1</a></sup> today.')


def test_headings_captions_references_and_front_matter_are_not_wrapped():
    md = ("## A Bold Title\n\nAnne Author, Some University\n\n## 1 Introduction\n\n"
          "Figure 1: An overview of the whole system pipeline.\n\n"
          "Body text with a key term inside it here.\n\n## References\n\n"
          "- [1] Anne Author. 2020. A Bold Title. In Proceedings.\n")
    stream = _stream(("A Bold Title", BO), ("Anne Author, Some University", IT), ("1 Introduction", BO),
                     ("Figure 1:", BO), (" An overview of the whole system pipeline.", 0),
                     ("Body text with a ", 0), ("key term", IT), (" inside it here.", 0), ("References", BO),
                     ("[1] Anne Author. 2020. ", 0), ("A Bold Title", IT), (". In Proceedings.", 0))
    out, n = em.apply(md, *stream)
    assert n == 1
    assert out == md.replace("a key term", "a *key term*")


def test_line_with_existing_asterisks_or_unknown_place_is_left_alone():
    out, n = _apply("A p* value and a key term that matter.", ("A p* value and a ", 0), ("key term", IT),
                    (" that matter.", 0))
    assert n == 0 and out == "A p* value and a key term that matter."
    # PDF에서 자리를 못 찾는 줄(다른 문장)은 건드리지 않는다
    out, n = _apply("Completely different sentence text here.", ("Body text with a ", 0), ("key", IT))
    assert n == 0


def test_runin_detects_emphasized_heads_and_keeps_body_emphasis():
    from md4paper.structure.runin import detect

    assert detect("*3.2.1 Task Design.* Prior work on agents") == ("3.2.1", "Task Design", "Prior work on agents", True)
    assert detect("*Generation and state*. Generative stages use Claude")[1] == "Generation and state"
    long = ("*4.1.1 Task parameterization and grounding: Decomposing requests into steerable task variables.* "
            "To support *grounding*, we do x.")
    assert detect(long) == ("4.1.1", "Task parameterization and grounding: Decomposing requests into steerable "
                            "task variables", "To support *grounding*, we do x.", True)
    num, title, body, _ = detect("***Expectation A***: *Awareness at non-critical agent decisions.* "
                                 "Participants wanted to be involved here.")
    assert (title, body) == ("Expectation A", "*Awareness at non-critical agent decisions.* "
                                              "Participants wanted to be involved here.")
    # 제목 강조의 닫는 표시가 본문에 걸리면 본문은 평문으로 (짝 없는 *** 를 남기지 않는다)
    _, title, body, _ = detect("***Batching: Align involvement with decision urgency and user attention.*** "
                               "Informed by Expectation A, users should be able to maintain awareness.")
    assert title == "Batching" and "*" not in body
    assert detect("We define a *CUA Decision Point* as a moment. Then more.") is None


def test_narrative_citation_with_italic_et_al_is_linked():
    from md4paper.cite.link import _NARRATIVE_CITE

    assert _NARRATIVE_CITE.search("as shown by Wei *et al.* (2021), prompts").group(0) == "Wei *et al.* (2021)"


def test_space_docling_puts_at_the_font_switch_before_punctuation_is_dropped():
    # Docling은 글꼴이 바뀌는 자리에 공백을 끼운다 — 강조 경계의 구두점 앞뒤 공백만 걷는다
    line = "proceed as Decision Points . The term ( grounding ) and p < .05 stay."
    out, _ = _apply(line, ("proceed as ", 0), ("Decision Points", IT), (". The term (", 0), ("grounding", IT),
                    (") and p < .05 stay.", 0))
    assert out == "proceed as *Decision Points*. The term (*grounding*) and p < .05 stay."
