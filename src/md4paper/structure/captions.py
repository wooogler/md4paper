"""그림/표 이미지와 캡션 텍스트 페어링 (마크다운 기반, 보수적).

marker는 이미지를 `![](path)`로, 캡션을 인접한 텍스트 줄로 낸다(종종 `<span id>` 앵커가 앞에 붙음).
확신이 없으면 짝짓지 않는다 — 잘못 붙이느니 그냥 둔다(graceful degradation).
"""

from __future__ import annotations

import os
import re

from md4paper.ir import FigurePair

_IMAGE_RE = re.compile(r"^!\[[^\]]*\]\(([^)]+)\)\s*$")
# <span id="page-2-1"></span>Figure 1: caption  →  word="Figure", num="1", rest="caption"
_HTML_RE = re.compile(r"<[^>]+>")
_CAPTION_RE = re.compile(
    r"^\**\s*(Figure|Fig\.?|Table|Tab\.?)\s+([0-9]+|[IVXLC]+)\**[.:]?\s*(.*)$",
    re.IGNORECASE,
)


def _strip_html(line: str) -> str:
    return _HTML_RE.sub("", line).strip()


_MD_ESCAPE_RE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!|<>~&$])")


def caption_key(line: str) -> str:
    """캡션 글자 대조용 키 — 태그·HTML 엔티티(&amp;)·마크다운 이스케이프(\\_)·공백 차이를 지운다.

    같은 캡션이 날글자(Docling caption_text)와 마크다운 줄(이스케이프됨)로 두 번 나올 때 같은 글로 보려고.
    """
    import html

    s = html.unescape(_HTML_RE.sub("", line))
    return re.sub(r"\s+", " ", _MD_ESCAPE_RE.sub(r"\1", s)).strip()


def caption_match_key(line: str) -> str:
    """글자·숫자만 남긴 대조 키 — 띄어쓰기 복원(한쪽만 'Questionnairewith…')이나 수식 변환
    ('N = 16' ↔ '$N=16$')처럼 본문 줄만 거친 손질이 있어도 같은 캡션을 같은 키로 본다."""
    return re.sub(r"[\W_]+", "", caption_key(line)).casefold()


# 렌더된 캡션 줄 — '> **Figure 3:** …'(조립본)·'Fig. 3. …'(raw.md)·'그림 3:'. 번호 뒤 '.'/':'까지 봐야
# 'Figure 3 shows …' 같은 본문(그림 바로 앞에 흔하다)을 캡션으로 오인하지 않는다.
_CAPTION_NUM_RE = re.compile(
    r"^(?:>\s*)?(?:\*\*|__)?\s*(?:Figure|Fig\.?|Table|Tab\.?|그림|표)\s*(\d+)(?:\*\*|__)?\s*[.:]",
    re.IGNORECASE)


def _caption_num(line: str) -> str | None:
    m = _CAPTION_NUM_RE.match(_strip_html(line))
    return m.group(1) if m else None


def _same_caption(a: str, b: str) -> bool:
    """두 캡션이 같은 글인가 — 한쪽만 수식으로 바뀐('N = 16' → '$N=16$') 정도의 차이는 같다고 본다."""
    from difflib import SequenceMatcher

    ka, kb = (caption_match_key(s)[:400] for s in (a, b))
    return bool(ka) and SequenceMatcher(None, ka, kb, autojunk=False).ratio() >= 0.9


def _image_name(line: str) -> str | None:
    m = _IMAGE_RE.match(line.strip())
    if not m:
        return None
    return os.path.basename(m.group(1)).replace(".ko.png", ".png")


def drop_duplicate_captions(md: str, *, same_text: bool = True,
                            images: set[str] | None = None) -> tuple[str, list[tuple[int, int, str]]]:
    """그림 앞뒤로 두 번 찍힌 캡션 중 **앞의 것**을 지운다. 반환: (새 글, [(지운 첫 줄, 지운 줄 수, 그림 이름)]).

    추출이 캡션을 그림 뒤로 옮기며 원위치를 못 지운 옛 논문용(§docling_backend._place_captions).
    '캡션 줄 · (빈 줄) · 그림 · (빈 줄) · 같은 번호의 캡션 줄'일 때만 앞 줄을 지운다 — 짝지은 캡션은 그림
    뒤의 것이라 앞 줄은 떠돌이다. same_text면 두 캡션 글이 (거의) 같아야 한다(raw·en — 앞 것만 수식
    변환을 거쳐 조금 다를 수 있다). 번역본은 두 캡션을 따로 옮겨 글이 다르므로 same_text=False에
    images(원문에서 지운 그림)로 자리를 좁힌다.
    """
    lines = md.split("\n")
    drops: list[tuple[int, int, str]] = []
    for i, ln in enumerate(lines):
        name = _image_name(ln)
        if name is None or (images is not None and name not in images):
            continue
        j = i + 1 + (i + 1 < len(lines) and not lines[i + 1].strip())  # 그림 뒤 (빈 줄 하나까지)
        k = i - 1 - (i >= 1 and not lines[i - 1].strip())  # 그림 앞
        if not (0 <= k < i and j < len(lines)) or (k > 0 and lines[k - 1].strip()):
            continue  # 앞 캡션이 한 줄 문단이 아니면(여러 줄 문단의 끝 등) 건드리지 않는다
        n_before, n_after = _caption_num(lines[k]), _caption_num(lines[j])
        if n_before is None or n_before != n_after:
            continue
        if same_text and not _same_caption(lines[k], lines[j]):
            continue
        drops.append((k, i - k, name))  # 캡션 줄 + 그림 앞 빈 줄
    for k, n, _name in reversed(drops):
        del lines[k:k + n]
    return "\n".join(lines), drops


# 서브피규어 라벨 줄: "(a) …", "- (a) …", "a) …" (한 소문자)
_SUBCAP_RE = re.compile(r"^\s*(?:[-*+]\s+)?\(?[a-z]\)(?:\s|$)")


def _match_caption(line: str) -> tuple[str, str, str] | None:
    """캡션 줄이면 (word, number, rest) 반환, 아니면 None."""
    m = _CAPTION_RE.match(_strip_html(line))
    if not m:
        return None
    return m.group(1), m.group(2), m.group(3).strip()


def _scan_below_subcaptions(
    lines: list[str], i: int, used: set[int], max_steps: int = 8
) -> tuple[int, list[int]] | None:
    """이미지 아래로 빈 줄·서브피규어 라벨을 건너뛰며 메인 캡션(Figure/Table N)을 찾는다.

    서브피규어(예: ICLR의 (a)/(b))가 이미지와 메인 캡션 사이에 낀 경우 대응.
    반환: (캡션 줄 인덱스, 건너뛴 서브캡션 줄 인덱스들) 또는 None. 일반 본문·다음 이미지를 만나면 중단.
    """
    subcaps: list[int] = []
    for step in range(1, max_steps + 1):
        j = i + step
        if j >= len(lines):
            break
        s = lines[j].strip()
        if not s:  # 빈 줄
            continue
        if j not in used and _match_caption(lines[j]):
            return j, subcaps
        if _SUBCAP_RE.match(s):  # 서브피규어 라벨 → 건너뛰고 계속
            subcaps.append(j)
            continue
        break  # 일반 본문 또는 다음 이미지 → 캡션 없음
    return None


def find_pairs(lines: list[str]) -> list[FigurePair]:
    """이미지 줄과 그 위/아래 인접한 캡션 줄을 짝짓는다.

    한 캡션 줄은 한 이미지에만 배정한다(used) — 캡션 없는 서브이미지가 옆 그림의 캡션을
    가로채 중복 배정되는 것을 막는다.
    """
    pairs: list[FigurePair] = []
    used: set[int] = set()
    for i, line in enumerate(lines):
        m = _IMAGE_RE.match(line.strip())
        if not m:
            continue
        path = m.group(1)
        if os.path.basename(path).startswith("formula-"):
            continue  # 수식 크롭은 그림이 아니다 — 짝지으면 옆 그림의 캡션을 가로챈다
        pair = FigurePair(
            kind="table" if "table" in path.lower() else "figure",
            image_path=path,
            image_line=i,
        )
        cap_j: int | None = None
        subcaps: list[int] = []
        for j in _neighbors(i, len(lines)):  # 1) 인접 ±2줄 (일반적인 경우)
            if j not in used and _match_caption(lines[j]):
                cap_j = j
                break
        if cap_j is None:  # 2) 폴백: 아래로 빈 줄·서브캡션 건너뛰며 메인 캡션 탐색
            res = _scan_below_subcaptions(lines, i, used)
            if res:
                cap_j, subcaps = res
        if cap_j is not None:
            word, num, rest = _match_caption(lines[cap_j])
            kind = "table" if word.lower().startswith(("table", "tab")) else "figure"
            pair.kind = kind
            pair.label = f"{'Table' if kind == 'table' else 'Figure'} {num}"
            pair.caption = rest or None
            pair.caption_line = cap_j
            pair.consumed_lines = subcaps  # 원위치에서 제거
            # 서브캡션 텍스트는 그림 블록 안으로 옮겨 보존 (앞 불릿·HTML 제거)
            pair.subcaptions = [re.sub(r"^\s*[-*+]\s+", "", _strip_html(lines[j])) for j in subcaps]
            used.add(cap_j)
        pairs.append(pair)
    return pairs


def _neighbors(i: int, n: int) -> list[int]:
    """이미지 줄 i 주변에서 캡션을 찾을 후보 줄 (아래 우선, 최대 2줄)."""
    out = []
    for j in (i + 1, i + 2):
        if j < n:
            out.append(j)
    for j in (i - 1, i - 2):
        if j >= 0:
            out.append(j)
    return out


# 마크다운 표 그리드 줄(| … |)과 '캡션 구분자(Table N: / Figure N.)' 감지
_GRID_RE = re.compile(r"^\s*\|.*\|\s*$")
_SEP_RE = re.compile(r"(?:Figure|Fig\.?|Table|Tab\.?)\s+(?:[0-9]+|[IVXLC]+)\s*[.:]", re.I)


def _first_nonblank(lines: list[str], i: int, direction: int) -> int | None:
    j = i + direction
    while 0 <= j < len(lines):
        if lines[j].strip():
            return j
        j += direction
    return None


def _adjacent_to_grid(lines: list[str], i: int) -> bool:
    """캡션 줄 i의 바로 위/아래(빈 줄만 건너뜀) 첫 줄이 마크다운 표 그리드면 True."""
    for d in (1, -1):
        j = _first_nonblank(lines, i, d)
        if j is not None and _GRID_RE.match(lines[j]):
            return True
    return False


def find_table_captions(lines: list[str], consumed: set[int]) -> dict[int, tuple[str, str | None]]:
    """이미지에 짝지어지지 않은 표/그림 캡션 줄 → {줄 인덱스: (label, caption)}.

    그림과 같은 caption_style을 적용할 대상. 오탐을 줄이려 명시적 구분자(':'/'.')가 있거나
    마크다운 표 그리드에 인접한 경우만 캡션으로 인정한다. consumed(이미 이미지에 붙은 캡션)는 제외.
    """
    out: dict[int, tuple[str, str | None]] = {}
    for i, line in enumerate(lines):
        if i in consumed:
            continue
        parsed = _match_caption(line)
        if not parsed:
            continue
        word, num, rest = parsed
        if not (_SEP_RE.search(_strip_html(line)) or _adjacent_to_grid(lines, i)):
            continue
        label = ("Table" if word.lower().startswith(("table", "tab")) else "Figure") + f" {num}"
        out[i] = (label, rest or None)
    return out
