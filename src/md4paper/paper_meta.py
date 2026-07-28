"""논문 서지 정보(제목·저자·연도·venue) 추출 — front matter를 LLM으로 구조화.

목록 표시·검색·정렬에 쓴다. 키가 없거나 실패하면 조용히 건너뛴다(제목은 manifest 폴백).
"""

from __future__ import annotations

import json
import re

from md4paper.ir import PaperMeta
from md4paper.llm.base import Provider
from md4paper.workdir import WorkDir

_SYSTEM = """You extract bibliographic metadata from the front matter of an academic paper.
Return: title, authors (list of names as written), year (publication year as an integer, or omit if unknown),
venue (the conference proceedings or journal name, e.g. "Proceedings of the CHI Conference on Human Factors
in Computing Systems"; empty string if not stated). Use only what appears in the text — do not guess a venue
or year that is not present. Do not include affiliations or emails in `authors`.

Also return `short_title`: a compact CamelCase identifier for the paper's MAIN title, for use in a filename.
- Use the main title only (ignore any subtitle after a colon or dash).
- ASCII letters and digits only; CamelCase; no spaces, hyphens, or punctuation.
- Drop articles/prepositions (a, an, the, of, for, and, to, with, in, on, from, by, via).
- Abbreviate well-known multiword terms to their standard acronym: Human-in-the-Loop→HITL,
  Large Language Model(s)→LLM, Reinforcement Learning→RL, Retrieval-Augmented Generation→RAG.
- Keep it recognizable: about 3-5 words, at most ~28 characters. Example: "Continual Human-in-the-Loop
  Optimization of ..." → "ContinualHITLOptimization"."""


def front_text(wd: WorkDir, cap: int = 4000) -> str:
    """서지 추출 입력 — raw.md 앞부분(제목·저자·초록) + 걷어낸 저작권/venue 텍스트."""
    parts: list[str] = []
    if wd.raw_md.exists():
        parts.append(wd.raw_md.read_text(encoding="utf-8")[:2600])
    if wd.frontmatter_txt.exists():
        fm = wd.frontmatter_txt.read_text(encoding="utf-8").strip()
        if fm:
            parts.append("[FRONT MATTER / COPYRIGHT / VENUE]\n" + fm)
    return "\n\n".join(parts)[:cap]


def extract(provider: Provider, text: str, *, max_tokens: int = 1024) -> PaperMeta:
    return provider.parse(_SYSTEM, text, PaperMeta, max_tokens=max_tokens)


def save(wd: WorkDir, meta: PaperMeta) -> None:
    wd.paper_meta_json.write_text(meta.model_dump_json(indent=2), encoding="utf-8")


def load(wd: WorkDir) -> dict | None:
    """저장된 서지 dict (없거나 깨지면 None)."""
    if not wd.paper_meta_json.exists():
        return None
    try:
        return json.loads(wd.paper_meta_json.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def authors_short(authors: list[str], limit: int = 2) -> str:
    """'Minjae Lee, Minsuk Kahng' 또는 'Minjae Lee et al.' (표시용)."""
    names = [a.strip() for a in authors if a.strip()]
    if not names:
        return ""
    if len(names) <= limit:
        return ", ".join(names)
    return ", ".join(names[:limit]) + " et al."


# --- 폴더 자동 명명: {year}_{ShortTitle}_{1저자성} --------------------------
_STOP = {"a", "an", "the", "of", "for", "and", "to", "with", "in", "on",
         "from", "by", "via", "using", "under", "over", "at"}


def _year_from_pdf_date(raw: str) -> int | None:
    """PDF 날짜 문자열('D:20260409...' 등)에서 연도. 상식 범위(1990~내년) 밖이면 None."""
    from datetime import datetime

    m = re.search(r"(?:D:)?((?:19|20)\d{2})", raw or "")
    if not m:
        return None
    y = int(m.group(1))
    return y if 1990 <= y <= datetime.now().year + 1 else None


def pdf_year(pdf_path: str) -> int | None:
    """PDF 내장 메타데이터의 생성/수정 연도 — front matter에 출판연도가 없을 때의 폴백."""
    from md4paper import pdfio

    try:
        meta = pdfio.metadata(str(pdf_path))
    except Exception:  # noqa: BLE001 — 손상 PDF·pypdfium2 미설치 등
        return None
    return _year_from_pdf_date(meta.get("CreationDate") or meta.get("ModDate") or "")


def _last_name(full: str) -> str:
    """'Minjae Lee' → 'Lee', 'Jane von Neumann' → 'Neumann' (ASCII 영숫자만)."""
    toks = [t for t in re.split(r"\s+", full.strip()) if t]
    return re.sub(r"[^A-Za-z0-9]", "", toks[-1]) if toks else ""


def _camel_fallback(title: str) -> str:
    """LLM short_title이 없을 때: 주 제목의 의미 단어를 CamelCase로 (규칙 폴백)."""
    main = re.split(r"[:–—]", title, maxsplit=1)[0]  # 부제(콜론·엠/엔대시)만 제거, ASCII 하이픈은 단어 구분
    words = [w for w in re.findall(r"[A-Za-z0-9]+", main) if w.lower() not in _STOP]
    return "".join(w[:1].upper() + w[1:] for w in words[:5])[:28]


def folder_base(meta: dict | PaperMeta) -> str:
    """서지에서 폴더 기준명 '{year}_{ShortTitle}_{LastName}' (없는 조각은 생략).

    ShortTitle을 못 만들면(제목 없음) 빈 문자열 반환 → 호출측에서 리네임을 건너뛴다.
    """
    d = meta.model_dump() if isinstance(meta, PaperMeta) else dict(meta)
    short = re.sub(r"[^A-Za-z0-9]", "", d.get("short_title") or "") or _camel_fallback(d.get("title") or "")
    if not short:
        return ""
    authors = d.get("authors") or []
    parts = [str(d["year"])] if d.get("year") else []
    parts.append(short)
    last = _last_name(authors[0]) if authors else ""
    if last:
        parts.append(last)
    return "_".join(parts)
