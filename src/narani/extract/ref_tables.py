"""표로 잘못 잡힌 참고문헌 목록을 항목 목록으로 되돌린다.

번호 매긴 참고문헌([n] + 내어쓰기)은 왼쪽에 좁은 번호 열, 오른쪽에 본문 열이 있는 2단 격자처럼 보여서
Docling이 그 일부를 TableItem으로 뽑곤 한다: "| [63] | Florian Weber, … |", 이어지는 줄은 첫 칸이
빈 행. 행 나눔도 틀려 한 셀에 앞 항목 꼬리와 다음 항목 머리가 섞인다("…3642743 David James Woo, …").

판정 증거 (모두 만족할 때만):
- References/Bibliography 절 안이다 (앞선 마지막 절 제목).
- 캡션이 없다.
- 글자가 있는 열이 정확히 둘이고, 왼쪽 열의 비어 있지 않은 칸은 전부 "[n]"/"n." 번호이며
  1씩 증가한다. 오른쪽(본문) 열은 번호 열 오른쪽에서 시작한다 (내어쓰기).

항목은 셀 행이 아니라 PDF 기준선으로 다시 짠다: 번호가 찍힌 기준선이 새 항목의 시작이고,
본문 열의 각 줄은 그 위(또는 같은 높이)의 마지막 번호에 붙는다. 첫 번호보다 위의 줄은 앞 쪽에서
넘어온 앞 항목의 꼬리라 바로 앞 본문 아이템에 잇는다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from narani.extract.tables import _ALIGN, _join, _line_text, _lines_in

_MARKER_RE = re.compile(r"^\[(\d{1,4})\]$|^(\d{1,4})\.$")
_REFS_TITLE_RE = re.compile(r"^(\d+(\.\d+)*\.?\s+)?(references|bibliography|works cited|literature cited|참고문헌)\b",
                            re.I)


@dataclass
class _Box:  # docling BoundingBox(TOPLEFT)와 같은 모양 — tables._lines_in이 l/t/r/b만 본다
    l: float  # noqa: E741
    t: float
    r: float
    b: float


def _marker_num(text: str) -> int | None:
    m = _MARKER_RE.match(text.strip())
    return int(m.group(1) or m.group(2)) if m else None


def _columns(t) -> tuple[int, int] | None:  # noqa: ANN001 — TableItem
    """(번호 열, 본문 열) — 참고문헌 목록 모양이 아니면 None."""
    cells = t.data.table_cells
    if any(c.row_span != 1 or c.col_span != 1 for c in cells):
        return None
    filled = sorted({c.start_col_offset_idx for c in cells if c.text.strip()})
    if len(filled) != 2:
        return None
    mcol, bcol = filled
    marks = [c for c in sorted(cells, key=lambda c: c.start_row_offset_idx)
             if c.start_col_offset_idx == mcol and c.text.strip()]
    nums = [_marker_num(c.text) for c in marks]
    if len(nums) < 2 or None in nums or any(b != a + 1 for a, b in zip(nums, nums[1:])):
        return None
    bodies = [c for c in cells if c.start_col_offset_idx == bcol and c.text.strip()]
    if any(c.bbox is None for c in marks + bodies):
        return None
    if min(c.bbox.l for c in bodies) <= max(c.bbox.r for c in marks) - 1:
        return None  # 본문이 번호보다 오른쪽에서 시작해야 (내어쓰기)
    return mcol, bcol


def _in_references(document, table) -> bool:  # noqa: ANN001
    """표 앞의 마지막 절 제목이 References류인가."""
    from docling_core.types.doc import DocItemLabel

    last = ""
    for it, _ in document.iterate_items():
        if it is table:
            return bool(_REFS_TITLE_RE.match(last))
        if getattr(it, "label", None) in (DocItemLabel.SECTION_HEADER, DocItemLabel.TITLE):
            last = (it.text or "").strip()
    return False


def candidates(document) -> list[tuple]:  # noqa: ANN001 — DoclingDocument
    """참고문헌 목록으로 볼 표: [(table, 번호 열, 본문 열)]."""
    out = []
    for t in getattr(document, "tables", []):
        if t.captions or not t.data.table_cells:
            continue
        cols = _columns(t)
        if cols and _in_references(document, t):
            out.append((t, *cols))
    return out


def group_lines(marks: list[tuple[float, str]], lines: list[tuple[float, float, str]]
                ) -> tuple[list[str], list[tuple[str, list[str]]]]:
    """기준선으로 줄을 항목에 배정. marks: [(기준선, "[n]")], lines: [(기준선, 글자 크기, 글)].

    반환: (첫 번호보다 위의 꼬리 줄들, [(번호, 줄들)]). 기준선은 위에서부터 잰 값.
    """
    marks = sorted(marks)
    lead: list[str] = []
    entries: list[tuple[str, list[str]]] = [(m, []) for _, m in marks]
    for base, size, text in sorted(lines):
        k = -1
        for i, (mb, _) in enumerate(marks):
            if mb - _ALIGN * size <= base:
                k = i
        (entries[k][1] if k >= 0 else lead).append(text)
    return lead, entries


def join_lines(parts: list[str]) -> str:
    """줄 잇기 — 줄 끝 하이픈 낱말, '/'에서 끊긴 DOI·URL은 공백 없이."""
    out = ""
    for p in parts:
        out = out + p if out.endswith("/") else _join([out, p]) if out else p
    return out


def _from_pdf(t, mcol: int, bcol: int, textpage, glyphs, height: float):  # noqa: ANN001,ANN202
    cells = t.data.table_cells
    if any(c.bbox is None or getattr(c.bbox, "coord_origin", None) is None
           or c.bbox.coord_origin.value != "TOPLEFT" for c in cells if c.text.strip()):
        return None
    mc = [c for c in cells if c.start_col_offset_idx == mcol and c.text.strip()]
    bc = [c for c in cells if c.start_col_offset_idx == bcol and c.text.strip()]
    top = min(c.bbox.t for c in mc + bc)
    bottom = max(c.bbox.b for c in mc + bc)
    body_l = min(c.bbox.l for c in bc)
    mbox = _Box(min(c.bbox.l for c in mc) - 1, top, body_l - 1, bottom)
    bbox = _Box(body_l, top, max(c.bbox.r for c in bc) + 1, bottom)
    marks = []
    for ln in _lines_in(glyphs, mbox, height):
        text = "".join(g.ch for g in ln.glyphs)
        if _marker_num(text) is None:
            return None  # 번호 열에 번호 아닌 글자 — PDF와 셀 판정이 어긋나면 손대지 않는다
        marks.append((ln.base, text))
    if [m for _, m in sorted(marks)] != [c.text.strip() for c in
                                         sorted(mc, key=lambda c: c.start_row_offset_idx)]:
        return None
    lines = [(ln.base, ln.size, _line_text(textpage, ln, height)) for ln in _lines_in(glyphs, bbox, height)]
    return group_lines(marks, [x for x in lines if x[2]])


def _from_cells(t, mcol: int, bcol: int):  # noqa: ANN001,ANN202
    """PDF를 못 읽을 때: Docling 행 그대로 — 번호 있는 행이 새 항목."""
    rows: dict[int, dict[int, str]] = {}
    for c in t.data.table_cells:
        rows.setdefault(c.start_row_offset_idx, {})[c.start_col_offset_idx] = c.text.strip()
    lead: list[str] = []
    entries: list[tuple[str, list[str]]] = []
    for r in sorted(rows):
        mark, body = rows[r].get(mcol, ""), rows[r].get(bcol, "")
        if mark:
            entries.append((mark, [body] if body else []))
        elif body:
            (entries[-1][1] if entries else lead).append(body)
    return lead, entries


def _previous_text_item(document, table):  # noqa: ANN001,ANN202
    from docling_core.types.doc import DocItemLabel

    prev = None
    for it, _ in document.iterate_items():
        if it is table:
            return prev
        if getattr(it, "label", None) in (DocItemLabel.LIST_ITEM, DocItemLabel.TEXT):
            prev = it
        elif getattr(it, "label", None) in (DocItemLabel.SECTION_HEADER, DocItemLabel.TITLE):
            prev = None
    return None


def _replace(document, t, lead: list[str], entries: list[tuple[str, list[str]]]) -> int:  # noqa: ANN001
    entries = [(m, ls) for m, ls in entries if ls]
    if not entries:
        return 0
    if lead:
        prev = _previous_text_item(document, t)
        if prev is not None:
            prev.text = join_lines([prev.text, *lead])
            prev.orig = prev.text
    group = document.insert_list_group(sibling=t, after=False)
    prov = t.prov[0] if t.prov else None
    for mark, ls in entries:
        text = f"{mark} {join_lines(ls)}"
        p = prov.model_copy(update={"charspan": (0, len(text))}) if prov is not None else None
        document.add_list_item(text=text, parent=group, prov=p)
    document.delete_items(node_items=[t])
    return len(entries)


def _pdf_groups(document, found: list[tuple], pdf) -> dict[int, tuple]:  # noqa: ANN001
    """표별 기준선 배정 {id(table): (꼬리, 항목들)} — 증거를 못 얻은 표는 빠진다."""
    from narani import pdfio
    from narani.extract.inline_math import _page_glyphs

    out: dict[int, tuple] = {}
    cache: dict[int, tuple] = {}
    with pdfio.open_document(pdf) as doc:
        for t, mcol, bcol in found:
            if not t.prov:
                continue
            page_no = int(t.prov[0].page_no)
            if page_no not in cache:
                tp = doc[page_no - 1].get_textpage()
                cache[page_no] = (tp, _page_glyphs(tp))
            got = _from_pdf(t, mcol, bcol, *cache[page_no], float(document.pages[page_no].size.height))
            if got is not None:
                out[id(t)] = got
    return out


def unwrap(document, pdf) -> int:  # noqa: ANN001 — DoclingDocument, 경로
    """참고문헌 표를 목록 항목으로 바꾼다. 반환: 되살린 항목 수."""
    found = candidates(document)
    if not found:
        return 0
    try:
        groups = _pdf_groups(document, found, pdf)
    except Exception:  # noqa: BLE001 — 기준선 증거를 못 읽으면 Docling 셀 행으로
        groups = {}
    return sum(_replace(document, t, *(groups.get(id(t)) or _from_cells(t, mcol, bcol)))
               for t, mcol, bcol in found)
