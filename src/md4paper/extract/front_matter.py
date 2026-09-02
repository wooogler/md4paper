"""앞부분(front matter) 정규화 — LLM이 블록에 라벨을 매기고, 코드가 원본 블록만으로 재조립.

첫 페이지는 논문에서 포맷 편차가 가장 크다(ACM 2단·IEEE·SAGE·arXiv…). 다열 저자 그리드를
추출기가 잘못 읽으면 저자 일부가 헤더로, 나머지는 초록 뒤로 흩어지고 저작권/venue/ISBN 조각이
끼어든다. 규칙만으론 새 포맷마다 깨지므로 LLM을 하이브리드로 쓴다:

  LLM은 "어느 블록이 제목/저자/유지섹션/본문시작인지"만 판단(라벨) → 코드가 **원본 블록만으로**
  재조립. 출력 텍스트가 전부 원본에서 오므로 포맷에 강하고 환각(내용 조작) 위험이 없다.

키가 없거나 LLM 결과가 검증을 통과 못 하면 보수적 규칙(normalize_heuristic)으로 폴백한다.
어느 경로든 본문(첫 번호형/Introduction 섹션 이후)은 절대 건드리지 않는다.
"""

from __future__ import annotations

import re

from md4paper.extract.docling_backend import (
    _ADDR_HINT_RE,
    _AUTHOR_SEG_RE,
    _EMAIL_RE,
    AUTHOR_NOTE_ANCHOR,
    _split_blocks,
)
from md4paper.ir import AuthorEntry, FrontMatterLayout
from md4paper.llm.base import Provider

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_LINK_RE = re.compile(r"^\[([^\]]+)\]\(([^)]+)\)\s*$")
_PERSON_RE = re.compile(r"^[A-Z][A-Za-z.'\-]+(?:\s+[A-Z][A-Za-z.'\-]+){0,3}$")
_SECTION_WORDS = frozenset({
    "abstract", "introduction", "keywords", "index terms", "references", "acknowledgments",
    "acknowledgements", "ccs concepts", "ccs", "acm reference format", "acm reference format:",
    "background", "related work", "conclusion", "appendix", "contents",
})
_ISBN_RE = re.compile(r"^\s*97[89][\d\s\-/]+\s*$")
_JUNK_RE = re.compile(
    r"^\s*(ACM|ISBN|ACM\s+ISBN.*|Permission to make digital|This work is licensed|"
    r"©.*|Copyright held.*|.*\bhttps?://doi\.org\b.*)\s*$",
    re.I,
)
_VENUE_RE = re.compile(r"\b(CHI|UIST|CSCW|IEEE|Proceedings)\b.*\b(ACM|pages|\d{4})\b")
_STAR_RE = re.compile(r"^\s*[\*∗]")
_JUNK_HEADINGS = frozenset({"acm reference format", "acm reference format:"})
_MAX_FRONT = 50  # 이 안에서 첫 본문 섹션을 못 찾으면 front matter 판단 불가 → 손대지 않음
# 2단 조판 첫 페이지는 추출기가 좌우 단을 뒤집어 읽는 일이 있다 — Abstract 블록이 "1 Introduction"
# 뒤로 밀려난다. 본문 시작 추정 지점보다 이만큼 더 보여줘야 LLM이 그 블록을 지목할 수 있다.
_BODY_LOOKAHEAD = 8
_MAX_PULLED = 6  # 본문 뒤에서 앞으로 끌어올릴 수 있는 블록 수 상한 — 오판했을 때 피해를 제한


# --- 공통 판별 -------------------------------------------------------------
def _heading(block: str) -> tuple[int, str] | None:
    m = _HEADING_RE.match(block.strip())
    return (len(m.group(1)), m.group(2).strip()) if m else None


def _disp_url(text: str) -> tuple[str, str | None]:
    m = _LINK_RE.match(text.strip())
    return (m.group(1).strip(), m.group(2).strip()) if m else (text.strip(), None)


def _is_person(text: str) -> bool:
    disp, _ = _disp_url(text)
    disp = re.sub(r"[\*∗\d,]+$", "", disp).strip()  # 위첨자 소속번호·별표 제거
    return disp.lower().rstrip(":") not in _SECTION_WORDS and bool(_PERSON_RE.match(disp))


# 저자 이름 끝의 각주/소속 마커 — 추출·LLM 판단에 따라 붙었다 떨어졌다 하므로 코드에서 항상 뗀다.
# (표기 결정: 마커는 **버리고**, 그 마커가 가리키는 저자 주석 본문은 저자 블록 뒤에 그대로 보여준다.)
_AUTHOR_MARK_RE = re.compile(r"[\s,]*(?:[*∗＊†‡§¶‖¹²³⁴⁵⁶⁷⁸⁹⁰\d]+[\s,]*)+$")


def strip_author_mark(name: str) -> str:
    """저자 이름 끝의 각주 마커(∗ * † ‡ § ¶, 위첨자·소속 번호)를 뗀다. 결과가 비면 원본 유지."""
    stripped = _AUTHOR_MARK_RE.sub("", name).strip()
    return stripped or name.strip()


# 추출 단계가 저자 주석에 심어 둔 앵커 — 라벨이 없어도 이 블록만은 버리지 않고 저자 뒤에 붙인다.
_AUTHOR_NOTE_RE = re.compile(rf'^\s*<a id="{AUTHOR_NOTE_ANCHOR}-\d+">')


def _is_author_note(block: str) -> bool:
    """추출 단계에서 저자 주석으로 분류돼 앵커가 붙은 블록인지."""
    return bool(_AUTHOR_NOTE_RE.match(block.strip()))


def _is_affiliation(block: str) -> bool:
    s = block.strip()
    if "\n" in s or _heading(block):
        return False
    return bool(_EMAIL_RE.search(s)) and ("," in s or bool(_ADDR_HINT_RE.search(s)))


def _is_junk(block: str) -> bool:
    s = block.strip()
    if _JUNK_RE.match(s) or _ISBN_RE.match(s) or _VENUE_RE.search(s):
        return True
    if _STAR_RE.match(s) and len(s) < 200 and _EMAIL_RE.search(s) is None:
        return True  # 저자 각주(∗ … visiting scholar 등)
    return s in ("ACM", "ISBN")


def _is_body_start(block: str) -> bool:
    h = _heading(block)
    if not h:
        return False
    t = h[1].lstrip("·* ").strip()
    return bool(re.match(r"^\d", t)) or t.lower().startswith("introduction")


def _find_body_start(blocks: list[str]) -> int | None:
    idx = next((i for i in range(1, len(blocks)) if _is_body_start(blocks[i])), None)
    return idx if (idx is not None and idx <= _MAX_FRONT) else None


def _render_author(block: str) -> str:
    """저자 헤더 블록 렌더 — 굵게(더는 섹션 아님)."""
    disp, url = _disp_url(_heading(block)[1])
    disp = strip_author_mark(disp)
    return f"**[{disp}]({url})**" if url else f"**{disp}**"


def _author_lines(block: str) -> list[str]:
    """저자 블록 → 표시 줄들. 헤더는 굵게, 이메일 2개 이상 뭉친 줄은 저자별로 분리."""
    h = _heading(block)
    if h and _is_person(h[1]):
        return [_render_author(block)]
    if len(_EMAIL_RE.findall(block)) >= 2:
        return [s.strip() for s in _AUTHOR_SEG_RE.findall(block) if s.strip()]
    return [block.strip()]


# --- 규칙 기반(폴백) -------------------------------------------------------
def normalize_heuristic(md: str) -> str:
    """규칙만으로 앞부분 정리 — 저자 헤더 강등 + 흩어진 소속줄 수집 + boilerplate 제거.

    파편화 신호(저자 헤더 있거나 소속줄 ≥2)가 있을 때만 재구성한다(정상 논문 보호).
    """
    blocks = _split_blocks(md)
    if not blocks or not _heading(blocks[0]):
        return md
    end = _find_body_start(blocks)
    if end is None:
        return md

    authors: list[str] = []
    notes: list[str] = []  # 저자 주석 — 저자 블록 바로 뒤에 붙인다
    keep: list[str] = []
    n_head = n_affil = 0
    skip_body = False
    i = 1
    while i < end:
        b = blocks[i]
        h = _heading(b)
        if h:
            skip_body = h[1].lower().rstrip(":").strip() in _JUNK_HEADINGS
            if _is_person(h[1]):
                line = _render_author(b)
                if i + 1 < end and _is_affiliation(blocks[i + 1]):
                    line = f"{line} · {blocks[i + 1].strip()}"
                    i += 1
                authors.append(line)
                n_head += 1
            elif not skip_body:
                keep.append(b)
            i += 1
            continue
        if _is_author_note(b):
            notes.append(b)
        elif _is_affiliation(b):
            for seg in (s.strip() for s in _AUTHOR_SEG_RE.findall(b) if s.strip()):
                authors.append(seg)
                n_affil += 1
        elif _is_junk(b) or skip_body:
            pass
        else:
            keep.append(b)
        i += 1

    if n_head == 0 and n_affil <= 1:
        return md
    out = [blocks[0]]
    if authors:
        out.append("\n\n".join(authors))
    out.extend(notes)
    out.extend(keep)
    out.extend(blocks[end:])
    text = "\n\n".join(out)
    return text + "\n" if md.endswith("\n") else text


# --- LLM 라벨 + 코드 재조립 ------------------------------------------------
_SYSTEM = """You label the LEADING blocks of an academic paper's extracted markdown so the front
matter can be cleaned. A multi-column author grid was often mis-read, scattering authors and mixing in
copyright/venue/ISBN boilerplate.

You receive numbered blocks. Return, using ONLY the given indices:
- title: the index of the block holding the paper title.
- authors: indices of blocks that are author names or their affiliation/email lines, in natural reading
  order (top-to-bottom, left-to-right of the original author grid). Include every author block, even ones
  that appear far down, interleaved with boilerplate.
- sections: indices of LEGITIMATE front-matter section blocks to keep — Abstract, CCS Concepts, Keywords
  and the body blocks that belong to them — listed in the order they should be READ. Do NOT include
  copyright, venue, ISBN, DOI, "ACM Reference Format", or the author-note footnote here.
  A two-column first page is often read column-out-of-order, so these blocks may be scrambled: the
  "Abstract" heading and its text can land AFTER body_start, and a stray tail fragment of the abstract
  (a block starting mid-sentence, before body_start) can land before it. List them anyway, and list them
  in reading order — heading first, then its text, then any continuation fragment — even when that means
  an index after body_start comes before one that precedes it. Blocks you list from after body_start must
  be a CONTIGUOUS run starting at the section's own heading ("Abstract", "CCS Concepts", "Keywords"):
  never list an Introduction paragraph, and never skip over one to reach a later block.
- body_start: the index of the first MAIN body section (usually "Introduction" or "1 ...").
- authors_detail: the SAME authors, re-grouped one entry per person, in reading order. For each author give
  name, their email(s), and their affiliation line(s). A multi-column grid interleaves people, so attach each
  email/affiliation to the correct person. Copy text EXACTLY as it appears — never invent, correct, translate,
  or complete an email or affiliation. If you cannot tell whom an affiliation belongs to, omit authors_detail
  entirely (leave it empty); a partial or guessed grouping is worse than none.

Everything before body_start that you did not list is treated as boilerplate and dropped; everything
after body_start that you did not list stays where it is, untouched. Never invent
indices. If unsure whether a block is an author or boilerplate, and it contains an email, treat it as an
author."""


def _layout_max_tokens(n_front: int) -> int:
    """출력 토큰 상한 자동 산정 — authors_detail(저자별 이름·이메일·소속)이 출력 대부분을 차지한다.

    1024 고정이면 저자가 많은 논문(8명 이상)에서 JSON이 중간에 잘리고, 파싱 실패는
    조용한 규칙 폴백으로 이어져 저자 줄이 원문 그대로 남는다. 앞부분 블록 수에 비례시킨다.
    """
    return min(8192, max(2048, n_front * 320))


# 프롬프트에 넣을 블록당 최대 길이. 2단 조판 논문은 저자 여러 명이 한 블록에 흘러 나오는데
# (예: "A ∗ Google llion@… B ∗ Google nikip@… C ∗ …"), 여기서 자르면 뒤쪽 저자가 LLM에
# 아예 보이지 않아 조용히 누락되고 이메일도 중간에 잘린다. 앞부분 블록은 원래 짧아 비용 영향은 작다.
_BLOCK_CHARS = 800


def _llm_layout(provider: Provider, blocks: list[str], n_front: int) -> FrontMatterLayout:
    numbered = "\n".join(f"[{i}] {blocks[i].strip()[:_BLOCK_CHARS]}" for i in range(n_front))
    user = f"{numbered}\n\n(There are {n_front} leading blocks, indices 0..{n_front - 1}.)"
    return provider.parse(_SYSTEM, user, FrontMatterLayout,
                          max_tokens=_layout_max_tokens(n_front))


def _alnum(s: str) -> str:
    """영숫자만 남기고 소문자화 — 구두점·공백·줄바꿈 차이를 무시한 근거 대조용."""
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def _grounded_authors(entries: list[AuthorEntry], source: str) -> list[AuthorEntry] | None:
    """구조화 저자를 원문(저자 블록)에 대조 — 이름·이메일·소속이 전부 원문에 있어야 채택.

    하나라도 원문에 없으면(환각·오타·추정) None을 돌려 호출부가 규칙 재조립으로 폴백한다.
    이메일은 대소문자 무시 완전 포함, 이름·소속은 영숫자 정규화 후 부분 포함(구두점 차이 허용).
    """
    if not entries:
        return None
    src_alnum, src_low = _alnum(source), source.lower()
    clean: list[AuthorEntry] = []
    for e in entries:
        name = (e.name or "").strip()
        if not name or _alnum(name) not in src_alnum:
            return None
        emails = [em.strip() for em in e.emails if em.strip()]
        if any(em.lower() not in src_low for em in emails):
            return None
        affils = [a.strip() for a in e.affiliations if a.strip()]
        if any(_alnum(a) not in src_alnum for a in affils):
            return None
        clean.append(AuthorEntry(name=strip_author_mark(name), emails=emails, affiliations=affils))
    return clean


def _render_authors_detail(entries: list[AuthorEntry], parts: list[str] | None = None) -> str:
    """검증된 구조화 저자를 일관된 형식으로 — 저자마다 굵은 이름 / (선택)이메일 / (선택)소속.

    parts로 무엇을 표시할지 고른다(이름은 항상): "email", "affiliation". None이면 둘 다.
    한 저자 안은 마크다운 하드 브레이크('  \\n')로 줄을 나누고, 저자 사이는 빈 줄로 문단 분리.
    이름은 굵은 문단(헤더 아님)이라 섹션 트리·목차에 저자가 끼지 않는다.
    """
    show = ("email", "affiliation") if parts is None else parts
    out: list[str] = []
    for e in entries:
        lines = [f"**{e.name}**"]
        if "email" in show and e.emails:
            lines.append(" · ".join(e.emails))
        if "affiliation" in show:
            lines.extend(e.affiliations)
        out.append("  \n".join(lines))
    return "\n\n".join(out)


def _valid(layout: FrontMatterLayout, n_front: int, n_blocks: int) -> bool:
    def ok(i: int) -> bool:
        return isinstance(i, int) and 0 <= i < n_blocks
    if not ok(layout.title) or not ok(layout.body_start):
        return False
    if not (layout.title < layout.body_start <= n_blocks):
        return False
    if layout.body_start > _MAX_FRONT:
        return False
    if not layout.authors or not all(ok(i) and i < layout.body_start for i in layout.authors):
        return False
    # 섹션은 본문 시작 뒤에 있어도 된다(단 뒤집힘) — 단, LLM에 보여준 창(n_front) 안이어야 하고
    # 본문 시작 블록 자체는 옮길 수 없으며, 끌어올리는 개수도 제한한다.
    if not all(ok(i) and i < n_front and i != layout.body_start for i in layout.sections):
        return False
    return sum(1 for i in layout.sections if i > layout.body_start) <= _MAX_PULLED


# front matter 안의 그림(teaser)·캡션 블록 — boilerplate가 아니라 콘텐츠라 버리면 안 된다.
_FM_IMG_RE = re.compile(r"^\s*(?:<[^>]+>\s*)?!\[")  # 이미지 블록(앞에 <span> 앵커 허용)
_FM_CAP_RE = re.compile(r"^\s*(?:<[^>]+>\s*)?\**\s*(?:Figure|Fig\.?|Table|Tab\.?)\s+\d", re.I)


def _is_figure_block(block: str) -> bool:
    """이미지 참조 또는 Figure/Table 캡션 블록인지 (teaser 유실 방지용)."""
    first = block.strip().split("\n", 1)[0] if block.strip() else ""
    return bool(_FM_IMG_RE.match(first) or _FM_CAP_RE.match(first))


# 본문보다 앞에 올 수 있는 섹션 헤딩 — 뒤집힌 단을 되돌릴 때 "여기서부터 앞으로" 기준점이 된다.
_FRONT_SECTION_RE = re.compile(
    r"^(abstract|keywords?|author keywords|index terms|ccs concepts|ccs|general terms|"
    r"categories and subject descriptors)\b", re.I)


def _is_front_heading(block: str) -> bool:
    h = _heading(block)
    return bool(h and _FRONT_SECTION_RE.match(h[1].strip().lstrip("·* ")))


def _pullable(blocks: list[str], sections: list[int], body_start: int) -> set[int]:
    """본문 뒤에 놓인 섹션 블록 중 실제로 앞으로 되돌릴 것만 고른다.

    단이 뒤집히면 왼쪽 단이 **한 덩어리로** 밀리므로 되돌릴 대상은 "Abstract 같은 front matter
    헤딩 + 뒤따르는 연속 블록"이다. 그 모양이 아닌 지목(본문 문단을 초록으로 오인한 경우 등)은
    무시하고 본문에 그대로 둔다 — 라벨이 조금 틀려도 본문 순서가 흐트러지지 않는다.
    """
    runs: list[list[int]] = []
    for i in sorted({i for i in sections if i > body_start}):
        if runs and i == runs[-1][-1] + 1 and not _is_front_heading(blocks[i]):
            runs[-1].append(i)
        else:
            runs.append([i])
    return {i for run in runs if _is_front_heading(blocks[run[0]]) for i in run}


def _ordered_sections(sections: list[int], extra_fig: list[int]) -> list[int]:
    """유지할 front matter 블록의 출력 순서 — LLM이 준 읽기 순서를 그대로 쓴다.

    단이 뒤집힌 첫 페이지는 Abstract가 "1 Introduction" 뒤에 놓이므로 인덱스 정렬(sorted)로는
    복구할 수 없다. 라벨이 없는 그림(teaser) 블록만 인덱스 위치에 맞춰 끼워 넣는다.
    정상 논문(sections가 오름차순)에서는 결과가 종전과 같다.
    """
    out = list(dict.fromkeys(sections))
    for i in sorted(set(extra_fig)):
        pos = next((k for k, j in enumerate(out) if j > i), len(out))
        out.insert(pos, i)
    return out


def _build_llm(provider: Provider, md: str, parts: list[str] | None) -> tuple[str, list[AuthorEntry]] | None:
    """LLM 라벨 → 코드 재조립. (정규화 md, 검증된 구조화 저자) 반환. 검증 실패 시 None."""
    blocks = _split_blocks(md)
    # 제목이 첫 블록이 아닐 수 있다 — 저작권 고지·arXiv 스탬프·학회 배너가 제목 앞에 오는 논문이 흔하다.
    # (blocks[0]만 검사하면 그런 논문에서 LLM 경로가 통째로 건너뛰어져 저자 줄이 원문 그대로 남는다.)
    # LLM이 title 인덱스를 짚어주므로, 앞부분 어딘가에 헤더가 하나라도 있으면 진행한다.
    if not blocks or not any(_heading(b) for b in blocks[:_MAX_FRONT]):
        return None
    # LLM에 보낼 앞부분 범위 (본문 시작 추정 + 여유, 상한 캡)
    approx = _find_body_start(blocks) or _MAX_FRONT
    n_front = min(len(blocks), max(approx + _BODY_LOOKAHEAD, 8), _MAX_FRONT + _BODY_LOOKAHEAD)
    try:
        layout = _llm_layout(provider, blocks, n_front)
    except Exception:  # noqa: BLE001 — 실패 시 폴백
        return None
    if not _valid(layout, n_front, len(blocks)):
        return None

    out = [blocks[layout.title]]
    # 저자: LLM이 저자별로 재구성한 authors_detail이 원문 근거를 통과하면 일관된 형식으로 렌더,
    # 아니면(검증 실패·미제공) 기존처럼 원본 블록을 그대로 내보낸다(환각 없는 보수적 폴백).
    author_src = "\n\n".join(blocks[i] for i in layout.authors)
    grounded = _grounded_authors(layout.authors_detail, author_src) or []
    if grounded:
        out.append(_render_authors_detail(grounded, parts))
    else:
        author_lines: list[str] = []
        for i in layout.authors:
            author_lines.extend(_author_lines(blocks[i]))
        if author_lines:
            out.append("\n\n".join(author_lines))
    seen = {layout.title, *layout.authors}
    # 저자 주석(∗ …)은 라벨 유무와 무관하게 저자 바로 뒤에 붙인다 — 추출 단계가 앵커로 표시해 둔다.
    for i in range(min(layout.body_start, len(blocks))):
        if i not in seen and _is_author_note(blocks[i]):
            out.append(blocks[i])
            seen.add(i)
    # front matter의 그림(teaser)·캡션 블록은 라벨이 없어도 콘텐츠이므로 유지한다(유실 방지).
    # LLM이 sections에 안 넣은 첫 페이지 teaser가 boilerplate처럼 버려지던 문제를 막는다.
    extra_fig = [i for i in range(min(layout.body_start, len(blocks)))
                 if i not in seen and _is_figure_block(blocks[i])]
    pulled = _pullable(blocks, layout.sections, layout.body_start)
    for i in _ordered_sections(layout.sections, extra_fig):
        if i not in seen and (i < layout.body_start or i in pulled):
            out.append(blocks[i])
            seen.add(i)
    # 본문은 원본 순서 그대로. 단이 뒤집혀 본문 뒤로 밀려났던 front matter 블록(위에서 이미
    # 앞으로 옮긴 것)만 제외한다 — 그대로 두면 Abstract가 Introduction 중간에 또 나온다.
    out.extend(b for j, b in enumerate(blocks[layout.body_start:], layout.body_start)
               if j not in pulled)
    text = "\n\n".join(out)
    return (text + "\n" if md.endswith("\n") else text), grounded


def normalize_llm(provider: Provider, md: str) -> str | None:
    """LLM 라벨 → 코드 재조립 (문자열만). 검증 실패 시 None(호출부가 규칙 폴백)."""
    from md4paper import config

    r = _build_llm(provider, md, config.resolve_author_parts())
    return r[0] if r is not None else None


def normalize_authors(provider: Provider | None, md: str,
                      parts: list[str] | None = None) -> tuple[str, list[AuthorEntry]]:
    """정규화 + 구조화 저자 반환. LLM 성공 시 (md, authors), 실패/없으면 (규칙 md, [])."""
    if provider is not None:
        r = _build_llm(provider, md, parts)
        if r is not None:
            return r
    return normalize_heuristic(md), []


def normalize(provider: Provider | None, md: str) -> str:
    """앞부분 정규화 진입점(문자열) — provider 있으면 LLM(검증), 실패/없으면 규칙 폴백."""
    from md4paper import config

    return normalize_authors(provider, md, config.resolve_author_parts())[0]
