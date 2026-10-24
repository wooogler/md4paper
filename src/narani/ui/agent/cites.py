"""에이전트 답의 인용 마커 해석 + 칩 렌더 — 여러 논문을 가로지르는 인용.

`ui/chat.py`의 resolve_citations/render_html을 옮겨 와 **다중 논문**으로 넓혔다. 세션이 전역이라
한 답이 지금 논문과 다른 논문을 함께 인용할 수 있다.

마커 세 가지:
- `[[rN]]` — 지금 화면의 논문의 정렬 행 N
- `[[tok:rN]]` — 다른 논문(tok = 12자리 wd 토큰)의 행 N
- `[[a<id>]]` — 독자 메모(annotations의 id, 'a'로 시작)

허용 집합(tool이 실제로 돌려준 `(token, row)`와 메모 id) 밖의 마커는 모델의 환각이므로 지운다.
번호는 논문을 가리지 않고 등장 순서로 1, 2, 3…을 공유한다.

마크다운 렌더는 commonmark에 **한글 친화 강조**를 얹는다(`_cjk_emphasis`). commonmark는
`**"Study I"**이`처럼 닫는 `**` 안쪽이 문장부호이고 바깥이 글자(조사)면 강조를 닫지 못한다 —
영어는 그 자리에 공백이 오지만 한국어는 조사가 바로 붙는다. 그래서 `*` 구분자 바로 바깥 글자가
한글·한자·가나(또는 인용 칩 자리표)이면 그 글자를 문장부호처럼 쳐서 여닫기를 판단한다
(markdown-cjk-friendly와 같은 생각). 닫는 표시 뒤에 U+200B를 끼우는 전처리로는 안 된다 —
U+200B는 markdown-it에게 공백도 문장부호도 아닌 Cf라 같은 이유로 닫히지 않는다.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from html import escape

from markdown_it.common.utils import isMdAsciiPunct, isPunctChar, isWhiteSpace
from markdown_it.rules_inline.state_inline import Delimiter, Scanned

# 토큰 형식(12자리 소문자 hex)을 먼저 시도한다 — 'a'로 시작하는 토큰이 메모 태그로 읽히지 않게.
MARKER_RE = re.compile(
    r"\[\[\s*(?:(?:([0-9a-f]{12})\s*:\s*)?[rR]\s*(\d+)|([aA][A-Za-z0-9_.\-]*))\s*\]\]")
_PH_OPEN, _PH_CLOSE = "", ""   # 사용자 정의 영역 — 마크다운 렌더를 통과시킬 자리표
_PH_RE = re.compile(_PH_OPEN + r"(\d+)" + _PH_CLOSE)
_TAG_BAD_RE = re.compile(r"[^A-Za-z0-9_.\-]")

RowRef = tuple[str, int]     # (논문 토큰, 행)
Ref = RowRef | str           # 행 또는 메모 태그


def note_tag(note_id: str) -> str:
    """메모 id → 프롬프트·인용 마커에 쓸 태그. 항상 `a`로 시작하고 마커 문법을 깨지 않는다."""
    s = _TAG_BAD_RE.sub("", str(note_id))[:64]
    return s if s[:1] in ("a", "A") else "a" + s


def row_label(token: str, row: int, current: str | None) -> str:
    """tool 결과에 붙일 행 표기 — 지금 논문이면 `rN`, 다른 논문이면 `tok:rN`."""
    return f"r{row}" if token == current else f"{token}:r{row}"


def _ref_of(m: re.Match, current: str | None) -> Ref | None:
    """마커 → 참조 키. `[[rN]]`은 지금 논문으로 풀린다 (지금 논문이 없으면 None = 버림)."""
    if m.group(2) is not None:
        tok = m.group(1) or current
        return (tok, int(m.group(2))) if tok else None
    return m.group(3)


def resolve_citations(text: str, allowed_rows: Iterable[RowRef],
                      allowed_notes: Iterable[str] = (),
                      current: str | None = None) -> tuple[str, list[Ref]]:
    """마커를 정리한다 → (정리된 텍스트, 등장 순서의 참조 목록).

    참조 목록에는 `(token, row)`와 메모 태그(str)가 섞여 있고 번호를 공유한다.
    `allowed_notes`는 메모 **id**다(태그로 바꿔 비교한다). 허용 밖 마커는 지운다.
    정리된 텍스트의 마커는 지금 논문이면 `[[rN]]`, 아니면 `[[tok:rN]]`로 정규화된다.
    """
    rows = {(str(t), int(r)) for t, r in allowed_rows}
    tags = {note_tag(n) for n in allowed_notes}
    order: list[Ref] = []

    def sub(m: re.Match) -> str:
        ref = _ref_of(m, current)
        if ref is None or (ref not in rows if isinstance(ref, tuple) else ref not in tags):
            return ""
        if ref not in order:
            order.append(ref)
        if isinstance(ref, tuple):
            return f"[[{row_label(ref[0], ref[1], current)}]]"
        return f"[[{ref}]]"

    out = MARKER_RE.sub(sub, text or "")
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"[ \t]+([.,;:!?)])", r"\1", out)
    return out.strip(), order


# 한글 자모·음절, 한자, 가나, 사용자 정의 영역(인용 칩 자리표) — 강조 여닫기에서 문장부호처럼 친다
_CJK_RE = re.compile("[\u1100-\u11ff\u3040-\u30ff\u3130-\u318f\u3400-\u4dbf\u4e00-\u9fff"
                     "\uac00-\ud7a3\ue000-\uf8ff]")


def _scan_delims_cjk(state, start: int) -> Scanned:  # noqa: ANN001 — StateInline
    """`StateInline.scanDelims`(`*` 전용)에 한글 규칙을 더한 것 — 바깥 글자가 CJK면 문장부호 취급."""
    src, pos, end = state.src, start, state.posMax
    marker = src[start]
    last = src[start - 1] if start > 0 else " "     # 줄 처음·끝은 공백으로 친다 (원본과 같게)
    while pos < end and src[pos] == marker:
        pos += 1
    nxt = src[pos] if pos < end else " "

    def punct(ch: str) -> bool:
        return isMdAsciiPunct(ord(ch)) or isPunctChar(ch) or bool(_CJK_RE.match(ch))

    last_p, next_p = punct(last), punct(nxt)
    last_ws, next_ws = isWhiteSpace(ord(last)), isWhiteSpace(ord(nxt))
    left = not (next_ws or (next_p and not (last_ws or last_p)))
    right = not (last_ws or (last_p and not (next_ws or next_p)))
    # `*`는 단어 안에서도 강조할 수 있으므로(canSplitWord) flanking만으로 여닫는다
    return Scanned(left, right, pos - start)


def _emphasis_cjk(state, silent: bool) -> bool:  # noqa: ANN001 — StateInline
    """markdown-it의 emphasis 토큰화 규칙과 같되 `*`는 `_scan_delims_cjk`로 판단한다.

    `_`는 그대로 둔다 — `변수_이름_이다` 같은 글이 기울임으로 바뀌지 않게.
    """
    from markdown_it.rules_inline import emphasis

    marker = state.src[state.pos]
    if silent or marker != "*":
        return emphasis.tokenize(state, silent)
    scanned = _scan_delims_cjk(state, state.pos)
    for _ in range(scanned.length):
        token = state.push("text", "", 0)
        token.content = marker
        state.delimiters.append(Delimiter(marker=ord(marker), length=scanned.length,
                                          token=len(state.tokens) - 1, end=-1,
                                          open=scanned.can_open, close=scanned.can_close))
    state.pos += scanned.length
    return True


def markdown():  # noqa: ANN201 — MarkdownIt
    """답 렌더용 MarkdownIt — commonmark, raw HTML 끔, 한글 친화 강조."""
    from markdown_it import MarkdownIt

    # html=False — 답에 섞인 raw HTML(<script> 등)은 이스케이프한다 (commonmark 기본은 허용)
    md = MarkdownIt("commonmark", {"html": False})
    md.inline.ruler.at("emphasis", _emphasis_cjk)
    return md


def render_html(text: str, refs: list[Ref], notes: dict[str, dict] | None = None,
                current: str | None = None) -> str:
    """마크다운 → HTML + 인용 칩. raw HTML은 비활성이라 답 속 `<script>`는 이스케이프된다.

    `refs`는 resolve_citations가 준 참조 목록, `notes`는 메모 태그 → 메모 dict
    (`{id, row, token}` — 칩에 원래 id·행·논문을 실어 준다). 행 칩은 지금 논문이어도
    `data-token`을 싣는다 — 지금 논문인지는 프론트가 비교해 판단한다.
    """
    by_tag = notes or {}
    n_by_ref = {ref: i + 1 for i, ref in enumerate(refs)}

    def place(m: re.Match) -> str:
        ref = _ref_of(m, current)
        return f"{_PH_OPEN}{n_by_ref[ref]}{_PH_CLOSE}" if ref in n_by_ref else ""

    html = markdown().render(MARKER_RE.sub(place, text or ""))

    def chip(m: re.Match) -> str:
        n = int(m.group(1))
        ref = refs[n - 1]
        if isinstance(ref, tuple):
            tok = escape(ref[0], quote=True)
            return (f'<a class="chat-cite" data-kind="row" data-token="{tok}" '
                    f'data-row="{ref[1]}" data-n="{n}">{n}</a>')
        note = by_tag.get(ref, {})
        # data-id는 뷰어 하이라이트(mark[data-ids])와 맞춰야 하므로 **원래 id**를 넣는다
        return (f'<a class="chat-cite chat-cite-note" data-kind="note" '
                f'data-token="{escape(str(note.get("token") or ""), quote=True)}" '
                f'data-id="{escape(str(note.get("id") or ref), quote=True)}" '
                f'data-row="{int(note.get("row", 0) or 0)}" data-n="{n}">{n}</a>')

    return _PH_RE.sub(chip, html)
