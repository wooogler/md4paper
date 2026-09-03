"""읽기 순서 복구 — 합성 문서만으로 (PDF·컨버터 없이) 기하 판정을 검증."""

from __future__ import annotations

import pytest

from md4paper.extract.reading_order import repair_reading_order

pytest.importorskip("docling_core")

from docling_core.types.doc import (  # noqa: E402
    BoundingBox,
    CoordOrigin,
    DocItemLabel,
    DoclingDocument,
    ProvenanceItem,
)
from docling_core.types.doc.document import PageItem, Size  # noqa: E402

W, H = 612.0, 792.0
COL_L, COL_R = (53.0, 295.0), (317.0, 559.0)  # 2단 조판 본문 단


def _doc() -> DoclingDocument:
    d = DoclingDocument(name="t")
    d.pages[1] = PageItem(page_no=1, size=Size(width=W, height=H))
    d.pages[2] = PageItem(page_no=2, size=Size(width=W, height=H))
    return d


def _prov(page: int, box: tuple[float, float, float, float], span: tuple[int, int]):
    left, top, right, bottom = box
    return ProvenanceItem(
        page_no=page,
        bbox=BoundingBox(l=left, t=top, r=right, b=bottom, coord_origin=CoordOrigin.TOPLEFT),
        charspan=span,
    )


def _add(d: DoclingDocument, text: str, boxes: list, label=DocItemLabel.TEXT, page: int = 1):
    """boxes = [(page, (l,t,r,b), (span_lo, span_hi)), …] — 여러 prov를 가진 아이템도 만든다."""
    provs = [_prov(pg, box, span) for pg, box, span in boxes]
    return d.add_text(label=label, text=text, prov=provs[0]) if len(provs) == 1 else _multi(
        d, text, provs, label)


def _multi(d: DoclingDocument, text: str, provs: list, label):
    item = d.add_text(label=label, text=text, prov=provs[0])
    item.prov = provs
    return item


def _order(d: DoclingDocument) -> list[str]:
    return [ref.resolve(d).text for ref in d.body.children]


def _para(col: tuple[float, float], top: float, height: float = 60.0) -> tuple:
    return (col[0], top, col[1], top + height)


# --- 뒤집힌 2단 페이지 ------------------------------------------------------
def _flipped_page() -> DoclingDocument:
    """오른쪽 단을 왼쪽 단보다 먼저 내보낸 2단 페이지."""
    d = _doc()
    _add(d, "TITLE OF THE PAPER", [(1, (60.0, 84.0, 550.0, 110.0), (0, 18))],
         DocItemLabel.SECTION_HEADER)
    _add(d, "R1 " + "right column body text. " * 8, [(1, _para(COL_R, 130.0), (0, 195))])
    _add(d, "R2 " + "more right column text. " * 8, [(1, _para(COL_R, 200.0), (0, 195))])
    _add(d, "L1 " + "left column body text. " * 8, [(1, _para(COL_L, 130.0), (0, 187))])
    _add(d, "L2 " + "more left column text. " * 8, [(1, _para(COL_L, 200.0), (0, 187))])
    return d


def test_flipped_columns_are_repaired():
    d = _flipped_page()
    meta = repair_reading_order(d)
    assert meta["reordered_pages"] == [1]
    assert meta["reading_order"][1]["violations"] == ["column"]
    heads = [t.split()[0] for t in _order(d)]
    assert heads == ["TITLE", "L1", "L2", "R1", "R2"]


def test_correct_page_is_untouched():
    """이미 L→R인 페이지는 참조 목록까지 그대로 — 정상 논문의 출력이 바뀌지 않는다."""
    d = _doc()
    _add(d, "TITLE OF THE PAPER", [(1, (60.0, 84.0, 550.0, 110.0), (0, 18))],
         DocItemLabel.SECTION_HEADER)
    _add(d, "L1 " + "left column body text. " * 8, [(1, _para(COL_L, 130.0), (0, 187))])
    _add(d, "L2 " + "more left column text. " * 8, [(1, _para(COL_L, 200.0), (0, 187))])
    _add(d, "R1 " + "right column body text. " * 8, [(1, _para(COL_R, 130.0), (0, 195))])
    before = [r.cref for r in d.body.children]
    assert repair_reading_order(d)["reordered_pages"] == []
    assert [r.cref for r in d.body.children] == before


def test_single_column_page_is_untouched():
    d = _doc()
    _add(d, "TITLE OF THE PAPER", [(1, (60.0, 84.0, 550.0, 110.0), (0, 18))],
         DocItemLabel.SECTION_HEADER)
    _add(d, "B " + "one wide column of body text. " * 6, [(1, (60.0, 300.0, 550.0, 360.0), (0, 182))])
    _add(d, "A " + "another wide paragraph here. " * 6, [(1, (60.0, 130.0, 550.0, 190.0), (0, 176))])
    assert repair_reading_order(d)["reordered_pages"] == []


# --- 단을 가로지르는 아이템 분할 --------------------------------------------
def test_cross_column_item_is_split():
    """뒤집힌 페이지에서 좌우 단에 걸친 한 아이템은 두 조각으로 나뉘어 제자리에 놓인다."""
    d = _doc()
    _add(d, "TITLE OF THE PAPER", [(1, (60.0, 84.0, 550.0, 110.0), (0, 18))],
         DocItemLabel.SECTION_HEADER)
    text = "RIGHT tail of the paragraph. LEFT head of the paragraph."
    _add(d, text, [(1, _para(COL_R, 130.0), (0, 27)), (1, _para(COL_L, 500.0), (28, 55))])
    _add(d, "R2 " + "right column body text here. " * 6, [(1, _para(COL_R, 300.0), (0, 177))])
    _add(d, "L1 " + "left column body text goes here. " * 6, [(1, _para(COL_L, 130.0), (0, 201))])
    meta = repair_reading_order(d)
    assert meta["reordered_pages"] == [1]
    heads = [t.split()[0] for t in _order(d)]
    assert heads == ["TITLE", "L1", "LEFT", "RIGHT", "R2"]


def test_same_page_column_continuation_is_preserved():
    """정상 페이지의 좌→우 단 이어짐(한 아이템 2 prov)은 쪼개지 않고 그대로 둔다."""
    d = _doc()
    _add(d, "TITLE OF THE PAPER", [(1, (60.0, 84.0, 550.0, 110.0), (0, 18))],
         DocItemLabel.SECTION_HEADER)
    _add(d, "L1 " + "left column body text. " * 8, [(1, _para(COL_L, 130.0), (0, 187))])
    text = "TAIL of left column. HEAD of right column."
    _add(d, text, [(1, _para(COL_L, 500.0), (0, 20)), (1, _para(COL_R, 130.0), (21, 41))])
    _add(d, "R2 " + "right column body text. " * 8, [(1, _para(COL_R, 300.0), (0, 195))])
    n_before = len(d.texts)
    assert repair_reading_order(d)["reordered_pages"] == []
    assert len(d.texts) == n_before  # 새 아이템이 생기지 않았다


# --- 저자 그리드 -----------------------------------------------------------
def _grid_page() -> DoclingDocument:
    """3열 저자 그리드를 열 우선으로 내보낸 첫 페이지 (마지막 줄은 2셀·가운데 정렬)."""
    d = _doc()
    _add(d, "TITLE OF THE PAPER", [(1, (60.0, 84.0, 550.0, 110.0), (0, 18))],
         DocItemLabel.SECTION_HEADER)
    cells = {  # 이름 → (x 중심, top)
        "A1": (142.0, 130.0), "A2": (306.0, 130.0), "A3": (470.0, 130.0),
        "B1": (142.0, 200.0), "B2": (306.0, 200.0), "B3": (470.0, 200.0),
        "C1": (224.0, 270.0), "C2": (388.0, 270.0),
    }
    for name in ("A1", "B1", "C1", "A2", "B2", "C2", "A3", "B3"):  # 열 우선(잘못된 순서)
        cx, top = cells[name]
        _add(d, f"{name} Author Name University City", [(1, (cx - 60, top, cx + 60, top + 40), (0, 34))])
    _add(d, "L1 " + "left column body text. " * 8, [(1, _para(COL_L, 340.0), (0, 187))])
    _add(d, "R1 " + "right column body text. " * 8, [(1, _para(COL_R, 340.0), (0, 195))])
    return d


def test_author_grid_is_ordered_row_major():
    d = _grid_page()
    meta = repair_reading_order(d)
    assert meta["reordered_pages"] == [1]
    assert "grid" in meta["reading_order"][1]["violations"]
    heads = [t.split()[0] for t in _order(d)]
    assert heads == ["TITLE", "A1", "A2", "A3", "B1", "B2", "B3", "C1", "C2", "L1", "R1"]


def test_row_major_grid_is_untouched():
    """이미 행 우선으로 나온 그리드는 손대지 않는다."""
    d = _doc()
    _add(d, "TITLE OF THE PAPER", [(1, (60.0, 84.0, 550.0, 110.0), (0, 18))],
         DocItemLabel.SECTION_HEADER)
    for name, cx, top in (("A1", 142.0, 130.0), ("A2", 306.0, 130.0), ("A3", 470.0, 130.0),
                          ("B1", 224.0, 200.0), ("B2", 388.0, 200.0)):
        _add(d, f"{name} Author Name University City", [(1, (cx - 60, top, cx + 60, top + 40), (0, 34))])
    _add(d, "L1 " + "left column body text. " * 8, [(1, _para(COL_L, 300.0), (0, 187))])
    _add(d, "R1 " + "right column body text. " * 8, [(1, _para(COL_R, 300.0), (0, 195))])
    assert repair_reading_order(d)["reordered_pages"] == []


# --- 안전장치 -------------------------------------------------------------
def test_multiset_mismatch_reverts_the_page(monkeypatch):
    """되돌린 뒤 글자가 달라지면 그 쪽을 통째로 원복한다."""
    from md4paper.extract import reading_order as ro

    d = _flipped_page()
    before = [r.cref for r in d.body.children]
    calls = iter(range(99))
    monkeypatch.setattr(ro, "_alnum", lambda s: f"{s}{next(calls)}")  # 대조를 일부러 깨뜨린다
    assert repair_reading_order(d)["reordered_pages"] == []
    assert [r.cref for r in d.body.children] == before


def test_cross_page_merged_item_is_split_at_the_page_boundary():
    """앞 페이지 조각과 뒤 페이지 조각이 한 아이템으로 합쳐진 경우, 뒤 페이지 몫은 뒤에 남는다."""
    d = _doc()
    _add(d, "TITLE OF THE PAPER", [(1, (60.0, 84.0, 550.0, 110.0), (0, 18))],
         DocItemLabel.SECTION_HEADER)
    text = "TAIL of the abstract. NEXT page first paragraph."
    _add(d, text, [(1, _para(COL_R, 130.0), (0, 21)), (2, _para(COL_L, 100.0), (22, 47))])
    _add(d, "R2 " + "right column body text. " * 8, [(1, _para(COL_R, 300.0), (0, 195))])
    _add(d, "L1 " + "left column body text. " * 8, [(1, _para(COL_L, 130.0), (0, 187))])
    _add(d, "P2 " + "second page body text. " * 8, [(2, _para(COL_L, 200.0), (0, 187))])
    meta = repair_reading_order(d)
    assert meta["reordered_pages"] == [1]
    heads = [t.split()[0] for t in _order(d)]
    assert heads == ["TITLE", "L1", "TAIL", "R2", "NEXT", "P2"]


def test_footnotes_stay_at_the_bottom_of_the_page():
    d = _flipped_page()
    _add(d, "FN a first-page footnote line.", [(1, (53.0, 700.0, 295.0, 712.0), (0, 30))],
         DocItemLabel.FOOTNOTE)
    repair_reading_order(d)
    heads = [t.split()[0] for t in _order(d)]
    assert heads == ["TITLE", "L1", "L2", "R1", "R2", "FN"]


# --- 되돌리지 못한 쪽은 '의심'으로 보고한다 ---------------------------------
def test_unrepaired_violation_is_reported_as_suspect(monkeypatch):
    """원복된 쪽은 reordered_pages가 아니라 reading_order_suspect에 실린다.

    front_matter의 '본문 뒤 블록 끌어올리기' 복구는 오직 이 목록을 보고 켜진다 —
    되돌린 쪽은 이미 정경 순서라 끌어올릴 것이 없기 때문이다.
    """
    from md4paper.extract import reading_order as ro

    d = _flipped_page()
    calls = iter(range(99))
    monkeypatch.setattr(ro, "_alnum", lambda s: f"{s}{next(calls)}")  # 대조를 깨 원복시킨다
    meta = repair_reading_order(d)
    assert meta["reordered_pages"] == [] and meta["reading_order_suspect"] == [1]


def test_repaired_page_is_not_suspect():
    meta = repair_reading_order(_flipped_page())
    assert meta["reordered_pages"] == [1] and meta["reading_order_suspect"] == []


def test_clean_paper_has_no_suspect_pages():
    d = _doc()
    _add(d, "TITLE OF THE PAPER", [(1, (60.0, 84.0, 550.0, 110.0), (0, 18))],
         DocItemLabel.SECTION_HEADER)
    _add(d, "L1 " + "left column body text. " * 8, [(1, _para(COL_L, 130.0), (0, 187))])
    _add(d, "R1 " + "right column body text. " * 8, [(1, _para(COL_R, 130.0), (0, 195))])
    meta = repair_reading_order(d)
    assert meta["reordered_pages"] == [] and meta["reading_order_suspect"] == []


# --- export 순서 기하 (문단 재결합 근거) ------------------------------------
def test_export_geometry_reports_columns_and_full_width():
    from md4paper.extract.reading_order import export_geometry

    d = _doc()
    _add(d, "TITLE OF THE PAPER", [(1, (60.0, 84.0, 550.0, 110.0), (0, 18))],
         DocItemLabel.SECTION_HEADER)
    _add(d, "L1 " + "left column body text. " * 8, [(1, _para(COL_L, 130.0), (0, 187))])
    _add(d, "R1 " + "right column body text. " * 8, [(1, _para(COL_R, 130.0), (0, 195))])
    geom = export_geometry(d)
    assert [g.text.split()[0] for g in geom.items] == ["TITLE", "L1", "R1"]
    assert geom.items[0].full_width is True          # 제목은 두 단을 가로지른다
    assert (geom.items[1].col_start, geom.items[2].col_start) == (0, 1)
    assert geom.pages[1].n_cols == 2 and geom.pages[1].band_top <= 84.0


def test_export_geometry_tracks_both_ends_of_a_column_spanning_item():
    """좌→우 단에 걸친 아이템은 시작(왼쪽)과 끝(오른쪽)을 따로 기록한다."""
    from md4paper.extract.reading_order import export_geometry

    d = _doc()
    _add(d, "TITLE OF THE PAPER", [(1, (60.0, 84.0, 550.0, 110.0), (0, 18))],
         DocItemLabel.SECTION_HEADER)
    _add(d, "L1 " + "left column body text. " * 8, [(1, _para(COL_L, 130.0), (0, 187))])
    _add(d, "TAIL of left column. HEAD of right column.",
         [(1, _para(COL_L, 500.0), (0, 20)), (1, _para(COL_R, 130.0), (21, 41))])
    tail = export_geometry(d).items[-1]
    assert (tail.col_start, tail.col_end) == (0, 1)


def test_single_column_items_are_not_full_width():
    """1단 조판에서는 '전폭'이 뜻을 잃는다 — 전부 전폭으로 보면 문단 재결합이 통째로 막힌다."""
    from md4paper.extract.reading_order import export_geometry

    d = _doc()
    _add(d, "TITLE OF THE PAPER", [(1, (60.0, 84.0, 550.0, 110.0), (0, 18))],
         DocItemLabel.SECTION_HEADER)
    _add(d, "A " + "one wide column of body text. " * 6, [(1, (60.0, 130.0, 550.0, 190.0), (0, 182))])
    _add(d, "B " + "another wide paragraph here. " * 6, [(1, (60.0, 200.0, 550.0, 260.0), (0, 176))])
    geom = export_geometry(d)
    assert geom.pages[1].n_cols == 1
    assert not any(g.full_width for g in geom.items)
