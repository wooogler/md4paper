"""그림 설명 — 태그 PDF의 Figure 요소에 저자가 단 대체 텍스트(/Alt)를 본문 그림에 짝짓는다.

ACM DL 최종본(2023~)은 거의 모두 그림마다 접근성 설명을 단다 — 캡션 복사가 아니라 "왼쪽은 …,
오른쪽은 …"처럼 그림 자체를 글로 옮긴 것이다(중앙값 358자). docling은 이걸 노출하지 않으므로
원본 PDF의 구조 트리를 pdfium으로 직접 읽는다. 같은 논문도 arXiv 사본엔 태그가 없다.

**짝짓기.** LLM 없이 코드로만 한다.
1. 자리: 요소의 MCID → 페이지 객체 bbox ↔ 그림 자리(`figures.json`). 겹친 넓이가 작은 쪽의 60% 이상이면
   짝. 한 요소가 그림 조각 여럿을 덮으면(docling이 한 그림을 쪼갬) 조각 모두에, 여러 요소가 한 그림에
   들면(서브피규어·합쳐진 그림) 이어 붙인다. 겹치는 후보가 여럿이면 캡션 번호가 같은 그림을 고른다.
2. 번호: 자리를 모르는 그림은 요소의 캡션 번호("Figure 3:"로 시작하는 설명, 또는 형제 Caption 글자)로
   `figure-3.png`에 붙인다. 파일명이 캡션 번호이기 때문이다(assemble/figures.py).
   자리가 번호보다 먼저다 — docling이 캡션을 잘못 짝지은 그림(실측: Figure 14가 table-5.png)은
   번호로는 엉뚱한 그림에 붙는다.

**저장.** 마크다운 alt(`![Figure 3]`)는 그대로 둔다 — 그림↔캡션 짝(ui/figrefs.py)이 그 라벨을 쓰고,
설명은 수백 자에 줄바꿈·LaTeX 잔재가 섞인다. 대신 `extract/figure_alts.json`에 out 그림 파일명별로
두고 뷰어(확대 뷰어)·에이전트가 읽는다. 기록에 입력 열쇠(PDF·그림 바이트·그림 자리)를 남겨,
그림을 다시 자르거나 다시 조립하면 저절로 다시 짝짓는다.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path

from narani.workdir import WorkDir

VERSION = 1
_MIN_LEN = 20     # 이보다 짧으면 설명이 아니다 ("Figure 1", "image 3", 파일명)
_OVERLAP = 0.6    # 겹친 넓이 / 작은 쪽 넓이 — 이 이상이면 같은 그림

# 캡션 번호 — "Figure 3", "Fig. 2a", "Figure 7-a", "Figure C.3"
_NUM = r"((?:[A-Z]\.?)?\d+(?:\.\d+)?)"
_LABEL_RE = re.compile(r"^\s*[\"“]?\s*(?:Figure|Fig\.?)\s*" + _NUM + r"(?:\s*-?\s*\(?[a-z]\)?(?![a-z]))?", re.I)
_LABEL_END_RE = re.compile(r"^\s*(?:[:.\-–—]|\n|$)")  # 라벨 뒤가 구두점·줄바꿈이면 라벨만 떼어 낸다
_TEX_EMPTY_RE = re.compile(r"\\[A-Za-z]+\{\}")        # \sys{} 같은 매크로 잔재
# 짧은 값 중 설명이 아닌 것 — 로고·연락처·라이선스·자리표시자·머리말
_JUNK_RE = re.compile(
    r"logo|orcid|creative\s*commons|\bcc[- ]by|e-?mail|envelope|jstor"
    r"|^(?:image|figure|fig\.?|picture|img|graphic)\s*\d*$|\.(?:png|jpe?g|gif|eps|pdf|svg)$"
    r"|^(?:chi|cscw|uist|dis|iui)\s*'?\d{2,4}|^paper\s*\d+", re.I)
_FIG_NAME_RE = re.compile(r"^figure-" + _NUM + r"(?:-\d+)?\.\w+$")


@dataclass
class Alt:
    """PDF의 Figure 요소 하나."""

    page: int                     # 1-based
    bbox: tuple | None            # 좌상단 원점 pt (l, t, r, b). MCID로 못 찾으면 None
    text: str                     # 정리한 설명
    num: str | None = None        # 캡션 번호 ("3", "C.3")


@dataclass
class Fig:
    """본문 그림 하나 (out/images)."""

    name: str
    num: str | None = None
    page: int | None = None
    bbox: tuple | None = None


# --- 정리 ---------------------------------------------------------------


def clean(raw: str | None) -> tuple[str | None, str | None]:
    """대체 텍스트 → (설명 | None, 앞머리 캡션 번호 | None). 설명이 아니면(로고·자리표시자) 설명은 None.

    "Figure 3: …" 같은 앞머리 라벨은 번호로 떼어 낸다. "Figure 3 shows …"는 문장이라 남긴다.
    """
    s = (raw or "").replace("\x00", "").strip()
    num = None
    m = _LABEL_RE.match(s)
    if m:
        num = m.group(1).upper() if m.group(1)[0].isalpha() else m.group(1)
        if _LABEL_END_RE.match(s[m.end():]):
            s = re.sub(r"^\s*[:.\-–—]?", "", s[m.end():], count=1)
    s = _TEX_EMPTY_RE.sub("", s)
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) >= 2 and s[0] in "\"“" and s[-1] in "\"”" and s.count('"') + s.count("“") + s.count("”") == 2:
        s = s[1:-1].strip()
    if len(s) < _MIN_LEN or (len(s) < 80 and _JUNK_RE.search(s)):
        return None, num
    return s, num


def _fig_num(name: str) -> str | None:
    m = _FIG_NAME_RE.match(name)
    return m.group(1) if m else None


# --- 읽기 (pdfium 구조 트리) ----------------------------------------------


def _wstr(fn, handle) -> str:  # noqa: ANN001 — pdfium의 UTF-16LE 문자열 getter
    n = fn(handle, None, 0)
    if n <= 2:
        return ""
    buf = ctypes.create_string_buffer(n)
    fn(handle, buf, n)
    return buf.raw[:n - 2].decode("utf-16-le", "replace")


class _Page:
    """한 쪽의 MCID → (bbox, 글자). 글자는 캡션 번호가 필요할 때만 읽는다."""

    def __init__(self, page) -> None:  # noqa: ANN001
        import pypdfium2.raw as raw

        self.page = page
        self.w, self.h = page.get_size()
        self.boxes: dict[int, list[tuple]] = {}
        # 최상위 객체만 — 폼 XObject 안 자식의 좌표는 폼 공간이라 쪽 좌표가 아니다
        for obj in page.get_objects(max_depth=1):
            mc = raw.FPDFPageObj_GetMarkedContentID(obj.raw)
            if mc >= 0:
                self.boxes.setdefault(mc, []).append(obj.get_bounds())
        self._text: dict[int, str] | None = None

    def bbox(self, mcids: list[int]) -> tuple | None:
        bs = [b for m in mcids for b in self.boxes.get(m, [])]
        if not bs:
            return None
        l, r = max(0.0, min(b[0] for b in bs)), min(self.w, max(b[2] for b in bs))
        bot, top = max(0.0, min(b[1] for b in bs)), min(self.h, max(b[3] for b in bs))
        if r <= l or top <= bot:
            return None  # 쪽 밖 (잘라 보니 남는 게 없다)
        return (l, self.h - top, r, self.h - bot)

    def text(self, mcid: int) -> str:
        if self._text is None:
            import pypdfium2.raw as raw

            self._text = {}
            tp = self.page.get_textpage()
            try:
                for obj in self.page.get_objects(max_depth=1):
                    mc = raw.FPDFPageObj_GetMarkedContentID(obj.raw)
                    if mc < 0 or obj.type != 1:  # 1 = FPDF_PAGEOBJ_TEXT
                        continue
                    n = raw.FPDFTextObj_GetText(obj.raw, tp.raw, None, 0)
                    buf = ctypes.create_string_buffer(n * 2 + 2)
                    raw.FPDFTextObj_GetText(obj.raw, tp.raw, ctypes.cast(buf, ctypes.POINTER(ctypes.c_ushort)), n * 2)
                    self._text[mc] = self._text.get(mc, "") + buf.raw.decode("utf-16-le", "ignore").rstrip("\x00")
            finally:
                tp.close()
        return self._text.get(mcid, "")


def _mcids(el) -> list[int]:  # noqa: ANN001
    import pypdfium2.raw as raw

    out = [raw.FPDF_StructElement_GetMarkedContentIdAtIndex(el, i)
           for i in range(raw.FPDF_StructElement_GetMarkedContentIdCount(el))]
    return [m for m in out if m >= 0]


def _children(el) -> list:  # noqa: ANN001
    import pypdfium2.raw as raw

    kids = [raw.FPDF_StructElement_GetChildAtIndex(el, i) for i in range(raw.FPDF_StructElement_CountChildren(el))]
    return [k for k in kids if k]


def _el_text(el, pg: _Page, limit: int = 60, depth: int = 0) -> str:  # noqa: ANN001
    s = "".join(pg.text(m) for m in _mcids(el))
    if len(s) < limit and depth < 6:
        for k in _children(el):
            s += _el_text(k, pg, limit, depth + 1)
            if len(s) >= limit:
                break
    return s


_CAPTION_TYPES = {"Caption", "P", "Span", "Div", "Part"}
_NOT_GROUP = {"Document", "Sect", "Art", "Part", "Div", "NonStruct"}  # 부모 글자를 캡션으로 보지 않을 묶음


def _caption_num(kids: list, i: int, parent, pg: _Page) -> str | None:  # noqa: ANN001
    """Figure 요소 옆(보통 바로 뒤) 캡션 요소의 "Figure N" 번호."""
    import pypdfium2.raw as raw

    for j in (i + 1, i + 2, i - 1):
        if 0 <= j < len(kids) and _wstr(raw.FPDF_StructElement_GetType, kids[j]) in _CAPTION_TYPES:
            m = _LABEL_RE.match(_el_text(kids[j], pg))
            if m:
                return m.group(1)
    if parent is not None and _wstr(raw.FPDF_StructElement_GetType, parent) not in _NOT_GROUP:
        m = _LABEL_RE.match(_el_text(parent, pg))  # Figure 묶음 요소 = 그림 + 캡션
        if m:
            return m.group(1)
    return None


def read_alts(pdf: str | Path) -> list[Alt]:
    """원본 PDF의 Figure 요소 중 설명이 달린 것. 태그 없는 PDF면 []."""
    import pypdfium2.raw as raw

    from narani import pdfio

    out: list[Alt] = []
    with pdfio.open_document(str(pdf)) as doc:
        if not raw.FPDFCatalog_IsTagged(doc.raw):
            return []
        for pi in range(len(doc)):
            page = doc[pi]
            tree = raw.FPDF_StructTree_GetForPage(page.raw)
            if not tree:
                continue
            try:
                pg = _Page(page)

                def walk(el, parent, depth: int) -> None:  # noqa: ANN001
                    kids = _children(el)
                    for i, k in enumerate(kids):
                        if _wstr(raw.FPDF_StructElement_GetType, k) == "Figure":
                            text, num = clean(_wstr(raw.FPDF_StructElement_GetAltText, k)
                                              or _wstr(raw.FPDF_StructElement_GetActualText, k))
                            if text:
                                out.append(Alt(page=pi + 1, bbox=pg.bbox(_mcids(k)), text=text,
                                               num=num or _caption_num(kids, i, el, pg)))
                        if depth < 40:
                            walk(k, el, depth + 1)

                for i in range(raw.FPDF_StructTree_CountChildren(tree)):
                    top = raw.FPDF_StructTree_GetChildAtIndex(tree, i)
                    if top:
                        walk(top, None, 0)
                        if _wstr(raw.FPDF_StructElement_GetType, top) == "Figure":  # 최상위가 곧 Figure
                            text, num = clean(_wstr(raw.FPDF_StructElement_GetAltText, top))
                            if text:
                                out.append(Alt(page=pi + 1, bbox=pg.bbox(_mcids(top)), text=text, num=num))
            finally:
                raw.FPDF_StructTree_Close(tree)
    return out


# --- 짝짓기 -------------------------------------------------------------


def _inter(a: tuple, b: tuple) -> float:
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def _area(a: tuple) -> float:
    return max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])


def pair(alts: list[Alt], figs: list[Fig]) -> dict[str, tuple[list[Alt], str]]:
    """{그림 파일명: ([설명 요소…], 'bbox'|'caption')}. 못 짝지은 요소는 빠진다."""
    got: dict[str, list[Alt]] = {}
    via: dict[str, str] = {}
    rest: list[Alt] = []
    placed = [f for f in figs if f.page and f.bbox]
    for a in alts:
        hits = []
        if a.bbox:
            for f in placed:
                if f.page != a.page:
                    continue
                ov = _inter(a.bbox, f.bbox)
                if not ov:
                    continue
                e_in, f_in = ov / max(1e-6, _area(a.bbox)), ov / max(1e-6, _area(f.bbox))
                if max(e_in, f_in) >= _OVERLAP:
                    hits.append((f, e_in, f_in))
        if not hits:
            rest.append(a)
            continue
        same = [h for h in hits if a.num and h[0].num == a.num]
        pieces = [h for h in hits if h[2] >= _OVERLAP]  # 요소 안에 든 그림 = 쪼개진 조각
        if same:
            targets = [same[0][0]]
        elif len(pieces) > 1:
            targets = [h[0] for h in pieces]
        else:
            targets = [max(hits, key=lambda h: h[1] * h[2])[0]]
        for f in targets:
            got.setdefault(f.name, []).append(a)
            via[f.name] = "bbox"
    by_num: dict[str, Fig] = {}
    for f in figs:
        if f.num and f.num not in by_num:
            by_num[f.num] = f
    for a in rest:
        f = by_num.get(a.num or "")
        if f is not None and via.get(f.name) != "bbox":  # 자리로 붙은 그림엔 번호로 덧붙이지 않는다
            got.setdefault(f.name, []).append(a)
            via[f.name] = "caption"
    return {k: (v, via[k]) for k, v in got.items()}


def join(parts: list[Alt]) -> str:
    """한 그림에 붙은 설명들 — 쪽·위치 순으로, 같은 글은 한 번만."""
    seen: list[str] = []
    for a in sorted(parts, key=lambda a: (a.page, a.bbox[1] if a.bbox else 0.0)):
        if a.text not in seen:
            seen.append(a.text)
    return "\n\n".join(seen)


# --- 그림 목록·저장 -----------------------------------------------------


def _digest(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def _figures(wd: WorkDir) -> tuple[list[Fig], dict[str, str], dict[str, str]]:
    """(out 그림들, out 이름 → extract 이름, out 이름 → 바이트 해시). 수식 크롭은 그림이 아니다."""
    from narani import figure_fix

    if not wd.out_images.is_dir():
        return [], {}, {}
    src: dict[str, str] = {}
    if wd.extract_images.is_dir():
        for f in sorted(wd.extract_images.iterdir()):
            if f.is_file() and not f.name.startswith("formula-"):
                src.setdefault(_digest(f.read_bytes()), f.name)
    places = figure_fix.load(wd)
    figs, ext, digs = [], {}, {}
    for f in sorted(wd.out_images.iterdir()):
        if not f.is_file() or f.name.startswith("formula-") or f.name.startswith("."):
            continue
        d = _digest(f.read_bytes())
        digs[f.name] = d
        fig = Fig(name=f.name, num=_fig_num(f.name))
        e = src.get(d)
        if e:
            ext[f.name] = e
            rec = places.get(e) or {}
            if rec.get("page") and rec.get("bbox"):
                fig.page, fig.bbox = int(rec["page"]), tuple(rec["bbox"])
        figs.append(fig)
    return figs, ext, digs


def _key(pdf: str | None, digs: dict[str, str], wd: WorkDir) -> str:
    """입력 열쇠 — 이것이 같으면 다시 짝지어도 같은 결과다."""
    parts = [f"v{VERSION}"]
    if pdf:
        try:
            st = Path(pdf).stat()
            parts.append(f"{pdf}:{st.st_size}:{st.st_mtime_ns}")
        except OSError:
            parts.append(f"{pdf}:missing")
    parts += [f"{k}={v}" for k, v in sorted(digs.items())]
    try:
        parts.append(_digest(wd.figures_json.read_bytes()))
    except OSError:
        pass
    return _digest("\n".join(parts).encode())


def _read(wd: WorkDir) -> dict:
    try:
        data = json.loads(wd.figure_alts_json.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — 없거나 깨졌으면 기록 없음
        return {}
    return data if isinstance(data, dict) else {}


def build(wd: WorkDir, locate: bool = False) -> dict:
    """짝지어 `figure_alts.json`에 쓴다. locate면 자리를 모르는 그림을 PDF에서 찾아(템플릿 매칭, 그림당 ~1초)
    `figures.json`에 남긴 뒤 짝짓는다 — 옛 변환이라 자리 기록이 없는 논문용."""
    from narani import figure_fix

    pdf = figure_fix.source_pdf(wd)
    figs, ext, digs = _figures(wd)
    alts: list[Alt] = []
    if pdf and Path(pdf).is_file() and figs:
        try:
            alts = read_alts(pdf)
        except Exception:  # noqa: BLE001 — 구조 트리가 깨진 PDF: 설명 없이 간다
            alts = []
    if locate and alts:
        for f in figs:
            if f.page is None and f.name in ext:
                spot = figure_fix.where(wd, ext[f.name])  # 확실한 자리는 figures.json에 남는다
                if spot and not spot.get("guess"):
                    f.page, f.bbox = int(spot["page"]), tuple(spot["bbox"])
    paired = pair(alts, figs)
    data = {
        "version": VERSION,
        "key": _key(pdf, digs, wd),
        "located": bool(locate),
        "pdf_alts": len(alts),
        "unpaired": sum(1 for a in alts if not any(a in parts for parts, _ in paired.values())),
        "figures": {name: {"alt": join(parts), "via": how} for name, (parts, how) in sorted(paired.items())},
    }
    wd.extract.mkdir(parents=True, exist_ok=True)
    # 뷰어와 에이전트가 같은 논문을 동시에 짝지을 수 있다 — 임시 파일은 스레드마다 따로
    tmp = wd.figure_alts_json.with_name(f"{wd.figure_alts_json.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(wd.figure_alts_json)
    return data


def ensure(wd: WorkDir) -> dict:
    """기록이 지금 입력과 맞으면 그대로, 아니면 (템플릿 매칭 없이) 다시 짝짓는다."""
    from narani import figure_fix

    data = _read(wd)
    if data.get("version") == VERSION:
        _, _, digs = _figures(wd)
        if data.get("key") == _key(figure_fix.source_pdf(wd), digs, wd):
            return data
    return build(wd)


def needs_locate(data: dict) -> bool:
    """자리를 찾아 다시 짝지으면 더 붙을 설명이 남았나 — 아직 찾아보지 않았고, 못 짝지은 설명이 있다."""
    return bool(data) and not data.get("located") and int(data.get("unpaired") or 0) > 0


def alts(wd: WorkDir) -> dict[str, str]:
    """{out 그림 파일명: 설명} — 저장된 기록만 읽는다 (짝짓지 않는다)."""
    figs = _read(wd).get("figures") or {}
    return {k: v["alt"] for k, v in figs.items() if isinstance(v, dict) and v.get("alt")}
