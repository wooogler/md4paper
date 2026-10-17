"""뭉친 표 행 되살리기 — PDF 기준선과 '다음 낱말이 들어갈 자리'로 행 경계를 찾는다."""

from types import SimpleNamespace

from docling_core.types.doc import BoundingBox, CoordOrigin, TableCell, TableData

from md4paper.extract import tables
from md4paper.extract.inline_math import Glyph

H = 800.0  # 쪽 높이
SIZE, W = 9.0, 4.5  # 글자 크기·폭


def _glyphs(x: float, base: float, text: str) -> list[Glyph]:
    """쪽 위에서 base 거리 기준선에 text를 x부터 놓는다 (공백은 글자 없이 한 칸 띄움)."""
    out, i = [], 0
    for ch in text:
        if ch != " ":
            out.append(Glyph(i, ch, "Body", SIZE, x, x + W, H - base))
            i += 1
        x += W
    return out


class _TextPage:
    """pdfium textpage 흉내 — 상자 안 글자를 줄 순서대로, 간격이 벌어진 곳은 띄어서."""

    def __init__(self, glyphs):
        self.glyphs = glyphs

    def get_text_bounded(self, left, bottom, right, top):
        gs = sorted((g for g in self.glyphs if left <= g.x0 and g.x1 <= right and bottom <= g.y <= top),
                    key=lambda g: g.x0)
        out, prev = "", None
        for g in gs:
            if prev is not None and g.x0 - prev.x1 > 0.5:
                out += " "
            out += g.ch
            prev = g
        return out


def _cell(row, col, left, t, r, b, text, header=False):
    return TableCell(bbox=BoundingBox(l=left, t=t, r=r, b=b, coord_origin=CoordOrigin.TOPLEFT), row_span=1,
                     col_span=1, start_row_offset_idx=row, end_row_offset_idx=row + 1,
                     start_col_offset_idx=col, end_col_offset_idx=col + 1, text=text, column_header=header)


def _table(rows_c0, rows_c1, col1_x=200.0):
    """머리 행 + 본문 행 하나(여러 줄이 한 셀로 뭉친 상태)인 2열 표와 그 PDF 글자."""
    glyphs = _glyphs(100, 100, "Element") + _glyphs(col1_x, 100, "Question")
    for k, line in enumerate(rows_c0):
        glyphs += _glyphs(100, 120 + 12 * k, line)
    for k, line in enumerate(rows_c1):
        glyphs += _glyphs(col1_x, 120 + 12 * k, line)
    bottom = 120 + 12 * max(len(rows_c0), len(rows_c1))
    cells = [_cell(0, 0, 100, 92, 140, 102, "Element", True), _cell(0, 1, col1_x, 92, 260, 102, "Question", True),
             _cell(1, 0, 100, 112, 100 + W * max(map(len, rows_c0)), bottom, " ".join(rows_c0)),
             _cell(1, 1, col1_x, 112, col1_x + W * max(map(len, rows_c1)), bottom, " ".join(rows_c1))]
    t = SimpleNamespace(data=TableData(num_rows=2, num_cols=2, table_cells=cells))
    return t, _TextPage(glyphs), glyphs


def _rows(t):
    grid = {}
    for c in t.data.table_cells:
        grid.setdefault(c.start_row_offset_idx, {})[c.start_col_offset_idx] = c.text
    return [[grid[r][c] for c in sorted(grid[r])] for r in sorted(grid)]


def test_collapsed_rows_are_split_at_hard_breaks():
    t, tp, glyphs = _table(["Who", "What", "Where"], ["Which party engaged?", "What work occurred?",
                                                       "Which sources got attention?"])
    assert tables._split_table(t, tp, glyphs, H, TableData) == 2
    assert _rows(t) == [["Element", "Question"], ["Who", "Which party engaged?"],
                        ["What", "What work occurred?"], ["Where", "Which sources got attention?"]]


def test_wrapped_first_column_is_one_row():
    # 첫 열이 열 폭을 꽉 채우고 넘어간 줄 — 다음 낱말이 들어갈 자리가 없었으니 줄바꿈이다
    t, tp, glyphs = _table(["Constrained reser", "vation task"], ["A long description that", "wraps here too"],
                           col1_x=100 + W * 17 + 3)
    assert tables._split_table(t, tp, glyphs, H, TableData) == 0
    assert len(_rows(t)) == 2


def test_other_column_must_start_a_line_at_each_new_row():
    # 첫 열은 두 줄이지만 둘째 열은 한 줄뿐 — 행이 뭉쳤다는 확증이 없다
    t, tp, glyphs = _table(["Who", "What"], ["One single answer"])
    assert tables._split_table(t, tp, glyphs, H, TableData) == 0
