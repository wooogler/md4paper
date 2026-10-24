"""공백 복원 — Docling이 붙여 버린 줄을 원본 PDF 텍스트 레이어의 띄어쓰기로 되돌린다."""

from narani.extract import spacing
from narani.extract.spacing import Reference, respace_token, restore_spaces

PAGE = (
    "2 Background and Related Work\r\n"
    "2.1 Empirical Studies of AI Programming in IDE\r\n"
    "3.2.2 LLM-Based Classification. We classified all 76,231 user mes￾sages with GPT-5 mini8 via the\r\n"
    "OpenAI Batch API. Tools such as GitHub Copilot and langid.\r\npy were used.\r\n"
    "Ask a project-agnostic technical or domain\r\n3.56  11.35\r\nquestion.\r\n"
    "Failure-Driven Debugging. (=968, 19.90%). Sessions were dominated by\r\n"
    "[47] John R Searle. 1969. Speech acts. Cambridge university\r\npress.\r\n"
    "developers’ intent and see “vibe coding” here\r\n"
)


def _ref() -> Reference:
    return Reference.from_texts([PAGE])


def test_heading_and_runin_first_line_get_their_spaces_back():
    """실제 사례(ASE '26): 블록 첫 줄이 통째로 한 단어로 나왔다."""
    md = (
        "## 2 BackgroundandRelatedWork\n\n"
        "## 2.1 EmpiricalStudiesofAIProgramminginIDE\n\n"
        "3.2.2 LLM-BasedClassification.Weclassifiedall76,231usermessages with GPT-5 mini 8 via the OpenAI Batch API.\n"
    )
    out, n = restore_spaces(md, _ref())
    assert "## 2 Background and Related Work" in out
    assert "## 2.1 Empirical Studies of AI Programming in IDE" in out
    assert "LLM-Based Classification. We classified all 76,231 user messages with GPT-5 mini 8" in out
    assert n == 3


def test_legit_camelcase_and_normal_text_untouched():
    md = "Tools such as GitHub Copilot and OpenAI were used. developers' intent\n"
    out, n = restore_spaces(md, _ref())
    assert out == md and n == 0


def test_newline_gap_in_original_does_not_insert_a_space():
    """Docling이 두 줄을 공백 없이 이은 자리("langid.py", 하이픈 풀기)는 의도한 결합 — 그대로 둔다."""
    ref = _ref()
    assert respace_token("Cambridgeuniversitypress.", ref) == "Cambridge universitypress."  # 줄바꿈 자리는 안 넣는다
    assert respace_token("Toolssuchaslangid.py", ref) == "Tools such as langid.py"
    # 하이픈으로 갈린 단어(pdfium은 U+FFFE만 남기고 줄바꿈 없음) — 한 단어로 이어진다
    assert respace_token("Weclassifiedall76,231usermessages", ref) == "We classified all 76,231 user messages"


def test_table_cell_wrapped_across_other_columns_is_matched_piecewise():
    """표 셀은 줄바꿈 사이에 다른 열의 글자가 끼어 통째로는 못 찾는다 — 조각으로 맞추고 경계는 단어 사이."""
    assert respace_token("Askaproject-agnostictechnicalordomainquestion.", _ref()) == \
        "Ask a project-agnostic technical or domain question."


def test_glyph_missing_from_text_layer_is_skipped_not_fatal():
    """수식 이탤릭 𝑛이 사설 글리프로 박혀 텍스트 레이어에 없다 — 그 글자만 건너뛰고 나머지를 되살린다."""
    assert respace_token("(n=968,19.90%).Sessionsweredominated", _ref()) == "(n=968, 19.90%). Sessions were dominated"


def test_ambiguous_spacing_is_left_alone():
    ref = Reference.from_texts(["We saw data base here. Then database there."])
    assert respace_token("databasethere.", ref) is None  # 두 출현의 띄어쓰기가 다르다 → 판단 보류
    assert respace_token("databasehere.", Reference.from_texts(["a data base here. b database here."])) is None


def test_original_quotes_are_preserved_when_only_spaces_change():
    """정규화(’→')는 비교용일 뿐 — 결과 토큰엔 원래 글자를 남긴다."""
    md = "developers’intentandsee “vibe coding”\n"
    out, _ = restore_spaces(md, _ref())
    assert out == "developers’ intent and see “vibe coding”\n"


def test_skips_urls_code_and_images():
    md = "![fig](images/img-01.png)\n\n```\nBackgroundandRelatedWork\n```\n\n[BackgroundandRelatedWork](https://x/y)\n"
    out, n = restore_spaces(md, _ref())
    assert out == md and n == 0


def test_no_reference_is_a_noop():
    assert restore_spaces("BackgroundandRelatedWork\n", None) == ("BackgroundandRelatedWork\n", 0)


def test_reference_from_pdf_returns_none_without_text_layer(tmp_path):
    import pytest

    pdfium = pytest.importorskip("pypdfium2")
    doc = pdfium.PdfDocument.new()
    doc.new_page(200, 200)
    path = tmp_path / "blank.pdf"
    doc.save(str(path))
    doc.close()
    assert spacing.Reference.from_pdf(path) is None


# 스몰캡(CHI '27 투고본): 첫 대문자만 보통 글리프라 Docling이 글꼴 경계에 공백을 넣고 하이픈을 잃는다
SC_PAGE = "In the Conversational, Magentic-UI, and PrefAct conditions at round C using roots\r\n"
SC_PAGE2 = "than in the Manual and Magentic-UI conditions\r\n"


def _sc_fonts() -> list[list[str]]:
    fonts = ["LinLibertineT"] * len(SC_PAGE)
    fonts[SC_PAGE.index("C using")] = "LibertineMathMI"  # 수식 변수는 다른 글꼴
    return [fonts]


def _sc_faces() -> list[list[int]]:
    """Quartz처럼 스몰캡 소문자만 다른 서브셋(객체 2), 나머지는 객체 1."""
    faces = [1] * len(SC_PAGE)
    for word, sc in (("Conversational", "onversational"), ("Magentic-UI", "agentic-"),
                     ("PrefAct", "ref"), ("PrefAct", "ct")):
        at = SC_PAGE.index(word) + word.index(sc)
        faces[at:at + len(sc)] = [2] * len(sc)
    faces[SC_PAGE.index("C using")] = 3
    return [faces]


def test_small_caps_split_words_are_rejoined_from_the_original():
    ref = Reference.from_texts([SC_PAGE], _sc_fonts(), _sc_faces())
    md = "In the C onversational , M agenticUI , and P ref A ct conditions at round C using roots\n"
    out, n = restore_spaces(md, ref)
    assert out == "In the Conversational , Magentic-UI , and PrefAct conditions at round C using roots\n"
    assert n == 3  # 수식 변수 + 본문 단어("C using")는 원본에서 글꼴이 달라 잇지 않는다


def test_rejoin_needs_font_evidence():
    md = "In the C onversational condition\n"
    assert restore_spaces(md, Reference.from_texts([SC_PAGE]))[0] == md  # 글꼴 정보 없으면 안 건드림


def test_rejoin_never_drops_spaces_the_original_lacks_between_words():
    # pdfium도 가끔 띄어쓰기를 놓친다("A webagent") — 한 단어로 짜인 게 아니면 잇지 않는다
    page = "A webagent and I actuallywanted it. Istarted\r\n"
    ref = Reference.from_texts([page], [["LinLibertineT"] * len(page)], [[1] * len(page)])
    md = "A web agent and I actually wanted it. I started\n"
    assert restore_spaces(md, ref) == (md, 0)


def test_rejoin_retries_inside_a_longer_failed_span():
    # "M anual and M agenticUI": 앞쪽 4토큰 묶음이 실패해 "Manual"만 이었을 때 뒤쪽 M도 다시 본다
    page = SC_PAGE2
    faces = [1] * len(page)
    for word, sc in (("Manual", "anual"), ("Magentic-UI", "agentic-")):
        at = page.index(word) + word.index(sc)
        faces[at:at + len(sc)] = [2] * len(sc)
    ref = Reference.from_texts([page], [["LinLibertineT"] * len(page)], [faces])
    out, n = restore_spaces("than in the M anual and M agenticUI conditions\n", ref)
    assert out == "than in the Manual and Magentic-UI conditions\n" and n == 2


# --- 글리프 간격: Docling이 끼운 공백은 빼고, 놓친 공백은 넣는다 -------------------------------------

def _laid_out(text: str, size: float = 10.0, gaps: dict[int, float] | None = None,
              scripts: set[int] | None = None) -> Reference:
    """text를 한 줄씩 조판한 가짜 텍스트 레이어. 글자 폭 0.5em, 공백은 0.25em 간격(글리프 없음).

    gaps: 글자 위치 → 그 글자 앞에 더 벌릴 간격(em). scripts: 위첨자로 짤 글자 위치(0.7배, 기준선 위).
    """
    gaps, scripts = gaps or {}, scripts or set()
    sizes, boxes = [], []
    x = y = 0.0
    for i, ch in enumerate(text):
        if ch in "\r\n":
            x, y = 0.0, y - 2 * size
            sizes.append(0.0)
            boxes.append(spacing._NO_BOX)
            continue
        if ch == " ":
            x += 0.25 * size
            sizes.append(0.0)
            boxes.append(spacing._NO_BOX)
            continue
        s = 0.7 * size if i in scripts else size
        x += gaps.get(i, 0.0) * size
        base = y + 0.4 * size if i in scripts else y
        sizes.append(s)
        boxes.append((x, x + 0.5 * s, base))
        x += 0.5 * s
    return Reference.from_texts([text], [["LinLibertineT"] * len(text)], [[1] * len(text)],
                                [sizes], [boxes])


def test_space_docling_puts_at_a_font_switch_is_dropped_where_the_glyphs_touch():
    """실제 사례(Scout & Steer): 기울임 → 본문 경계마다 "Points ." / "( Identification"."""
    ref = _laid_out("moments as Decision Points. While the screenshot (Identification), for example\r\n")
    md = "moments as Decision Points . While the screenshot ( Identification ), for example\n"
    out, n = restore_spaces(md, ref)
    assert out == "moments as Decision Points. While the screenshot (Identification), for example\n"
    assert n == 3


def test_real_spaces_before_punctuation_stay():
    # 원본에서도 떨어져 있으면("p < .05", 수식 뒤 " ,") 둔다 — 구두점이라서가 아니라 글리프가 떨어져서
    ref = _laid_out("significant at p < .05 overall and x , y are listed here\r\n")
    md = "significant at p < .05 overall and x , y are listed here\n"
    assert restore_spaces(md, ref) == (md, 0)


def test_script_returning_to_punctuation_is_glued_but_not_to_a_word():
    # "LLM^a ," → "LLM^a," / 첨자 뒤에 바로 붙은 글자("¹Department")는 표식과 낱말의 경계라 둔다
    text = "feedback from an LLMa, defined below. 1Department of Computer Science\r\n"
    scripts = {text.index("a,"), text.index("1Dep")}
    ref = _laid_out(text, scripts=scripts)
    md = "feedback from an LLM a , defined below. 1 Department of Computer Science\n"
    out, _ = restore_spaces(md, ref)
    assert out == "feedback from an LLM a, defined below. 1 Department of Computer Science\n"


def test_words_docling_glued_are_split_where_the_original_has_a_word_gap():
    """실제 사례(TextGrad, docling 2.140): "feedbackwe", "Inparticular" — pdfium도 공백을 봤고 0.25em 떨어져 있다."""
    ref = _laid_out("is the feedback we obtained for the variable. In particular, we implement it\r\n")
    md = "is the feedbackwe obtained for the variable. Inparticular, we implement it\n"
    out, n = restore_spaces(md, ref)
    assert out == "is the feedback we obtained for the variable. In particular, we implement it\n"
    assert n == 2


def test_letter_spaced_heading_is_not_split_into_letters():
    # 자간을 벌린 제목은 글자마다 떨어져 있다 — pdfium이 공백을 봐도 낱말을 가르는 게 아니다
    text = "A B S T R A C T and then the body text continues\r\n"
    ref = _laid_out(text)
    md = "ABSTRACT and then the body text continues\n"
    assert restore_spaces(md, ref) == (md, 0)


def test_without_glyph_boxes_spacing_is_left_alone():
    ref = Reference.from_texts(["moments as Decision Points. While the screenshot\r\n"])
    md = "moments as Decision Points . While the screenshot\n"
    assert restore_spaces(md, ref) == (md, 0)


def test_markdown_escapes_do_not_hide_tokens_from_the_original():
    """실제 사례(GEPA): 헤딩 "create \\_query\\_ hop2.predict" — 원본엔 \\ 가 없어 못 찾던 토큰."""
    ref = _laid_out("HotpotQA GPT-4.1 Mini create_query_hop2.predict and summary_1 here\r\n")
    md = "## HotpotQA GPT-4.1 Mini create \\_query\\_ hop2.predict and summary\\_ 1 here\n"
    out, _ = restore_spaces(md, ref)
    assert out == "## HotpotQA GPT-4.1 Mini create\\_query\\_hop2.predict and summary\\_1 here\n"
