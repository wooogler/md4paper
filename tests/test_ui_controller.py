"""웹 UI 컨트롤러 테스트 — NiceGUI 없이 순수 로직 검증."""

from pathlib import Path

import pytest

from md4paper import pipeline
from md4paper.ir import Flavor
from md4paper.ui.controller import UIController
from md4paper.workdir import WorkDir

CORPUS = Path(__file__).parent / "corpus"


@pytest.fixture
def ctrl(tmp_path):
    wd = WorkDir(tmp_path / "paper.md4")
    pipeline.convert(CORPUS / "sample_arxiv.md", wd)
    return UIController(wd)


def test_set_level_clears_needs_review(ctrl):
    sec = ctrl.manifest.sections[1]
    sec.needs_review = True  # 확인 필요 상태를 만들고
    ctrl.set_level(sec.id, 2)
    assert sec.level == 2
    assert not sec.needs_review  # 사용자가 레벨을 정하면 해제된다


def test_title_treated_separately(ctrl):
    # 첫 무번호 헤더는 제목으로 인식 → h1 고정, 확인 대상 아님, 별도 그룹
    title = next(s for s in ctrl.manifest.sections if s.is_title)
    assert title.text == "Attention Is All You Need"
    assert title.level == 1 and not title.needs_review
    assert ctrl.group_key(title) == ("title", 0)


def test_set_level_special_ops(ctrl):
    sid = ctrl.manifest.sections[1].id
    ctrl.set_level(sid, "skip")
    assert ctrl.section(sid).level == "skip"




def test_set_setting(ctrl):
    ctrl.set_setting("flavor", "obsidian")
    assert ctrl.manifest.flavor is Flavor.OBSIDIAN
    ctrl.set_setting("citation_parts", ["number", "short"])
    assert ctrl.manifest.citation_parts == ["number", "short"]
    ctrl.set_setting("reference_links", False)
    assert ctrl.manifest.reference_links is False
    with pytest.raises(ValueError):
        ctrl.set_setting("nonexistent", "x")


def test_set_author_parts_rerenders_from_stored_authors(ctrl):
    import json

    # 저장된 구조화 저자 + raw.md의 저자 블록(이메일·소속 둘 다 표기 = 기본값)을 심는다
    ctrl.wd.authors_json.write_text(json.dumps([
        {"name": "Anna Neumann", "emails": ["anna@uni.de"], "affiliations": ["UA Ruhr University, Germany"]},
    ]), encoding="utf-8")
    block = "**Anna Neumann**  \nanna@uni.de  \nUA Ruhr University, Germany"
    raw = ctrl.wd.raw_md.read_text(encoding="utf-8")
    ctrl.wd.raw_md.write_text(raw.replace("# ", f"{block}\n\n# ", 1), encoding="utf-8")
    ctrl.manifest.author_parts = ["email", "affiliation"]

    # 소속 끄기 → raw.md에서 소속 줄이 사라지고 이메일은 유지
    assert ctrl.set_author_parts(["email"]) is True
    out = ctrl.wd.raw_md.read_text(encoding="utf-8")
    assert "anna@uni.de" in out and "UA Ruhr University, Germany" not in out
    assert ctrl.manifest.author_parts == ["email"]

    # 저장된 저자가 없으면 매니페스트만 갱신하고 False
    ctrl.wd.authors_json.unlink()
    assert ctrl.set_author_parts(["email", "affiliation"]) is False
    assert ctrl.manifest.author_parts == ["email", "affiliation"]


def test_save_and_reassemble_reflects_edits(ctrl):
    sid = ctrl.manifest.sections[3].id  # "2 Background" → 원래 h1
    ctrl.set_level(sid, 3)
    ctrl.set_setting("flavor", "obsidian")
    ctrl.save_and_reassemble()
    # 재로드해도 편집 보존
    reloaded = UIController(ctrl.wd)
    assert reloaded.section(sid).level == 3
    assert reloaded.manifest.flavor is Flavor.OBSIDIAN
    # en.md에 반영
    out = ctrl.en_markdown()
    assert "### 2 Background" in out


def test_no_pdf_for_markdown_source(ctrl):
    # 사전 추출 .md 입력이므로 원본 PDF 없음
    assert ctrl.source_pdf() is None
    assert ctrl.pdf_page_count() == 0
    assert ctrl.pdf_page_png(0) is None


def _fake_gloss():
    from md4paper.ir import GlossaryEntry, GlossaryList
    from md4paper.llm import FakeProvider

    gl = GlossaryList(entries=[GlossaryEntry(term="attention", korean="어텐션", policy="transliterate")])
    return FakeProvider(complete_fn=lambda s, u: u, parse_fn=lambda s, u, sc: gl, model="gpt-5.6-luna")


def test_glossary_generate_and_load(ctrl):
    assert ctrl.glossary_entries() == []  # 아직 없음
    entries = ctrl.generate_glossary(_fake_gloss())
    assert len(entries) == 1 and entries[0].term == "attention"
    # 저장돼서 다시 로드됨
    assert len(ctrl.glossary_entries()) == 1


def test_glossary_save_edit(ctrl):
    from md4paper.ir import GlossaryEntry

    ctrl.save_glossary([GlossaryEntry(term="encoder", korean="인코더", policy="translate")])
    loaded = ctrl.glossary_entries()
    assert loaded[0].term == "encoder" and loaded[0].korean == "인코더"


def test_is_dense_section():
    from md4paper.translate.context import _is_dense_section

    assert _is_dense_section("Abstract")
    assert _is_dense_section("1 Introduction")
    assert _is_dense_section("I. Introduction")
    assert not _is_dense_section("2 Related Work")
    assert not _is_dense_section("Interactive Systems")  # 'I' 접두 오탐 방지


def test_glossary_source_has_abstract_full_and_titles(ctrl):
    from md4paper.translate import context

    src = context.extract_glossary_source(ctrl.wd, ctrl.manifest)
    assert "## Abstract" in src
    assert "## References" in src  # 섹션 제목이 들어감
    # 초록은 전문(한 문장 이상), 비밀도 섹션은 제목만
    assert len(src) > 200


def test_glossary_extend_excludes_existing():
    from md4paper.ir import GlossaryEntry, GlossaryList
    from md4paper.llm import FakeProvider
    from md4paper.translate import glossary

    returned = GlossaryList(entries=[
        GlossaryEntry(term="attention", korean="어텐션", policy="transliterate"),
        GlossaryEntry(term="Transformer", korean="트랜스포머", policy="keep"),  # 이미 있음
    ])
    fake = FakeProvider(parse_fn=lambda s, u, sc: returned, model="fake")
    new = glossary.extend(fake, "T", "body", ["Transformer"])
    assert [e.term for e in new.entries] == ["attention"]  # 기존 용어 제외


def test_new_terms_from_selected(ctrl):
    from md4paper.ir import GlossaryEntry, GlossaryList
    from md4paper.llm import FakeProvider

    ctrl.set_all_translate(False)
    assert ctrl.selected_sections_text() == ""  # 선택 없으면 빈 텍스트
    ctrl.translatable_sections()[1].translate = True
    assert ctrl.selected_sections_text()  # 선택 섹션 텍스트 있음

    ret = GlossaryList(entries=[GlossaryEntry(term="new-term", korean="새용어", policy="translate")])
    fake = FakeProvider(parse_fn=lambda s, u, sc: ret, model="fake")
    new = ctrl.new_terms_from_selected(fake, ["existing"])
    assert [e.term for e in new] == ["new-term"]


def test_translate_via_controller(ctrl):
    fake = _fake_gloss()
    ctrl.generate_glossary(fake)  # 용어집 먼저
    summary = ctrl.translate(fake, "해라체")
    assert ctrl.wd.ko_md.exists()
    assert summary["chunks"] >= 1
    assert ctrl.ko_markdown() != ""


def test_export(ctrl, tmp_path):
    dest = tmp_path / "export"
    copied = ctrl.export(str(dest))
    assert (dest / "paper.en.md").exists()
    assert any("paper.en.md" in c for c in copied)


def test_bulk_zip_combines_workdirs(ctrl, tmp_path):
    import io
    import zipfile

    from md4paper import pipeline
    from md4paper.ui.controller import bulk_zip
    from md4paper.workdir import WorkDir

    ctrl.wd.out_images.mkdir(parents=True, exist_ok=True)
    (ctrl.wd.out_images / "figure-1.png").write_bytes(b"\x89PNG\r\n\x1a\n0")
    ctrl.save_en_markdown(ctrl.en_markdown() + "\n![Figure 1](images/figure-1.png)\n")

    wd2 = WorkDir(tmp_path / "p2.md4")
    pipeline.convert(CORPUS / "sample_arxiv.md", wd2)

    name, data = bulk_zip([ctrl.wd.root, wd2.root], "en")
    assert "md4paper-2" in name  # 2편
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = z.namelist()
    tops = {n.split("/")[0] for n in names}
    assert len(tops) == 2  # 두 논문이 각자 폴더
    assert sum(1 for n in names if n.endswith(".en.md")) == 2
    assert any(n.endswith("images/figure-1.png") for n in names)


def test_export_zip_bundles_markdown_and_images(ctrl):
    import io
    import zipfile

    # 이미지가 참조되도록 out/images에 파일 하나 두고 en.md에서 참조
    ctrl.wd.out_images.mkdir(parents=True, exist_ok=True)
    (ctrl.wd.out_images / "fig-01.png").write_bytes(b"\x89PNG\r\n\x1a\n0123")
    ctrl.save_en_markdown(ctrl.en_markdown() + "\n\n![Figure 1](images/fig-01.png)\n")

    name, data = ctrl.export_zip("en")
    assert name.endswith(".zip")
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = z.namelist()
    # 최상위 폴더 아래 마크다운 + images/ 이미지가 함께 들어간다
    assert any(n.endswith(".en.md") for n in names)
    assert any(n.endswith("images/fig-01.png") for n in names)
    # 모두 같은 최상위 폴더 밑
    tops = {n.split("/")[0] for n in names}
    assert len(tops) == 1


def test_translate_headers_setting(ctrl):
    ctrl.set_setting("translate_headers", False)
    assert ctrl.manifest.translate_headers is False


def test_drop_in_level_options():
    from md4paper.ui.controller import LEVEL_OPTIONS

    assert "drop" in LEVEL_OPTIONS


def test_served_markdown_rewrites_all_images():
    from md4paper.ui.app import served_markdown

    md = (
        "![a](images/fig-01.jpeg)\n"
        "![b](_page_6_Diagram_2.jpeg)\n"
        '<img src="_page_10_Figure_4.jpeg" width="300">\n'
        "![c](https://x.com/remote.png)\n"
    )
    out = served_markdown(md, "tok123")
    assert "](/wdimages/tok123/fig-01.jpeg)" in out
    assert "](/wdimages/tok123/_page_6_Diagram_2.jpeg)" in out  # 베어 마크다운 이미지
    assert 'src="/wdimages/tok123/_page_10_Figure_4.jpeg"' in out  # HTML img
    assert "https://x.com/remote.png" in out  # 원격 URL은 그대로


def test_format_reference_one_line():
    from md4paper.ir import RefEntry
    from md4paper.ui.controller import _format_reference

    r = RefEntry(label="7", authors=["Ashish Vaswani", "Noam Shazeer"], year=2017,
                 title="Attention Is All You Need.", venue="NeurIPS", arxiv_id="1706.03762")
    out = _format_reference(r)
    assert "Ashish Vaswani, Noam Shazeer (2017)" in out
    assert "Attention Is All You Need" in out
    assert "NeurIPS" in out
    assert "arxiv" not in out  # URL은 본문에서 빠지고 별도 필드로 간다
    # 구조 필드가 전혀 없으면 원문으로 폴백
    assert _format_reference(RefEntry(label="9", raw="Some raw citation text.")) == "Some raw citation text."


def test_citation_tooltips_structure(ctrl):
    from md4paper.cite.apply import load_cached_refs

    # 파싱된 참고문헌 캐시를 심어 툴팁 사전이 {text, url} 형태로 나오는지 확인
    from md4paper.ir import RefEntry
    import json
    ctrl.wd.cite.mkdir(parents=True, exist_ok=True)
    refs = [RefEntry(label="7", authors=["A B"], year=2017, title="T", venue="V", arxiv_id="1706.03762")]
    ctrl.wd.references_json.write_text(
        json.dumps({"accepted": [r.model_dump() for r in refs]}), encoding="utf-8")
    assert load_cached_refs(ctrl.wd)  # 캐시 확인
    tips = ctrl.citation_tooltips()
    assert tips["7"]["text"].startswith("A B (2017)")
    assert tips["7"]["url"] == "https://arxiv.org/abs/1706.03762"
    assert tips["7"]["title"] == "T"  # 제목 복사용


def test_cite_tips_js_serializes_and_escapes():
    from md4paper.ui.app import cite_tips_js

    js = cite_tips_js({"54": {"text": 'Subramonyam et al. (2025). "Prototyping".', "url": "https://doi.org/x"}})
    assert js.startswith("window.__mdCiteTips = ")
    # 번호·구조 키가 실리고, </script> 조기 종료 방지를 위해 < 는 이스케이프
    assert '"54"' in js and '"text"' in js and '"url"' in js
    assert "<" not in js  # 모든 < 는 \\u003c 로
    assert cite_tips_js({}) == "window.__mdCiteTips = {};"


def test_upload_save_and_convert(tmp_path):
    # 웹 UI 업로드 핸들러가 쓰는 헬퍼 (바이트 → 저장 → 변환)
    from md4paper.ui.app import save_and_convert

    data = (CORPUS / "sample_arxiv.md").read_bytes()
    wd = save_and_convert(data, "sample_arxiv.md", tmp_path / "uploads", "docling", "obsidian")
    assert wd.root.name == "sample_arxiv.md4"
    # 파일별 전용 하위 폴더에 담김 (root에 흩어지지 않음)
    assert wd.root.parent.name == "sample_arxiv"
    assert wd.en_md.exists()
    assert "# Abstract" in wd.en_md.read_text(encoding="utf-8")


def test_level_groups_match_tree_options(ctrl):
    """일괄 조정 그룹 — 같은 모양(체계·깊이)끼리 묶이고 예시가 붙는다."""
    groups = {g["key"]: g for g in ctrl.level_groups()}
    assert "dotted-arabic:1" in groups   # 1 Introduction 등
    assert "dotted-arabic:2" in groups   # 3.1 ...
    g1 = groups["dotted-arabic:1"]
    assert g1["count"] >= 1 and g1["examples"]
    assert g1["current"] is not None     # 전부 같은 레벨이면 현재값 표시


def test_apply_group_level_uses_same_options_as_tree(ctrl):
    """그룹 적용은 트리와 같은 선택지(정수/skip/drop)를 그대로 받는다."""
    n = ctrl.apply_group_level("dotted-arabic:2", 4)
    assert n > 0
    lv = {s.level for s in ctrl.manifest.sections
          if ctrl.group_key(s) == ("dotted-arabic", 2)}
    assert lv == {4}
    # skip/drop 같은 연산도 동일하게 적용
    ctrl.apply_group_level("dotted-arabic:2", "drop")
    lv2 = {s.level for s in ctrl.manifest.sections
           if ctrl.group_key(s) == ("dotted-arabic", 2)}
    assert lv2 == {"drop"}
    # 다른 그룹은 영향 없음
    d1 = {s.level for s in ctrl.manifest.sections if ctrl.group_key(s) == ("dotted-arabic", 1)}
    assert "drop" not in d1


def test_manual_edit_saves_and_flags(ctrl):
    """프리뷰에서 직접 고친 내용이 저장되고 '수동 편집' 표시가 남는다."""
    assert not ctrl.has_manual_edit()
    edited = ctrl.en_markdown() + "\n\n수동으로 덧붙인 문장.\n"
    ctrl.save_en_markdown(edited)
    assert ctrl.has_manual_edit()
    assert "수동으로 덧붙인 문장" in ctrl.en_markdown()
    # 번역은 en.md를 읽으므로 편집 내용이 그대로 반영된다
    assert "수동으로 덧붙인 문장" in ctrl.wd.en_md.read_text(encoding="utf-8")


def test_reassemble_clears_manual_edit_flag(ctrl):
    ctrl.save_en_markdown(ctrl.en_markdown() + "\n임시 편집\n")
    assert ctrl.has_manual_edit()
    ctrl.save_and_reassemble()  # 섹션 트리에서 다시 생성 → 편집 사라짐
    assert not ctrl.has_manual_edit()
    assert "임시 편집" not in ctrl.en_markdown()


def test_rerender_reflects_level_without_yaml_save(ctrl):
    """rerender는 메모리 manifest로 en.md만 갱신 (yaml 저장·학습 없이 — 라이브 프리뷰)."""
    sid = next(s.id for s in ctrl.manifest.sections if s.text == "2 Background")
    yaml_before = ctrl.wd.sections_yaml.read_text(encoding="utf-8")
    prefs_path = __import__("md4paper.prefs", fromlist=["PREFS_PATH"]).PREFS_PATH

    ctrl.set_level(sid, 3)
    ctrl.rerender()
    assert "### 2 Background" in ctrl.en_markdown()          # 프리뷰(en.md)에 즉시 반영
    assert ctrl.wd.sections_yaml.read_text(encoding="utf-8") == yaml_before  # yaml은 안 건드림
    assert not prefs_path.exists()                            # 학습도 안 함


def test_section_map_anchors_align_with_headings(ctrl):
    from md4paper.ui.app import anchored_markdown

    smap = ctrl.section_map()
    out = anchored_markdown(ctrl.en_markdown(), smap)
    # 각 헤더마다 sec-<id> 앵커가 하나씩 붙는다
    import re
    anchors = re.findall(r'id="sec-(h_\d+)"', out)
    headings = [line for line in out.splitlines() if re.match(r"^#{1,6}\s", line)]
    assert len(anchors) == len(headings)
    assert anchors  # 최소 하나



def test_title_excluded_from_translate_tree_and_toggled_separately(ctrl):
    """논문 제목은 섹션 트리(체크박스)가 아니라 별도 토글로 다룬다."""
    title = ctrl.title_section()
    if title is None:  # 코퍼스에 제목 헤더가 없으면 의미 없는 검증
        return
    assert not any(s.is_title for s in ctrl.translatable_sections())
    assert title.id not in ctrl.ticked_ids()

    ctrl.set_translate_title(False)
    assert ctrl.translate_title() is False
    ctrl.set_all_translate(True)  # '전체 선택'이 제목을 건드리면 안 됨
    assert ctrl.translate_title() is False


def test_translate_settings_persist_across_reload(tmp_path):
    """번역 설정·섹션 선택은 sections.yaml에 저장돼 새로고침(컨트롤러 재생성)해도 남는다."""
    from md4paper import pipeline
    from md4paper.ui.controller import UIController
    from md4paper.workdir import WorkDir

    corpus = Path(__file__).parent / "corpus" / "sample_arxiv.md"
    wd = WorkDir(tmp_path / "p.md4")
    pipeline.convert(corpus, wd)

    c = UIController(wd)
    c.set_setting("korean_style", "합니다체")
    c.set_setting("translate_headers", False)
    c.set_translate_ids([])
    c.save()

    reloaded = UIController(wd)
    assert reloaded.manifest.korean_style == "합니다체"
    assert reloaded.manifest.translate_headers is False
    assert reloaded.ticked_ids() == []


def _seed_figure(ctrl) -> None:  # noqa: ANN001 — .md 입력은 실제 이미지 파일이 없어 직접 심는다
    ctrl.wd.out_images.mkdir(parents=True, exist_ok=True)
    (ctrl.wd.out_images / "figure-1.jpeg").write_bytes(b"\x89PNG\r\n\x1a\n0")
    ctrl.save_en_markdown(ctrl.en_markdown() + "\n\n![Figure 1](images/figure-1.jpeg)\n")


def test_notion_export_zip_uses_common_images_folder(ctrl):
    """Notion export도 공통 구조(<stem>-en/ + images/) — content가 정상 import되는 유일한 구조."""
    import io
    import zipfile

    _seed_figure(ctrl)
    _name, data = ctrl.export_zip("en", "notion")
    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    stem = ctrl.wd.root.stem
    assert f"{stem}-en/{stem}.en.md" in names
    assert any(n.startswith(f"{stem}-en/images/") for n in names)


def test_universal_export_zip_keeps_images_folder(ctrl):
    import io
    import zipfile

    _seed_figure(ctrl)
    _name, data = ctrl.export_zip("en", "universal")
    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    stem = ctrl.wd.root.stem
    assert f"{stem}-en/{stem}.en.md" in names  # 종전 구조 유지
    assert any(n.startswith(f"{stem}-en/images/") for n in names)


def test_set_author_parts_shifts_line_anchors(tmp_path):
    """이메일 표기를 끄면 raw.md 줄 수가 줄어드니 라인 앵커도 함께 밀려야 한다.

    렌더러는 raw.md의 절대 줄 번호로 섹션을 찾으므로, 안 밀면 헤더가 엉뚱한 줄에 붙어
    원본 헤더는 남고 매니페스트 헤더가 또 찍혀 문서가 통째로 중복된다(실측).
    """
    import json

    from md4paper import pipeline
    from md4paper.extract.front_matter import _render_authors_detail
    from md4paper.ir import AuthorEntry
    from md4paper.ui.controller import UIController
    from md4paper.workdir import WorkDir

    wd = WorkDir(tmp_path / "p.md4")
    wd.extract.mkdir(parents=True)
    authors = [
        AuthorEntry(name="A One", emails=["a@x.edu"], affiliations=["X Lab"]),
        AuthorEntry(name="B Two", emails=["b@y.edu"], affiliations=["Y Lab"]),
    ]
    block = _render_authors_detail(authors, ["email", "affiliation"])
    wd.raw_md.write_text(
        f"## Paper Title\n\n{block}\n\n## Abstract\n\nAbstract body.\n\n"
        "## 1 Introduction\n\nIntro body.\n",
        encoding="utf-8",
    )
    wd.authors_json.write_text(
        json.dumps([a.model_dump() for a in authors], ensure_ascii=False), encoding="utf-8")
    pipeline.run_structure(wd)

    ctrl = UIController(wd)
    assert ctrl.set_author_parts(["affiliation"]) is True  # 이메일 끄기 → 2줄 줄어듦
    ctrl.save_and_reassemble()

    en = wd.en_md.read_text(encoding="utf-8")
    assert "a@x.edu" not in en                       # 이메일이 실제로 빠졌고
    heads = [ln for ln in en.splitlines() if ln.startswith("#")]
    assert len(heads) == len(set(heads)), f"헤더가 중복됐다: {heads}"
    assert sum(1 for h in heads if h.endswith("Abstract")) == 1
    assert sum(1 for h in heads if h.endswith("1 Introduction")) == 1
