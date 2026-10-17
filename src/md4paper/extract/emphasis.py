"""본문 강조 복원 — Docling이 버리는 굵게·기울임을 PDF 글꼴로 되살려 **…** / *…* 로 표시한다.

Docling의 PDF 경로는 글자의 글꼴을 마크다운까지 넘기지 않는다(formatting은 Word·HTML 입력에서만 채운다).
그런데 PDF 텍스트 레이어에는 답이 있다: 글자마다 글꼴이 있고, 글꼴은 이름(LinLibertineTI·Times-Bold·
CMBX10)과 서술자(굵기·기울기)로 굵게/기울임을 알려 준다. 본문 글꼴과 다른 쪽만 강조로 본다.

1) 페이지 글자를 스트림 순서로 이어 '문서 글자열'을 만들고 글자마다 강조 비트를 단다.
2) 마크다운 본문 줄마다 같은 정규화로 글자열을 만들어 문서 글자열에서 자리를 찾고(앞·가운데·끝 닻이
   한 군데로 모일 때만), 그 창 안에서 SequenceMatcher로 글자를 맞대어 비트를 옮겨 온다.
3) 비트를 **낱말 단위**로 다잡는다 — 낱말 글자 과반이 강조면 낱말 전체. 같은 강조가 이어지는 낱말은
   한 구간으로 합치고, 구간 끝의 구두점은 PDF에서도 같은 강조일 때만 안에 둔다.
제목·캡션·표·수식·참고문헌·각주 목록·앞부분은 건드리지 않는다. 확신이 없으면 그대로 둔다.
"""

from __future__ import annotations

import re
import statistics
import unicodedata
from difflib import SequenceMatcher

from md4paper.extract.inline_math import _MATH_FONT, _PROTECT, _page_glyphs, norm

BOLD, ITALIC = 1, 2
_MARK = {BOLD: "**", ITALIC: "*", BOLD | ITALIC: "***"}

# 본문 줄이 아닌 것 — 제목·캡션·그림·표·수식·HTML·코드
_SKIP_LINE = re.compile(
    r"^\s*(?:#|\||!\[|\$\$|<|```|~~~|\\\[)"
    r"|^\s*(?:<[^>]+>\s*)?\**\s*(?:Figure|Fig\.?|Table|Tab\.?)\s+(?:\d|[IVXLC]+\b)"
    r"|^\s*(?:[-*+]\s+)?\(?[a-z]\)(?:\s|$)",  # 부그림 캡션 "(a) …"
    re.I,
)
_LIST_PREFIX = re.compile(r"^\s*(?:[-+]\s+|\d+[.)]\s+|>\s*)")
# 강조를 넣지 않는 절 — 참고문헌·각주 목록(이미 **N.** 표기가 있다)
_NO_EMPH_HDR = re.compile(r"^#{1,6}\s+(?:references|bibliography|참고문헌|footnotes)\b", re.I)
# 본문이 시작되는 헤딩 — 이 앞(제목·저자·소속·초록)은 앞부분 정규화의 몫이라 건드리지 않는다.
# 앞부분 정규화는 별표로 시작하는 블록을 저자 주석으로 보므로 거기에 표시를 넣으면 안 된다.
# 번호·Introduction 헤딩이 없으면 Abstract 헤딩 뒤부터, 그것도 없으면 아무 데도 넣지 않는다.
_BODY_HDR = re.compile(r"^#{1,6}\s+(?:\d|[IVX]+\.?\s|introduction\b)", re.I)
_ABSTRACT_HDR = re.compile(r"^#{1,6}\s+abstract\b", re.I)
_TAG = re.compile(r"<[^>]+>")

_ANCHOR = 20  # 줄 자리 찾기 닻 길이
_MIN_FLAT = 8  # 이보다 짧은 줄은 자리를 확신할 수 없다
_MIN_MATCH = 0.85  # 줄 글자 중 이만큼은 PDF와 맞아야 비트를 믿는다
_SPLIT = re.compile(r"[^\s\-‐–—/]+")  # 낱말 — 공백·하이픈·대시·빗금에서 끊는다
_OPENERS, _CLOSERS = "([{“‘\"'", ")]}”’\"'"


def _norm_char(ch: str) -> str:
    """문서 글자 하나를 마크다운 쪽 norm()과 같은 규칙으로."""
    if unicodedata.category(ch) == "Cc" or ch == "\xad":
        return ""  # pdfium의 줄바꿈 하이픈 자리(\x02)·소프트 하이픈
    return norm(ch)[0]


def _doc_stream(pdf) -> tuple[str, list[int]]:  # noqa: ANN001 — 경로 | bytes
    """문서 전체의 (정규화 글자열, 글자별 강조 비트). 비트는 본문 글꼴 기준으로 상대화한다."""
    from md4paper import pdfio

    with pdfio.open_document(pdf) as doc:
        pages = [_page_glyphs(doc[pi].get_textpage()) for pi in range(len(doc))]
    fonts = [g for gs in pages for g in gs if g.ch.isalpha() and not _MATH_FONT.search(g.font)]
    if not fonts:
        return "", []
    body = statistics.mode(g.font for g in fonts)
    base = next(g.style for g in fonts if g.font == body)
    chars: list[str] = []
    bits: list[int] = []
    for gs in pages:
        for g in sorted(gs, key=lambda g: g.idx):
            s = _norm_char(g.ch)
            bit = 0 if _MATH_FONT.search(g.font) else g.style & ~base
            for c in s:
                chars.append(c)
                bits.append(bit)
    return "".join(chars), bits


def _flat_line(line: str, start: int) -> tuple[str, list[int]]:
    """줄의 정규화 글자열과 원문 위치 — 수식·코드·HTML 태그 안은 뺀다(PDF 글자와 모양이 다르다)."""
    blocked = bytearray(len(line))
    for rx in (_PROTECT, _TAG):
        for m in rx.finditer(line):
            blocked[m.start():m.end()] = b"\x01" * (m.end() - m.start())
    out: list[str] = []
    pos: list[int] = []
    for i in range(start, len(line)):
        if blocked[i]:
            continue
        for c in norm(line[i])[0]:
            out.append(c)
            pos.append(i)
    return "".join(out), pos


def _locate(flat: str, stream: str) -> tuple[int, int] | None:
    """줄 글자열이 문서 글자열의 어디쯤인지 — 닻 여럿이 한 자리를 가리킬 때만 (창 시작, 끝)."""
    n = len(flat)
    k = min(_ANCHOR, n)
    offsets = sorted({0, (n - k) // 4, (n - k) // 2, 3 * (n - k) // 4, n - k})
    starts: list[int] = []
    for off in offsets:
        a = flat[off:off + k]
        hit = stream.find(a)
        if hit == -1 or stream.find(a, hit + 1) != -1:
            continue  # 없거나 여러 군데 — 이 닻은 증거가 못 된다
        starts.append(hit - off)
    if not starts:
        return None
    mid = statistics.median(starts)
    agree = [s for s in starts if abs(s - mid) <= max(40, n // 2)]
    if len(agree) * 2 <= len(starts):
        return None  # 닻끼리 엇갈린다 — 같은 문장이 여러 번 나오는 경우 등
    slack = max(60, n // 4)
    return max(0, min(agree) - 30), min(len(stream), max(agree) + n + slack)


def _line_bits(flat: str, stream: str, bits: list[int]) -> list[int | None] | None:
    """줄 글자마다의 강조 비트 (맞대지 못한 글자는 None). 자리를 못 찾거나 덜 맞으면 None."""
    if len(flat) < _MIN_FLAT:
        return None
    win = _locate(flat, stream)
    if win is None:
        return None
    lo, hi = win
    sm = SequenceMatcher(None, flat, stream[lo:hi], autojunk=False)
    out: list[int | None] = [None] * len(flat)
    matched = 0
    for a, b, size in sm.get_matching_blocks():
        if size < 3:
            continue  # 우연히 맞는 한두 글자는 믿지 않는다
        matched += size
        for t in range(size):
            out[a + t] = bits[lo + b + t]
    return out if matched >= _MIN_MATCH * len(flat) else None


def _token_style(line: str, s: int, e: int, at: dict[int, int]) -> int | None:
    """낱말의 강조 — 글자(영숫자) 과반 기준. 판단할 글자가 없으면 None(중립)."""
    known = [at[i] for i in range(s, e) if i in at and line[i].isalnum()]
    if not known:
        return None
    style = 0
    for bit in (BOLD, ITALIC):
        if sum(1 for b in known if b & bit) * 2 > len(known):
            style |= bit
    return style


def _trim(line: str, s: int, e: int, style: int, at: dict[int, int]) -> tuple[int, int]:
    """구간 양끝의 구두점 정리 — 짝 없는 괄호·따옴표는 밖으로, 나머지는 PDF에서도 같은 강조일 때만 안에."""
    def keep(i: int, edge: str) -> bool:
        ch = line[i]
        if ch.isalnum():
            return True
        if at.get(i) != style:
            return False
        inner = line[s:e]
        if edge == "start" and ch in _OPENERS:
            return _CLOSERS[_OPENERS.index(ch)] in inner[1:]
        if edge == "end" and ch in _CLOSERS:
            return _OPENERS[_CLOSERS.index(ch)] in inner[:-1]
        return True

    while s < e and not keep(s, "start"):
        s += 1
    while e > s and not keep(e - 1, "end"):
        e -= 1
    return s, e


_FRAGMENT = 2  # 이 글자 수 이하의 강조 조각은 낱말이 아니라 글자 강조(머리글자 등)


def _merge_fragments(line: str, runs: list[list[int]], toks: list) -> list[list[int]]:
    """맞붙은 다른 강조 구간 중 한쪽이 글자 한두 개짜리 조각이면 공통 강조로 합친다.

    머리글자만 굵게 한 이름("**Pr**ompt **O**ptimization", 기울임 안의 굵은 글자)을 Docling이
    글꼴 경계에서 띄어 써 "***Pr*** *ompt* ***O*** *ptimization*"처럼 조각나는 것을 막는다.
    """
    out: list[list[int]] = []
    for r in runs:
        if out:
            p = out[-1]
            first_tok = next(k for k, t in enumerate(toks) if t[0] == r[0])
            common = p[2] & r[2]
            small = min(sum(c.isalnum() for c in line[x[0]:x[1]]) for x in (p, r)) <= _FRAGMENT
            if p[3] == first_tok - 1 and common and (small or p[2] == r[2]):
                p[1], p[2], p[3] = r[1], common, r[3]
                continue
        out.append(list(r))
    return out


def _emphasize_line(line: str, start: int, at: dict[int, int]) -> tuple[str, int]:
    """강조 비트(at: 원문 위치 → 비트)를 낱말로 다잡아 표시를 넣은 줄과 넣은 구간 수."""
    blocked = [(m.start(), m.end()) for rx in (_PROTECT, _TAG) for m in rx.finditer(line)]
    toks: list[tuple[int, int, int | None]] = []  # (시작, 끝, 강조|None) — -1은 넘을 수 없는 벽
    for m in _SPLIT.finditer(line, start):
        s, e = m.start(), m.end()
        if any(bs < e and s < be for bs, be in blocked):
            toks.append((s, e, -1))  # 수식·태그가 낀 낱말은 구간을 끊는다
        else:
            toks.append((s, e, _token_style(line, s, e, at)))
    # 중립 낱말(구두점·기호만)은 양옆이 같은 강조일 때만 그 강조에 낀다
    styles = [t[2] for t in toks]
    for k, st in enumerate(styles):
        if st is None:
            prev = next((x for x in reversed(styles[:k]) if x is not None), 0)
            nxt = next((x for x in styles[k + 1:] if x is not None), 0)
            styles[k] = prev if prev == nxt and prev > 0 else 0
    # 같은 강조가 이어지는 낱말들을 한 구간으로 (사이의 공백·하이픈째)
    runs: list[list[int]] = []  # [시작, 끝, 강조, 마지막 낱말 번호]
    for k, ((s, e, _), st) in enumerate(zip(toks, styles)):
        if st <= 0:
            continue
        if runs and runs[-1][2] == st and runs[-1][3] == k - 1:
            runs[-1][1], runs[-1][3] = e, k
        else:
            runs.append([s, e, st, k])
    runs = _merge_fragments(line, runs, toks)
    edits: list[tuple[int, int, int]] = []
    for s, e, st, _ in runs:
        s, e = _trim(line, s, e, st, at)
        if s < e and any(c.isalnum() for c in line[s:e]):
            edits.append((s, e, st))
    for s, e, st in reversed(edits):
        mk = _MARK[st]
        line = line[:s] + mk + line[s:e] + mk + line[e:]
    return line, len(edits)


def apply(md: str, stream: str, bits: list[int]) -> tuple[str, int]:
    """마크다운 본문 줄에 강조 표시를 넣는다. 반환: (새 마크다운, 넣은 구간 수)."""
    if not stream:
        return md, 0
    lines = md.split("\n")
    start_hdr = _BODY_HDR if any(_BODY_HDR.match(ln) for ln in lines) else _ABSTRACT_HDR
    in_body = skip_section = fence = False
    total = 0
    for n, line in enumerate(lines):
        st = line.lstrip()
        if st.startswith(("```", "~~~")):
            fence = not fence
            continue
        if fence:
            continue
        if st.startswith("#"):
            in_body = in_body or bool(start_hdr.match(st))
            skip_section = bool(_NO_EMPH_HDR.match(st))
            continue
        if not in_body or skip_section or not st or _SKIP_LINE.match(line):
            continue
        outside = _PROTECT.sub("", _TAG.sub("", line))
        if "*" in outside:
            continue  # 이미 별표가 있는 줄 — 새 표시가 엉뚱한 짝을 만들 수 있다
        m = _LIST_PREFIX.match(line)
        start = m.end() if m else 0
        flat, pos = _flat_line(line, start)
        got = _line_bits(flat, stream, bits)
        if got is None or not any(got):
            continue
        at = {pos[i]: b for i, b in enumerate(got) if b is not None}
        lines[n], k = _emphasize_line(line, start, at)
        total += k
    return "\n".join(lines), total


def restore(md: str, pdf) -> tuple[str, int]:  # noqa: ANN001
    """PDF 글꼴로 본문 강조를 되살린다. 텍스트 레이어를 못 읽으면 그대로."""
    try:
        stream, bits = _doc_stream(pdf)
    except Exception:  # noqa: BLE001 — 안전망: 못 하면 원문 그대로
        return md, 0
    return apply(md, stream, bits)
