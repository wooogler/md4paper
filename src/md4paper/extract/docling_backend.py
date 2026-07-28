"""Docling 추출 백엔드 (기본).

born-digital 논문에서 실측 최고 품질: 유니코드 무손실(수학 이탤릭 보존), 2단 조판 읽기 순서 정확,
헤더 감지 최다, 그림 파일 추출. 라이선스 MIT.
"""

from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path

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
_ORDERED_LI_RE = re.compile(r"^\d+[.)]\s")
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


def _join_broken_paragraphs(md: str) -> str:
    """열·페이지 경계나 footer/저작권 블록 제거로 끊긴 문단을 재결합.

    2단 조판에서 초록 같은 문단이 컬럼 경계에서 쪼개지고, 그 사이의 저작권 boilerplate를
    제거하면 "…test set. A" / "user study…" 처럼 빈 줄로 나뉜 두 문단이 남는다.
    앞 문단이 종결부호 없이(그리고 충분히 길게) 끝나고 뒤 문단이 소문자로 시작하면 한 문장의
    연속으로 보고 공백 결합. 헤더·리스트·표·이미지·코드·이메일 블록은 건드리지 않는다.
    """
    blocks = _split_blocks(md)
    merged: list[str] = []
    for b in blocks:
        prev = merged[-1] if merged else ""
        if (merged and _is_prose_block(prev) and _is_prose_block(b)
                and len(prev.rstrip()) >= _MIN_CONT_LEN
                and _CONT_END_RE.search(prev.rstrip())
                and _CONT_START_RE.match(b.lstrip())):
            merged[-1] = prev.rstrip() + " " + b.lstrip()
        else:
            merged.append(b)
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


def _body_labels() -> set:
    """본문 export에 쓸 라벨 집합 = docling 기본 export 라벨 − 제외 라벨 + 캡션.

    caption은 DEFAULT_EXPORT_LABELS에 없어(!) 그대로 두면 그림/표 캡션이 통째로 누락된다.
    """
    from docling_core.types.doc import DocItemLabel
    from docling_core.types.doc.document import DEFAULT_EXPORT_LABELS

    labels = {lbl for lbl in DEFAULT_EXPORT_LABELS if lbl.value not in _EXCLUDE_LABELS}
    labels.add(DocItemLabel.CAPTION)
    return labels


# 로고/배지 판정 크기(pt) — 이보다 작은 그림은 CC 라이선스 배지·학회 로고 등으로 보고 제거
_LOGO_MAX_W = 120
_LOGO_MAX_H = 120


def _relocate_footer_blocks(document) -> tuple[list[str], list[str]]:  # noqa: ANN001 — DoclingDocument
    """각주 수집 + 저작권 boilerplate/로고 배지를 본문에서 제거.

    - footnote 라벨: 문서 끝으로 모으기 위해 텍스트만 수집.
    - 저작권/라이선스 boilerplate(text 라벨): 내용 기반으로 삭제(단, venue/연도가 들어 있어 서지용으로 보관).
    - 작은 그림(CC 배지·학회 로고): 크기 기준으로 삭제(진짜 figure는 크므로 보존).
    반환: (문서 끝에 붙일 각주 목록, 서지 추출용 boilerplate 목록).
    """
    footnotes: list[str] = []
    boilerplate: list[str] = []
    to_remove: list = []
    for it in getattr(document, "texts", []):
        label = str(getattr(it, "label", "")).replace("DocItemLabel.", "").lower()
        text = (getattr(it, "text", "") or "").strip()
        if not text:
            continue
        if label == "footnote":
            footnotes.append(text)
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
    return footnotes, boilerplate


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
    footnotes, boilerplate = _relocate_footer_blocks(result.document)
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
        md = _join_broken_paragraphs(md)  # 컬럼/footer 제거로 끊긴 문단 재결합
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
    return {"backend": "docling", "ocr": ocr, "images": len(mapping) // 2 if mapping else 0}
