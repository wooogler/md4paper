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
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return md, 0
    try:
        doc = pdfium.PdfDocument(pdf)
    except Exception:  # noqa: BLE001
        return md, 0
    try:
        pdf_norm = _norm_for_match(
            " ".join(doc[p].get_textpage().get_text_range() for p in range(len(doc)))
        )
    finally:
        doc.close()

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


def sniff_text_coverage(pdf: Path) -> float:
    """텍스트 레이어가 있는 페이지 비율 (born-digital 판별). pypdfium2 없으면 -1."""
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return -1.0
    doc = pdfium.PdfDocument(pdf)
    try:
        pages = len(doc) or 1
        return sum(1 for pg in doc if pg.get_textpage().get_text_range().strip()) / pages
    finally:
        doc.close()


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
