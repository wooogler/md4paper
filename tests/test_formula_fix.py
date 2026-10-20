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

    doc = "# T\n\nIntro $x$ and $x$.\n\n$$a$$\n"
    src = doc[5:]
    cache = {hash_text(src): "소개 $x$와 $x$.\n\n$$a$$\n"}
    at = doc.index("$$a$$")
    assert formula_fix.rekey_cache(cache, [src, "other"], doc, at, "$$a$$", "$$b$$") == 1
    assert cache[hash_text(src.replace("$$a$$", "$$b$$"))] == "소개 $x$와 $x$.\n\n$$b$$\n"
    at2 = doc.index("$x$", doc.index("$x$") + 1)  # 두 번째 $x$만
    assert formula_fix.rekey_cache(cache, [src], doc, at2, "$x$", "$y$") == 1
    assert cache[hash_text("Intro $x$ and $y$.\n\n$$a$$\n")] == "소개 $x$와 $y$.\n\n$$a$$\n"


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
    assert got == {"latex": "a - b", "kind": "display", "block": r"$$a - b \tag{1}$$", "error": "",
                   "crop": None}
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


def test_apply_multiline_to_single_line_keeps_headings(tmp_path):
    # 여러 줄 aligned → 한 줄: raw.md 줄 수가 줄어도 뒤 섹션 헤더가 중복되면 안 된다 (실측 회귀)
    src = tmp_path / "paper.md"
    src.write_text(PAPER.replace(r"$$a + b \tag{1}$$", formula_fix.block(R4)), encoding="utf-8")
    wd = WorkDir(tmp_path / "paper.md4")
    pipeline.convert(src, wd)
    wd.extract_images.mkdir(parents=True, exist_ok=True)
    wd.formulas_json.write_text(json.dumps([R4]), encoding="utf-8")
    c = UIController(wd)
    heads = [ln for ln in c.en_markdown().splitlines() if ln.startswith("#")]
    c.apply_formula("formula-04", "x = 1, y = 2", "display")
    assert "$$x = 1, y = 2$$" in c.en_markdown()
    assert [ln for ln in c.en_markdown().splitlines() if ln.startswith("#")] == heads
    assert "Then the rest." in c.en_markdown() and c.en_markdown().count("Done.") == 1


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


def _translate(ctrl, check=r"$$a + b \tag{1}$$"):
    from md4paper.ir import GlossaryList

    fake = FakeProvider(complete_fn=lambda s, u: u, parse_fn=lambda s, u, sc: GlossaryList(entries=[]),
                        model="fake-tr")
    ctrl.generate_glossary(fake)
    ctrl.translate(fake, "해라체")
    assert check in ctrl.ko_markdown()
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


# --- 인라인 수식 ---

def test_inline_spans_follow_markdown2_and_skip_code():
    md = "Let $x_i$ be `$not$` and\n\n```\n$no$\n```\n\n$$y$$ then $z$."
    assert [t for _s, _e, t in formula_fix.inline_spans(md)] == ["$x_i$", "$z$"]


def test_tag_inline_shares_ids_between_en_and_ko():
    en = "We set $a$ and $b$, then $a$ again.\n\n$$c$$"
    ko = "$a$와 $b$를 두고, 다시 $a$.\n\n$$c$$"
    idx = formula_fix.inline_index(en)
    out_en = formula_fix.tag(en, {}, idx)
    out_ko = formula_fix.tag(ko, {}, idx)
    mark = '<span class="md4-fx" data-fx="inline-{}"></span>'
    assert mark.format(2) + "$a$ again" in out_en  # 두 번째 $a$ = 인라인 2번
    assert out_ko.startswith(mark.format(0) + "$a$와 ")
    assert mark.format(2) + "$a$." in out_ko  # 번역에서도 같은 글자의 같은 순번
    assert "$$c$$" in out_en and out_en.count("md4-fx") == 3  # 블록은 인라인으로 세지 않는다


def test_record_inline_line_is_not_tagged_twice():
    md = r"Intro." + "\n\n" + r"$u(x) \sim k$" + "\n"
    out = formula_fix.tag(md, formula_fix.assign(md, [R2]), formula_fix.inline_index(md))
    assert out.count("md4-fx") == 1 and 'data-fx="formula-02"' in out


def test_guess_page_from_surrounding_words(monkeypatch, tmp_path):
    from md4paper import pdfio

    wd = WorkDir(tmp_path / "p.md4")
    monkeypatch.setattr(formula_fix, "source_pdf", lambda w: "x.pdf")
    monkeypatch.setattr(pdfio, "page_texts", lambda src: [
        "Intro text here.", "the pairwise com-\nparison of outcomes where 𝑥 ≻ 𝑦 holds", "end"])
    before = "Some para.\n\nWe model the pairwise comparison of outcomes where "
    assert formula_fix.guess_page(wd, before, " holds for all.") == 2
    assert formula_fix.guess_page(wd, "nothing matches at all here", "") is None


def test_guess_bbox_finds_old_crop_on_page(monkeypatch, tmp_path):
    cv2 = pytest.importorskip("cv2")
    import numpy as np

    from md4paper import pdfio

    page = np.full((300, 240), 255, np.uint8)  # 배율 1.5의 쪽 (160×200pt)
    cv2.putText(page, "E=mc2", (60, 150), cv2.FONT_HERSHEY_SIMPLEX, 1.0, 0, 2)
    cv2.rectangle(page, (50, 120), (190, 160), 0, 1)
    big = cv2.resize(page, (480, 600), interpolation=cv2.INTER_CUBIC)  # 배율 3 = 크롭 배율
    tpl = big[230:330, 90:390]  # 쪽 (30, 76.7)pt~(130, 110)pt
    wd = WorkDir(tmp_path / "p.md4")
    wd.extract_images.mkdir(parents=True)
    (wd.extract_images / "formula-01.png").write_bytes(cv2.imencode(".png", tpl)[1].tobytes())
    monkeypatch.setattr(formula_fix, "source_pdf", lambda w: "x.pdf")
    monkeypatch.setattr(pdfio, "render_page_png",
                        lambda src, p, zoom: cv2.imencode(".png", page)[1].tobytes())
    box = formula_fix.guess_bbox(wd, {"file": "formula-01.png", "page": 1})
    assert box is not None and abs(box[0] - 30) <= 1.5 and abs(box[1] - 76.7) <= 1.5
    assert formula_fix.guess_bbox(wd, {"bbox": [1, 2, 3, 4]}) == [1, 2, 3, 4]  # 저장된 게 있으면 그것


INLINE_PAPER = """# A Paper

## 1 Intro

We define $x_i$ as the input and $y$ as output, and again $x_i$ here.

## 2 Method

$$a + b \\tag{1}$$

Done.
"""


@pytest.fixture
def ictrl(tmp_path, monkeypatch):
    src = tmp_path / "paper.md"
    src.write_text(INLINE_PAPER, encoding="utf-8")
    wd = WorkDir(tmp_path / "paper.md4")
    pipeline.convert(src, wd)
    wd.extract_images.mkdir(parents=True, exist_ok=True)
    (wd.extract_images / R1["file"]).write_bytes(b"\x89PNG old")
    wd.formulas_json.write_text(json.dumps([R1]), encoding="utf-8")
    monkeypatch.setattr(formula_fix, "render_crop", lambda w, page, rect: b"\x89PNG new crop")
    monkeypatch.setattr(formula_fix, "guess_page", lambda w, b, a: 2)
    return UIController(wd)


def _inline_id(ctrl, text, nth=0):
    spans = formula_fix.inline_spans(ctrl.en_markdown())
    hits = [i for i, (_s, _e, t) in enumerate(spans) if t == text]
    return f"inline-{hits[nth]}"


def test_inline_subject_and_regenerate_needs_region(ictrl):
    fid = _inline_id(ictrl, "$y$")
    sub = formula_fix.subject(ictrl.wd, fid)
    assert sub["inline"] and sub["latex"] == "y" and sub["page"] == 2
    prov, _ = _fake("y_0", kind="display")
    assert "영역" in ictrl.regenerate_formula(fid, prov)["error"]  # 크롭이 없으니 영역부터
    got = ictrl.regenerate_formula(fid, prov, page=2, rect=[10, 20, 60, 40])
    assert got["kind"] == "inline" and got["block"] == "$y_0$"  # 문장 속 자리는 인라인으로
    assert got["crop"] == b"\x89PNG new crop"


def test_apply_inline_replaces_only_that_occurrence(ictrl):
    _translate(ictrl, check=r"$x_i$")
    fid = _inline_id(ictrl, "$x_i$", nth=1)  # 두 번째 $x_i$만
    res = ictrl.apply_formula(fid, r"x_{i}'", "inline", expect="x_i")
    assert res["ko"]
    for md in (ictrl.wd.raw_md.read_text(), ictrl.en_markdown(), ictrl.ko_markdown()):
        assert "$x_i$ as the input" in md and "again $x_{i}'$ here" in md
    assert ictrl.stale_sections() == set()


def test_apply_inline_refuses_when_doc_changed(ictrl):
    fid = _inline_id(ictrl, "$y$")
    with pytest.raises(ValueError):
        ictrl.apply_formula(fid, "z", "inline", expect="not-y")


def test_apply_with_region_saves_new_crop(ictrl):
    ictrl.apply_formula("formula-01", r"a - b", "display", page=3, rect=[50, 80, 20, 100])
    assert (ictrl.wd.extract_images / "formula-01.png").read_bytes() == b"\x89PNG new crop"
    assert (ictrl.wd.out_images / "formula-01.png").read_bytes() == b"\x89PNG new crop"
    rec = formulas.load(ictrl.wd)[0]
    assert rec["page"] == 3 and rec["bbox"] == [20.0, 80.0, 50.0, 100.0] and rec["manual_crop"]
