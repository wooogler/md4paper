"""수식 한 개 다시 읽기 — 화면 수식 ↔ 레코드 매핑, 원천 반영, 번역 쪽 처리."""

import json

import pytest

from md4paper import formula_fix, formulas, pipeline
from md4paper.formulas import FormulaRead
from md4paper.llm import FakeProvider
from md4paper.ui.controller import UIController
from md4paper.workdir import WorkDir

R1 = {"id": "formula-01", "file": "formula-01.png", "page": 3, "orig": "a + b",
      "number": "1", "latex": r"a + b", "kind": "display"}
R2 = {"id": "formula-02", "file": "formula-02.png", "page": 3, "orig": "u(x)",
      "number": "", "latex": r"u(x) \sim k", "kind": "inline"}
R3 = {"id": "formula-03", "file": "formula-03.png", "page": 4, "orig": "c = d",
      "number": "", "latex": "", "kind": ""}  # 읽기 실패 — 그림으로 남은 수식
R4 = {"id": "formula-04", "file": "formula-04.png", "page": 5, "orig": "x",
      "number": "", "latex": r"\begin{aligned}x&=1\\" + "\n" + r"y&=2\end{aligned}", "kind": "display"}
R5 = {"id": "formula-05", "file": "formula-05.png", "page": 5, "orig": "print(x)",
      "number": "", "latex": "print(x)", "kind": "code"}

DOC = "\n\n".join([
    "Intro with $y$ inline math.",
    r"$$a + b \tag{1}$$",
    r"$u(x) \sim k$",
    "![formula-03](images/formula-03.png)",
    "$$\\begin{aligned}x&=1\\\\\ny&=2\\end{aligned}$$",
    "```\nprint(x)\n```",
    "End.",
])


def test_block_matches_formula_stage():
    assert formula_fix.block(R1) == r"$$a + b \tag{1}$$"
    assert formula_fix.block(R2) == r"$u(x) \sim k$"
    assert formula_fix.block(R3) == "![formula-03](formula-03.png)"


def test_assign_exact_blocks():
    m = formula_fix.assign(DOC, [R1, R2, R3, R4, R5])
    assert m[r"$$a + b \tag{1}$$"] == "formula-01"
    assert m[r"$u(x) \sim k$"] == "formula-02"
    assert m["![formula-03](images/formula-03.png)"] == "formula-03"  # 경로가 달라도 id로
    assert m[formula_fix.block(R4)] == "formula-04"  # 여러 줄 aligned
    assert "formula-05" not in m.values()  # 코드로 읽힌 것은 수식이 아니다
    assert "$y$" not in m  # 문장 속 인라인 수식은 레코드가 아니다


def test_assign_approximates_by_order_between_anchors():
    # 직접 편집으로 R4 글자가 바뀌었다 — 닻(R1, R3) 사이 남은 블록 1 · 레코드 1 → 순서로 잇는다
    edited = DOC.replace(formula_fix.block(R4), r"$$x = 1, y = 2$$")
    m = formula_fix.assign(edited, [R1, R2, R3, R4])
    assert m[r"$$x = 1, y = 2$$"] == "formula-04"
    # 개수가 안 맞으면 잇지 않는다 (어긋나게 붙이느니 없는 게 낫다)
    two = edited.replace("End.", "$$z = 9$$\n\nEnd.")
    m2 = formula_fix.assign(two, [R1, R2, R3, R4])
    assert "$$x = 1, y = 2$$" not in m2 and "$$z = 9$$" not in m2


def test_tag_prefixes_marker_only_on_screen():
    m = formula_fix.assign(DOC, [R1, R2, R3, R4])
    out = formula_fix.tag(DOC, m)
    assert '<span class="md4-fx" data-fx="formula-01"></span>$$a + b' in out
    assert '<span class="md4-fx" data-fx="formula-03"></span>![formula-03]' in out
    assert out.count('class="md4-fx"') == 4
    # 표식을 지우면 원문 그대로 — 글자를 바꾸지 않는다
    import re
    assert re.sub(r'<span class="md4-fx" data-fx="[^"]+"></span>', "", out) == DOC


def test_tagged_markdown_renders_marker_before_math():
    from nicegui.elements.markdown import prepare_content

    html = prepare_content(formula_fix.tag(DOC, formula_fix.assign(DOC, [R1, R4])),
                           "fenced-code-blocks tables latex")
    assert '<span class="md4-fx" data-fx="formula-01"></span><math' in html
    assert '<span class="md4-fx" data-fx="formula-04"></span><math' in html


def test_replace_once_refuses_ambiguity():
    md = "a\n$$x$$\nb\n$$x$$\n"
    assert formula_fix.replace_once(md, "$$x$$", "$$y$$") is None  # 둘 — 추측하지 않는다
    assert formula_fix.replace_once("a\n$$x$$\n", "$$x$$", "$$y$$") == "a\n$$y$$\n"
    assert formula_fix.replace_once("a $$x$$ b", "$$x$$", "$$y$$") is None  # 줄 단위만


def test_rekey_cache_moves_translation():
    from md4paper.workdir import hash_text

    src = "Intro.\n\n$$a$$\n"
    cache = {hash_text(src): "소개.\n\n$$a$$\n"}
    assert formula_fix.rekey_cache(cache, [src, "other"], "$$a$$", "$$b$$") == 1
    assert cache[hash_text("Intro.\n\n$$b$$\n")] == "소개.\n\n$$b$$\n"


def test_collect_records_bbox(monkeypatch, tmp_path):
    from types import SimpleNamespace as NS

    from md4paper import pdfio
    from md4paper.extract import formulas as ex

    box = NS(l=100.0, r=200.0, t=700.0, b=680.0, coord_origin="BOTTOMLEFT")
    item = NS(label="formula", text="", orig="a + b (1)", prov=[NS(page_no=2, bbox=box)])
    doc = NS(iterate_items=lambda: [(item, 0)], pages={2: NS(size=NS(width=612.0, height=792.0))})
    monkeypatch.setattr(pdfio, "render_region_png", lambda src, page, rect: b"PNG")
    recs = ex.collect(doc, "x.pdf", tmp_path)
    assert recs[0]["page"] == 2 and recs[0]["number"] == "1"
    assert len(recs[0]["bbox"]) == 4 and recs[0]["bbox"][0] == 96.0  # 여백 4pt


# --- 컨트롤러 끝까지: 원천 반영 → 재조립 → 번역 ---

PAPER = """# A Paper

## 1 Intro

We define the score.

$$a + b \\tag{1}$$

Then the rest.

## 2 Method

![formula-03](formula-03.png)

Done.
"""


@pytest.fixture
def ctrl(tmp_path):
    src = tmp_path / "paper.md"
    src.write_text(PAPER, encoding="utf-8")
    wd = WorkDir(tmp_path / "paper.md4")
    pipeline.convert(src, wd)
    wd.extract_images.mkdir(parents=True, exist_ok=True)
    for r in (R1, R3):
        (wd.extract_images / r["file"]).write_bytes(b"\x89PNG fake")
    wd.formulas_json.write_text(json.dumps([R1, R3]), encoding="utf-8")
    return UIController(wd)


def _fake(latex, kind="display"):
    seen = []

    def parse(system, user, schema):
        seen.append(user)
        return FormulaRead(kind=kind, latex=latex)
    return FakeProvider(parse_fn=parse, model="fake"), seen


def test_regenerate_returns_candidate_without_touching_doc(ctrl):
    before = ctrl.wd.raw_md.read_text()
    prov, seen = _fake(r"a - b")
    got = ctrl.regenerate_formula("formula-01", prov)
    assert got == {"latex": "a - b", "kind": "display", "block": r"$$a - b \tag{1}$$", "error": ""}
    assert "judged it WRONG" in seen[0] and "a + b" in seen[0]  # 틀린 판본을 알려 준다
    assert ctrl.wd.raw_md.read_text() == before  # 후보일 뿐 — 적용 전엔 문서 그대로


def test_regenerate_rejects_unrenderable(ctrl):
    prov, _ = _fake(r"\frac{a")
    assert ctrl.regenerate_formula("formula-01", prov)["error"]


def test_apply_updates_source_and_reassembles(ctrl):
    assert "formula-01" in ctrl.formula_mapping().values()
    res = ctrl.apply_formula("formula-01", r"a - b", "display")
    assert res["block"] == r"$$a - b \tag{1}$$"
    assert r"$$a - b \tag{1}$$" in ctrl.wd.raw_md.read_text()  # 원천 (재조립에도 남는다)
    assert r"$$a - b \tag{1}$$" in ctrl.en_markdown()
    rec = next(r for r in formulas.load(ctrl.wd) if r["id"] == "formula-01")
    assert rec["latex"] == "a - b" and rec["edited"]
    pipeline.run_assemble(ctrl.wd, force=True)  # 다시 조립해도 사라지지 않는다
    assert r"$$a - b \tag{1}$$" in ctrl.en_markdown()
    assert ctrl.formula_mapping()[r"$$a - b \tag{1}$$"] == "formula-01"  # 새 글자로도 이어진다


def test_apply_image_fallback(ctrl):
    ctrl.apply_formula("formula-03", r"c = d", "display")
    assert "$$c = d$$" in ctrl.wd.raw_md.read_text()
    assert "$$c = d$$" in ctrl.en_markdown() and "![formula-03]" not in ctrl.en_markdown()


def test_apply_refuses_bad_latex(ctrl):
    with pytest.raises(ValueError):
        ctrl.apply_formula("formula-01", r"\frac{a", "display")
    assert r"$$a + b \tag{1}$$" in ctrl.wd.raw_md.read_text()


def test_apply_keeps_manual_edit(ctrl):
    ctrl.save_en_markdown(ctrl.en_markdown().replace("Then the rest.", "Then the EDITED rest."))
    ctrl.apply_formula("formula-01", r"a - b", "display")
    en = ctrl.en_markdown()
    assert "EDITED" in en and r"$$a - b \tag{1}$$" in en  # 재조립하지 않고 그 자리만
    assert ctrl.has_manual_edit()


def _translate(ctrl):
    from md4paper.ir import GlossaryList

    fake = FakeProvider(complete_fn=lambda s, u: u, parse_fn=lambda s, u, sc: GlossaryList(entries=[]),
                        model="fake-tr")
    ctrl.generate_glossary(fake)
    ctrl.translate(fake, "해라체")
    assert r"$$a + b \tag{1}$$" in ctrl.ko_markdown()
    assert ctrl.stale_sections() == set()


def test_apply_patches_translation_and_keeps_it_fresh(ctrl):
    _translate(ctrl)
    res = ctrl.apply_formula("formula-01", r"a - b", "display")
    assert res["ko"] and res["cache_moved"] >= 1
    assert r"$$a - b \tag{1}$$" in ctrl.ko_markdown()
    assert ctrl.stale_sections() == set()  # 같은 자리만 바꿨으니 다시 번역할 필요가 없다


def test_apply_marks_stale_when_translation_lacks_block(ctrl):
    _translate(ctrl)
    ctrl.save_ko_markdown(ctrl.ko_markdown().replace(r"$$a + b \tag{1}$$", "(수식)"))
    res = ctrl.apply_formula("formula-01", r"a - b", "display")
    assert not res["ko"]
    assert ctrl.stale_sections()  # 번역에 못 옮긴 섹션은 '원문 바뀜'으로 잡힌다
