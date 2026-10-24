"""놓친 괘선 표 되살리기 — PDF 괘선 격자로 행·열 경계와 병합 셀을 찾는다 (LitPivot Table 1)."""

from narani.extract import ruled_tables as R
from narani.extract.ruled_tables import _Seg


def _box_grid(xs, ys, skip_v=()):
    """xs 세로선 · ys 가로선(아래가 원점, 위→아래)으로 칸마다 그은 괘선. skip_v: 긋지 않을 (행, 세로선 번호)."""
    segs = [_Seg(True, y, xs[0], xs[-1]) for y in ys]
    for r in range(len(ys) - 1):
        for k, x in enumerate(xs):
            if (r, k) not in skip_v:
                segs.append(_Seg(False, x, ys[r + 1], ys[r]))
    return segs


XS, YS = [53.0, 162.0, 362.0, 562.0], [675.0, 666.0, 426.0, 290.0]


def test_grid_lines_give_rows_columns_and_cells():
    grids = R._grids(_box_grid(XS, YS))
    assert len(grids) == 1
    xs, ys, cells = R._layout(grids[0])
    assert (len(xs), len(ys)) == (4, 4)
    assert cells == [(r, c, 1, 1) for r in range(3) for c in range(3)]


def test_missing_vertical_line_merges_cells_across():
    segs = _box_grid(XS, YS, skip_v={(0, 1)})  # 머리 행의 첫 안쪽 세로선이 없다 — 첫 두 칸이 한 칸
    _, _, cells = R._layout(R._grids(segs)[0])
    assert (0, 0, 1, 2) in cells
    assert (0, 2, 1, 1) in cells
    assert len(cells) == 8


def test_plain_frame_is_not_a_table():
    frame = _box_grid([53.0, 562.0], [675.0, 290.0])  # 상자 하나 (프롬프트 테두리 등)
    assert R._layout(R._grids(frame)[0]) is None


def test_hyphen_dropped_only_when_the_closed_word_is_attested():
    words = {"interpretable"}
    assert R._join_lines(["more inter-", "pretable and"], words) == "more interpretable and"
    assert R._join_lines(["on surface-", "level similarity"], words) == "on surface-level similarity"


def _doc():
    """LitPivot 18쪽 모양 — 캡션, 열 머리 두 개(헤더로 오분류), 셀 문단들이 따로 나온 문서."""
    from docling_core.types.doc import (
        BoundingBox,
        CoordOrigin,
        DoclingDocument,
        ProvenanceItem,
        Size,
    )

    d = DoclingDocument(name="t")
    d.add_page(page_no=1, size=Size(width=612, height=792))

    def prov(l, t, r, b):  # noqa: E741
        return ProvenanceItem(page_no=1, charspan=(0, 0),
                              bbox=BoundingBox(l=l, t=t, r=r, b=b, coord_origin=CoordOrigin.BOTTOMLEFT))

    d.add_text(label="text", text="Table 1: Side-by-side comparison.", prov=prov(54, 705, 560, 690))
    d.add_heading(text="Baseline", prov=prov(167, 671, 193, 668))
    d.add_heading(text="LitPivot", prov=prov(366, 671, 392, 668))
    for (l, r), t in zip([(58, 160), (166, 359), (366, 559)] * 2, ["a1", "b1", "c1", "a2", "b2", "c2"]):
        top = 664 if t.endswith("1") else 424
        d.add_text(label="text", text=f"cell {t}", prov=prov(l, top, r, top - 40))
    return d


def test_caption_next_to_grid_is_found_and_body_text_is_not():
    d = _doc()
    items = R._page_items(d, 1)
    cap = R._caption_for(items, 53, 562, 675, 290, set())
    assert cap is not None and cap.text.startswith("Table 1")
    assert R._TABLE_CAP_RE.match("Table 1 shows the comparison") is None  # 본문 문장은 캡션이 아니다


def test_block_spilling_out_of_the_grid_blocks_the_rebuild():
    d = _doc()
    d.add_text(label="text", text="A paragraph that crosses the bottom rule.",
               prov=d.texts[-1].prov[0].model_copy(update={"bbox": d.texts[-1].prov[0].bbox.model_copy(
                   update={"t": 300, "b": 250})}))
    grid = R._grids(_box_grid(XS, YS))[0]
    assert R._build(d, 1, R._page_items(d, 1), grid, [], set()) is False


def _glyphs(cells):
    """[(x, 위에서 잰 기준선, 글자)] → Glyph (공백은 텍스트 스트림 자리만 건너뛴다)."""
    from narani.extract.inline_math import Glyph

    out, idx = [], 0
    for x, base, text in cells:
        for ch in text:
            idx += 1
            if ch != " ":
                out.append(Glyph(idx, ch, "Body", 9.0, x, x + 4.5, 792 - base))
            x += 4.5
        idx += 1
    return out


def test_ruled_grid_becomes_a_table_in_place_of_the_cell_paragraphs():
    d = _doc()
    glyphs = _glyphs([(167, 123, "Baseline"), (366, 123, "LitPivot")]
                     + [(x, base, f"cell {t}") for (x, t), base in
                        zip([(58, "a1"), (166, "b1"), (366, "c1"), (58, "a2"), (166, "b2"), (366, "c2")],
                            [140] * 3 + [380] * 3)])
    grid = R._grids(_box_grid(XS, YS))[0]
    assert R._build(d, 1, R._page_items(d, 1), grid, glyphs, set()) is True
    [t] = d.tables
    rows = {}
    for c in t.data.table_cells:
        rows.setdefault(c.start_row_offset_idx, []).append(c.text)
    assert rows == {0: ["", "Baseline", "LitPivot"], 1: ["cell a1", "cell b1", "cell c1"],
                    2: ["cell a2", "cell b2", "cell c2"]}
    assert [c.column_header for c in t.data.table_cells][:3] == [True] * 3
    order = [str(it.label).split(".")[-1].lower() for it, _ in d.iterate_items()]
    assert order == ["caption", "table"]  # 셀 문단·열 머리 헤더는 표로 들어가 사라졌다
    md = d.export_to_markdown()
    assert md.count("Table 1: Side-by-side comparison.") == 1
    assert "| cell a1 " in md


def test_cell_text_that_does_not_match_the_blocks_blocks_the_rebuild():
    d = _doc()
    glyphs = _glyphs([(167, 123, "Baseline"), (366, 123, "LitPivot"), (58, 140, "something else")])
    grid = R._grids(_box_grid(XS, YS))[0]
    assert R._build(d, 1, R._page_items(d, 1), grid, glyphs, set()) is False
    assert not d.tables
