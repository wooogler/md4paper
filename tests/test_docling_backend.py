"""docling 백엔드 보조 로직 — 저자 블록 분리, 캡션 라벨 포함."""

import pytest

from md4paper.extract.docling_backend import _join_broken_paragraphs, _split_author_block


def test_join_broken_paragraphs_merges_column_split_abstract():
    # footer 제거 후 컬럼 경계에서 끊긴 초록 — 앞이 종결부호 없이 끝, 뒤가 소문자 시작
    md = (
        "## Abstract\n\n"
        "We present an interactive system that operationalizes this workflow, guiding "
        "developers to discover edge cases and evaluate revised prompts against a growing test set. A\n\n"
        "user study shows our workflow helps people refine prompts systematically.\n"
    )
    out = _join_broken_paragraphs(md)
    assert "growing test set. A user study shows" in out
    assert "## Abstract" in out  # 헤더는 그대로


def test_join_broken_paragraphs_keeps_author_email_separate():
    # 저자명(짧은 라인) + 이메일(소문자 시작)은 결합하면 안 됨
    md = "## T\n\nIllia Polosukhin\n\nillia.polosukhin@gmail.com\n"
    out = _join_broken_paragraphs(md)
    assert "Illia Polosukhin\n\nillia.polosukhin@gmail.com" in out


def test_join_broken_paragraphs_leaves_normal_paragraphs():
    # 정상 종결(마침표) + 대문자 시작 문단은 건드리지 않음
    md = "첫 문장은 여기서 끝난다.\n\nThis is a separate sentence that stands alone here.\n"
    assert _join_broken_paragraphs(md) == md


def test_join_broken_paragraphs_skips_lists_and_headings():
    md = "A long enough introductory paragraph that ends without any period here\n\n- 리스트 항목\n"
    out = _join_broken_paragraphs(md)
    assert "\n\n- 리스트 항목" in out  # 리스트는 결합 대상 아님


def test_split_author_block_two_authors():
    md = (
        "## Paper Title\n\n"
        "Minjae Lee Yonsei University minjaelee@yonsei.ac.kr "
        "Minsuk Kahng ∗ Yonsei University minsuk@yonsei.ac.kr\n\n"
        "## Abstract\n"
    )
    out = _split_author_block(md)
    lines = [ln for ln in out.split("\n") if ln.strip()]
    # 두 저자가 서로 다른 줄(문단)로 분리
    assert any(ln.endswith("minjaelee@yonsei.ac.kr") and ln.startswith("Minjae") for ln in lines)
    assert any(ln.endswith("minsuk@yonsei.ac.kr") and ln.startswith("Minsuk") for ln in lines)


def test_split_author_block_single_author_untouched():
    md = "## T\n\nJane Doe University jane@x.edu\n\n## Abstract\n"
    assert _split_author_block(md) == md  # 이메일 1개면 그대로


def test_split_author_block_no_heading_untouched():
    md = "just some text a@b.co and c@d.co without headings"
    assert _split_author_block(md) == md


def test_split_author_block_ignores_body_emails():
    # 다음 섹션(본문)에 이메일이 많아도 저자 영역(제목~첫 섹션)만 대상
    md = "## T\n\nSolo Author solo@x.edu\n\n## Body\n\ncontact a@x.com or b@y.com here\n"
    assert _split_author_block(md) == md


def test_place_captions_pairs_by_page_order():
    from md4paper.extract.docling_backend import _place_captions

    # Docling이 캡션을 이미지보다 위/딴 곳에 흩어 놓은 상황 (같은 페이지, 순서로 짝지음)
    md = (
        "Figure 2: Second caption here.\n\n"
        "![Image](img-01.png)\n\n"
        "Figure 1: First caption here.\n\n"
        "![Image](img-02.png)\n"
    )
    captions = [("Figure 1: First caption here.", 1), ("Figure 2: Second caption here.", 1)]
    page_by_name = {"img-01.png": 1, "img-02.png": 1}
    out = _place_captions(md, captions, page_by_name).split("\n")

    i1 = out.index("![Image](img-01.png)")
    assert out[i1 + 2] == "Figure 1: First caption here."
    i2 = out.index("![Image](img-02.png)")
    assert out[i2 + 2] == "Figure 2: Second caption here."
    assert out.count("Figure 1: First caption here.") == 1
    assert out.count("Figure 2: Second caption here.") == 1


def test_place_captions_injects_orphan_caption_by_page():
    # 이미지 없는 그룹 그림의 캡션(md에 없음)을 같은 페이지 이미지 뒤에 주입 → 유실 방지
    from md4paper.extract.docling_backend import _place_captions

    md = "Body text on page.\n\n![Image](img-05.png)\n\nMore body.\n"
    captions = [("Figure 2: Prompts for LLMs", 3)]  # md에 없는 캡션
    page_by_name = {"img-05.png": 3}
    out = _place_captions(md, captions, page_by_name)
    assert "Figure 2: Prompts for LLMs" in out
    lines = out.split("\n")
    i = lines.index("![Image](img-05.png)")
    assert lines[i + 2] == "Figure 2: Prompts for LLMs"


def test_place_captions_leaves_caption_when_page_has_no_image():
    # 페이지에 이미지가 없으면 못 붙임 → 원문 그대로 (건드리지 않음)
    from md4paper.extract.docling_backend import _place_captions

    md = "![Image](img-01.png)\n\nsome text\n"
    assert _place_captions(md, [], {}) == md


def test_body_labels_includes_caption():
    pytest.importorskip("docling_core", reason="docling 미설치")
    from docling_core.types.doc import DocItemLabel

    from md4paper.extract.docling_backend import _body_labels

    labels = _body_labels()
    assert DocItemLabel.CAPTION in labels  # 캡션이 export에 포함돼야 그림/표 설명이 안 빠진다


# --- run-in 헤더 감지 (추출기가 뭉갠 소제목) ---
def test_runin_detects_list_flattened_and_long_numbered_titles():
    from md4paper.structure import runin

    # Docling이 리스트 항목으로 뭉갠 run-in
    assert runin.detect("- 4.2.1 The Prompt Instruction Panel . The left side is the control center.")
    # 긴 번호형 제목(예전 12단어 제한에 걸려 놓치던 것)
    got = runin.detect(
        "4.1.1 From Prompt to Data: Using the Specification to Discover Its Own Blind Spots. "
        "The refinement process begins with the current prompt."
    )
    assert got and got[0] == "4.1.1"


def test_runin_detects_colon_form_without_number_or_emphasis():
    """굵게 표시를 잃은 콜론형 소제목: 'Prompt Instruction Editor: 본문…'."""
    from md4paper.structure import runin

    got = runin.detect(
        "Prompt Instruction Editor: At the top is a text editor where users write instructions."
    )
    assert got is not None
    num, title, body, _ital = got
    assert num is None and title == "Prompt Instruction Editor"
    assert body.startswith("At the top is")


def test_runin_colon_form_ignores_ordinary_sentences():
    """콜론이 있어도 앞이 타이틀케이스 구가 아니거나 뒤가 짧으면 제목이 아니다."""
    from md4paper.structure import runin

    assert runin.detect(
        "We use the following setup: the model was trained on eight GPUs for three days."
    ) is None
    assert runin.detect("Note: See above.") is None  # 뒤 본문이 너무 짧음
    assert runin.detect("- [37] Crystal Qian. 2025. LLM Adoption. In Proceedings.") is None
    # 약칭·열거 라벨(RQ4, TAM 등) 콜론형 리스트 항목은 소제목이 아니다 (제목/본문으로 쪼개면 안 됨)
    assert runin.detect(
        "- RQ4: How does students' ChatGPT usage shape their perception of the writing experience?"
    ) is None
    assert runin.detect(
        "TAM: Technology Acceptance Model captures attitudes toward perceived usefulness and ease."
    ) is None
    # 진짜 소제목(소문자 포함 타이틀케이스)은 계속 감지
    assert runin.detect(
        "Prompt Instruction Editor: At the top is a text editor where users write instructions."
    ) is not None
    # 굵게를 잃은 정의형 불릿 목록('- Label: 문장')은 소제목이 아니라 본문 목록 → 콜론형 미적용
    assert runin.detect(
        "- Systematize Iteration: The constant, reactive cycle practitioners described requires a fast workflow."
    ) is None
    # 단, 번호형 소제목은 리스트로 뭉개져도 계속 감지 (추출기가 소제목을 리스트로 만든 경우)
    assert runin.detect(
        "- 4.2.1 The Prompt Instruction Panel. The left side is the control center for editing."
    ) is not None


def test_runin_detects_period_terminated_plain_titles():
    """추출기가 굵게를 떼어낸 마침표 종결 run-in — 'Categorization.', 'Step 1: ….', 'Simulated Personas (Oracles).'."""
    from md4paper.structure import runin

    got = runin.detect("Categorization. We defined a taxonomy of 24 categorical values spanning four dimensions.")
    assert got is not None and got[1] == "Categorization"
    got2 = runin.detect(
        "Step 1: Discovering Failures. The workflow begins in the Generated Examples view where the system runs.")
    assert got2 is not None and got2[1] == "Step 1: Discovering Failures"
    got3 = runin.detect(
        "Simulated Personas (Oracles). For each domain we defined four distinct personas governed by rules.")
    assert got3 is not None and got3[1] == "Simulated Personas (Oracles)"


def test_runin_period_form_ignores_ordinary_sentences_and_captions():
    from md4paper.structure import runin

    # 소문자 내용 단어가 있으면 일반 문장 → 감지 안 함
    assert runin.detect(
        "The workflow begins in the Generated Examples view where the system generates candidate inputs.") is None
    assert runin.detect(
        "For example, they might enter a question about credit scores and job applications in the box.") is None
    # 표·그림 캡션/참조는 마침표형에서 제외
    assert runin.detect(
        "Table 1. The results show significant improvement across all conditions tested in the study here.") is None
    # 본문이 너무 짧으면(제목만 있는 문장 등) 감지 안 함
    assert runin.detect("Neural Networks. We use them.") is None


def test_runin_detects_bold_emphasis():
    from md4paper.structure import runin

    # 볼드(**Title.**)도 이탤릭처럼 감지
    got = runin.detect("**Length.** Prompt instructions were varied across conditions in this controlled study.")
    assert got is not None and got[1] == "Length" and got[3] is True
    # 이탤릭도 여전히 감지
    got2 = runin.detect("*Setup.* We describe the experimental configuration used throughout the evaluation here.")
    assert got2 is not None and got2[1] == "Setup"


# --- 본문에 흘러든 'Corresponding author' 연락처 footer 제거 ---
def test_strip_contact_footer_removes_block_and_enables_rejoin():
    from md4paper.extract.docling_backend import _join_broken_paragraphs, _strip_contact_footer

    md = (
        "AI systems for synchronous classroom use should be legible, bounded, and supervisable in ways "
        "that foreground teachers' ability to configure, interpret, and oversee learning within the "
        "technology, rather\n\n"
        "## Corresponding author:\n\n"
        "Alex Liu, Univeristy of Washington College of Education, "
        "AmplifyLearn AI Center, Seattle, WA, 98195, US.\n\n"
        "Email: alexliux@uw.edu\n\n"
        "than emphasizing solely on underlying model capabilities Roschelle et al. (2013).\n"
    )
    stripped = _strip_contact_footer(md)
    assert "Corresponding author" not in stripped
    assert "alexliux@uw.edu" not in stripped and "Seattle" not in stripped
    # 연락처가 갈라놓았던 두 문단이 이어붙는다
    assert "technology, rather than emphasizing" in _join_broken_paragraphs(stripped)


def test_strip_contact_footer_keeps_following_body_paragraph():
    from md4paper.extract.docling_backend import _strip_contact_footer

    # 헤더 다음이 연락처가 아니라 본문 문단이면 헤더만 지운다 (오제거 방지)
    md = (
        "Corresponding author:\n\n"
        "This is an ordinary long body paragraph that must not be removed just because a contact "
        "header happened to precede it in the reflowed reading order here.\n"
    )
    out = _strip_contact_footer(md)
    assert "Corresponding author" not in out
    assert "ordinary long body paragraph" in out


def test_strip_contact_footer_noop_without_trigger():
    from md4paper.extract.docling_backend import _strip_contact_footer

    md = "## Intro\n\nBody one is here.\n\nBody two follows normally.\n"
    assert _strip_contact_footer(md) == md


# --- 그림 안 텍스트(차트 제목·축 이름)가 헤더로 오분류된 것 제거 ---
def test_drop_figure_text_removes_chart_titles_adjacent_to_image():
    from md4paper.extract.docling_backend import _drop_figure_text

    md = (
        "## 6.4 General comfort regarding system prompts\n\n"
        "Comfort levels for specific prompt topics are discussed here in the body.\n\n"
        "## Mean Benefit Points by Topic\n\n"          # 차트 제목(번호 없음) + 아래가 이미지
        "![Image](img-11.png)\n\n"
        "(b) Participants' perceptions of risks.\n\n"    # 서브캡션
        "## Benefits of System Prompts\n\n"            # 축 이름(위·아래가 그림 요소)
        "(a) Participants' perceptions of benefits.\n\n"
        "Figure 9: Participants' perceptions.\n\n"
        "## 6.5 All values are important\n\n"
        "When judging the importance of all design values, participants ...\n"
    )
    out = _drop_figure_text(md)
    assert "## Mean Benefit Points by Topic" not in out
    assert "## Benefits of System Prompts" not in out
    # 진짜 섹션·캡션·본문은 유지
    assert "## 6.4 General comfort regarding system prompts" in out
    assert "## 6.5 All values are important" in out
    assert "Figure 9: Participants' perceptions." in out
    assert "![Image](img-11.png)" in out


def test_drop_figure_text_keeps_numbered_and_body_headers():
    from md4paper.extract.docling_backend import _drop_figure_text

    # 번호형 헤더가 그림 바로 위에 있어도 진짜 섹션이므로 보존
    md = "## 5 Results\n\n![Image](img-01.png)\n\nFigure 1: Overview.\n"
    assert _drop_figure_text(md) == md
    # 번호 없는 헤더라도 뒤가 본문이면(그림 요소 아님) 보존
    md2 = "## Discussion\n\nWe discuss the results in detail across several paragraphs here.\n"
    assert _drop_figure_text(md2) == md2


# --- 각주: 본문 마커를 위첨자 링크로, 내용은 목록 + 툴팁 맵 ---
def test_apply_footnotes_links_markers_and_collects():
    from md4paper.extract.docling_backend import _apply_footnotes

    md = (
        "System prompts are 'confidential and permanent.' 1\n\n"
        "OpenAI reduced sycophancy quickly.' 2\n\n"
        "adding viewpoint specifications 4 (see Figure 2).\n\n"
        "We compare Figure 2 and Table 3 here, and cite [7, 158].\n\n"
        "## References\n\n- [8] Author. 2020. Title. Vol. 8. 1266-1277.\n"
    )
    footnotes = ["1 https://example.com/a", "2 https://example.com/b", "4 A note about neutrality"]
    out, tips = _apply_footnotes(md, footnotes)

    # 진짜 마커 → 위첨자 링크
    assert '<sup class="md-fn"><a href="#fn-1">1</a></sup>' in out
    assert '<sup class="md-fn"><a href="#fn-2">2</a></sup>' in out
    assert '<sup class="md-fn"><a href="#fn-4">4</a></sup>' in out
    # 그림·표 참조와 참고문헌 영역의 숫자는 링크하지 않음
    assert "Figure 2 and Table 3" in out
    assert "Vol. 8. 1266-1277" in out  # 참고문헌 영역은 손대지 않음
    assert "[7, 158]" in out
    # 각주 목록 + 앵커 + 툴팁 맵
    assert "## Footnotes" in out
    assert '<a id="fn-1"></a>' in out and '<a id="fn-4"></a>' in out
    assert tips == {"1": "https://example.com/a", "2": "https://example.com/b", "4": "A note about neutrality"}


def test_apply_footnotes_no_number_kept_in_list_only():
    from md4paper.extract.docling_backend import _apply_footnotes

    out, tips = _apply_footnotes("Body text with no marker.\n", ["Just a note without a leading number"])
    assert "## Footnotes" in out and "Just a note without a leading number" in out
    assert tips == {}  # 번호 없으면 툴팁/링크 대상 아님
    assert "<sup" not in out


# --- 헤더로 오분류된 그림·표 캡션을 캡션 문단으로 되돌리기 ---
def test_unheader_captions_demotes_table_and_figure_headers():
    from md4paper.extract.docling_backend import _unheader_captions

    md = (
        "## Table 10: Participants' Age: Distribution of Survey Participants (N = 109)\n\n"
        "| | 18-24 | 25-34 |\n| --- | --- | --- |\n| n | 14 | 36 |\n\n"
        "### Figure 3. Overview of the pipeline\n\n"
        "![Image](img-01.png)\n"
    )
    out = _unheader_captions(md)
    # 더 이상 헤더(#)가 아니고, 평문 'Label: 설명' 캡션 줄로 (표기는 조립 단계 caption_style이 담당)
    assert "## Table 10" not in out and "### Figure 3" not in out
    assert "Table 10: Participants' Age: Distribution of Survey Participants (N = 109)" in out
    assert "Figure 3: Overview of the pipeline" in out
    # 표 그리드·이미지는 그대로
    assert "| n | 14 | 36 |" in out
    assert "![Image](img-01.png)" in out


def test_delist_headers_demotes_bullet_prefixed_headers():
    from md4paper.extract.docling_backend import _delist_headers

    # 같은 불릿 목록인데 일부만 헤더로 승격된 경우(· = U+00B7) → 모두 리스트 항목으로 통일
    md = (
        "## · RQ2: What factors can predict how students use ChatGPT?\n\n"
        "Body paragraph.\n\n"
        "## • RQ3: How does usage manifest?\n\n"
        "## 4 Real Section\n\n"
        "Real body.\n"
    )
    out = _delist_headers(md)
    assert "- RQ2: What factors can predict how students use ChatGPT?" in out
    assert "- RQ3: How does usage manifest?" in out
    assert "## · RQ2" not in out and "## • RQ3" not in out
    assert "## 4 Real Section" in out  # 불릿 없는 진짜 헤더는 그대로


def test_unheader_captions_keeps_real_sections():
    from md4paper.extract.docling_backend import _unheader_captions

    # 진짜 섹션 제목은 'Table N/Figure N'으로 시작하지 않으므로 건드리지 않는다
    md = "## 5 Results\n\n## Tables and Figures\n\n## Figure-Ground Segmentation\n\nBody.\n"
    assert _unheader_captions(md) == md
