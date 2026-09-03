"""Docling 추출 백엔드 (기본).

born-digital 논문에서 실측 최고 품질: 유니코드 무손실(수학 이탤릭 보존), 2단 조판 읽기 순서 정확,
헤더 감지 최다, 그림 파일 추출. 라이선스 MIT.
"""

from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path

from md4paper.extract.reading_order import export_geometry, repair_reading_order
from md4paper.extract.text_clean import ExtractError, rewrite_image_refs
from md4paper.workdir import WorkDir


def available() -> bool:
    import importlib.util

    return importlib.util.find_spec("docling") is not None


def _short_name(path: Path, index: int) -> str:
    """docling의 image_000003_<64자해시>.png → img-03.png (읽기 쉬운 안정적 이름)."""
    return f"img-{index:02d}{path.suffix or '.png'}"


# 저자 블록 분리: 이메일 하나로 끝나는 저자 조각들
_EMAIL_RE = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")
_AUTHOR_SEG_RE = re.compile(r".*?[^\s@]+@[^\s@]+\.[^\s@]+")


def _split_author_block(md: str) -> str:
    """제목 다음 저자 블록이 한 줄로 뭉쳐 있으면 이메일 경계로 저자별 문단 분리.

    2단 조판에서 여러 저자(이름·소속·이메일)가 한 줄로 평탄화되는 문제를 완화한다.
    첫 헤딩 직후~다음 헤딩 전에서 이메일이 2개 이상인 문단에만 적용(오탐 방지).
    """
    lines = md.split("\n")
    first_h = next((i for i, ln in enumerate(lines) if ln.startswith("#")), None)
    if first_h is None:
        return md
    for i in range(first_h + 1, len(lines)):
        if lines[i].startswith("#"):  # 다음 섹션 도달 — 저자 블록 없음
            break
        if len(_EMAIL_RE.findall(lines[i])) >= 2:
            segs = [s.strip() for s in _AUTHOR_SEG_RE.findall(lines[i]) if s.strip()]
            tail = _AUTHOR_SEG_RE.sub("", lines[i]).strip()  # 마지막 이메일 뒤 잔여
            if tail:
                segs.append(tail)
            lines[i] = "\n\n".join(segs)
            break
    return "\n".join(lines)


# 이어지는 문단 재결합용 — 앞 블록이 종결부호 없이 letter/숫자/쉼표로 끝(문장 미완),
# 뒤 블록이 소문자/여는 괄호로 시작(= 앞 문장의 연속).
_CONT_END_RE = re.compile(r"[A-Za-z0-9,]$")
_CONT_START_RE = re.compile(r"^[a-z(]")
# 대문자로 이어지는 경우는 앞 블록에 **닫히지 않은 여는 괄호**가 남아 있을 때만 허용한다.
# 약어를 풀어 쓰는 삽입구("… we present NIRVANA (Naturalistic Interactions and" /
# "Replay of Voluntary …)")는 고유명사라 대문자로 시작한다. 괄호가 열린 채로 끝났다는 사실
# 자체가 문장이 안 끝났다는 증거라, '소문자로 시작' 만큼 강한 근거가 된다.
# (대문자 시작을 무조건 허용하면, 마침표를 잃은 평범한 문단 뒤의 **다른** 문단까지 삼킨다 —
#  같은 단에서 바로 아래 이어지는 두 문단은 기하 게이트를 그대로 통과하기 때문이다.)
_CONT_START_UPPER_RE = re.compile(r"^[A-Z]")
_ORDERED_LI_RE = re.compile(r"^\d+[.)]\s")


def _open_paren(text: str) -> bool:
    """블록이 **닫히지 않은 여는 괄호**를 남긴 채 끝났는지 — 문장이 안 끝났다는 구조적 증거."""
    depth = 0
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
    return depth > 0


def _continues(prev: str, nxt: str) -> bool:
    """뒤 블록이 앞 블록 문장의 연속으로 보이는지 (텍스트 근거만 — 기하 확인은 호출부에서)."""
    if _CONT_START_RE.match(nxt):
        return True
    return bool(_CONT_START_UPPER_RE.match(nxt)) and _open_paren(prev)
_NON_PROSE_START = ("#", "-", "*", "+", ">", "|", "!", "```", "~~~", "<", "$$")
# 앞 블록이 이 길이 이상일 때만 결합 → 저자명·소속 같은 짧은 라인 오결합 방지
# (진짜 컬럼 경계에서 끊긴 문단 조각은 컬럼이 거의 꽉 차 훨씬 길다).
_MIN_CONT_LEN = 60


def _is_prose_block(block: str) -> bool:
    """헤더·리스트·표·이미지·코드·인용·수식·이메일·URL이 아닌 일반 문단인지."""
    s = block.lstrip()
    if not s or s.startswith(_NON_PROSE_START) or _ORDERED_LI_RE.match(s):
        return False
    if _EMAIL_RE.match(s) or s.startswith(("http://", "https://", "www.")):
        return False  # 저자 블록의 이메일·URL 라인은 문단 연속이 아님
    return True


def _split_blocks(md: str) -> list[str]:
    """빈 줄 경계로 문단 블록 분할 (코드펜스는 통째로 유지)."""
    blocks: list[str] = []
    cur: list[str] = []
    fence = False
    for line in md.split("\n"):
        st = line.lstrip()
        if st.startswith(("```", "~~~")):
            fence = not fence
            cur.append(line)
        elif fence:
            cur.append(line)
        elif line.strip() == "":
            if cur:
                blocks.append("\n".join(cur))
                cur = []
        else:
            cur.append(line)
    if cur:
        blocks.append("\n".join(cur))
    return blocks


# 본문에 흘러든 연락처 footer — 2단 조판에서 1페이지 하단(컬럼 끝)의 저자 연락처가
# 읽기 순서상 본문(서론 등) 한가운데로 밀려 들어온다. Docling이 'text'로 태깅해 저작권
# 필터에도 안 걸린다. "Corresponding author"로 시작하는 블록 + 뒤따르는 주소/이메일까지 제거.
_CORRESP_RE = re.compile(r"^[#>*_\s]*Corresponding\s+author", re.I)
_ADDR_HINT_RE = re.compile(
    r"\b(Univers\w+|Institut\w*|College|Department|School|Tech|Laborator\w+|Lab|Center|Centre|"
    r"Inc\.?|LLC|Corp\w*|Company|USA|United States|Republic of)\b",
    re.I,
)


def _looks_contact(block: str) -> bool:
    """주소/소속 한 줄이거나 이메일 라인처럼 보이는지 (연락처 블록 구성요소)."""
    s = block.strip()
    if _EMAIL_RE.search(s):
        return True
    # 짧고, 쉼표로 나뉘며, 소속 키워드나 우편번호가 있는 주소 라인
    return len(s) <= 160 and "," in s and bool(_ADDR_HINT_RE.search(s) or re.search(r"\b\d{4,6}\b", s))


def _strip_contact_footer(md: str) -> str:
    """"Corresponding author: …" 연락처 블록(헤더+주소+이메일)을 본문에서 제거.

    헤더 블록 다음의 '연락처처럼 보이는' 블록만(주소/소속/이메일) 이메일을 만날 때까지 함께
    제거한다 — 본문 문단이 뒤따르면 헤더만 지운다(오제거 방지).
    문단 재결합(_join_broken_paragraphs)보다 먼저 돌려야, 이 블록에 갈라졌던 앞뒤 문단이
    인접해져 다시 이어붙는다("…rather" + "than emphasizing…").
    """
    blocks = _split_blocks(md)
    out: list[str] = []
    i = 0
    while i < len(blocks):
        if _CORRESP_RE.match(blocks[i]):
            end = i
            for k in range(i + 1, min(i + 4, len(blocks))):
                if not _looks_contact(blocks[k]):
                    break  # 본문 문단 등 — 연락처 아님
                end = k
                if _EMAIL_RE.search(blocks[k]):
                    break  # 이메일 라인까지가 연락처 블록
            i = end + 1
            continue
        out.append(blocks[i])
        i += 1
    text = "\n\n".join(out)
    return text + "\n" if md.endswith("\n") else text


# 그림 안 텍스트(차트 제목·축 이름)를 Docling이 헤더/문단으로 따로 뽑아내 섹션 트리·본문을 오염시킨다.
# 이 텍스트는 이미 래스터 이미지 안에 그려져 있으므로(중복) 그림의 일부로 보고 해당 줄을 제거한다.
# 대상: 번호 없는 짧은 헤더/줄 + 바로 인접(빈 줄만 사이)한 줄이 그림 요소(이미지/캡션/서브캡션).
_MD_HEADER_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_IMG_LINE_RE = re.compile(r"^\s*!\[")
_FIG_CAP_RE = re.compile(r"^\s*(?:<[^>]+>\s*)?(?:Figure|Fig\.?|Table|Tab\.?)\s+\d", re.I)
# 헤더(#)로 잘못 표기된 그림·표 캡션 — 'Table N: …' / 'Figure N. …'. 섹션 제목은 이렇게 시작하지 않는다.
_HDR_CAP_RE = re.compile(
    r"^#{1,6}\s+(?:<[^>]+>\s*)?((?:Figure|Fig\.?|Table|Tab\.?)\s+(?:\d+|[IVXLCivxlc]+))\s*[:.]?\s*(.*)$",
    re.I,
)
_SUBFIG_RE = re.compile(r"^\s*(?:[-*+]\s+)?\(?[a-z]\)(?:\s|$)")
_NUMBERED_HDR_RE = re.compile(r"^\s*(?:\d|Part\b|Appendix\b|Chapter\b|Section\b)", re.I)
_KEEP_HDR = frozenset({
    "abstract", "references", "introduction", "acknowledgments", "acknowledgements",
    "conclusion", "conclusions", "keywords", "ccs concepts", "related work", "background",
    "appendix", "discussion", "methods", "method", "results", "limitations", "footnotes",
})
_FIG_TITLE_MAX_CHARS = 60  # 그림 제목·축 이름은 대개 짧다 (본문 문단을 잘못 지우지 않도록)


def _is_fig_element(line: str) -> bool:
    s = line.strip()
    return bool(_IMG_LINE_RE.match(s) or _FIG_CAP_RE.match(re.sub(r"<[^>]+>", "", s)) or _SUBFIG_RE.match(s))


def _drop_figure_text(md: str) -> str:
    """그림 안 차트 제목·축 이름(중복 텍스트/오분류 헤더)을 그림의 일부로 보고 제거."""
    lines = md.split("\n")
    drop: set[int] = set()

    def adjacent_fig(i: int) -> bool:  # 위/아래로 빈 줄만 건너뛴 첫 비어있지 않은 줄이 그림 요소인지
        for rng in (range(i + 1, len(lines)), range(i - 1, -1, -1)):
            for j in rng:
                if not lines[j].strip() or j in drop:
                    continue
                if _is_fig_element(lines[j]):
                    return True
                break  # 첫 비-빈 줄이 그림 요소가 아니면 그 방향은 아님
        return False

    for i, ln in enumerate(lines):
        m = _MD_HEADER_RE.match(ln)
        if not m:  # 헤더만 대상 (그림 근처 짧은 본문 오제거 방지)
            continue
        title = m.group(2).strip()
        # 번호형·알려진 섹션 헤더는 진짜 섹션이므로 보존
        if _NUMBERED_HDR_RE.match(title) or title.lower().rstrip(":").strip() in _KEEP_HDR:
            continue
        # 짧은(제목·축) + 그림에 인접한 헤더 → 그림 안 텍스트로 보고 제거
        if len(title) <= _FIG_TITLE_MAX_CHARS and adjacent_fig(i):
            drop.add(i)

    return "\n".join(ln for i, ln in enumerate(lines) if i not in drop)


# 불릿 글리프로 시작하는 헤더 = 불릿 목록 항목을 추출기가 헤더로 잘못 승격한 것 → 목록으로 되돌린다.
# (예: '## · RQ2: …'처럼 같은 목록의 다른 항목은 리스트로 남는데 일부만 헤더가 되어 불일치가 생긴다.)
_BULLET_HDR_RE = re.compile(r"^#{1,6}\s+[•·‣●▪◦∙・]\s+(\S.*)$")


def _delist_headers(md: str) -> str:
    """불릿(·/•/…)으로 시작하는 헤더를 마크다운 리스트 항목으로 되돌린다(잘못 승격된 목록 통일)."""
    out: list[str] = []
    for line in md.split("\n"):
        m = _BULLET_HDR_RE.match(line)
        out.append(f"- {m.group(1).strip()}" if m else line)
    return "\n".join(out)


def _unheader_captions(md: str) -> str:
    """헤더(#)로 잘못 표기된 그림·표 캡션을 평문 캡션 줄로 되돌린다.

    Docling이 'Table 10: Participants' Age…' 같은 캡션을 section_header로 내보내면
    섹션 트리·목차·본문에 큰 제목으로 들어간다. 'Table/Figure N' 형태는 섹션 제목이 아니라
    캡션이므로 헤더 마커(#)를 벗겨 평문 'Table 10: …' 줄로 만든다(섹션에서 빠짐). 실제 표기
    (bold-italic/blockquote/…)는 조립 단계에서 caption_style로 그림 캡션과 동일하게 적용된다.
    """
    out: list[str] = []
    for line in md.split("\n"):
        m = _HDR_CAP_RE.match(line)
        if not m:
            out.append(line)
            continue
        label, rest = m.group(1).strip(), m.group(2).strip()
        out.append(f"{label}: {rest}" if rest else label)
    return "\n".join(out)


# --- 각주(footnote) — 마커를 위첨자 링크로, 내용은 문서 끝 목록 + 구조화 저장 ---
_FN_PARSE_RE = re.compile(r"^\s*(\d{1,3})[.\s]+(.*)$", re.DOTALL)
_REFS_HDR_RE = re.compile(r"^#{1,6}\s+(references|bibliography|참고문헌)\b", re.I)
# 본문 마커 후보: '앞글자 + 공백 + 1~2자리 숫자' 뒤에 공백/여는괄호/문장부호/끝.
_FN_MARK_RE = re.compile(r"(?P<pre>\S)[ ](?P<num>\d{1,2})(?=[ \n)(]|[.,;]|$)")
# 숫자가 각주가 아니라 그림·표·절 참조인 경우(제외). 마커 앞 단어가 이런 라벨이면 링크하지 않는다.
_FN_ABBR = frozenset({
    "vol", "fig", "figure", "figs", "table", "tab", "eq", "eqn", "equation", "sec", "section",
    "ch", "chapter", "no", "nos", "p", "pp", "part", "appendix", "app", "step", "level", "phase",
    "study", "round", "wave", "day", "week", "item", "rq", "q", "algorithm", "alg", "def", "thm",
    "theorem", "lemma", "corollary", "version", "v", "article", "art", "line", "row", "col",
    "column", "page", "pages", "footnote", "note", "n", "eg", "ie", "cf", "vs",
})


def _parse_footnotes(footnotes: list[str]) -> list[tuple[int | None, str]]:
    """Docling이 수집한 각주 문자열('3 https://…')을 (번호, 내용)으로 파싱. 번호 없으면 (None, 내용)."""
    out: list[tuple[int | None, str]] = []
    for raw in footnotes:
        s = raw.strip()
        m = _FN_PARSE_RE.match(s)
        if m:
            out.append((int(m.group(1)), m.group(2).strip()))
        else:
            out.append((None, s))
    return out


def _split_at_references(md: str) -> tuple[str, str]:
    """참고문헌 헤더에서 (본문, 참고문헌~끝)으로 분리. 각주 마커는 본문에서만 링크(참고문헌 오탐 방지)."""
    lines = md.split("\n")
    for i, ln in enumerate(lines):
        if _REFS_HDR_RE.match(ln):
            return "\n".join(lines[:i]), "\n".join(lines[i:])
    return md, ""


def _linkify_footnote_markers(body: str, ids: set[int]) -> str:
    """본문의 각주 마커 숫자를 위첨자 링크(<sup><a href="#fn-N">N</a></sup>)로 바꾼다.

    보수적: 아는 각주 번호이고, 앞이 문장부호/단어 끝이며, 그림·표·절 참조 라벨 뒤가 아니고,
    대괄호 인용/연도/큰 수의 일부가 아닌 경우에만 링크. 애매하면 그대로 둔다(내용은 목록에 보존).
    각 번호는 첫 유효 출현 1회만 링크.
    """
    if not ids:
        return body
    used: set[int] = set()

    def repl(m: re.Match) -> str:
        num = int(m.group("num"))
        if num not in ids or num in used:
            return m.group(0)
        pre = m.group("pre")
        if pre.isdigit() or pre in ",[/-–—":  # 인용 대괄호·범위·큰 수 내부
            return m.group(0)
        # 마커 앞 단어(라벨) 확인 — Figure/Table/Vol. 등이면 그림·표·권 참조이므로 제외
        wm = re.search(r"([A-Za-z]+)\.?$", body[max(0, m.start() - 24):m.start() + 1])
        if wm and wm.group(1).lower() in _FN_ABBR:
            return m.group(0)
        used.add(num)
        return f'{pre} <sup class="md-fn"><a href="#fn-{num}">{num}</a></sup>'

    return _FN_MARK_RE.sub(repl, body)


def _apply_footnotes(md: str, footnotes: list[str]) -> tuple[str, dict[str, str]]:
    """본문 마커를 위첨자 링크로 치환 + 문서 끝에 각주 목록(앵커 포함) 추가.

    반환: (갱신된 md, {번호: 내용} 툴팁용 맵). 번호 없는 각주는 링크 못 하지만 목록엔 남긴다.
    """
    items = _parse_footnotes(footnotes)
    ids = {i for i, _ in items if i is not None}
    before, after = _split_at_references(md)
    before = _linkify_footnote_markers(before, ids)
    md = before + ("\n" + after if after else "")
    # 각주 목록 — 각 항목에 #fn-N 앵커(클릭 점프 대비). 툴팁이 주 UX지만 목록도 남긴다.
    lines: list[str] = []
    for i, content in items:
        if i is not None:
            lines.append(f'<a id="fn-{i}"></a>**{i}.** {content}')
        else:
            lines.append(content)
    md = md.rstrip() + "\n\n## Footnotes\n\n" + "\n\n".join(lines) + "\n"
    tips = {str(i): content for i, content in items if i is not None}
    return md, tips


# --- 첫 페이지 각주 분류: 본문 각주 / 저자 주석 / 출판사 boilerplate ------------------
# 첫 페이지 아래 컬럼에는 성격이 다른 세 가지가 같은 'footnote' 라벨로 섞여 나온다.
# 번호가 붙은 것만 본문 각주이고, 나머지는 앞부분(저자 주석)이거나 서지 정보(허가문·ⓒ·ISBN·DOI)다.
# 셋을 구분하지 않으면 저작권 문구가 문서 끝 '## Footnotes'에 본문처럼 실린다.
_FN_PAGE_MAX = 1  # 이 쪽까지의 무번호 각주만 front matter로 본다 (본문 각주는 건드리지 않음)
_AUTHOR_NOTE_MARK_RE = re.compile(r"^\s*[*∗＊†‡§¶‖]|^\s*(?:Corresponding author|Equal contribution)", re.I)
# 출판사 boilerplate — 학회·출판사를 안 가리는 표지들(허가문·라이선스·저작권·ISBN·DOI 단독 줄).
_FRONT_BOILERPLATE_RE = re.compile(
    r"(Permission to make digital|This work is licensed|Creative Commons|"
    r"Copyright held by|©|\(c\)\s*\d{4}|\bISBN\b|^\s*https?://doi\.org/|^\s*doi:)",
    re.I,
)


def _page_of(item) -> int | None:  # noqa: ANN001 — DocItem
    prov = getattr(item, "prov", None)
    return int(prov[0].page_no) if prov else None


def _classify_footnote(text: str, page: int | None) -> str:
    """각주 텍스트를 'body'(번호 각주) / 'author'(저자 주석) / 'boilerplate'로 분류.

    번호로 시작하면 본문 각주(마커 링크 대상). 첫 페이지의 무번호 각주만 앞부분 것으로 보고,
    각주 기호(∗ † ‡ …)로 시작하면 저자 주석, 허가문·ⓒ·ISBN·DOI면 서지 boilerplate로 돌린다.
    어느 쪽도 아니면 기존대로 본문 각주 목록에 남긴다(내용 유실 방지).
    """
    if _FN_PARSE_RE.match(text.strip()):
        return "body"
    if page is not None and page > _FN_PAGE_MAX:
        return "body"
    if _AUTHOR_NOTE_MARK_RE.match(text):
        return "author"
    return "boilerplate" if _FRONT_BOILERPLATE_RE.search(text) else "body"


# 저자 주석 블록 표시 — 앞부분 정규화(front_matter)가 라벨 없이도 이 블록만은 지우지 않고
# 저자 바로 뒤로 옮길 수 있게 하는 앵커. 렌더링에는 보이지 않는다.
AUTHOR_NOTE_ANCHOR = "fn-author"
_BODY_HDR_RE = re.compile(r"^#{1,6}\s+[·*\s]*(\d|Introduction\b)", re.I)


def _place_author_notes(md: str, notes: list[str]) -> str:
    """저자 주석을 앞부분 끝(첫 본문 섹션 직전)에 넣는다 — 문서 끝 각주 목록이 아니라.

    front_matter 단계가 이 앵커를 보고 저자 블록 바로 뒤로 다시 옮긴다. 첫 본문 섹션을 못 찾으면
    맨 앞(제목 다음)에 둔다 — 어느 경우든 본문 한가운데로는 가지 않는다.
    """
    if not notes:
        return md
    block = "\n\n".join(
        f'<a id="{AUTHOR_NOTE_ANCHOR}-{i}"></a>{n.strip()}' for i, n in enumerate(notes, 1))
    blocks = _split_blocks(md)
    at = next((i for i, b in enumerate(blocks) if _BODY_HDR_RE.match(b.strip())), min(1, len(blocks)))
    blocks.insert(at, block)
    text = "\n\n".join(blocks)
    return text + "\n" if md.endswith("\n") else text


# --- 문단 재결합의 기하 근거 ------------------------------------------------
# 재결합은 원문을 되돌릴 수 없게 바꾸므로(두 블록이 한 블록이 된다) **근거가 없으면 하지 않는다**.
# 텍스트 예측(앞이 미완결·뒤가 소문자)만으로는 캡션이 뒤 문단을 삼키는 오결합을 못 막는다:
# "Figure 2: … across participants" + "(CSI) [13], excluding …" 는 두 조건을 모두 만족한다.
_JOIN_LABELS = frozenset({"text", "paragraph"})   # 이 라벨끼리만 이어붙인다 (캡션·수식·표·제목 제외)
_JOIN_ALIGN_MIN = 24     # 정렬 확인에 쓸 최소 정규화 길이 — 이보다 짧으면 완전일치만 인정
_JOIN_EDGE_LINES = 4.0   # 단의 위/아래 '끝자락'으로 볼 줄 수 — 단 넘김/쪽 넘김 판정
_JOIN_GAP_LINES = 3.0    # 같은 단에서 이어지는 것으로 볼 최대 세로 간격 (줄 높이 배수)


def _join_key(s: str) -> str:
    """마크다운 표기를 걷어낸 영숫자만 — export 텍스트와 아이템 텍스트를 맞대볼 지문.

    export가 '<' 를 '&lt;' 로 이스케이프하므로 먼저 되돌린다. 안 그러면 부등호가 든 문단
    (통계 결과 'p &lt; 0.001' 등)이 아이템에 정렬되지 않아 애먼 재결합이 거부된다.
    """
    import html

    return re.sub(r"[^a-z0-9]+", "", html.unescape(s).lower())


def _align_blocks(blocks: list[str], items: list) -> list[int | None]:
    """마크다운 블록 → export 아이템 인덱스. 확실하지 않으면 None (fail closed).

    two-pointer로 앞에서부터만 훑는다. 리스트처럼 아이템 여러 개가 한 블록이 되는 자리는
    일부러 정렬되지 않게 두어(완전일치 요구) 재결합 대상에서 빠지게 한다.
    """
    keys = [_join_key(getattr(it, "text", "") or "") for it in items]
    out: list[int | None] = []
    at = 0
    for b in blocks:
        key = _join_key(b)
        hit = None
        if key:
            for j in range(at, len(items)):
                k = keys[j]
                if not k:
                    continue
                if k == key or (len(key) >= _JOIN_ALIGN_MIN and len(k) >= _JOIN_ALIGN_MIN
                                and (k.startswith(key) or key.startswith(k))):
                    hit = j
                    break
        out.append(hit)
        if hit is not None:
            at = hit + 1
    return out


def _joinable_geom(geom, prev_i: int | None, next_i: int | None,  # noqa: ANN001
                   aligned: set[int]) -> bool:
    """두 아이템이 정말 '한 문단이 끊긴 자리'인지 — 라벨·인접·기하 세 가지를 모두 본다."""
    if geom is None or prev_i is None or next_i is None or next_i <= prev_i:
        return False
    # G2: export 스트림에서 이웃이어야 한다. 사이에 낀 아이템이 있어도, 그것이 **마크다운에서
    # 이미 빠진** 것(연락처 footer 등)이면 두 문단은 원래 붙어 있던 자리다. 마크다운에 남아 있는
    # 아이템(그림·표·다른 문단)이 끼어 있으면 이어지는 문단이 아니다.
    if any(prev_i < j < next_i for j in aligned):
        return False
    a, b = geom.items[prev_i], geom.items[next_i]
    if a.label not in _JOIN_LABELS or b.label not in _JOIN_LABELS:
        return False  # G1: 캡션·제목·수식·표는 문단 연속이 될 수 없다
    if a.full_width or b.full_width:
        return False  # G3: 단을 가로지르는 블록(러닝 헤더·전폭 그림 설명)은 이어짐의 상대가 아니다
    pa, pb = geom.pages.get(a.page_end), geom.pages.get(b.page_start)
    if pa is None or pb is None:
        return False
    if a.page_end == b.page_start and a.col_end == b.col_start:
        return 0 <= b.top - a.bottom <= _JOIN_GAP_LINES * pa.line_h  # 같은 단에서 바로 아래
    # 단·쪽을 넘는 이어짐: 앞 문단이 자기 단의 **끝까지** 내려가 있어야 한다. 단 중간에서
    # 끊긴 문단은 다음 단으로 이어질 리가 없다(단의 끝은 각주를 뺀 본문 흐름 기준으로 잰다).
    if a.bottom < pa.col_bottom.get(a.col_end, pa.band_bottom) - _JOIN_EDGE_LINES * pa.line_h:
        return False
    if a.page_end == b.page_start:
        # 다음 단으로 넘어가면 흐름은 위로 되돌아간다 — 아래로 내려가면 이어짐이 아니다.
        return b.col_start == a.col_end + 1 and b.top <= a.top
    if b.page_start != a.page_end + 1 or a.col_end != pa.n_cols - 1 or b.col_start != 0:
        return False
    return b.top <= pb.col_top.get(b.col_start, pb.band_top) + _JOIN_EDGE_LINES * pb.line_h


def _join_broken_paragraphs(md: str, geom=None, stats: dict | None = None) -> str:  # noqa: ANN001
    """열·페이지 경계나 footer/저작권 블록 제거로 끊긴 문단을 재결합 — 근거가 있을 때만.

    2단 조판에서 초록 같은 문단이 컬럼 경계에서 쪼개지고, 그 사이의 저작권 boilerplate를
    제거하면 "…test set. A" / "user study…" 처럼 빈 줄로 나뉜 두 문단이 남는다.
    앞 문단이 종결부호 없이(그리고 충분히 길게) 끝나고 뒤 문단이 소문자로 시작하면 한 문장의
    연속 **후보**로 본다. 여기까지는 텍스트 예측일 뿐이라, 실제로 그 자리가 단·쪽 경계인지
    `geom`(export 순서 기하)으로 확인한 뒤에만 결합한다. geom이 없거나 블록이 아이템에
    정렬되지 않으면 결합하지 않는다 — 잘못 붙인 문단은 되돌릴 수 없기 때문이다.
    """
    blocks = _split_blocks(md)
    align = _align_blocks(blocks, geom.items) if geom is not None else [None] * len(blocks)
    aligned = {j for j in align if j is not None}
    made = refused = 0
    merged: list[str] = []
    at: list[int | None] = []  # merged[-1]이 정렬된 아이템 인덱스
    for b, idx in zip(blocks, align):
        prev = merged[-1] if merged else ""
        if (merged and _is_prose_block(prev) and _is_prose_block(b)
                and len(prev.rstrip()) >= _MIN_CONT_LEN
                and _CONT_END_RE.search(prev.rstrip())
                and _continues(prev.rstrip(), b.lstrip())):
            if _joinable_geom(geom, at[-1], idx, aligned):
                merged[-1] = prev.rstrip() + " " + b.lstrip()
                at[-1] = idx  # 이어붙인 문단의 끝은 이제 뒤 아이템
                made += 1
                continue
            refused += 1
        merged.append(b)
        at.append(idx)
    if stats is not None:
        stats["joins_made"] = stats.get("joins_made", 0) + made
        stats["joins_refused"] = stats.get("joins_refused", 0) + refused
    text = "\n\n".join(merged)
    return text + "\n" if md.endswith("\n") else text



# 본문에서 제외할 라벨 (러닝 헤더/푸터·각주는 2단 조판 읽기 순서를 깨뜨린다)
_EXCLUDE_LABELS = {"page_header", "page_footer", "footnote"}
# ACM/IEEE 첫 페이지 저작권 잡동사니 — text로 태깅되지만 본문 흐름이 아님
_BOILERPLATE_RE = re.compile(
    r"(This work is licensed|Permission to make digital|ACM ISBN|"
    r"Copyright held by|©\s*\d{4}\s+Copyright|ACM Reference Format|"
    r"Creative Commons Attribution)",
    re.IGNORECASE,
)


# --- 러닝 헤더/푸터 — 라벨이 아니라 '쪽마다 되풀이되는 가장자리 텍스트'로 판정 -------------
# page_header/page_footer 라벨은 _EXCLUDE_LABELS로 이미 빠지지만, Docling이 어떤 쪽에서만
# 라벨을 놓치면(관찰: 좌우 러닝 헤더가 한 'text' 아이템으로 합쳐진 쪽) 그 한 줄이 본문에 샌다.
# 그래서 라벨과 무관하게 "페이지 위/아래 띠 + 여러 쪽에 반복" 인 텍스트를 지운다.
# 합쳐진 변형("<venue> <저자목록>")도 잡히도록, 알려진 반복 문구를 빼고 남는 잔여가 짧으면 제거한다.
_RUNHEAD_BAND = 0.10       # 페이지 높이의 위/아래 이 비율 안 (좌표계 원점과 무관하게 가장자리 거리로)
_RUNHEAD_MIN_PAGES = 2     # 서로 다른 쪽 이만큼에 나와야 러닝 헤더로 인정
_RUNHEAD_MAX_CHARS = 200   # 이보다 길면 러닝 헤더가 아니라 본문 문단
_RUNHEAD_MIN_CHARS = 8     # 쪽번호처럼 짧은 건 시그니처로 안 씀 (본문 오탐 위험)
_RUNHEAD_RESIDUE = 0.4     # 반복 문구를 뺀 잔여가 원문의 이 비율 미만이면 러닝 헤더 줄로 본다
_RUNHEAD_DIGITS_RE = re.compile(r"\d+")
# 러닝 헤더로 볼 수 없는 라벨 — 페이지 위쪽에 오는 진짜 섹션 제목·캡션·표를 지우지 않기 위해 아예 제외.
_RUNHEAD_KEEP_LABELS = frozenset({
    "section_header", "title", "document_index", "caption", "table", "picture", "formula",
    "code", "list_item", "reference",
})


def _runhead_sig(text: str) -> str:
    """러닝 헤더 대조용 정규화 — 소문자·공백 축약·숫자 마스킹(쪽번호·연도 차이 무시)."""
    return _RUNHEAD_DIGITS_RE.sub("#", " ".join(text.split()).lower())


def _edge_items(document) -> list[tuple]:  # noqa: ANN001 — DoclingDocument
    """페이지 위/아래 띠에 있는 (아이템, 쪽번호, 시그니처) 목록."""
    out: list[tuple] = []
    for it in getattr(document, "texts", []):
        text = (getattr(it, "text", "") or "").strip()
        prov = getattr(it, "prov", None)
        label = str(getattr(it, "label", "")).replace("DocItemLabel.", "").lower()
        if label in _RUNHEAD_KEEP_LABELS:
            continue  # 섹션 제목·캡션은 페이지 맨 위에 와도 러닝 헤더가 아니다
        if not prov or not (_RUNHEAD_MIN_CHARS <= len(text) <= _RUNHEAD_MAX_CHARS):
            continue
        page = int(prov[0].page_no)
        size = getattr(getattr(document, "pages", {}).get(page, None), "size", None)
        height = float(getattr(size, "height", 0) or 0)
        b = prov[0].bbox
        if not height:
            continue
        lo, hi = min(b.t, b.b), max(b.t, b.b)
        if min(lo, height - hi) <= height * _RUNHEAD_BAND:  # 좌표 원점(상단/하단) 무관
            out.append((it, page, _runhead_sig(text)))
    return out


def _drop_running_heads(document) -> int:  # noqa: ANN001 — DoclingDocument
    """쪽마다 되풀이되는 러닝 헤더/푸터를 문서에서 제거. 제거한 아이템 수를 돌려준다.

    홀/짝 변형(왼쪽=venue, 오른쪽=저자목록)은 각각 별도 시그니처로 잡히고, 둘이 한 아이템으로
    합쳐진 쪽은 '알려진 시그니처를 빼면 잔여가 거의 없다'로 잡힌다.
    """
    from collections import defaultdict

    edge = _edge_items(document)
    pages_by_sig: dict[str, set[int]] = defaultdict(set)
    for _, page, sig in edge:
        pages_by_sig[sig].add(page)
    repeated = {sig for sig, pages in pages_by_sig.items() if len(pages) >= _RUNHEAD_MIN_PAGES}
    if not repeated:
        return 0
    ordered = sorted(repeated, key=len, reverse=True)  # 긴 것부터 빼야 부분 문구에 안 먹힌다
    to_remove = []
    for it, _, sig in edge:
        residue = sig
        for known in ordered:
            residue = residue.replace(known, " ")
        if len(" ".join(residue.split())) < len(sig) * _RUNHEAD_RESIDUE:
            to_remove.append(it)
    for it in to_remove:
        try:
            document.delete_items(node_items=[it])
        except Exception:  # noqa: BLE001 — 삭제 실패해도 치명적이지 않음
            pass
    return len(to_remove)


def _body_labels() -> set:
    """본문 export에 쓸 라벨 집합 = docling 기본 export 라벨 − 제외 라벨 + 캡션.

    caption은 DEFAULT_EXPORT_LABELS에 없어(!) 그대로 두면 그림/표 캡션이 통째로 누락된다.
    """
    from docling_core.types.doc import DocItemLabel
    from docling_core.types.doc.document import DEFAULT_EXPORT_LABELS

    labels = {lbl for lbl in DEFAULT_EXPORT_LABELS if lbl.value not in _EXCLUDE_LABELS}
    labels.add(DocItemLabel.CAPTION)
    return labels


# --- 투고 원고(working draft) 여백의 줄 번호 gutter -----------------------------
# 학회 투고본은 여백에 줄 번호(1, 2, 3 …)를 찍는다. Docling은 이걸 본문 'text' 아이템으로
# 하나씩 뽑아 페이지마다 수십 개를 본문 앞에 쌓아 놓고, 그 자리에서 문단도 끊어 놓는다.
# 판정: **한 세로줄로 정렬**된 맨숫자 아이템이 위→아래로 **증가**하고, 그 세로 띠가 그 페이지
# 어떤 본문 블록과도 **가로로 겹치지 않으며**(= 여백/컬럼 사이), 문서에서 **두 쪽 이상** 그렇게
# 나타날 때만. 표에서 떨어져 나온 숫자 열이나 진짜 본문 숫자는 이 조건을 다 만족하지 못한다.
_LINE_NO_RE = re.compile(r"^\d{1,4}$")
_GUTTER_ALIGN_TOL = 6.0   # 같은 세로줄로 볼 x 오차(pt) — 자릿수가 늘면 폭이 조금 달라진다
_GUTTER_MIN_COUNT = 6     # 한 페이지에서 이만큼 이어져야 gutter로 인정
_GUTTER_BODY_GAP = 2.0    # 본문과 이만큼 떨어져 있어야 '겹치지 않음'
_BODY_MIN_WIDTH = 40.0    # 본문 블록으로 볼 최소 bbox 폭(pt) — 짧은 조각은 기준으로 안 씀
# Docling이 gutter 번호를 옆 블록에 섞어 넣으면(참고문헌 목록에서 관찰) 그 블록 bbox가 여백까지
# 늘어나 '겹침'으로 잡힌다. 그런 오염 블록 하나까지는 봐준다.
_GUTTER_BODY_OVERLAP_MAX = 1
# 그렇게 섞여 들어간 번호는 블록 텍스트 맨 앞에 남는다("729 730 [5] Brian Huot. 1990. …").
# 참고문헌 항목 번호 인식을 깨뜨리므로 텍스트에서도 떼어낸다.
_LEADING_NUM_RE = re.compile(r"^\s*(\d{1,4})\s+")


def _align_groups(items: list, edge) -> list[list]:  # noqa: ANN001 — edge: item → x(pt)
    """x 좌표가 _GUTTER_ALIGN_TOL 안에서 같은 아이템끼리 세로줄로 묶는다."""
    groups: list[list] = []
    for it in sorted(items, key=edge):
        if groups and edge(it) - edge(groups[-1][0]) <= _GUTTER_ALIGN_TOL:
            groups[-1].append(it)
        else:
            groups.append([it])
    return groups


def _bbox(it):  # noqa: ANN001,ANN201 — DocItem의 첫 prov bbox
    prov = getattr(it, "prov", None)
    return prov[0].bbox if prov else None


def _increasing(cluster: list) -> bool:
    """위에서 아래로 읽을 때 번호가 커지는지 (줄 번호의 필수 조건)."""
    vals = [int(it.text.strip()) for it in sorted(cluster, key=lambda it: -_bbox(it).t)]
    return all(a < b for a, b in zip(vals, vals[1:]))


def _clear_of_body(cluster: list, bodies: list[tuple[float, float]]) -> bool:
    """세로 띠가 본문 블록과 가로로 겹치지 않는지 (여백이거나 컬럼 사이인지)."""
    left = min(min(_bbox(it).l, _bbox(it).r) for it in cluster) - _GUTTER_BODY_GAP
    right = max(max(_bbox(it).l, _bbox(it).r) for it in cluster) + _GUTTER_BODY_GAP
    hits = sum(1 for bl, br in bodies if left < br and bl < right)
    return hits <= _GUTTER_BODY_OVERLAP_MAX


def _page_gutters(cands: list, bodies: list[tuple[float, float]], min_count: int) -> list[list]:
    """한 페이지에서 줄 번호 세로줄을 찾는다. 왼쪽 정렬·오른쪽 정렬 둘 다 시도."""
    found: list[list] = []
    seen: set[int] = set()
    for edge in (lambda it: _bbox(it).l, lambda it: _bbox(it).r):
        for group in _align_groups(cands, edge):
            if len(group) < min_count or any(id(it) in seen for it in group):
                continue
            if _increasing(group) and _clear_of_body(group, bodies):
                found.append(group)
                seen.update(id(it) for it in group)
    return found


def _drop_line_numbers(document) -> int:  # noqa: ANN001 — DoclingDocument
    """투고 원고 여백의 줄 번호를 본문에서 제거. 반환: 제거한 개수.

    확실한 페이지(줄 번호가 _GUTTER_MIN_COUNT개 이상)로 먼저 gutter 위치를 확정하고,
    그 위치에 맞는 낱개 번호를 나머지 페이지에서 마저 걷어낸다(본문이 짧은 마지막 쪽 등).
    """
    from collections import defaultdict

    cands: dict[int, list] = defaultdict(list)
    bodies: dict[int, list[tuple[float, float]]] = defaultdict(list)
    for it in getattr(document, "texts", []):
        label = str(getattr(it, "label", "")).replace("DocItemLabel.", "").lower()
        b = _bbox(it)
        if b is None or label in _EXCLUDE_LABELS:
            continue  # 러닝 헤더/푸터의 쪽 번호는 애초에 export에서 빠진다
        page = int(it.prov[0].page_no)
        if _LINE_NO_RE.match((getattr(it, "text", "") or "").strip()):
            cands[page].append(it)
        elif abs(b.r - b.l) >= _BODY_MIN_WIDTH:
            bodies[page].append((min(b.l, b.r), max(b.l, b.r)))

    gutters = {pg: _page_gutters(its, bodies[pg], _GUTTER_MIN_COUNT) for pg, its in cands.items()}
    pages = [pg for pg, gs in gutters.items() if gs]
    if len(pages) < 2:
        return 0  # 한 쪽에서만 보이면 줄 번호가 아니라 그 페이지 사정(표 조각 등)

    # 확정된 세로줄의 x 위치 — 홀/짝수 쪽 여백이 좌우로 갈리므로 여러 개가 나온다
    bands = [min(_bbox(it).l for it in g) for gs in gutters.values() for g in gs]
    remove: list = []
    for pg, its in cands.items():
        taken = {id(it) for g in gutters[pg] for it in g}
        remove.extend(it for g in gutters[pg] for it in g)
        rest = [it for it in its if id(it) not in taken]
        for group in _align_groups(rest, lambda it: _bbox(it).l):
            # 이미 문서 차원에서 확정된 gutter 위치에 놓인 번호 — 본문 겹침은 더 안 따진다
            on_band = any(abs(_bbox(group[0]).l - x) <= _GUTTER_ALIGN_TOL for x in bands)
            if on_band and _increasing(group):
                remove.extend(group)

    # 블록 안으로 섞여 들어간 번호 — bbox가 gutter에서 시작하는 본문 블록의 맨 앞 숫자를 뗀다
    values = [int(it.text.strip()) for it in remove]
    lo, hi = min(values), max(values)
    dropped = {id(it) for it in remove}
    stripped = 0
    for it in getattr(document, "texts", []):
        b = _bbox(it)
        if b is None or id(it) in dropped:
            continue
        if not any(abs(b.l - x) <= _GUTTER_ALIGN_TOL for x in bands):
            continue
        text = getattr(it, "text", "") or ""
        while (m := _LEADING_NUM_RE.match(text)) and lo <= int(m.group(1)) <= hi:
            text = text[m.end():]
            stripped += 1
        if text != it.text:
            it.text = text

    for it in remove:
        try:
            document.delete_items(node_items=[it])
        except Exception:  # noqa: BLE001 — 삭제 실패해도 치명적이지 않음
            pass
    return len(remove) + stripped


# 로고/배지 판정 크기(pt) — 이보다 작은 그림은 CC 라이선스 배지·학회 로고 등으로 보고 제거
_LOGO_MAX_W = 120
_LOGO_MAX_H = 120


def _relocate_footer_blocks(document) -> tuple[list[str], list[str], list[str]]:  # noqa: ANN001
    """각주 분류 + 저작권 boilerplate/로고 배지를 본문에서 제거.

    - footnote 라벨: 번호 각주는 문서 끝 목록으로, 첫 페이지의 무번호 각주는 성격에 따라
      저자 주석(∗ † … 마커) / 출판사 boilerplate(허가문·ⓒ·ISBN·DOI)로 나눈다.
    - 저작권/라이선스 boilerplate(text 라벨): 내용 기반으로 삭제(단, venue/연도가 들어 있어 서지용으로 보관).
    - 작은 그림(CC 배지·학회 로고): 크기 기준으로 삭제(진짜 figure는 크므로 보존).
    반환: (문서 끝에 붙일 각주 목록, 서지 추출용 boilerplate 목록, 앞부분에 붙일 저자 주석 목록).
    """
    footnotes: list[str] = []
    boilerplate: list[str] = []
    author_notes: list[str] = []
    to_remove: list = []
    for it in getattr(document, "texts", []):
        label = str(getattr(it, "label", "")).replace("DocItemLabel.", "").lower()
        text = (getattr(it, "text", "") or "").strip()
        if not text:
            continue
        if label == "footnote":
            kind = _classify_footnote(text, _page_of(it))
            {"body": footnotes, "author": author_notes, "boilerplate": boilerplate}[kind].append(text)
        elif label == "text" and _BOILERPLATE_RE.search(text) and len(text) < 400:
            boilerplate.append(text)  # venue/연도가 있을 수 있어 서지 추출용으로 남김
            to_remove.append(it)

    for pic in getattr(document, "pictures", []):
        prov = getattr(pic, "prov", None)
        if not prov:
            continue
        b = prov[0].bbox
        if not (abs(b.r - b.l) < _LOGO_MAX_W and abs(b.t - b.b) < _LOGO_MAX_H):
            continue  # 로고/배지 크기 아님 → 진짜 그림
        # 진짜 CC 배지·학회 로고는 첫 페이지 저작권 근처에만 있고 캡션이 없다.
        # 캡션이 있거나 본문 페이지(2쪽~)면 작아도 실제 (서브)그림이므로 삭제하지 않는다.
        try:
            has_cap = bool((pic.caption_text(document) or "").strip())
        except Exception:  # noqa: BLE001
            has_cap = False
        if int(prov[0].page_no) <= 1 and not has_cap:
            to_remove.append(pic)

    # 그림 bbox 안의 텍스트(축 라벨·셀 값 등)는 이미 래스터 이미지에 그려져 있어 중복 → 제거.
    # 캡션은 보존(별도 처리). 진짜 본문은 그림 영역과 겹치지 않으므로 안전.
    pic_boxes: dict[int, list] = {}
    for pic in getattr(document, "pictures", []):
        pr = getattr(pic, "prov", None)
        if pr:
            pic_boxes.setdefault(int(pr[0].page_no), []).append(pr[0].bbox)
    if pic_boxes:
        for it in getattr(document, "texts", []):
            label = str(getattr(it, "label", "")).replace("DocItemLabel.", "").lower()
            if label == "caption" or it in to_remove:
                continue
            pr = getattr(it, "prov", None)
            if not pr:
                continue
            b = pr[0].bbox
            cx, cy = (b.l + b.r) / 2, (b.t + b.b) / 2
            for pb in pic_boxes.get(int(pr[0].page_no), []):
                if pb.l <= cx <= pb.r and min(pb.t, pb.b) <= cy <= max(pb.t, pb.b):
                    to_remove.append(it)  # 그림 안 텍스트 → 삭제
                    break

    for it in to_remove:
        try:
            document.delete_items(node_items=[it])
        except Exception:  # noqa: BLE001 — 삭제 실패해도 치명적이지 않음
            pass
    return footnotes, boilerplate, author_notes


# Docling export 아티팩트 파일명: image_000002_<hash>.png → 픽처 인덱스 2 (document.pictures[2])
_ARTIFACT_IDX_RE = re.compile(r"image_0*(\d+)_")


def _artifact_pic(pictures: list, fname: str):  # noqa: ANN001,ANN201 — DoclingDocument PictureItem
    """아티팩트 파일명(image_000002_…)의 픽처 인덱스로 해당 PictureItem을 돌려준다."""
    m = _ARTIFACT_IDX_RE.match(fname)
    if not m:
        return None
    k = int(m.group(1))
    return pictures[k] if 0 <= k < len(pictures) else None



def _artifact_page(pictures: list, fname: str) -> int | None:
    """아티팩트가 속한 PDF 페이지 (같은 페이지 이미지에 캡션을 붙이는 폴백용)."""
    pic = _artifact_pic(pictures, fname)
    prov = getattr(pic, "prov", None) if pic is not None else None
    return int(prov[0].page_no) if prov else None


_FIGNUM_RE = re.compile(r"^\**\s*(?:Figure|Fig\.?|Table|Tab\.?)\s+(\d+)", re.IGNORECASE)


def _fignum(text: str) -> int:
    m = _FIGNUM_RE.match(re.sub(r"<[^>]+>", "", text))
    return int(m.group(1)) if m else 10**9


def _collect_captions(document) -> list[tuple[str, int | None]]:  # noqa: ANN001 — DoclingDocument
    """모든 그림(이미지 없는 그룹 픽처 포함)의 Docling 캡션을 (텍스트, 페이지)로 수집."""
    out: list[tuple[str, int | None]] = []
    for pic in getattr(document, "pictures", []):
        try:
            cap = (pic.caption_text(document) or "").strip()
        except Exception:  # noqa: BLE001
            cap = ""
        if not cap:
            continue
        prov = getattr(pic, "prov", None)
        out.append((cap, int(prov[0].page_no) if prov else None))
    return out


def _place_captions(
    md: str, captions: list[tuple[str, int | None]], page_by_name: dict[str, int]
) -> str:
    """Docling 캡션을 이미지 뒤로 배치. 같은 페이지에서 이미지·캡션을 순서로 짝지어 붙이므로,
    캡션이 md에 없거나(이미지 없는 그룹 그림) 흩어져 있어도 유실 없이 올바른 이미지에 연결된다.
    붙일 이미지가 없는 캡션(페이지에 이미지 0개)만 건드리지 않는다.
    """
    if not captions:
        return md

    def norm(s: str) -> str:
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s)).strip()

    from collections import defaultdict

    lines = md.split("\n")
    img_re = re.compile(r"^\s*!\[[^\]]*\]\((.+?)\)\s*$")
    page_imgs: dict[int, list[int]] = defaultdict(list)  # 페이지 → [이미지 줄 인덱스(문서순)]
    for idx, ln in enumerate(lines):
        m = img_re.match(ln)
        if m:
            pg = page_by_name.get(m.group(1).split("/")[-1])
            if pg is not None:
                page_imgs[pg].append(idx)

    page_caps: dict[int, list[str]] = defaultdict(list)  # 페이지 → [캡션(그림번호순)]
    for text, pg in sorted(captions, key=lambda c: _fignum(c[0])):
        if pg is not None:
            page_caps[pg].append(text)

    inject: dict[int, list[str]] = {}
    placed: set[str] = set()
    for pg, caps in page_caps.items():
        imgs = page_imgs.get(pg)
        if not imgs:
            continue  # 페이지에 이미지 없음 → 못 붙임 (원위치 유지)
        for k, text in enumerate(caps):  # k번째 캡션 → k번째 이미지(초과분은 마지막에)
            target = imgs[min(k, len(imgs) - 1)]
            inject.setdefault(target, []).append(text)
            placed.add(norm(text))

    out: list[str] = []
    for idx, ln in enumerate(lines):
        if norm(ln) in placed and not img_re.match(ln):
            continue  # 원위치의 캡션 텍스트 제거 (이미지 뒤로 이동)
        out.append(ln)
        for cap in inject.get(idx, []):
            out.extend(("", cap))
    return "\n".join(out)


def _save_heading_pages(document, wd: WorkDir) -> int:  # noqa: ANN001 — DoclingDocument
    """헤더별 원본 PDF 페이지/세로위치를 저장 (섹션 클릭 → PDF 해당 페이지 대조용).

    러닝 헤더(page_header)는 섹션이 아니므로 제외한다.
    """
    import json

    items = []
    for it in getattr(document, "texts", []):
        label = str(getattr(it, "label", "")).lower()
        if "section_header" not in label and not label.endswith("title"):
            continue
        prov = getattr(it, "prov", None)
        if not prov:
            continue
        bbox = getattr(prov[0], "bbox", None)
        items.append({
            "text": (it.text or "").strip(),
            "page": int(prov[0].page_no),
            "top": round(float(bbox.t)) if bbox is not None else None,
        })
    wd.extract.mkdir(parents=True, exist_ok=True)
    wd.headings_json.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(items)


def extract_to(source: Path, wd: WorkDir, ocr: bool = False) -> dict:
    """PDF → wd.raw_md + wd.extract_images. 반환: 백엔드별 meta 조각."""
    if not available():
        raise ExtractError("docling이 설치되지 않았습니다. `uv sync`로 설치하세요.")

    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling_core.types.doc import ImageRefMode

    opts = PdfPipelineOptions()
    opts.generate_picture_images = True
    opts.images_scale = 2.0
    opts.do_ocr = ocr  # born-digital 논문은 OCR 불필요 (기본 off)

    converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
    )
    try:
        result = converter.convert(str(source))
    except Exception as e:  # noqa: BLE001 — 사용자에게 원인 노출
        raise ExtractError(f"docling 변환 실패: {e}") from e

    _save_heading_pages(result.document, wd)
    line_numbers = _drop_line_numbers(result.document)  # 투고 원고 여백의 줄 번호 gutter
    run_heads = _drop_running_heads(result.document)  # 쪽마다 되풀이되는 러닝 헤더/푸터
    footnotes, boilerplate, author_notes = _relocate_footer_blocks(result.document)
    # 다단 조판에서 뒤집힌 단·열 우선 저자 그리드를 되돌린다 (prov bbox가 살아 있는 마지막 지점)
    order_meta = repair_reading_order(result.document)
    try:  # 되돌린 뒤의 방출 순서 기하 — 문단 재결합의 근거. 못 얻으면 재결합을 안 할 뿐이다.
        geom = export_geometry(result.document)
    except Exception:  # noqa: BLE001 — 기하 실패가 추출 전체를 막지 않게
        geom = None
    join_stats: dict = {"joins_made": 0, "joins_refused": 0, "geometry": geom is not None}
    wd.extract.mkdir(parents=True, exist_ok=True)
    wd.frontmatter_txt.write_text("\n".join(boilerplate), encoding="utf-8")  # 서지(venue/연도) 추출용
    pictures = list(getattr(result.document, "pictures", []))  # 아티팩트 파일명 인덱스와 매핑

    with tempfile.TemporaryDirectory(prefix="md4paper-docling-") as tmp:
        md_path = Path(tmp) / "out.md"
        # 러닝 헤더/푸터·각주를 본문 export에서 제외 → 2단 조판에서 본문(초록 등)이 끊기지 않음
        result.document.save_as_markdown(
            md_path, image_mode=ImageRefMode.REFERENCED, labels=_body_labels(),
        )
        md = md_path.read_text(encoding="utf-8")
        md = _split_author_block(md)  # 2단 저자 블록(한 줄)을 저자별로 분리
        md = _strip_contact_footer(md)  # 본문에 흘러든 'Corresponding author' 연락처 블록 제거
        md = _join_broken_paragraphs(md, geom, join_stats)  # 컬럼/footer로 끊긴 문단 재결합(기하 확인)
        md = _place_author_notes(md, author_notes)  # 저자 주석(∗ …)은 앞부분에 (문서 끝 각주 아님)
        # 각주: 본문 마커를 위첨자 링크로, 내용은 문서 끝 목록 + 구조화 저장(호버 툴팁용)
        if footnotes:
            import json as _json

            md, fn_tips = _apply_footnotes(md, footnotes)
            if fn_tips:
                wd.footnotes_json.write_text(
                    _json.dumps(fn_tips, ensure_ascii=False, indent=2), encoding="utf-8")

        # 이미지: 해시 이름 → img-NN 으로 옮기고 마크다운 참조를 베어 파일명으로
        wd.extract_images.mkdir(parents=True, exist_ok=True)
        artifacts = md_path.parent / f"{md_path.stem}_artifacts"
        mapping: dict[str, str] = {}
        page_by_name: dict[str, int] = {}
        if artifacts.is_dir():
            for i, img in enumerate(sorted(p for p in artifacts.iterdir() if p.is_file()), start=1):
                new_name = _short_name(img, i)
                shutil.copy2(img, wd.extract_images / new_name)
                mapping[str(img)] = new_name
                mapping[img.name] = new_name
                pg = _artifact_page(pictures, img.name)
                if pg is not None:
                    page_by_name[new_name] = pg
        md = rewrite_image_refs(md, mapping)
        # 모든 그림(그룹 픽처 포함)의 캡션을 페이지·순서로 이미지에 짝지어 배치 (유실 방지)
        md = _place_captions(md, _collect_captions(result.document), page_by_name)
        md = _drop_figure_text(md)  # 그림 안 차트 제목·축 이름이 헤더로 오분류된 것 제거(래스터에 이미 있음)

    md = _unheader_captions(md)  # 헤더로 오분류된 'Table N:/Figure N:' 캡션을 캡션 문단으로 (이미지 유무 무관)
    md = _delist_headers(md)  # 불릿으로 시작하는 잘못 승격된 헤더를 리스트 항목으로 (목록 일관성)
    wd.raw_md.write_text(md, encoding="utf-8")
    return {
        "backend": "docling",
        "ocr": ocr,
        "images": len(mapping) // 2 if mapping else 0,
        "line_numbers_dropped": line_numbers,
        "running_heads_dropped": run_heads,
        **order_meta,
        **join_stats,
    }
