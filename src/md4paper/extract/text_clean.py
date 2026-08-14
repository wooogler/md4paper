"""추출 백엔드 공용 — 텍스트 정리·복구 유틸과 공통 예외.

어떤 백엔드를 쓰든 추출 직후 같은 정리를 거친다: 수학용 유니코드 정규화, 쪼개진 HTML 엔티티 복구,
(필요 시) PDF 텍스트 레이어로 깨진 글자 복구.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

MARKDOWN_SUFFIXES = {".md", ".markdown"}


class ExtractError(RuntimeError):
    """추출 실패 — 호출부가 사용자에게 그대로 보여준다."""


# `<`가 `&lt;`로 나오다 `&`만 태그에 갇혀 쪼개진 경우: `<sup>&</sup>lt;` → `<`
_SPLIT_ENTITY_RE = re.compile(r"<(\w+)>&</\1>(lt|gt|amp|quot);")
_PLAIN_ENTITY_RE = re.compile(r"&(lt|gt|amp|quot);")
_ENTITY_CHAR = {"lt": "<", "gt": ">", "amp": "&", "quot": '"'}


def repair_entities(text: str) -> str:
    """태그로 쪼개졌거나 이스케이프된 HTML 엔티티를 실제 문자로 복구."""
    text = _SPLIT_ENTITY_RE.sub(lambda m: _ENTITY_CHAR[m.group(2)], text)
    return _PLAIN_ENTITY_RE.sub(lambda m: _ENTITY_CHAR[m.group(1)], text)


def normalize_math_letters(text: str) -> str:
    """수학용 알파벳 기호(U+1D400–U+1D7FF: 𝑡 𝑝 𝛼)를 ASCII/표준 문자로.

    LaTeX 논문의 이탤릭 수식 글자가 이 블록으로 나오면 일반 폰트에서 tofu로 보인다.
    이 블록만 NFKC 정규화하므로 위첨자(²)·분수(½)는 보존된다.
    """
    if not any(0x1D400 <= ord(ch) <= 0x1D7FF for ch in text):
        return text
    return "".join(
        unicodedata.normalize("NFKC", ch) if 0x1D400 <= ord(ch) <= 0x1D7FF else ch
        for ch in text
    )


def clean_extracted(text: str) -> str:
    """추출 직후 공통 정리 — 수학용 유니코드 정규화 + 엔티티 복구."""
    return repair_entities(normalize_math_letters(text))


_GARBLED_RE = re.compile("�+")


def _norm_for_match(s: str) -> str:
    s = re.sub(r"[*_`]", "", s)
    s = re.sub(r"-\s*\n\s*", "", s)  # 줄바꿈 하이픈 결합
    return re.sub(r"\s+", " ", s)


def repair_garbled_from_pdf(md: str, pdf: Path, anchor: int = 18) -> tuple[str, int]:
    """U+FFFD로 깨진 글자를 PDF 텍스트 레이어(pypdfium2)에서 찾아 복구. 안전망.

    반환: (복구된 텍스트, 복구 개수)
    """
    if "�" not in md:
        return md, 0
    from md4paper import pdfio

    try:
        pdf_norm = _norm_for_match(pdfio.full_text(pdf))
    except Exception:  # noqa: BLE001 — 손상 PDF·pypdfium2 미설치 등
        return md, 0

    out: list[str] = []
    pos = fixed = 0
    for m in _GARBLED_RE.finditer(md):
        before = _norm_for_match(md[max(0, m.start() - 80) : m.start()])[-anchor:]
        after = _norm_for_match(md[m.end() : m.end() + 80])[:anchor]
        repl = None
        if len(before) >= 8 and len(after) >= 8:
            found = re.search(re.escape(before) + r"(.{1,6}?)" + re.escape(after), pdf_norm)
            if found:
                repl = found.group(1)
        out.append(md[pos : m.start()])
        out.append(repl if repl is not None else m.group(0))
        fixed += repl is not None
        pos = m.end()
    out.append(md[pos:])
    return "".join(out), fixed


# --- 2바이트 코드가 한 글자로 잘못 묶인 깨짐 (docling + 구형 CID 폰트) -------------
# "Looki" 6바이트가 UTF-16처럼 3글자로 묶여 "⁌潯歩"가 된다. 두 바이트가 모두 출력 가능한
# ASCII일 때만 후보로 본다 → 진짜 한자·한글은 대부분 여기서 걸러진다(교체는 PDF 확인 후에만).
_PAIR_GAP = r".{0,40}?"  # 복호된 조각은 단어의 앞부분만 남으므로 사이 간격을 허용
_MAX_HEAD_FRAGS = 6      # 정규식 폭발 방지 — 앞쪽 조각 몇 개로 위치를 잡는다
_MAX_INLINE_FRAGS = 12   # 이 이하면 조각 전부를 한 패턴에 (앞/뒤로 나누면 겹쳐서 오정렬)


def _pair_bytes(ch: str) -> tuple[int, int] | None:
    """이 글자가 'ASCII 두 바이트가 묶인 것'으로 보이면 그 두 바이트."""
    o = ord(ch)
    hi, lo = o >> 8, o & 0xFF
    return (hi, lo) if hi >= 0x20 and 0x20 <= lo < 0x7F and hi < 0x7F else None


def _unpair(text: str) -> str:
    """묶인 글자를 원래 두 ASCII 문자로 되돌린다 (아닌 글자는 그대로)."""
    out: list[str] = []
    for ch in text:
        pair = _pair_bytes(ch)
        out.extend([chr(pair[0]), chr(pair[1])] if pair else [ch])
    return "".join(out)


def _mojibake_span(line: str, min_ratio: float, min_chars: int) -> tuple[int, int] | None:
    """줄에서 깨진 구간 (처음~마지막 깨진 글자). 비율·개수 기준 미달이면 None."""
    idx = [i for i, ch in enumerate(line) if ord(ch) > 0xFF and _pair_bytes(ch)]
    visible = [ch for ch in line if not ch.isspace()]
    if len(idx) < min_chars or not visible or len(idx) / len(visible) < min_ratio:
        return None
    return idx[0], idx[-1] + 1


def _find_in_pdf(lossy: str, flat: str) -> str | None:
    """복호된(글자가 빠진) 문자열로 PDF 텍스트에서 원문 구간을 찾는다. 애매하면 None."""
    # 조각은 원래 단어의 앞부분만 남은 것이라 3글자까지 단서로 쓴다("Rou" ← "Rouse").
    frags = re.findall(r"[A-Za-z]{3,}", lossy)
    if not frags:
        return None
    # 조각이 적으면 전부 한 패턴으로(정확). 많으면 앞 6개로 시작을, 뒤 3개로 끝을 잡는다
    # — 이때 앞뒤가 겹치면 같은 단어를 두 번 요구해 엉뚱한 곳까지 삼키므로 겹치지 않을 때만 나눈다.
    split = len(frags) > _MAX_INLINE_FRAGS
    head = frags[:_MAX_HEAD_FRAGS] if split else frags
    pat = r"\b" + _PAIR_GAP.join(map(re.escape, head)) + r"\w*"
    matches = list(re.finditer(pat, flat))
    if not matches:
        return None
    if split:  # 뒤쪽 조각으로 끝 위치를 잡는다
        tail = r"\b" + _PAIR_GAP.join(map(re.escape, frags[-3:])) + r"\w*"
        found = set()
        for m in matches:
            end = re.search(tail, flat[m.end():])
            if end:
                found.add(flat[m.start() : m.end() + end.end()])
    else:
        found = {m.group(0) for m in matches}
    # 후보가 여럿이어도 결과 문자열이 같으면(머리말 반복 등) 안전하다. 다르면 추측하지 않는다.
    if len(found) != 1:
        return None
    text = found.pop()
    # 길이가 터무니없으면 오정렬 — 빠진 글자를 감안해도 원문이 4배를 넘진 않는다
    return text if len(lossy) / 2 <= len(text) <= len(lossy) * 4 else None


def repair_mojibake_from_pdf(md: str, pdf: Path, min_ratio: float = 0.3,
                             min_chars: int = 3) -> tuple[str, int, int]:
    """2바이트 코드가 CJK로 잘못 묶인 줄을 PDF 텍스트 레이어의 원문으로 교체.

    docling이 구형 CID 폰트 PDF에서 앞부분 텍스트를 통째로 이렇게 뽑는 일이 있다(제목·초록이
    한자로 보임). pypdfium2가 읽는 텍스트 레이어는 멀쩡한 경우가 많으므로, 깨진 줄을 바이트로
    되돌려(글자가 일부 빠진 단서) 원문 위치를 찾고 그 구간을 그대로 가져온다.
    **PDF에서 한 곳으로 확정될 때만** 교체하므로 진짜 한자 문서나 애매한 줄은 건드리지 않는다.

    반환: (복구된 텍스트, 복구한 줄 수, 복구하지 못한 깨진 글자 수)
    """
    lines = md.split("\n")
    targets = [(i, span) for i, ln in enumerate(lines)
               if (span := _mojibake_span(ln, min_ratio, min_chars))]
    if not targets:
        return md, 0, 0

    from md4paper import pdfio

    try:
        flat = re.sub(r"\s+", " ", pdfio.full_text(pdf))
    except Exception:  # noqa: BLE001 — 손상 PDF·pypdfium2 미설치 등
        return md, 0, sum(end - start for _, (start, end) in targets)

    fixed = left = 0
    for i, (start, end) in targets:
        line = lines[i]
        clean = _find_in_pdf(_unpair(line[start:end]), flat)
        if clean:
            lines[i] = line[:start] + clean + line[end:]
            fixed += 1
        else:
            left += sum(1 for ch in line[start:end] if ord(ch) > 0xFF)
    return "\n".join(lines), fixed, left


def sniff_text_coverage(pdf: Path) -> float:
    """텍스트 레이어가 있는 페이지 비율 (born-digital 판별). pypdfium2 없으면 -1."""
    from md4paper import pdfio

    try:
        return pdfio.text_page_ratio(pdf)
    except ImportError:
        return -1.0


_IMG_REF_RE = re.compile(r"(!\[[^\]]*\]\()([^)]+)(\))")


def rewrite_image_refs(md: str, mapping: dict[str, str]) -> str:
    """마크다운의 이미지 경로를 파일명(mapping 결과)으로 치환.

    렌더 단계가 `images/` 접두를 붙이므로 여기서는 베어 파일명으로 통일한다.
    """
    def repl(m: re.Match) -> str:
        path = m.group(2).strip()
        name = mapping.get(path) or mapping.get(Path(path).name)
        return m.group(1) + (name or Path(path).name) + m.group(3)

    return _IMG_REF_RE.sub(repl, md)
