"""단어 사이 공백 복원 — Docling이 붙여 버린 줄을 원본 PDF 텍스트 레이어의 띄어쓰기로 되돌린다.

Docling(docling-parse)은 공백 글리프가 아니라 글자 간격으로 단어 경계를 추정한다. 어떤 PDF에서는
블록의 첫 줄(헤딩, run-in 소제목이 붙은 문단의 첫 줄, 참고문헌 항목의 첫 줄)에서 그 추정이 통째로
실패해 "BackgroundandRelatedWork", "Weclassifiedall76,231usermessages"처럼 한 줄이 한 단어로 나온다.
같은 PDF를 pdfium으로 읽으면 공백이 멀쩡하다 — 그래서 **원본 텍스트 레이어를 공백의 진실원**으로 삼는다.

방법: PDF 전체 텍스트에서 공백을 뺀 문자열(norm)과 "이 글자 앞에 공백이 있었나" 표를 만든다.
마크다운의 수상한 토큰(소문자→대문자, 문장부호 뒤 바로 글자, 글자↔숫자 경계가 있는 긴 토큰)을
같은 식으로 정규화해 norm에서 찾고, 원본에 공백이 있던 자리마다 공백을 넣는다. 못 찾거나 출현마다
띄어쓰기가 다르면 건드리지 않는다(확신 없으면 추측하지 않는다).

마지막으로 줄 전체를 글리프 간격으로 본다(_fix_seams): 원본에서 맞닿은 글자 사이의 공백은 빼고
("Decision Points ." → "Points."), 낱말 간격만큼 떨어진 글자 사이엔 넣는다("feedbackwe" → "feedback we").
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# 원본 텍스트 레이어에서 지울 것 — pdfium이 줄 끝 하이픈 자리에 두는 U+FFFE, 소프트 하이픈, 사용자 영역 글리프
_DROP = {"￾", "\xad", "​"}
_QUOTES = {"’": "'", "‘": "'", "“": '"', "”": '"'}

# 수상한 토큰: 공백 없는 8자 이상 덩어리에 단어 경계 흔적이 있다("Weclassifiedall76,231user"),
# 또는 소문자만 14자 이상("universitypress" — 영어 단어는 이보다 긴 게 드물다; 진위는 원본이 가른다).
_SUSPECT_RE = re.compile(
    r"[a-z][A-Z]|[a-z][.,;:!?)][A-Za-z0-9(]|[a-z]['\"][A-Za-z]{2,}|[A-Za-z][0-9]|[0-9][A-Za-z]|[a-z]{14,}")
_MIN_LEN = 8
NO_GAP, SPACE, NEWLINE = 0, 1, 2
Box = tuple[float, float, float]  # (왼쪽, 오른쪽, 아래) — pt, 페이지 좌표
_NO_BOX: Box = (0.0, 0.0, 0.0)


def _norm_char(ch: str) -> str:
    if ch in _DROP:
        return ""
    if ch in _QUOTES:
        return _QUOTES[ch]
    if ord(ch) >= 0xF000:  # 사용자 영역 — 글꼴 사설 글리프(불릿 등), 비교 대상이 못 된다
        return ""
    return unicodedata.normalize("NFKC", ch)  # ﬁ → fi 등 합자 풀기


@dataclass
class Reference:
    norm: str  # 공백을 뺀 정규화 텍스트
    gap: list[int]  # gap[k]: norm[k] 앞(원본)이 NO_GAP / SPACE / NEWLINE
    font: list[str] | None = None  # font[k]: norm[k]의 글꼴 이름(서브셋 접두 뺀 것, 모르면 "")
    face: list[int] | None = None  # face[k]: norm[k]의 글꼴 객체 식별자 (같은 이름의 다른 서브셋 구분)
    size: list[float] | None = None  # size[k]: norm[k]의 글자 크기 (아래첨자 구분)
    box: list[Box] | None = None  # box[k]: norm[k] 글리프의 진행 폭 상자 (붙어 있나 판단)

    @classmethod
    def from_texts(cls, texts: list[str], fonts: list[list[str]] | None = None,
                   faces: list[list[int]] | None = None,
                   sizes: list[list[float]] | None = None,
                   boxes: list[list[Box]] | None = None) -> Reference:
        chars: list[str] = []
        gap: list[int] = []
        font: list[str] = []
        face: list[int] = []
        size: list[float] = []
        box: list[Box] = []
        pending = NO_GAP
        for pi, t in enumerate(texts):
            pending = NEWLINE  # 페이지 경계
            page_fonts = fonts[pi] if fonts and len(fonts[pi]) == len(t) else None
            page_faces = faces[pi] if faces and len(faces[pi]) == len(t) else None
            page_sizes = sizes[pi] if sizes and len(sizes[pi]) == len(t) else None
            page_boxes = boxes[pi] if boxes and len(boxes[pi]) == len(t) else None
            for ci, ch in enumerate(t):
                if ch.isspace():
                    pending = max(pending, NEWLINE if ch in "\r\n" else SPACE)
                    continue
                for c in _norm_char(ch):
                    chars.append(c)
                    gap.append(pending)
                    font.append(page_fonts[ci] if page_fonts else "")
                    face.append(page_faces[ci] if page_faces else 0)
                    size.append(page_sizes[ci] if page_sizes else 0.0)
                    box.append(page_boxes[ci] if page_boxes else _NO_BOX)
                    pending = NO_GAP
        return cls("".join(chars), gap, font if fonts else None, face if faces else None,
                   size if sizes else None, box if boxes else None)

    @classmethod
    def from_pdf(cls, src) -> Reference | None:  # noqa: ANN001 — 경로 | bytes
        from narani import pdfio

        try:
            with pdfio.open_document(src) as doc:
                pages = [_char_fonts(doc[p].get_textpage()) for p in range(len(doc))]
        except Exception:  # noqa: BLE001 — 텍스트 레이어를 못 읽으면 복원을 안 할 뿐
            return None
        texts, fonts, faces, sizes, boxes = (list(x) for x in zip(*pages)) if pages else ([],) * 5
        ref = cls.from_texts(texts, fonts, faces, sizes, boxes)
        return ref if len(ref.norm) >= 200 else None


def _char_fonts(textpage) -> tuple[str, list[str], list[int], list[float], list[Box]]:  # noqa: ANN001
    """페이지 텍스트와 글자마다 (글꼴 이름, 글꼴 객체 식별자, 크기, 진행 폭 상자).
    pdfium이 만든 공백·줄바꿈은 ("", 0, 0, _NO_BOX).

    텍스트도 글자 단위로 만든다 — get_text_range는 서로게이트 쌍(수식 이탤릭 𝑥)을 한 글자로 합쳐
    글자 인덱스와 어긋난다.

    같은 이름이라도 서브셋이 다르면 객체가 다르다 — 스몰캡 글리프만 따로 든 서브셋 등.
    """
    import ctypes

    import pypdfium2.raw as C

    names: dict[int, str] = {}
    chars: list[str] = []
    out: list[str] = []
    faces: list[int] = []
    sizes: list[float] = []
    boxes: list[Box] = []
    rect = C.FS_RECTF()
    n_chars = textpage.count_chars()
    merged = False
    for i in range(n_chars):
        if merged:  # 서로게이트 쌍의 뒷조각 — 앞 글자에 합쳤다
            merged = False
            continue
        u = C.FPDFText_GetUnicode(textpage, i)
        if 0xD800 <= u <= 0xDBFF and i + 1 < n_chars:
            lo = C.FPDFText_GetUnicode(textpage, i + 1)
            if 0xDC00 <= lo <= 0xDFFF:
                u = 0x10000 + ((u - 0xD800) << 10) + (lo - 0xDC00)
                merged = True
        chars.append(chr(u) if u else " ")
        obj = C.FPDFText_GetTextObject(textpage, i)
        font = C.FPDFTextObj_GetFont(obj) if obj else None
        key = ctypes.cast(font, ctypes.c_void_p).value if font else None
        if key is None:
            out.append("")
            faces.append(0)
            sizes.append(0.0)
            boxes.append(_NO_BOX)
            continue
        # 느슨한 상자 = 글리프의 진행 폭 × 글꼴 높이. 맞붙은 두 글자는 상자가 맞닿는다(모노스페이스의 "."도).
        ok = C.FPDFText_GetLooseCharBox(textpage, i, ctypes.byref(rect))
        boxes.append((rect.left, rect.right, rect.bottom) if ok else _NO_BOX)
        if key not in names:
            n = C.FPDFFont_GetBaseFontName(font, None, 0)
            buf = ctypes.create_string_buffer(n)
            C.FPDFFont_GetBaseFontName(font, buf, n)
            names[key] = re.sub(r"^[A-Z]{6}\+", "", buf.value.decode("latin-1"))  # 서브셋 접두
        out.append(names[key])
        faces.append(key)
        sizes.append(float(C.FPDFText_GetFontSize(textpage, i)))
    return "".join(chars), out, faces, sizes, boxes


def _normalize_token(tok: str) -> str:
    return "".join(_norm_char(c) for c in tok)


def respace_token(tok: str, ref: Reference) -> str | None:
    """원본 띄어쓰기로 되살린 토큰. 못 찾았거나 출현마다 다르면 None.

    통째로 못 찾으면(표 셀처럼 줄바꿈 사이에 다른 열의 글자가 끼어든 경우) 찾아지는 가장 긴 앞조각부터
    차례로 맞춘다. 조각 경계는 줄바꿈 자리라 단어 사이로 보고 공백을 넣는다.
    원본에서 줄바꿈이던 자리는 공백을 넣지 않는다 — Docling이 두 줄을 공백 없이 이었다면 그건
    의도한 결합(하이픈 풀기, "langid.\npy")이다. 실패한 건 한 줄 안의 띄어쓰기다.
    """
    key = _normalize_token(tok)
    if len(key) < _MIN_LEN or not _SUSPECT_RE.search(key):
        return None
    pieces = _match_pieces(key, ref)
    if pieces is None:
        return None
    out: list[str] = []
    prev_found = False
    for piece, start in pieces:
        if out and prev_found and start >= 0 and out[-1][-1:].isalnum() and piece[:1].isalnum():
            out.append(" ")  # 찾은 조각 사이의 경계 = 줄바꿈 자리 = 단어 사이
        out.append(_spaced(piece, ref, start) if start >= 0 else piece)
        prev_found = start >= 0
    spaced = "".join(out)
    if spaced == key:
        return None
    return _apply_to_original(tok, key, spaced)


def _match_pieces(key: str, ref: Reference) -> list[tuple[str, int]] | None:
    """key를 원본에서 찾아지는 조각들로 덮는다. [(조각, 원본 시작; 못 찾은 글자는 -1)].

    원본 텍스트 레이어에 없는 글자(수식 이탤릭 𝑛처럼 사설 글리프로 박힌 것)는 한 글자씩 건너뛴다.
    찾은 글자가 _MIN_LEN에 못 미치면 None — 그런 토큰은 판단할 근거가 없다.
    """
    pieces: list[tuple[str, int]] = []
    skipped = ""
    rest = key
    matched = 0
    while rest:
        found = 0
        for length in range(len(rest), _MIN_LEN - 1, -1):
            start = _unique_find(rest[:length], ref)
            if start is not None:
                found = length
                break
        if not found:
            skipped += rest[0]
            rest = rest[1:]
            continue
        if skipped:
            pieces.append((skipped, -1))
            skipped = ""
        pieces.append((rest[:found], start))
        matched += found
        rest = rest[found:]
    if skipped:
        pieces.append((skipped, -1))
    return pieces if matched >= _MIN_LEN else None


def _unique_find(piece: str, ref: Reference) -> int | None:
    """piece가 원본에 있고, 출현마다 띄어쓰기가 같으면 첫 위치. 없거나 엇갈리면 None."""
    start = ref.norm.find(piece)
    if start < 0:
        return None
    last = ref.norm.rfind(piece)
    if last != start and _spaced(piece, ref, last) != _spaced(piece, ref, start):
        return None
    return start


def _spaced(key: str, ref: Reference, start: int) -> str:
    out = [key[0]]
    for k in range(1, len(key)):
        if ref.gap[start + k] == SPACE:
            out.append(" ")
        out.append(key[k])
    return "".join(out)


def _apply_to_original(tok: str, key: str, spaced: str) -> str:
    """공백만 원래 토큰에 끼운다(따옴표·합자 정규화가 결과에 새지 않게). 글자 수가 어긋나면 정규화본을 쓴다."""
    if len(key) != len(tok) or any(len(_norm_char(c)) != 1 for c in tok):
        return spaced
    out: list[str] = []
    i = 0
    for c in spaced:
        if c == " " and (i >= len(key) or key[i] != " "):
            out.append(" ")
            continue
        out.append(tok[i])
        i += 1
    return "".join(out)


# 글꼴이 바뀌는 자리에서 쪼개진 단어 — 스몰캡은 첫 대문자만 보통 글리프라 Docling이 그 경계에 공백을
# 넣는다("C onversational", "P ref A ct"). 그 사이 하이픈을 잃기도 한다("M agenticUI" ← Magentic-UI).
# 이번엔 반대 방향: 원본에 없는 공백을 뺀다. 판단은 역시 원본 텍스트 레이어가 하고, 원본에서
# **한 글꼴로 짠 한 단어**이고, Docling이 공백을 넣은 자리마다 **글꼴 객체가 바뀔 때만** 잇는다.
# 수식 변수 + 본문 단어("C using")는 글꼴 이름이 달라, pdfium이 띄어쓰기를 놓친 평범한 자리
# ("I started" → pdfium "Istarted")는 같은 글꼴 객체가 이어져, 아래첨자("D train")는 크기가 달라 걸러진다.
_SPLIT_RE = re.compile(r"(?<![\w-])[A-Z](?: [A-Za-z]+){1,3}(?![\w-])")
_JOIN_MIN_LEN = 4


def _bounded(ref: Reference, start: int, end: int) -> bool:
    """원본에서 [start, end)가 단어 하나로 떨어져 있나 (앞뒤가 다른 글자와 붙어 있지 않다)."""
    before = start == 0 or ref.gap[start] != NO_GAP or not ref.norm[start - 1].isalnum()
    after = end >= len(ref.norm) or ref.gap[end] != NO_GAP or not ref.norm[end].isalnum()
    return before and after


def _one_word(ref: Reference, start: int, end: int) -> bool:
    """원본에서 [start, end)가 공백 없이, 한 글꼴로 짠 한 단어인가."""
    fonts = set(ref.font[start:end]) if ref.font else {""}
    return all(g == NO_GAP for g in ref.gap[start + 1:end]) and len(fonts) == 1 and "" not in fonts


def _face_breaks(ref: Reference, start: int, key: str, cuts: list[int]) -> bool:
    """Docling이 끊은 자리(cuts: key 인덱스)마다 원본에서 글꼴 객체가 바뀌나 (끊긴 까닭이 있나)."""
    if not ref.face:
        return False
    pos: list[int] = []  # key 글자 → 원본 위치 (사이에 끼운 하이픈은 건너뛴다)
    ri = start
    for ch in key:
        while ref.norm[ri] != ch:
            ri += 1
        pos.append(ri)
        ri += 1
    def same_size(a: int, b: int) -> bool:
        if not ref.size:
            return True
        hi = max(ref.size[a], ref.size[b])
        return hi > 0 and abs(ref.size[a] - ref.size[b]) <= 0.05 * hi

    return all(ref.face[pos[c - 1]] != ref.face[pos[c]] and same_size(pos[c - 1], pos[c]) for c in cuts)


def rejoin_words(tokens: list[str], ref: Reference) -> str | None:
    """쪼개진 토큰들의 원본 표기(하이픈 포함). 원본에서 한 단어로 찾아지고 출현마다 같을 때만."""
    parts = [_normalize_token(t) for t in tokens]
    key = "".join(parts)
    if len(key) < _JOIN_MIN_LEN:
        return None
    cuts = [sum(map(len, parts[:i])) for i in range(1, len(parts))]
    found = {
        m.group(0)
        for m in re.finditer("-?".join(map(re.escape, key)), ref.norm)  # 잃어버린 하이픈 허용
        if _bounded(ref, m.start(), m.end()) and _one_word(ref, m.start(), m.end())
        and _face_breaks(ref, m.start(), key, cuts)
    }
    return found.pop() if len(found) == 1 else None


def _rejoin_line(line: str, ref: Reference) -> tuple[str, int]:
    out: list[str] = []
    pos = fixed = 0
    search = 0
    while m := _SPLIT_RE.search(line, search):
        # 다음 탐색은 첫 토큰 뒤에서 — "M anual and M agenticUI"에서 묶이지 않은 뒤쪽 M도 다시 본다
        search = m.start() + 1
        tokens = m.group(0).split(" ")
        if not tokens[1][:1].islower():  # "C onversational"처럼 대문자 뒤가 소문자로 이어질 때만
            continue
        for n in range(len(tokens), 1, -1):  # 길게 묶이는 것부터 ("P ref A ct" → PrefAct)
            joined = rejoin_words(tokens[:n], ref)
            if joined is not None:
                out.append(line[pos:m.start()] + joined)
                pos = search = m.start() + len(" ".join(tokens[:n]))
                fixed += 1
                break
    out.append(line[pos:])
    return "".join(out), fixed


# 글리프 간격으로 가르는 띄어쓰기 — Docling(docling-parse)은 글꼴·텍스트 객체가 바뀌는 자리에서 줄 셀을
# 끊고 공백으로 이어 붙이고("Decision Points .", "( Identification"), 버전에 따라 멀쩡한 낱말 사이 공백을
# 놓친다("feedbackwe", "Inparticular"). 구두점·대소문자 규칙이 아니라 원본 글리프로 가른다: 공백 양옆(또는
# 토큰 안 글자 사이)을 앞뒤 낱말째 원본에서 찾아, 두 글리프의 진행 폭 상자가
#   · 같은 기준선에서 맞닿고 pdfium도 공백을 안 봤으면 → Docling의 공백을 뺀다
#   · 낱말 간격만큼 떨어져 있고 pdfium도 공백을 봤으면 → 붙은 낱말 사이에 공백을 넣는다
# 출현마다 판정이 다르면 둔다.
_SEAM_CONTEXT = 12  # 원본 위치를 하나로 좁힐 만큼 앞뒤에서 모으는 글자 수
_SEAM_MAX_HITS = 8
_SEAM_TOL = 0.1  # 맞닿음 허용 간격 (글자 크기 대비)
_WORD_GAP = 0.06  # 낱말 사이로 볼 최소 간격 — 한 낱말 안 글자의 진행 폭 상자는 거의 맞닿는다(±0.03em)


def _glyph_pair(ref: Reference, i: int) -> tuple[float, float, float, float, float] | None:
    """norm[i-1]→norm[i]의 (가로 간격, 기준선 차, 앞 크기, 뒤 크기, 큰 크기). 상자를 모르면 None."""
    if i <= 0 or not ref.box or not ref.size or _NO_BOX in (ref.box[i - 1], ref.box[i]):
        return None
    (_, r0, b0), (l1, _, b1) = ref.box[i - 1], ref.box[i]
    s0, s1 = ref.size[i - 1], ref.size[i]
    hi = max(s0, s1)
    return (l1 - r0, abs(b0 - b1), s0, s1, hi) if hi > 0 else None


def _touching(ref: Reference, i: int) -> bool:
    """원본에서 norm[i-1]과 norm[i]가 공백 없이 맞닿은 같은 줄의 글리프인가.

    크기가 같으면 기준선도 같아야 한다. 크기가 다르면 작은 첨자에서 본문 크기의 **글자 아닌 것**으로
    돌아오는 자리만 본다("LLM^a ," → "LLM^a,"). 본문에서 첨자로 들어가는 자리("x i")는 수식 복원이
    따로 다루고, 첨자 뒤에 바로 붙은 글자는 표식과 낱말의 경계다("¹Department", 매크로가 공백을 먹은
    "TEXTGRADmakes"). 첨자는 기준선이 달라 pdfium이 줄바꿈으로 보기도 하므로, 그때는 상자만 믿는다.
    """
    pair = _glyph_pair(ref, i)
    if pair is None or ref.gap[i] == SPACE:
        return False
    dx, dy, s0, s1, hi = pair
    if abs(s0 - s1) <= 0.05 * hi:
        if ref.gap[i] != NO_GAP or dy >= 0.2 * hi:
            return False
    elif s0 > s1 or ref.norm[i].isalnum() or dy >= 0.6 * hi:
        return False
    return -_SEAM_TOL * hi < dx < _SEAM_TOL * hi


def _apart(ref: Reference, i: int) -> bool:
    """원본에서 norm[i-1]과 norm[i]가 같은 줄·같은 크기에서 낱말 간격만큼 떨어진 글자인가."""
    pair = _glyph_pair(ref, i)
    if pair is None or ref.gap[i] != SPACE:
        return False
    dx, dy, s0, s1, hi = pair
    return abs(s0 - s1) <= 0.05 * hi and dy < 0.2 * hi and _WORD_GAP * hi <= dx < 2 * hi


def _occurrences(key: str, ref: Reference) -> list[int] | None:
    """key의 원본 출현 위치들. 없거나 너무 많으면(위치를 못 좁힘) None."""
    hits: list[int] = []
    start = ref.norm.find(key)
    while start >= 0:
        hits.append(start)
        if len(hits) > _SEAM_MAX_HITS:
            return None
        start = ref.norm.find(key, start + 1)
    return hits or None


def _locate(left: str, core: str, right: str, ref: Reference) -> tuple[str, list[int]] | None:
    """left+core+right를 원본에서 찾는다. core는 늘 넣고, 문맥 낱말에 원본에 없는 글자(엔티티·마크업)가
    끼어 못 찾으면 바깥 문맥을 한쪽씩 덜어 다시 찾는다. 반환: (실제로 쓴 왼쪽 문맥, 출현 위치들)."""
    for lc, rc in ((left, right), (left, ""), ("", right)):
        if len(lc + core + rc) < _SEAM_CONTEXT:
            continue
        hits = _occurrences(lc + core + rc, ref)
        if hits is not None:
            return lc, hits
    return None


def _seam_verdict(left: str, prev: str, word: str, right: str, ref: Reference) -> bool:
    """prev|word 이음매가 원본에서 맞닿은 글리프인가 — 모든 출현이 그렇다고 할 때만 True."""
    found = _locate(left, prev + word, right, ref)
    return found is not None and all(
        _touching(ref, h + len(found[0]) + len(prev)) for h in found[1])


def _split_points(left: str, word: str, right: str, ref: Reference) -> list[int]:
    """word 안에서 원본이 낱말 간격으로 떨어뜨린 자리(word 인덱스). 모든 출현이 같을 때만."""
    found = _locate(left, word, right, ref)
    if found is None:
        return []
    left, hits = found
    found: set[tuple[int, ...]] = set()
    for h in hits:
        base = h + len(left)
        found.add(tuple(k for k in range(1, len(word))
                        if word[k - 1].isalnum() and word[k].isalnum() and _apart(ref, base + k)))
    if len(found) != 1:
        return []
    cuts = list(found.pop())
    # 자간을 벌린 제목("A B S T R A C T")은 글자마다 떨어져 있다 — 낱말을 가르는 게 아니다
    pieces = [b - a for a, b in zip([0, *cuts], [*cuts, len(word)])]
    return cuts if len(cuts) == 1 or min(pieces, default=0) >= 2 else []


def _context(norms: list[str], k: int, step: int) -> str:
    """norms[k]에서 step 방향(-1 왼쪽, +1 오른쪽)으로 _SEAM_CONTEXT자까지 이어 붙인 문맥."""
    out = ""
    k += step
    while len(out) < _SEAM_CONTEXT and 0 <= k < len(norms) and norms[k]:
        out = norms[k] + out if step < 0 else out + norms[k]
        k += step
    return out


def _fix_seams(line: str, ref: Reference) -> tuple[str, int]:
    """줄의 띄어쓰기를 원본 글리프 간격대로 — 맞닿은 자리의 공백은 빼고, 떨어진 자리엔 넣는다.

    반환: (줄, 고친 자리 수).
    """
    if not ref.box:
        return line, 0
    toks = line.split(" ")
    norms = [
        "" if not t or _SKIP_TOKEN_RE.search(t) or _MD_MARK_RE.fullmatch(t) or "$" in t
        else _normalize_token(_MD_ESCAPE_RE.sub(r"\1", t))  # "create \_query" — 원본엔 \ 가 없다
        for t in toks
    ]
    glue = [False] * len(toks)  # glue[k]: toks[k-1]과 toks[k] 사이 공백을 뺀다
    cuts: list[list[int]] = [[] for _ in toks]  # cuts[k]: toks[k] 안에 공백을 넣을 자리
    for k, word in enumerate(norms):
        if not word:
            continue
        left, right = _context(norms, k, -1), _context(norms, k, +1)
        if k and norms[k - 1]:
            glue[k] = _seam_verdict(_context(norms, k - 1, -1), norms[k - 1], word, right, ref)
        # 글자 수가 그대로인 토큰만 (합자·따옴표 정규화로 길이가 바뀌면 자리를 못 옮긴다)
        if len(word) >= 4 and len(word) == len(toks[k]):
            cuts[k] = _split_points(left, word, right, ref)
    if not any(glue) and not any(cuts):
        return line, 0
    out: list[str] = []
    for k, t in enumerate(toks):
        if k and not glue[k]:
            out.append(" ")
        last = 0
        for c in cuts[k]:
            out.append(t[last:c] + " ")
            last = c
        out.append(t[last:])
    return "".join(out), sum(glue) + sum(map(len, cuts))


_SKIP_LINE_RE = re.compile(r"^\s*(!\[|<|\$\$|```|\|?\s*:?-{3,})")  # 그림·HTML·수식 블록·코드·표 구분선
_MD_ESCAPE_RE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!|<>])")  # 마크다운 이스케이프
_MD_MARK_RE = re.compile(r"#+|[-*+>|]")  # 헤딩·목록·인용·표 표식 — 원본에 없는 글자
_SKIP_TOKEN_RE = re.compile(r"://|\]\(|^\$|^<|^\[\^|^`")


def restore_spaces(md: str, ref: Reference | None) -> tuple[str, int]:
    """마크다운 본문의 띄어쓰기를 원본대로 — 붙은 토큰은 떼고 쪼개진 단어는 잇는다. 반환: (본문, 고친 수)."""
    if ref is None:
        return md, 0
    fixed = 0
    out_lines: list[str] = []
    in_code = False
    for line in md.split("\n"):
        if line.startswith("```"):
            in_code = not in_code
        if in_code or _SKIP_LINE_RE.match(line):
            out_lines.append(line)
            continue
        parts = re.split(r"(\s+)", line)
        for i, tok in enumerate(parts):
            if not tok or tok.isspace() or _SKIP_TOKEN_RE.search(tok):
                continue
            # 마크다운 강조 표식은 떼고 본다 — "**Finding7.**" 안쪽만 비교
            m = re.match(r"^(\W*?)([A-Za-z0-9].*?[A-Za-z0-9.,;:!?)\]])(\W*?)$", tok)
            if not m:
                continue
            core = respace_token(m.group(2), ref)
            if core is not None:
                parts[i] = m.group(1) + core + m.group(3)
                fixed += 1
        line, rejoined = _rejoin_line("".join(parts), ref)
        line, seams = _fix_seams(line, ref)
        fixed += rejoined + seams
        out_lines.append(line)
    return "\n".join(out_lines), fixed
