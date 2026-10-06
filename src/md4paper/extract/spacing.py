"""단어 사이 공백 복원 — Docling이 붙여 버린 줄을 원본 PDF 텍스트 레이어의 띄어쓰기로 되돌린다.

Docling(docling-parse)은 공백 글리프가 아니라 글자 간격으로 단어 경계를 추정한다. 어떤 PDF에서는
블록의 첫 줄(헤딩, run-in 소제목이 붙은 문단의 첫 줄, 참고문헌 항목의 첫 줄)에서 그 추정이 통째로
실패해 "BackgroundandRelatedWork", "Weclassifiedall76,231usermessages"처럼 한 줄이 한 단어로 나온다.
같은 PDF를 pdfium으로 읽으면 공백이 멀쩡하다 — 그래서 **원본 텍스트 레이어를 공백의 진실원**으로 삼는다.

방법: PDF 전체 텍스트에서 공백을 뺀 문자열(norm)과 "이 글자 앞에 공백이 있었나" 표를 만든다.
마크다운의 수상한 토큰(소문자→대문자, 문장부호 뒤 바로 글자, 글자↔숫자 경계가 있는 긴 토큰)을
같은 식으로 정규화해 norm에서 찾고, 원본에 공백이 있던 자리마다 공백을 넣는다. 못 찾거나 출현마다
띄어쓰기가 다르면 건드리지 않는다(확신 없으면 추측하지 않는다).
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

    @classmethod
    def from_texts(cls, texts: list[str], fonts: list[list[str]] | None = None,
                   faces: list[list[int]] | None = None,
                   sizes: list[list[float]] | None = None) -> Reference:
        chars: list[str] = []
        gap: list[int] = []
        font: list[str] = []
        face: list[int] = []
        size: list[float] = []
        pending = NO_GAP
        for pi, t in enumerate(texts):
            pending = NEWLINE  # 페이지 경계
            page_fonts = fonts[pi] if fonts and len(fonts[pi]) == len(t) else None
            page_faces = faces[pi] if faces and len(faces[pi]) == len(t) else None
            page_sizes = sizes[pi] if sizes and len(sizes[pi]) == len(t) else None
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
                    pending = NO_GAP
        return cls("".join(chars), gap, font if fonts else None, face if faces else None,
                   size if sizes else None)

    @classmethod
    def from_pdf(cls, src) -> Reference | None:  # noqa: ANN001 — 경로 | bytes
        from md4paper import pdfio

        try:
            with pdfio.open_document(src) as doc:
                pages = [_char_fonts(doc[p].get_textpage()) for p in range(len(doc))]
        except Exception:  # noqa: BLE001 — 텍스트 레이어를 못 읽으면 복원을 안 할 뿐
            return None
        texts, fonts, faces, sizes = (list(x) for x in zip(*pages)) if pages else ([], [], [], [])
        ref = cls.from_texts(texts, fonts, faces, sizes)
        return ref if len(ref.norm) >= 200 else None


def _char_fonts(textpage) -> tuple[str, list[str], list[int], list[float]]:  # noqa: ANN001
    """페이지 텍스트와 글자마다 (글꼴 이름, 글꼴 객체 식별자, 크기). pdfium이 만든 공백·줄바꿈은 ("", 0, 0).

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
            continue
        if key not in names:
            n = C.FPDFFont_GetBaseFontName(font, None, 0)
            buf = ctypes.create_string_buffer(n)
            C.FPDFFont_GetBaseFontName(font, buf, n)
            names[key] = re.sub(r"^[A-Z]{6}\+", "", buf.value.decode("latin-1"))  # 서브셋 접두
        out.append(names[key])
        faces.append(key)
        sizes.append(float(C.FPDFText_GetFontSize(textpage, i)))
    return "".join(chars), out, faces, sizes


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


_SKIP_LINE_RE = re.compile(r"^\s*(!\[|<|\$\$|```|\|?\s*:?-{3,})")  # 그림·HTML·수식 블록·코드·표 구분선
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
        fixed += rejoined
        out_lines.append(line)
    return "\n".join(out_lines), fixed
