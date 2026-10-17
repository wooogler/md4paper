"""뭉친 표 행 되살리기 — Docling이 선 없는 표의 여러 행을 한 행으로 합친 것을 PDF 줄로 다시 나눈다.

가로줄 없는(booktabs 머리줄만 있는) 표에서 Docling의 표 모델은 본문 행 여러 개를 한 셀로 뭉치곤 한다:
"Who What Where When How Why | Which party …? What work …? …". 그런데 PDF에는 답이 있다.
뭉친 셀 안에서 각 항목은 **자기 기준선**에 있고, 첫 열의 줄은 열 폭을 한참 남기고 끝난다 — 다음 낱말이
충분히 들어갈 자리가 있는데 줄을 바꿨다면 그것은 줄바꿈(wrap)이 아니라 **새 행**이다. 다른 열도 같은
기준선에서 새 줄을 시작하면 확실하다. 이 두 증거가 모두 있을 때만 그 기준선들에서 행을 나눈다.
"""

from __future__ import annotations

from dataclasses import dataclass

from md4paper.extract.inline_math import Glyph, _page_glyphs

_GAP = 0.25  # 낱말 사이로 볼 글자 간격 (글자 크기 배수)
_ALIGN = 0.35  # 같은 기준선으로 볼 오차 (글자 크기 배수)


@dataclass
class _Line:
    base: float  # 기준선 (쪽 위에서부터의 거리)
    x0: float
    x1: float
    size: float
    first_word: float  # 첫 낱말 폭 — 앞 줄 끝에 들어갈 수 있었는지 재는 데 쓴다
    glyphs: list[Glyph]


def _lines_in(glyphs: list[Glyph], box, height: float) -> list[_Line]:  # noqa: ANN001 — docling BoundingBox(TOPLEFT)
    """셀 상자 안 글자를 기준선별 줄로."""
    inside = [g for g in glyphs
              if box.l - 1 <= (g.x0 + g.x1) / 2 <= box.r + 1 and box.t - 2 <= height - g.y <= box.b + 2]
    rows: list[list[Glyph]] = []
    for g in sorted(inside, key=lambda g: (height - g.y, g.x0)):
        if rows and abs((height - rows[-1][0].y) - (height - g.y)) <= _ALIGN * g.size:
            rows[-1].append(g)
        else:
            rows.append([g])
    out = []
    for row in rows:
        row.sort(key=lambda g: g.x0)
        size = max(g.size for g in row)
        k = 1
        while k < len(row) and row[k].x0 - row[k - 1].x1 <= _GAP * size:
            k += 1
        out.append(_Line(height - row[0].y, row[0].x0, max(g.x1 for g in row), size,
                         row[k - 1].x1 - row[0].x0, row))
    return out


def _row_starts(key: list[_Line], right: float) -> list[float]:
    """첫 열 줄 중 새 행을 여는 줄의 기준선 — 앞 줄 끝에 이 줄 첫 낱말이 들어갈 자리가 있었으면 새 행."""
    starts = [key[0].base]
    for prev, cur in zip(key, key[1:]):
        if prev.x1 + _GAP * prev.size + cur.first_word <= right:
            starts.append(cur.base)
    return starts


def _line_text(textpage, line: _Line, height: float) -> str:  # noqa: ANN001
    top = height - line.base + line.size  # pdfium은 아래가 원점
    bottom = height - line.base - 0.35 * line.size
    return " ".join(textpage.get_text_bounded(line.x0 - 0.5, bottom, line.x1 + 0.5, top).split())


def _join(parts: list[str]) -> str:
    out = ""
    for p in parts:
        if out.endswith("-") and p[:1].islower():
            out += p  # 줄 끝에서 끊긴 낱말
        else:
            out = f"{out} {p}" if out else p
    return out


def _split_row(cells: list, starts: list[float], lines: dict[int, list[_Line]],
               textpage, height: float) -> list[list[str]]:  # noqa: ANN001
    """한 행의 셀들을 기준선 구간별 하위 행 글자로."""
    bounds = starts[1:] + [float("inf")]
    sub: list[list[str]] = []
    for k, start in enumerate(starts):
        row = []
        for c in cells:
            ls = [ln for ln in lines[c.start_col_offset_idx]
                  if start - _ALIGN * ln.size <= ln.base < bounds[k] - _ALIGN * ln.size]
            row.append(_join([_line_text(textpage, ln, height) for ln in ls]))
        sub.append(row)
    return sub


def _confirmed(starts: list[float], cells: list, lines: dict[int, list[_Line]]) -> bool:
    """글자가 있는 다른 열도 각 새 행 기준선에서 줄을 시작하는가 — 행이 뭉쳤다는 두 번째 증거."""
    for c in cells[1:]:
        ls = lines[c.start_col_offset_idx]
        if not ls:
            continue
        for s in starts[1:]:
            if not any(abs(ln.base - s) <= _ALIGN * ln.size for ln in ls):
                return False
    return True


def split_collapsed_rows(document, pdf) -> int:  # noqa: ANN001 — DoclingDocument, 경로
    """문서의 표에서 뭉친 본문 행을 PDF 줄 증거로 나눈다. 반환: 새로 생긴 행 수."""
    from docling_core.types.doc import TableData

    from md4paper import pdfio

    tables = [t for t in getattr(document, "tables", []) if getattr(t, "prov", None) and t.data.table_cells]
    if not tables:
        return 0
    added = 0
    with pdfio.open_document(pdf) as doc:
        cache: dict[int, tuple] = {}
        for t in tables:
            page_no = int(t.prov[0].page_no)
            height = float(document.pages[page_no].size.height)
            if page_no not in cache:
                tp = doc[page_no - 1].get_textpage()
                cache[page_no] = (tp, _page_glyphs(tp))
            textpage, glyphs = cache[page_no]
            added += _split_table(t, textpage, glyphs, height, TableData)
    return added


def _split_table(t, textpage, glyphs: list[Glyph], height: float, TableData) -> int:  # noqa: ANN001,N803
    data = t.data
    cells = data.table_cells
    if any(c.row_span != 1 or c.col_span != 1 for c in cells) or any(
            getattr(c.bbox, "coord_origin", None) is not None and c.bbox.coord_origin.value != "TOPLEFT"
            for c in cells if c.bbox is not None):
        return 0  # 병합 셀이 있는 표·좌표계가 다른 표는 건드리지 않는다
    lefts: dict[int, float] = {}
    for c in cells:
        if c.bbox is not None:
            col = c.start_col_offset_idx
            lefts[col] = min(lefts.get(col, c.bbox.l), c.bbox.l)
    table_right = max((c.bbox.r for c in cells if c.bbox is not None), default=0.0)

    def right_of(col: int) -> float:  # 열이 쓸 수 있는 오른쪽 끝 = 다음 열 시작 직전
        nxt = [x for k, x in lefts.items() if k > col]
        return min(nxt) - 2 if nxt else table_right

    by_row: dict[int, list] = {}
    for c in cells:
        by_row.setdefault(c.start_row_offset_idx, []).append(c)
    new_cells = []
    row_at = 0
    added = 0
    for r in range(data.num_rows):
        row = sorted(by_row.get(r, []), key=lambda c: c.start_col_offset_idx)
        sub = None
        if row and not any(c.column_header for c in row) and all(c.bbox is not None for c in row):
            lines = {c.start_col_offset_idx: _lines_in(glyphs, c.bbox, height) for c in row}
            key = lines[row[0].start_col_offset_idx]
            if len(key) >= 2:
                starts = _row_starts(key, right_of(row[0].start_col_offset_idx))
                if len(starts) >= 2 and _confirmed(starts, row, lines):
                    sub = _split_row(row, starts, lines, textpage, height)
        if sub is None:
            for c in row:
                new_cells.append(c.model_copy(update={"start_row_offset_idx": row_at,
                                                      "end_row_offset_idx": row_at + 1}))
            row_at += 1
            continue
        for texts in sub:
            for c, text in zip(row, texts):
                new_cells.append(c.model_copy(update={"text": text, "start_row_offset_idx": row_at,
                                                      "end_row_offset_idx": row_at + 1}))
            row_at += 1
        added += len(sub) - 1
    if added:
        t.data = TableData(num_rows=row_at, num_cols=data.num_cols, table_cells=new_cells)
    return added


