"""그림 페어링·리네임·뷰어별 플레이버 렌더 테스트."""

from md4paper.assemble import figures
from md4paper.ir import Flavor
from md4paper.structure import captions

# marker 실물과 유사: 이미지 뒤에 <span> 앵커가 붙은 캡션
SAMPLE = [
    "Some body text before the figure.",
    "",
    "![](_page_2_Diagram_0.jpeg)",
    "",
    '<span id="page-2-1"></span>Figure 1: The Transformer - model architecture.',
    "",
    "More body text.",
    "",
    "![](_page_7_Table_0.jpeg)",
    "Table 2: BLEU scores comparison.",
]


def test_caption_pairing_strips_span():
    pairs = captions.find_pairs(SAMPLE)
    assert len(pairs) == 2
    fig = pairs[0]
    assert fig.kind == "figure"
    assert fig.label == "Figure 1"
    assert fig.caption == "The Transformer - model architecture."
    assert fig.image_line == 2 and fig.caption_line == 4

    tab = pairs[1]
    assert tab.kind == "table"
    assert tab.label == "Table 2"


def test_stable_rename():
    plan = figures.build_plan(SAMPLE, Flavor.STANDARD)
    # 캡션 번호를 파일명에 반영: "Figure 1" → figure-1, "Table 2" → table-2
    assert plan.rename["_page_2_Diagram_0.jpeg"] == "figure-1.jpeg"
    assert plan.rename["_page_7_Table_0.jpeg"] == "table-2.jpeg"
    # 캡션 줄은 버려짐
    assert 4 in plan.drop_lines


def test_flavor_standard():
    # 기본 캡션 스타일은 bold-italic (라벨만 굵게 + 설명 이탤릭)
    plan = figures.build_plan(SAMPLE, Flavor.STANDARD)
    block = plan.image_blocks[2]
    assert block[0] == "![Figure 1](images/figure-1.jpeg)"
    assert block[1] == "**Figure 1:** *The Transformer - model architecture.*"


def test_flavor_obsidian():
    plan = figures.build_plan(SAMPLE, Flavor.OBSIDIAN)
    block = plan.image_blocks[2]
    assert block[0] == "![[images/figure-1.jpeg]]"
    assert block[1] == "**Figure 1:** *The Transformer - model architecture.*"


def test_caption_style_options_apply_to_figure_and_table():
    # blockquote — 그림·표 모두 적용
    plan = figures.build_plan(SAMPLE, Flavor.STANDARD, "blockquote")
    assert plan.image_blocks[2][1] == "> **Figure 1:** The Transformer - model architecture."
    assert plan.image_blocks[8][1] == "> **Table 2:** BLEU scores comparison."
    # italic — 전체 이탤릭 (기존 표기)
    plan_i = figures.build_plan(SAMPLE, Flavor.STANDARD, "italic")
    assert plan_i.image_blocks[2][1] == "*Figure 1: The Transformer - model architecture.*"
    assert plan_i.image_blocks[8][1] == "*Table 2: BLEU scores comparison.*"
    # bold-italic — 표에도 라벨 굵게
    plan_b = figures.build_plan(SAMPLE, Flavor.STANDARD, "bold-italic")
    assert plan_b.image_blocks[8][1] == "**Table 2:** *BLEU scores comparison.*"


def test_markdown_table_caption_styled_like_figure():
    # 이미지가 아닌 마크다운 표 그리드 + 캡션 → caption_style이 그림과 동일하게 적용 (replace_lines)
    lines = [
        "Body text before the table.",
        "",
        "Table 10: Participants' Age (N = 109).",
        "",
        "| | 18-24 | 25-34 |",
        "| --- | --- | --- |",
        "| n | 14 | 36 |",
        "",
        "More body.",
    ]
    plan = figures.build_plan(lines, Flavor.STANDARD, "bold-italic")
    assert plan.replace_lines[2] == "**Table 10:** *Participants' Age (N = 109).*"
    plan_bq = figures.build_plan(lines, Flavor.STANDARD, "blockquote")
    assert plan_bq.replace_lines[2] == "> **Table 10:** Participants' Age (N = 109)."
    plan_it = figures.build_plan(lines, Flavor.STANDARD, "italic")
    assert plan_it.replace_lines[2] == "*Table 10: Participants' Age (N = 109).*"


def test_table_reference_sentence_not_styled():
    # 본문 속 표 참조 문장(구분자 없음·그리드 비인접)은 캡션으로 오인하지 않는다
    lines = ["Table 1 shows the results across all conditions here.", "", "A normal paragraph follows."]
    plan = figures.build_plan(lines, Flavor.STANDARD)
    assert plan.replace_lines == {}


def test_flavor_notion():
    plan = figures.build_plan(SAMPLE, Flavor.NOTION)
    block = plan.image_blocks[2]
    # Notion: 전체 캡션을 alt에
    assert block == ["![Figure 1: The Transformer - model architecture.](images/figure-1.jpeg)"]


def test_flavor_html():
    plan = figures.build_plan(SAMPLE, Flavor.HTML)
    block = plan.image_blocks[2]
    assert block[0] == "<figure>"
    assert '<img src="images/figure-1.jpeg" alt="Figure 1">' in block
    assert any(line.startswith("<figcaption>") for line in block)




# ICLR/NeurIPS 서브피규어: 이미지와 메인 캡션 사이에 (a)/(b) 서브캡션이 낀 경우
SUBFIG = [
    "Body text before.",
    "",
    "![Image](img-01.png)",
    "",
    "- (a) Automatic Prompt Engineer (APE) workflow",
    "",
    "(b) Interquartile mean across 24 tasks",
    "",
    "Figure 1: (a) Our method APE generates instructions. (b) It surpasses humans.",
    "",
    "can execute a broad range of natural language programs.",
]


def test_subfigure_caption_found_past_sublabels():
    pairs = captions.find_pairs(SUBFIG)
    fig = next(p for p in pairs if p.image_line == 2)
    assert fig.label == "Figure 1"
    assert fig.caption.startswith("(a) Our method APE")
    assert fig.caption_line == 8
    # (a)/(b) 서브캡션 줄이 소비 대상으로 기록
    assert fig.consumed_lines == [4, 6]


def test_subfigure_sublabels_absorbed_into_block():
    pairs = captions.find_pairs(SUBFIG)
    fig = next(p for p in pairs if p.image_line == 2)
    # 서브캡션 텍스트는 (불릿 제거되어) 보존된다 — 삭제가 아니라 흡수
    assert fig.subcaptions == ["(a) Automatic Prompt Engineer (APE) workflow",
                               "(b) Interquartile mean across 24 tasks"]

    plan = figures.build_plan(SUBFIG, Flavor.STANDARD)
    block = "\n".join(plan.image_blocks[2])
    assert "Figure 1" in block and "images/figure-1" in block
    # 서브캡션이 원위치에서 제거되고 그림 블록 안(이미지 아래·캡션 위)에 이탤릭으로 들어간다
    assert {4, 6, 8} <= plan.drop_lines
    assert "*(a) Automatic Prompt Engineer (APE) workflow*" in block
    assert "*(b) Interquartile mean across 24 tasks*" in block


def test_subfigure_scan_stops_at_body_not_caption():
    # 이미지 아래가 서브캡션이 아니라 일반 본문이면 캡션으로 오인하지 않는다
    lines = ["![Image](x.png)", "", "This is ordinary body text, not a caption.", "",
             "Figure 9: unrelated caption far below."]
    fig = captions.find_pairs(lines)[0]
    assert fig.label is None  # 본문에서 멈춰 먼 캡션을 가져오지 않음
