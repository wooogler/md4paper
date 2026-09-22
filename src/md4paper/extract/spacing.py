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

    @classmethod
    def from_texts(cls, texts: list[str]) -> Reference:
        chars: list[str] = []
        gap: list[int] = []
        pending = NO_GAP
        for t in texts:
            pending = NEWLINE  # 페이지 경계
            for ch in t:
                if ch.isspace():
                    pending = max(pending, NEWLINE if ch in "\r\n" else SPACE)
                    continue
                for c in _norm_char(ch):
                    chars.append(c)
                    gap.append(pending)
                    pending = NO_GAP
        return cls("".join(chars), gap)

    @classmethod
    def from_pdf(cls, src) -> Reference | None:  # noqa: ANN001 — 경로 | bytes
        from md4paper import pdfio

        try:
            with pdfio.open_document(src) as doc:
                texts = [doc[p].get_textpage().get_text_range() for p in range(len(doc))]
        except Exception:  # noqa: BLE001 — 텍스트 레이어를 못 읽으면 복원을 안 할 뿐
            return None
        ref = cls.from_texts(texts)
        return ref if len(ref.norm) >= 200 else None


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


_SKIP_LINE_RE = re.compile(r"^\s*(!\[|<|\$\$|```|\|?\s*:?-{3,})")  # 그림·HTML·수식 블록·코드·표 구분선
_SKIP_TOKEN_RE = re.compile(r"://|\]\(|^\$|^<|^\[\^|^`")


def restore_spaces(md: str, ref: Reference | None) -> tuple[str, int]:
    """마크다운 본문의 붙은 토큰을 원본 띄어쓰기로 되살린다. 반환: (본문, 고친 토큰 수)."""
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
        out_lines.append("".join(parts))
    return "\n".join(out_lines), fixed
