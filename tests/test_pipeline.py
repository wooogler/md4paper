"""엔드투엔드 스모크 — 사전 추출 마크다운을 통해 marker 없이 전체 파이프라인 검증."""

from pathlib import Path

import pytest

from md4paper import pipeline
from md4paper.review import manifest as manifest_io
from md4paper.structure import build
from md4paper.workdir import WorkDir

CORPUS = Path(__file__).parent / "corpus"


@pytest.fixture
def wd(tmp_path):
    return WorkDir(tmp_path / "paper.md4")


def test_convert_relevels_headers(wd):
    src = CORPUS / "sample_arxiv.md"
    pipeline.convert(src, wd)

    out = wd.en_md.read_text(encoding="utf-8")
    # marker는 모두 ## 였지만 번호 기반으로 재레벨링돼야 함
    assert "# Abstract" in out  # 무번호 → L1
    assert "\n# 1 Introduction" in out
    assert "\n## 3.1 Encoder and Decoder Stacks" in out
    assert "\n### 3.2.1 Scaled Dot-Product Attention" in out
    assert "\n# References" in out
    # 그림은 안정적 파일명으로 리네임 + 캡션 흡수 (기본 스타일 bold-italic)
    assert "![Figure 1](images/figure-1.jpeg)" in out
    assert "**Figure 1:** *The Transformer - model architecture.*" in out
    # 원본 캡션 줄은 중복 제거됨
    assert out.count("The Transformer - model architecture.") == 1


def test_convert_ieee_roman_mixed(wd):
    src = CORPUS / "sample_ieee.md"
    pipeline.convert(src, wd)
    out = wd.en_md.read_text(encoding="utf-8")
    # 로마 최상위 → h1
    assert "\n# I. Introduction" in out
    assert "\n# III. Deep Residual Learning" in out
    assert "\n# V. Conclusion" in out
    # 문자 하위섹션 → h2
    assert "\n## A. Residual Learning" in out
    assert "\n## B. Identity Mapping by Shortcuts" in out
    # 무번호 → h1
    assert "\n# Abstract" in out
    assert "\n# References" in out


def test_manifest_roundtrip(wd):
    src = CORPUS / "sample_arxiv.md"
    pipeline.run_extract(src, wd)
    manifest = pipeline.run_structure(wd)

    # 저장했다 다시 읽어도 레벨/텍스트 보존
    reloaded = manifest_io.load(wd)
    assert len(reloaded.sections) == len(manifest.sections)
    orig = {s.id: s.level for s in manifest.sections}
    back = {s.id: s.level for s in reloaded.sections}
    assert orig == back
    # 라인 앵커가 blocks.json에서 복원됨
    assert all(s.line >= 0 for s in reloaded.sections)


def test_status_resume(wd):
    src = CORPUS / "sample_arxiv.md"
    pipeline.run_extract(src, wd)
    h = __import__("md4paper.workdir", fromlist=["hash_text"]).hash_text(f"{src}:docling:ocr=False")
    assert wd.is_fresh("extract", h)
    # 두 번째 extract는 스킵
    meta = pipeline.run_extract(src, wd)
    assert meta.get("skipped") is True


def test_parse_headings_ignores_fenced_code():
    raw = "# Real\n\n```\n# not a heading\n```\n\n## Also Real\n"
    heads = build.parse_headings(raw)
    texts = [t for _, _, t in heads]
    assert texts == ["Real", "Also Real"]


def test_parse_headings_strips_html_anchors():
    # marker가 헤더에 심는 <span> 앵커를 제거하고 번호를 정상 감지해야 함
    raw = '## <span id="page-2-0"></span>3.2 Attention\n'
    heads = build.parse_headings(raw)
    assert heads[0][2] == "3.2 Attention"


def test_drop_removes_section_and_body(wd):
    src = CORPUS / "sample_arxiv.md"
    pipeline.run_extract(src, wd)
    manifest = pipeline.run_structure(wd)
    # "2 Background" 섹션을 drop → 헤더와 본문 모두 사라짐
    bg = next(s for s in manifest.sections if s.text == "2 Background")
    bg.level = "drop"
    from md4paper.assemble.render import render_markdown

    raw = wd.raw_md.read_text(encoding="utf-8")
    text, _, _ = render_markdown(raw, manifest)
    assert "2 Background" not in text
    assert "The goal of reducing sequential computation" not in text  # 본문도 제거
    # 다음 섹션은 남아 있음
    assert "3 Model Architecture" in text


def test_bare_image_gets_images_prefix(wd):
    # 인라인(자기 줄이 아닌) 이미지는 figure 페어링을 못 받아 베어로 남음 → images/ 접두
    from md4paper.assemble.render import render_markdown
    from md4paper.ir import Manifest

    raw = "# Title\n\ntext with ![](_page_6_Diagram_2.jpeg) inline image\n"
    text, _, _ = render_markdown(raw, Manifest(sections=[]))
    assert "![](images/_page_6_Diagram_2.jpeg)" in text
    # 절대경로/URL/이미 images/ 인 것은 안 건드림
    raw2 = "![](images/fig-01.jpeg) and ![](http://x.com/a.png)\n"
    text2, _, _ = render_markdown(raw2, Manifest(sections=[]))
    assert "images/fig-01.jpeg" in text2 and "images/images/" not in text2
    assert "http://x.com/a.png" in text2


def test_references_reformatted_into_paragraphs(wd):
    # marker처럼 흘려 쓴 참고문헌([1]...[2]...)이 항목별로 분리되고 span 앵커가 제거되는지
    from md4paper.assemble.render import render_markdown
    from md4paper.ir import Manifest, ManifestSection

    raw = (
        "# References\n\n"
        '<span id="page-9-0"></span>[1] Alice Author. First paper. 2020. '
        "[2] Bob Writer. Second paper. 2021. [3] Carol Coder. Third paper. 2022.\n"
    )
    manifest = Manifest(sections=[ManifestSection(id="h_0000", text="References", line=0, level=1)])
    text, section_map, _ = render_markdown(raw, manifest)
    # 항목별로 분리 (각자 자기 문단)
    assert "\n[1] Alice Author. First paper. 2020.\n" in text
    assert "\n[2] Bob Writer. Second paper. 2021.\n" in text
    assert "\n[3] Carol Coder. Third paper. 2022.\n" in text
    # span 앵커 제거됨
    assert "<span" not in text



def test_runin_no_false_positive_on_prose(wd):
    # 소문자로 이어지는 일반 문장은 run-in 헤더로 오탐하면 안 됨
    from md4paper.assemble.render import render_markdown
    from md4paper.ir import Manifest

    raw = "# S\n\n3.2 shows the results clearly. We then analyze them.\n"
    text, _, _ = render_markdown(raw, Manifest(sections=[], runin_headings="header"))
    assert "### 3.2 shows" not in text  # 소문자 시작 → 감지 안 됨



def test_repair_split_entities():
    # marker가 `<`를 `&lt;`로 뽑다가 `&`만 <sup>에 가둔 실제 패턴
    from md4paper.extract.text_clean import clean_extracted, repair_entities

    assert repair_entities("significant (<sup>&</sup>lt; 0.001)") == "significant (< 0.001)"
    assert repair_entities("a &lt; b &gt; c &amp; d") == "a < b > c & d"
    # 정상 텍스트는 그대로
    assert clean_extracted("plain <sup>2</sup> text") == "plain <sup>2</sup> text"


def test_normalize_math_letters():
    # 수학용 이탤릭 유니코드(𝑡 U+1D461, 𝑝 U+1D45D)를 ASCII로; 위첨자²는 보존
    from md4paper.extract.text_clean import normalize_math_letters

    assert normalize_math_letters("A paired \U0001d461-test (\U0001d45d < 0.001)") == "A paired t-test (p < 0.001)"
    assert normalize_math_letters("area m²") == "area m²"  # 위첨자는 그대로
    assert normalize_math_letters("plain text") == "plain text"


def test_translate_headers_manifest_roundtrip(wd):
    src = CORPUS / "sample_arxiv.md"
    pipeline.run_extract(src, wd)
    manifest = pipeline.run_structure(wd)
    manifest.translate_headers = False
    manifest_io.save(manifest, wd)
    assert manifest_io.load(wd).translate_headers is False


def test_skip_and_merge_ops(wd):
    src = CORPUS / "sample_arxiv.md"
    pipeline.run_extract(src, wd)
    manifest = pipeline.run_structure(wd)
    # 첫 섹션을 skip으로, 둘째를 merge-up으로 바꿔 렌더 동작 확인
    from md4paper.assemble.render import render_markdown

    manifest.sections[1].level = "skip"
    manifest.sections[2].level = "merge-up"
    raw = wd.raw_md.read_text(encoding="utf-8")
    text, _, _ = render_markdown(raw, manifest)
    # skip → 헤더 마크 제거된 일반 텍스트
    assert manifest.sections[1].text in text
    assert f"# {manifest.sections[1].text}" not in text

def test_runin_detected_into_section_tree(wd):
    """run-in 헤더는 항상 섹션 트리에 들어가야 한다 (사용자가 조정 가능하도록)."""
    raw = (
        "# 4 Workflow\n\n"
        "4.1.3 A Living Test Set. Each discovered case is preserved in a growing test set.\n\n"
        "This is a normal paragraph.\n"
    )
    wd.ensure()
    wd.raw_md.write_text(raw, encoding="utf-8")
    m = build.build(raw, wd)
    ri = [s for s in m.sections if s.runin]
    assert len(ri) == 1
    assert ri[0].text == "4.1.3 A Living Test Set"
    assert ri[0].runin_body.startswith("Each discovered case")
    # 일반 문단은 run-in으로 오탐하지 않음
    assert not any("normal paragraph" in s.text for s in m.sections)


def test_runin_render_respects_level(wd):
    """트리에서 고른 레벨대로 렌더된다: 승격 / 이탤릭 / 본문 유지."""
    from md4paper.assemble.render import render_markdown

    raw = "# 4 Workflow\n\n4.1.3 A Living Test Set. Each discovered case is preserved.\n"
    wd.ensure()
    wd.raw_md.write_text(raw, encoding="utf-8")
    m = build.build(raw, wd)
    ri = next(s for s in m.sections if s.runin)

    ri.level = 3
    text, section_map, _ = render_markdown(raw, m)
    assert "### 4.1.3 A Living Test Set" in text
    assert "Each discovered case is preserved." in text
    assert any(e["text"] == "4.1.3 A Living Test Set" for e in section_map)

    ri.level = "italic"
    text2, _, _ = render_markdown(raw, m)
    assert "*4.1.3 A Living Test Set.* Each discovered" in text2

    ri.level = "skip"
    text3, _, _ = render_markdown(raw, m)
    assert "4.1.3 A Living Test Set. Each discovered case is preserved." in text3
    assert "###" not in text3


def test_recent_workdirs_lists_by_mtime(tmp_path):
    """홈 화면 '최근 작업' — 유효한 .md4만, 최근 수정순, 제목 추출."""
    import time as _t
    from md4paper.workdir import recent_workdirs

    ws = tmp_path / "ws"
    a = WorkDir(ws / "PaperA.md4")
    pipeline.convert(CORPUS / "sample_arxiv.md", a)
    _t.sleep(0.02)
    b = WorkDir(ws / "PaperB.md4")
    pipeline.convert(CORPUS / "sample_cite.md", b)
    # 손상된(빈) 디렉토리는 목록에서 제외
    (ws / "Broken.md4").mkdir()

    got = recent_workdirs(ws)
    names = [r["name"] for r in got]
    assert names == ["PaperB", "PaperA"]  # 최근 수정이 먼저
    assert "Broken" not in names
    assert got[0]["title"] == "A Study of Attention"  # sections.yaml title 추출


def test_runin_italic_option_renders(wd):
    """run-in 헤더에 italic 선택 → *제목.* 이탤릭 문단으로."""
    from md4paper.assemble.render import render_markdown

    raw = "# 4 Workflow\n\n4.1.3 A Living Test Set. Each discovered case is preserved.\n"
    wd.ensure()
    wd.raw_md.write_text(raw, encoding="utf-8")
    m = build.build(raw, wd)
    ri = next(s for s in m.sections if s.runin)
    ri.level = "italic"
    text, _, _ = render_markdown(raw, m)
    assert "*4.1.3 A Living Test Set.* Each discovered" in text
    assert "###" not in text


def test_first_unnumbered_is_title(wd):
    src = CORPUS / "sample_arxiv.md"
    pipeline.run_extract(src, wd)
    m = pipeline.run_structure(wd)
    titles = [s for s in m.sections if s.is_title]
    assert len(titles) == 1
    assert titles[0].text == "Attention Is All You Need"
    assert titles[0].level == 1
    # Abstract 같은 무번호 키워드는 제목이 아님
    assert not any(s.is_title for s in m.sections if s.text == "Abstract")


def test_docling_boilerplate_and_footnote_helpers():
    """저작권 boilerplate 탐지 + 각주 분리 로직 (docling 없이 순수 함수 테스트)."""
    from md4paper.extract.docling_backend import _BOILERPLATE_RE, _EXCLUDE_LABELS

    assert _BOILERPLATE_RE.search("This work is licensed under a Creative Commons...")
    assert _BOILERPLATE_RE.search("ACM ISBN 979-8-4007-2278-3/2026/04")
    assert _BOILERPLATE_RE.search("© 2026 Copyright held by the owner/author(s).")
    # 진짜 본문은 안 걸린다
    assert not _BOILERPLATE_RE.search("Large Language Models are increasingly embedded.")
    # 러닝 헤더/푸터·각주는 본문에서 제외 대상
    assert _EXCLUDE_LABELS == {"page_header", "page_footer", "footnote"}


def test_delete_workdir_safety(tmp_path):
    from md4paper.workdir import delete_workdir

    ws = tmp_path / "ws"
    wd = WorkDir(ws / "Paper" / "Paper.md4")  # 논문별 하위폴더 구조
    pipeline.convert(CORPUS / "sample_arxiv.md", wd)
    assert wd.root.exists()
    # 원본 PDF가 부모 폴더에 남아 있는 실제 상황 재현
    src_pdf = ws / "Paper" / "Paper.pdf"
    src_pdf.write_bytes(b"%PDF-1.4 fake")

    # 작업 폴더 밖은 거부
    outside = tmp_path / "other"
    outside.mkdir()
    assert delete_workdir(outside, ws) is False
    assert outside.exists()
    # .md4 아닌 것도 거부
    assert delete_workdir(ws / "Paper", ws) is False

    # 정상 삭제 → .md4뿐 아니라 남은 원본 PDF와 논문 폴더까지 정리
    assert delete_workdir(wd.root, ws) is True
    assert not wd.root.exists()
    assert not (ws / "Paper").exists()  # 원본 PDF까지 폴더째 삭제됨


def test_delete_workdir_keeps_sibling_paper(tmp_path):
    # 부모 폴더에 다른 논문(.md4)이 있으면 폴더째 지우지 않는다 (안전)
    from md4paper.workdir import delete_workdir

    ws = tmp_path / "ws"
    a = WorkDir(ws / "shared" / "A.md4")
    b = WorkDir(ws / "shared" / "B.md4")
    pipeline.convert(CORPUS / "sample_arxiv.md", a)
    pipeline.convert(CORPUS / "sample_arxiv.md", b)
    assert delete_workdir(a.root, ws) is True
    assert not a.root.exists()
    assert b.root.exists()  # 형제 논문은 보존
    assert (ws / "shared").exists()


def test_run_frontmatter_normalizes_and_caches(tmp_path):
    """run_frontmatter: 흩어진 저자 정리 후 raw.md 갱신 + 재호출 캐시 스킵."""
    from md4paper import pipeline
    from md4paper.workdir import WorkDir

    wd = WorkDir(tmp_path / "p.md4")
    wd.extract.mkdir(parents=True)
    wd.raw_md.write_text(
        "## Paper Title\n\n"
        "## [Andrew Jelson](https://orcid.org/1)\n\n"
        "CS Virginia Tech, USA jelson@vt.edu\n\n"
        "## [Daniel Dunlap](https://orcid.org/2)\n\n"
        "CS Virginia Tech, USA dunlapd@vt.edu\n\n"
        "979-8-4007-2278-3/26/04\n\n"
        "## Abstract\n\nReal abstract body.\n\n"
        "## 1 Introduction\n\nBody text.\n",
        encoding="utf-8",
    )
    r1 = pipeline.run_frontmatter(wd, provider=None)  # 규칙 경로
    assert r1["changed"] is True
    raw = wd.raw_md.read_text(encoding="utf-8")
    assert "**[Andrew Jelson]" in raw and "## [Andrew Jelson]" not in raw
    assert "979-8-4007" not in raw
    assert "## 1 Introduction" in raw and "Body text." in raw

    r2 = pipeline.run_frontmatter(wd, provider=None)  # 이미 정규화 → 캐시 스킵
    assert r2.get("skipped") is True


def test_sibling_runin_headers_share_level(tmp_path):
    """형제 무번호 run-in 소제목은 같은 레벨 — 4,5로 계단식으로 깊어지지 않는다."""
    from md4paper import config

    config.set_section_value("structure", "runin_headings", "header")
    raw = (
        "# Doc Title\n\n"
        "## 4 System Design\n\n"
        "The Test Set: The main component is the table representing the test set which "
        "collects labeled examples serving as the ground truth for the policy here.\n\n"
        "Generated Examples: Below the test set another table serves as a staging area for "
        "examples synthesized by models for the user to review here.\n"
    )
    wd = WorkDir(tmp_path / "p.md4")
    wd.ensure()
    wd.raw_md.write_text(raw, encoding="utf-8")
    man = build.build(raw, wd)
    by = {s.text: s for s in man.sections}
    ts, ge = by["The Test Set"], by["Generated Examples"]
    assert ts.runin and ge.runin
    assert ts.auto_level == ge.auto_level  # 형제 → 동일 레벨 (이전엔 4 vs 5)


def test_section_layout_classify_runins():
    """LLM run-in 판정 — 소제목/비소제목 분류 + 레벨 이상 제외."""
    import re as _re

    from md4paper.ir import RuninDecision, SectionLayout
    from md4paper.llm import FakeProvider
    from md4paper.structure import section_layout

    items = [
        {"index": 0, "text": "4 System Design", "level": 2},
        {"index": 1, "text": "The Test Set", "level": None, "context": "The main component is the table."},
        {"index": 2, "text": "Systematize Iteration", "level": None, "context": "The constant reactive cycle."},
    ]
    layout = SectionLayout(decisions=[
        RuninDecision(index=1, is_heading=True, level=3),
        RuninDecision(index=2, is_heading=False, level=0),
    ])
    prov = FakeProvider(parse_fn=lambda s, u, sc: layout, model="fake")
    assert section_layout.classify(prov, items) == {1: (True, 3), 2: (False, 0)}
    # 판정 대상 없으면 호출 없이 {}
    assert section_layout.classify(prov, [{"index": 0, "text": "x", "level": 1}]) == {}
    # 레벨 범위 밖은 결과에서 제외(규칙 폴백 유도)
    bad = SectionLayout(decisions=[RuninDecision(index=1, is_heading=True, level=9)])
    prov2 = FakeProvider(parse_fn=lambda s, u, sc: bad, model="f")
    assert 1 not in section_layout.classify(prov2, items)
    _ = _re  # noqa


def test_build_uses_llm_runin_decisions(tmp_path):
    """build가 LLM 판정을 따른다 — 소제목은 승격, '헤더 아님'은 본문(skip)."""
    import re as _re

    from md4paper import config
    from md4paper.ir import RuninDecision, SectionLayout
    from md4paper.llm import FakeProvider

    config.set_section_value("structure", "runin_headings", "header")
    raw = (
        "# Doc\n\n"
        "## 4 System Design\n\n"
        "The Test Set: The main component is the table representing the test set with labeled "
        "examples serving as the ground truth for the policy here.\n\n"
        "Systematize Iteration: The constant reactive cycle practitioners described requires a "
        "fast and structured workflow here.\n"
    )

    def parse_fn(system, user, schema):
        decs = []
        for ln in user.splitlines():
            m = _re.match(r"\[(\d+)\] \?\? (.*)", ln)
            if not m:
                continue
            i, text = int(m.group(1)), m.group(2)
            if text.startswith("The Test Set"):
                decs.append(RuninDecision(index=i, is_heading=True, level=3))
            elif text.startswith("Systematize Iteration"):
                decs.append(RuninDecision(index=i, is_heading=False, level=0))
        return SectionLayout(decisions=decs)

    prov = FakeProvider(parse_fn=parse_fn, model="fake")
    wd = WorkDir(tmp_path / "p.md4")
    wd.ensure()
    wd.raw_md.write_text(raw, encoding="utf-8")
    man = build.build(raw, wd, provider=prov)
    by = {s.text: s for s in man.sections}
    assert by["The Test Set"].level == 3          # LLM: 진짜 소제목 → 승격
    assert by["Systematize Iteration"].level == "skip"  # LLM: 헤더 아님 → 본문


def test_cli_convert_runs_frontmatter_stage(tmp_path, monkeypatch):
    """CLI convert도 웹 UI(pipeline.convert)와 같은 앞부분 정규화 단계를 거친다.

    예전엔 cli.convert가 extract→structure→assemble만 불러서 저자 줄이 원문 그대로
    뭉쳐 나왔다(웹 UI 경로에만 run_frontmatter가 있었음).
    """
    from click.testing import CliRunner

    from md4paper.cli import cli

    src = tmp_path / "paper.md"
    src.write_text(
        "## Paper Title\n\n"
        "## [Andrew Jelson](https://orcid.org/1)\n\n"
        "CS Virginia Tech, USA jelson@vt.edu\n\n"
        "## Abstract\n\nReal abstract body.\n\n"
        "## 1 Introduction\n\nBody text.\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(cli, ["convert", str(src)])
    assert result.exit_code == 0, result.output

    wd = WorkDir(tmp_path / "paper.md4")
    assert "frontmatter" in wd.load_status()  # 단계가 실제로 돌았다
    raw = wd.raw_md.read_text(encoding="utf-8")
    assert "## [Andrew Jelson]" not in raw  # 저자 줄이 헤더로 남지 않는다


def test_manifest_persists_author_parts(tmp_path):
    """저자 표기(author_parts)는 매니페스트에 저장·복원된다.

    저장 안 하면 다시 열 때 config 기본값으로 되돌아가, UI 체크박스와 실제 raw.md 저자 블록이
    어긋난다(그 상태에서 토글하면 옛 블록을 못 찾아 조용히 무시됨).
    """
    from md4paper.ir import Flavor, Manifest

    wd = WorkDir(tmp_path / "p.md4")
    wd.structure.mkdir(parents=True)
    m = Manifest(title="T", flavor=Flavor.STANDARD, author_parts=["affiliation"], sections=[])
    manifest_io.save(m, wd)

    assert "author_parts" in wd.sections_yaml.read_text(encoding="utf-8")
    assert manifest_io.load(wd).author_parts == ["affiliation"]


def test_manifest_without_author_parts_falls_back_to_config(tmp_path):
    """옛 매니페스트(키 없음)는 config 기본값(이메일+소속)을 따른다."""
    wd = WorkDir(tmp_path / "old.md4")
    wd.structure.mkdir(parents=True)
    wd.sections_yaml.write_text("title: T\ncitation_parts:\n- number\nsections: []\n", encoding="utf-8")

    assert manifest_io.load(wd).author_parts == ["email", "affiliation"]
