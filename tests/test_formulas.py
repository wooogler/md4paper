"""수식 — 크롭 → LaTeX 파이프라인.

가장 중요한 회귀는 **조용한 손상**이다: 구조화 출력의 JSON을 지나며 `\text{x}`가
TAB+"ext{x}"가 되어도 latex2mathml은 그걸 글자로 읽어 렌더에 성공한다. 검증을 통과한 채
본문에 들어가므로, 이 케이스만 눈으로 못 잡는다. 그래서 여기서 고정한다.
"""

import json

import pytest
from pydantic import BaseModel

from md4paper import formulas, pdfio
from md4paper.extract import formulas as regions
from md4paper.llm.base import FakeProvider
from md4paper.workdir import WorkDir


# --- 이스케이프 손상 복구 ---------------------------------------------------


def test_json_eaten_backslash_is_restored():
    r"""`\text` → TAB+"ext"로 먹힌 명령어를 되살린다 (앞머리 strip에 지워지기 전에)."""
    damaged = "\text{Prompt} + \text{Question}"  # noqa: W605 — 손상 자체를 재현
    assert formulas._clean(damaged) == r"\text{Prompt} + \text{Question}"


def test_all_four_damaged_escapes_are_restored():
    r"""LaTeX에 흔한 \t \f \b \r 명령어가 모두 복구된다."""
    assert formulas._clean("\theta \frac{1}{2} \beta \rangle") == r"\theta \frac{1}{2} \beta \rangle"


def test_control_char_not_followed_by_letter_is_left_alone():
    """뒤에 글자가 없으면 삼켜진 명령어가 아니다 — 건드리지 않는다."""
    assert formulas._clean("a\t 1") == "a\t 1"


def test_renderable_rejects_leftover_control_chars():
    """복구가 못 살린 제어문자는 반려한다 — latex2mathml은 이걸 통과시킨다."""
    assert not formulas.renderable("\x0bext{x}")
    assert formulas.renderable(r"\text{x}")


def test_renderable_rejects_unbalanced_and_unparsable():
    assert not formulas.renderable(r"\frac{1}{2")
    assert not formulas.renderable("")


# --- 이중 이스케이프 (반대 방향 사고) ---------------------------------------


def test_fully_doubled_escapes_are_collapsed():
    r"""홑백슬래시 명령어가 하나도 없으면 문자열 전체가 두 번 이스케이프된 것이다."""
    got = formulas._clean(r"R(N,T)=\\sum_{u=1}^{N}\\left[f_u(x)\\right]")
    assert got == r"R(N,T)=\sum_{u=1}^{N}\left[f_u(x)\right]"


def test_aligned_row_separator_is_not_collapsed():
    r"""`\\`는 aligned의 행 구분자다 — 뒤에 글자가 바로 와도 건드리면 안 된다."""
    ok = r"\begin{aligned}x&=1\\y&=2\end{aligned}"
    assert formulas._clean(ok) == ok


def test_doubled_command_is_fixed_even_when_mixed():
    r"""`\\sum`은 '줄바꿈 뒤 sum'일 리가 없다 — 멀쩡한 명령어와 섞여 있어도 고친다."""
    assert formulas._clean(r"\frac{1}{2} + \\sum_{i}") == r"\frac{1}{2} + \sum_{i}"


def test_renderable_rejects_leftover_doubled_command():
    r"""latex2mathml은 `\\frac`도 글자로 읽어 통과시킨다 — 검증에서 막아야 한다."""
    assert not formulas.renderable(r"a \\frac{1}{2}")


# --- 껍데기 벗기기 ----------------------------------------------------------


@pytest.mark.parametrize("raw", [
    "```latex\n\\alpha\n```", "$$\\alpha$$", "$\\alpha$", "\\[\\alpha\\]", "  \\alpha  ",
    "\\alpha \\tag{3}",
])
def test_clean_strips_wrappers_and_tags(raw):
    """구분자·펜스·\\tag는 우리가 붙인다 — 모델이 붙여 오면 벗긴다."""
    assert formulas._clean(raw) == "\\alpha"


# --- 블록 렌더 --------------------------------------------------------------


def test_block_uses_tag_for_numbered_display():
    rec = {"id": "formula-01", "file": "formula-01.png", "latex": r"a=b",
           "kind": "display", "number": "2"}
    assert formulas._block(rec) == "$$a=b \\tag{2}$$"


def test_block_falls_back_to_image_when_latex_missing():
    """변환 실패는 그림으로 남는다 — 깨진 수식보다 원문 그림이 낫다."""
    rec = {"id": "formula-01", "file": "formula-01.png", "latex": "", "kind": "display"}
    assert formulas._block(rec) == "![formula-01](formula-01.png)"


def test_block_does_not_mathify_code_or_text():
    """오검출(코드 한 줄)을 수식으로 만들지 않는다."""
    code = {"id": "f", "file": "f.png", "latex": "return x + y", "kind": "code"}
    assert formulas._block(code) == "```\nreturn x + y\n```"
    text = {"id": "f", "file": "f.png", "latex": "see below", "kind": "text"}
    assert formulas._block(text) == "see below"


def test_apply_replaces_only_formula_images():
    md = "intro\n\n![formula-01](formula-01.png)\n\n![img-03](img-03.png)\n"
    out, n = formulas.apply(md, [{"id": "formula-01", "file": "formula-01.png",
                                  "latex": "a=b", "kind": "display", "number": ""}])
    assert n == 1
    assert "$$a=b$$" in out
    assert "![img-03](img-03.png)" in out  # 본문 그림은 그대로


# --- 자리 맞추기 (표식 ↔ 레코드) --------------------------------------------


def test_place_maps_slots_in_order_and_drops_marked():
    md = f"a\n\n{regions.PLACEHOLDER}\n\nb\n\n{regions.PLACEHOLDER}\n\nc\n"
    out = regions.place(md, [
        {"slot": 0, "id": "formula-01", "file": "formula-01.png"},
        {"slot": 1, "drop": True},
    ])
    assert "![formula-01](formula-01.png)" in out
    assert regions.PLACEHOLDER not in out


def test_place_leaves_unmatched_placeholders_alone():
    """레코드보다 표식이 많으면 남는 건 그대로 둔다 — 어긋난 수식을 붙이는 것보다 낫다."""
    md = f"{regions.PLACEHOLDER}\n\n{regions.PLACEHOLDER}\n"
    out = regions.place(md, [{"slot": 0, "id": "formula-01", "file": "formula-01.png"}])
    assert out.count(regions.PLACEHOLDER) == 1


# --- 크롭 렌더 --------------------------------------------------------------


def test_render_region_crops_inside_page(tmp_path):
    pdfium = pytest.importorskip("pypdfium2")
    doc = pdfium.PdfDocument.new()
    doc.new_page(200, 260)
    path = tmp_path / "s.pdf"
    doc.save(str(path))
    doc.close()
    png = pdfio.render_region_png(path, 0, (10, 20, 110, 60), zoom=1.0)
    assert png is not None and png[:8] == b"\x89PNG\r\n\x1a\n"
    assert pdfio.render_region_png(path, 0, (10, 20, 10, 20)) is None  # 빈 영역
    assert pdfio.render_region_png(path, 9, (10, 20, 110, 60)) is None  # 범위 밖


# --- 단계 통합 --------------------------------------------------------------


def _wd_with_formula(tmp_path) -> WorkDir:
    wd = WorkDir(tmp_path / "p.md4")
    wd.extract.mkdir(parents=True)
    wd.extract_images.mkdir(parents=True)
    (wd.extract_images / "formula-01.png").write_bytes(b"\x89PNG\r\n\x1a\n fake")
    wd.raw_md.write_text("intro\n\n![formula-01](formula-01.png)\n\nrest\n", encoding="utf-8")
    wd.formulas_json.write_text(json.dumps([{
        "id": "formula-01", "slot": 0, "file": "formula-01.png", "page": 2,
        "orig": "a = b", "number": "1", "latex": "",
    }]), encoding="utf-8")
    return wd


def _fake(kind: str, latex: str) -> FakeProvider:
    def parse(system: str, user: str, schema: type[BaseModel]) -> BaseModel:
        return schema(kind=kind, latex=latex)
    return FakeProvider(parse_fn=parse)


def test_run_converts_and_caches(tmp_path):
    wd = _wd_with_formula(tmp_path)
    summary = formulas.run(wd, _fake("display", r"\alpha = \beta"))
    assert summary["converted"] == 1 and summary["failed"] == 0
    assert r"$$\alpha = \beta \tag{1}$$" in wd.raw_md.read_text(encoding="utf-8")

    # 두 번째 실행은 캐시에 걸려 모델을 부르지 않는다 (부르면 parse_fn이 없어 터진다)
    again = formulas.run(wd, FakeProvider())
    assert again["asked"] == 0


def test_run_keeps_image_when_latex_is_unrenderable(tmp_path):
    """반려된 수식은 그림으로 남고, 반려 사유와 원문이 기록에 남는다."""
    wd = _wd_with_formula(tmp_path)
    summary = formulas.run(wd, _fake("display", r"\frac{1}{2"))
    assert summary["converted"] == 0 and summary["failed"] == 1
    assert "![formula-01](formula-01.png)" in wd.raw_md.read_text(encoding="utf-8")
    assert json.loads(wd.formulas_json.read_text())[0]["rejected"] == r"\frac{1}{2"


def test_run_without_formulas_is_a_noop(tmp_path):
    wd = WorkDir(tmp_path / "p.md4")
    wd.extract.mkdir(parents=True)
    wd.raw_md.write_text("no math here\n", encoding="utf-8")
    assert formulas.run(wd, FakeProvider())["skipped"] is True


def test_pipeline_skips_without_provider(tmp_path):
    from md4paper import pipeline

    wd = _wd_with_formula(tmp_path)
    assert pipeline.run_formulas(wd, provider=None)["skipped"] is True
    assert "![formula-01](formula-01.png)" in wd.raw_md.read_text(encoding="utf-8")


# --- 영역 수집 (식 번호 · 오검출) -------------------------------------------
# DoclingDocument 대신 collect가 실제로 읽는 속성만 가진 스텁을 쓴다.

class _Box:
    coord_origin = "BOTTOMLEFT"

    def __init__(self, left, right, top, bottom):
        self.l, self.r, self.t, self.b = left, right, top, bottom


class _Prov:
    def __init__(self, page, bbox):
        self.page_no, self.bbox = page, bbox


class _Size:
    width, height = 612.0, 792.0


class _Page:
    size = _Size()


class _Formula:
    label = "formula"
    text = ""

    def __init__(self, orig, top=700.0):
        self.orig = orig
        self.prov = [_Prov(1, _Box(72.0, 540.0, top, top - 20.0))]


class _Doc:
    pages = {1: _Page()}

    def __init__(self, items):
        self._items = list(items)

    def iterate_items(self):
        return ((it, 0) for it in self._items)


def _collect(tmp_path, origs, monkeypatch):
    """PDF 없이 collect를 돌린다 — 크롭은 렌더러를 세워 두고 판정 로직만 본다."""
    monkeypatch.setattr(pdfio, "render_region_png", lambda *a, **k: b"png")
    return regions.collect(_Doc([_Formula(o) for o in origs]), tmp_path / "x.pdf",
                           tmp_path / "images")


def test_detached_equation_number_joins_previous_formula(tmp_path, monkeypatch):
    """Docling은 식 번호를 별도 수식 아이템으로 뱉는다 — 그대로 두면 `$$( 1 )$$`가 된다."""
    recs = _collect(tmp_path, ["a = b .", "(1)"], monkeypatch)
    assert [r.get("drop") for r in recs] == [None, True]
    assert recs[0]["number"] == "1"


def test_inline_equation_number_is_split_off(tmp_path, monkeypatch):
    """번호가 수식 원문 끝에 붙어 온 경우도 본문(orig)에서 떼어낸다."""
    recs = _collect(tmp_path, ["a = b . (2)"], monkeypatch)
    assert recs[0]["orig"] == "a = b ." and recs[0]["number"] == "2"


def test_punctuation_only_region_is_dropped_even_with_a_number(tmp_path, monkeypatch):
    """마침표 하나만 잡힌 오검출. **번호를 떼기 전에는** 구두점만인 걸 알 수 없다."""
    recs = _collect(tmp_path, ["a = b", ". (7)"], monkeypatch)
    assert [r.get("drop") for r in recs] == [None, True]
    assert recs[0]["number"] == "7"  # 번호는 직전 수식의 것 — 같이 버리지 않는다


def test_items_with_latex_already_filled_are_not_counted(tmp_path, monkeypatch):
    """docling 수식 인식을 켰다면 표식 자체가 안 나온다 — 세면 자리가 어긋난다."""
    doc = _Doc([_Formula("a = b")])
    doc._items[0].text = r"\alpha"
    monkeypatch.setattr(pdfio, "render_region_png", lambda *a, **k: b"png")
    assert regions.collect(doc, tmp_path / "x.pdf", tmp_path / "images") == []
