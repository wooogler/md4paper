"""놓친 괘선 표 되살리기 — 'Table N' 캡션은 있는데 표 아이템이 없으면 PDF 괘선 격자로 표를 다시 짓는다.

글이 긴 셀로 채운 표(아이디어 비교표처럼 셀마다 문단이 들어간 표)는 Docling 레이아웃 모델이 표로 보지
못하고 셀마다 text 블록, 열 머리는 section_header로 낸다. 그러면 TableFormer는 돌지도 않고, 읽기 순서
복원은 나란한 열을 다단 본문으로 보아 열 우선으로 다시 늘어놓는다 — 표가 뒤섞인 문단이 된다.

그런데 PDF에는 답이 그려져 있다. 괘선 표는 가로·세로 선을 벡터 경로로 긋는다. 선이 만나 이루는 격자가
곧 행·열 경계이고, 선이 끊긴 자리는 병합 셀이다. 판정 증거 (모두 만족할 때만):
- 그 쪽에 'Table N' 캡션 블록이 있고, 격자 바로 위나 아래에 놓였다.
- 격자 안팎에 걸친 블록이 없고, 격자 안에 그림·표 아이템이 없다 (그림 속 선 그림이 아니다).
- 안쪽 가로선·세로선이 하나 이상씩 있어 2행 2열 이상이다. 병합 영역은 직사각형이어야 한다.
- 셀 글자(PDF 글자층)와 덮인 Docling 블록의 글자가 같다 — 글자를 더하거나 잃지 않았다.

셀 글자는 PDF 글자층의 기준선 줄에서 읽고, 표는 덮인 블록 중 첫 블록 자리에 넣는다. 읽기 순서 복원보다 **먼저** 돌아야
한다 — 표 아이템은 플로트라 순서 판정에서 빠지므로, 그 쪽이 다단 위반으로 잘못 재배열되지 않는다.
가로선만 있는 표(booktabs)는 세로 경계 증거가 없어 손대지 않는다.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass

from narani.extract.spacing import _norm_char
from narani.extract.tables import _GAP, _join, _Line, _lines_in

_TABLE_CAP_RE = re.compile(r"^\s*(?:<[^>]+>\s*)?\**\s*(?:Table|Tab\.?)\s+(?:\d+|[IVXLC]+)\s*(?:[:.|]|$)", re.I)
_THIN = 2.0  # 선으로 볼 경로 두께 상한 (pt)
_MIN_LEN = 5.0  # 선으로 볼 경로 길이 하한 (pt)
_TOL = 1.5  # 같은 선·맞닿음으로 볼 오차 (pt)
_CAPTION_GAP = 40.0  # 캡션과 격자 사이 최대 간격 (pt)
_SPILL = 3.0  # 블록이 격자 밖으로 삐져나와도 되는 정도 (pt)


@dataclass
class _Seg:
    horiz: bool
    at: float  # 가로선의 y / 세로선의 x (pdfium 좌표: 아래가 원점)
    lo: float  # 가로선의 x0 / 세로선의 y0
    hi: float


@dataclass
class _Box:  # docling BoundingBox(TOPLEFT)와 같은 모양 — tables._lines_in이 l/t/r/b만 본다
    l: float  # noqa: E741
    t: float
    r: float
    b: float


def _segments(page) -> list[_Seg]:  # noqa: ANN001 — pypdfium2 PdfPage
    """쪽의 얇은 벡터 경로를 가로·세로 선분으로."""
    import pypdfium2.raw as C

    out = []
    for obj in page.get_objects(filter=[C.FPDF_PAGEOBJ_PATH]):
        x0, y0, x1, y1 = obj.get_bounds()
        w, h = x1 - x0, y1 - y0
        if h <= _THIN and w >= _MIN_LEN:
            out.append(_Seg(True, (y0 + y1) / 2, x0, x1))
        elif w <= _THIN and h >= _MIN_LEN:
            out.append(_Seg(False, (x0 + x1) / 2, y0, y1))
    return out


def _touch(a: _Seg, b: _Seg) -> bool:
    if a.horiz == b.horiz:  # 같은 줄 위에서 잇닿은 조각 (행마다 따로 그은 세로선)
        return abs(a.at - b.at) <= _TOL and a.lo <= b.hi + _TOL and b.lo <= a.hi + _TOL
    h, v = (a, b) if a.horiz else (b, a)
    return v.lo - _TOL <= h.at <= v.hi + _TOL and h.lo - _TOL <= v.at <= h.hi + _TOL


def _grids(segs: list[_Seg]) -> list[list[_Seg]]:
    """맞닿은 선분끼리 묶은 격자 후보 — 가로·세로선이 다 있는 묶음만."""
    parent = list(range(len(segs)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(segs)):
        for j in range(i + 1, len(segs)):
            if _touch(segs[i], segs[j]):
                parent[find(i)] = find(j)
    groups: dict[int, list[_Seg]] = {}
    for i, s in enumerate(segs):
        groups.setdefault(find(i), []).append(s)
    return [g for g in groups.values() if any(s.horiz for s in g) and any(not s.horiz for s in g)]


def _cluster(values: list[float]) -> list[float]:
    out: list[float] = []
    for v in sorted(values):
        if out and v - out[-1] <= _TOL:
            continue
        out.append(v)
    return out


def _covered(segs: list[_Seg], horiz: bool, at: float, point: float) -> bool:
    return any(s.horiz == horiz and abs(s.at - at) <= _TOL and s.lo - _TOL <= point <= s.hi + _TOL for s in segs)


def _layout(grid: list[_Seg]):  # noqa: ANN202
    """격자 → (xs, ys 위→아래, 셀 목록 [(행, 열, 행폭, 열폭)]). 증거가 모자라거나 병합이 비뚤면 None."""
    hs, vs = [s for s in grid if s.horiz], [s for s in grid if not s.horiz]
    # 바깥 테두리 세로선이 없는 표('c|c')도 있다 — 가로선 양 끝이 표의 좌우 끝이다
    xs = _cluster([s.at for s in vs] + [min(s.lo for s in hs), max(s.hi for s in hs)])
    ys = _cluster([s.at for s in hs] + [min(s.lo for s in vs), max(s.hi for s in vs)])[::-1]
    n_rows, n_cols = len(ys) - 1, len(xs) - 1
    if n_rows < 2 or n_cols < 2:
        return None
    parent = {(r, c): (r, c) for r in range(n_rows) for c in range(n_cols)}

    def find(k):  # noqa: ANN001,ANN202
        while parent[k] != k:
            k = parent[k]
        return k

    for r in range(n_rows):
        mid_y = (ys[r] + ys[r + 1]) / 2
        for c in range(n_cols):
            mid_x = (xs[c] + xs[c + 1]) / 2
            if c + 1 < n_cols and not _covered(grid, False, xs[c + 1], mid_y):
                parent[find((r, c + 1))] = find((r, c))
            if r + 1 < n_rows and not _covered(grid, True, ys[r + 1], mid_x):
                parent[find((r + 1, c))] = find((r, c))
    regions: dict[tuple, list[tuple]] = {}
    for k in parent:
        regions.setdefault(find(k), []).append(k)
    cells = []
    for ks in regions.values():
        rs, cs = [k[0] for k in ks], [k[1] for k in ks]
        rspan, cspan = max(rs) - min(rs) + 1, max(cs) - min(cs) + 1
        if rspan * cspan != len(ks):
            return None  # ㄱ자 병합 — 표 셀로 옮길 수 없다
        cells.append((min(rs), min(cs), rspan, cspan))
    if len({c[1] for c in cells}) < 2 or len({c[0] for c in cells}) < 2:
        return None  # 안쪽 경계가 실제로 그어지지 않았다 (테두리 상자일 뿐)
    return xs, ys, sorted(cells)


def _line_text(line: _Line) -> str:
    """줄의 글자를 잇는다 — 셀 행간이 좁으면 사각형으로 읽는 글자층(get_text_bounded)이 윗줄의
    내림 획(p·g·y)까지 끌어오므로 기준선으로 묶인 글자에서 읽는다. 텍스트 스트림에서 건너뛴 자리가 공백이다."""
    out, prev = "", None
    for g in line.glyphs:
        if prev is not None and (g.idx - prev.idx > 1 or g.x0 - prev.x1 > _GAP * g.size):
            out += " "
        # pdfium은 줄 끝 하이픈을 \x02로 준다. 나머지는 Docling 본문처럼 곧은 따옴표·합자 풀기로
        out += "-" if g.ch == "\x02" else _norm_char(g.ch)
        prev = g
    return out


def _join_lines(parts: list[str], words: set[str]) -> str:
    """줄 잇기 — 줄 끝 하이픈은 이어 붙인 낱말('interpretable')이 논문의 다른 곳에 쓰였으면 지우고,
    아니면 남긴다('surface-level'). 좁은 셀은 하이픈 줄바꿈이 잦아 남겨 두면 낱말이 쪼개져 읽힌다."""
    out = ""
    for p in parts:
        head, tail = re.search(r"([A-Za-z]*)-$", out), re.match(r"[a-z]+", p)
        if head and tail and (head.group(1) + tail.group(0)).lower() in words:
            out = out[:-1] + p
        else:
            out = _join([out, p]) if out else p
    return out


def _words(document, skip: set[int]) -> set[str]:  # noqa: ANN001
    return {w.lower() for it, _ in document.iterate_items() if id(it) not in skip
            for w in re.findall(r"[A-Za-z]{4,}", getattr(it, "text", "") or "")}


def _letters(texts) -> Counter:  # noqa: ANN001
    return Counter(ch for t in texts for ch in unicodedata.normalize("NFKC", t).lower() if ch.isalnum())


def _page_items(document, page: int) -> list:  # noqa: ANN001
    """쪽의 본문 아이템(러닝 헤더·푸터 제외)과 그 bbox(아래가 원점), 읽기 순서대로."""
    height = float(document.pages[page].size.height)
    out = []
    for it, _ in document.iterate_items():
        provs = getattr(it, "prov", None) or []
        if not any(int(p.page_no) == page for p in provs):
            continue
        label = str(getattr(it, "label", "")).replace("DocItemLabel.", "").lower()
        if label in ("page_header", "page_footer"):
            continue
        out.append((it, label, provs, [p.bbox.to_bottom_left_origin(height) for p in provs]))
    return out


def _caption_for(items: list, x0: float, x1: float, top: float, bottom: float, used: set):  # noqa: ANN202
    """격자 바로 위·아래의 'Table N' 블록 중 가장 가까운 것."""
    best, gap = None, _CAPTION_GAP
    for it, label, _, boxes in items:
        if id(it) in used or label in ("picture", "table") or not _TABLE_CAP_RE.match(getattr(it, "text", "") or ""):
            continue
        b = boxes[0]
        if b.r < x0 or b.l > x1:
            continue
        d = b.b - top if b.b >= top - _TOL else bottom - b.t if b.t <= bottom + _TOL else None
        if d is not None and d <= gap:
            best, gap = it, d
    return best


def _build(document, page: int, items: list, grid: list[_Seg], glyphs, used: set) -> bool:  # noqa: ANN001,C901
    from docling_core.types.doc import (
        BoundingBox,
        CoordOrigin,
        ProvenanceItem,
        TableCell,
        TableData,
    )

    layout = _layout(grid)
    if layout is None:
        return False
    xs, ys, cells = layout
    x0, x1, top, bottom = xs[0], xs[-1], ys[0], ys[-1]
    inside = []
    for it, label, provs, boxes in items:
        hit = [b for b in boxes if b.l < x1 and b.r > x0 and b.b < top and b.t > bottom]
        if not hit:
            continue
        if (label in ("picture", "table", "chart") or len(provs) != len(hit)
                or any(b.l < x0 - _SPILL or b.r > x1 + _SPILL or b.t > top + _SPILL or b.b < bottom - _SPILL
                       for b in hit)):
            return False  # 격자 안의 그림·표이거나, 격자 밖으로 이어지는 블록 — 이 격자는 표가 아니다
        inside.append(it)
    if not inside or any(id(it) in used for it in inside):
        return False
    caption = _caption_for(items, x0, x1, top, bottom, used)
    if caption is None or any(it is caption for it in inside):
        return False
    height = float(document.pages[page].size.height)
    texts = []
    for r, c, rspan, cspan in cells:
        box = _Box(l=xs[c], t=height - ys[r], r=xs[c + cspan], b=height - ys[r + rspan])
        # _lines_in은 위아래로 2pt 여유를 둔다 — 그만큼 안으로 줄여 이웃 셀의 끝줄을 끌어오지 않게
        lines = _lines_in(glyphs, _Box(box.l, box.t + 2, box.r, box.b - 2), height)
        texts.append((box, [_line_text(ln) for ln in lines]))
    # 하이픈 판정의 낱말 증거 — 논문의 다른 블록, 그리고 이 표에서 줄 가운데 온전히 놓인 낱말
    words = _words(document, {id(it) for it in inside}) | {
        w.lower() for _, ls in texts for ln in ls for w in re.findall(r"[A-Za-z]{4,}", " ".join(ln.split()[1:-1]))}
    new = []
    for (r, c, rspan, cspan), (box, ls) in zip(cells, texts):
        text = _join_lines(ls, words)
        new.append(TableCell(
            bbox=BoundingBox(l=box.l, t=box.t, r=box.r, b=box.b, coord_origin=CoordOrigin.TOPLEFT),
            row_span=rspan, col_span=cspan, start_row_offset_idx=r, end_row_offset_idx=r + rspan,
            start_col_offset_idx=c, end_col_offset_idx=c + cspan, text=text, column_header=r == 0))
    if _letters(c.text for c in new) != _letters(getattr(it, "text", "") or "" for it in inside):
        return False  # 셀 글자는 덮인 블록 글자를 옮기기만 해야 한다 — 다르면 글자층을 잘못 읽은 것
    caption.label = "caption"  # 표에 딸린 캡션 — 순서 판정·캡션 배치가 그림·표 캡션으로 다룬다
    prov = ProvenanceItem(page_no=page, charspan=(0, 0),
                          bbox=BoundingBox(l=x0, t=top, r=x1, b=bottom, coord_origin=CoordOrigin.BOTTOMLEFT))
    n_rows, n_cols = len(ys) - 1, len(xs) - 1
    document.insert_table(sibling=inside[0], data=TableData(num_rows=n_rows, num_cols=n_cols, table_cells=new),
                          caption=caption, prov=prov, after=False)
    document.delete_items(node_items=inside)
    used.update(id(it) for it in inside)
    used.add(id(caption))
    return True


def rebuild(document, pdf) -> int:  # noqa: ANN001 — DoclingDocument, 경로
    """캡션만 있고 표 아이템이 없는 괘선 표를 PDF 격자로 다시 짓는다. 반환: 지은 표 수."""
    from narani import pdfio
    from narani.extract.inline_math import _page_glyphs

    pages = sorted({int(p.page_no) for it, _ in document.iterate_items()
                    for p in (getattr(it, "prov", None) or [])
                    if _TABLE_CAP_RE.match(getattr(it, "text", "") or "")})
    if not pages:
        return 0
    built = 0
    with pdfio.open_document(pdf) as doc:
        for page in pages:
            pg = doc[page - 1]
            grids = _grids(_segments(pg))
            if not grids:
                continue
            glyphs = _page_glyphs(pg.get_textpage())
            used: set[int] = set()
            for grid in grids:
                built += _build(document, page, _page_items(document, page), grid, glyphs, used)
    return built
