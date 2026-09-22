"""공백 복원 — Docling이 붙여 버린 줄을 원본 PDF 텍스트 레이어의 띄어쓰기로 되돌린다."""

from md4paper.extract import spacing
from md4paper.extract.spacing import Reference, respace_token, restore_spaces

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
