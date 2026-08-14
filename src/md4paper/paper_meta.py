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


def _pdf_head(wd: WorkDir, limit: int = 700) -> str:
    """원본 PDF 1페이지 맨 앞 텍스트 (pypdfium2). 없거나 실패하면 빈 문자열.

    저널 머리말("Psychological Bulletin / 1986, Vol. 100 / Copyright 1986 by ...")은 연도·venue의
    가장 확실한 출처인데 추출기가 통째로 버리는 일이 있다(1986년 논문이 2025년으로 나온 실사례).
    PDF 텍스트 레이어에는 그대로 남아 있으므로 서지 추출 입력에 함께 넣는다.
    """
    if not wd.meta_json.exists():
        return ""
    try:
        src = json.loads(wd.meta_json.read_text(encoding="utf-8")).get("source", "")
        if not str(src).lower().endswith(".pdf"):
            return ""
        from md4paper import pdfio

        return re.sub(r"[ \t]+", " ", pdfio.first_page_text(src)).strip()[:limit]
    except Exception:  # noqa: BLE001 — 손상 PDF·pypdfium2 미설치 등
        return ""


def front_text(wd: WorkDir, cap: int = 4600) -> str:
    """서지 추출 입력 — PDF 1페이지 머리말 + raw.md 앞부분(제목·저자·초록) + 걷어낸 저작권 텍스트."""
    parts: list[str] = []
    head = _pdf_head(wd)
    if head:
        parts.append("[PDF PAGE 1 HEADER — journal name, volume, copyright year]\n" + head)
    if wd.raw_md.exists():
        parts.append(wd.raw_md.read_text(encoding="utf-8")[:2600])
    if wd.frontmatter_txt.exists():
        fm = wd.frontmatter_txt.read_text(encoding="utf-8").strip()
        if fm:
            parts.append("[FRONT MATTER / COPYRIGHT / VENUE]\n" + fm)
    return "\n\n".join(parts)[:cap]


def extract(provider: Provider, text: str, *, max_tokens: int = 1024) -> PaperMeta:
    meta = provider.parse(_SYSTEM, text, PaperMeta, max_tokens=max_tokens)
    # 템플릿 자리표시자("Journal Title", "Conference acronym 'XX")는 학회명이 아니다 → 비워 둔다
    # (그래야 enrich가 '빈 값'으로 보고 실제 venue를 채울 수 있다)
    from md4paper.enrich import clean_venue

    meta.venue = clean_venue(meta.venue)
    return meta


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


# --- 폴더·파일 자동 명명 — 이름 규칙 템플릿(config [output].naming, 기본 {year}_{title}_{author}) ---
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


# 1페이지의 저작권 표기 — "Copyright 1986 by ...", "© 2024" 등 (출판연도의 강한 단서)
_COPYRIGHT_YEAR_RE = re.compile(r"(?:©|\(c\)|copyright)\s*(?:by\s+)?((?:19|20)\d{2})", re.I)


def pdf_year(pdf_path: str) -> int | None:
    """front matter에 출판연도가 없을 때의 폴백 — 1페이지 저작권 연도 > PDF 생성 연도.

    오래된 논문을 최근에 다시 만든 PDF(스캔·재배포본)는 **생성 연도가 출판 연도와 무관하다**
    — 1986년 논문이 2025년으로 나온 실사례가 있었다. 1페이지의 'Copyright 1986' 쪽이
    훨씬 믿을 만하므로 먼저 본다.
    """
    from md4paper import pdfio

    try:
        m = _COPYRIGHT_YEAR_RE.search(pdfio.first_page_text(str(pdf_path)))
        if m:
            return int(m.group(1))
    except Exception:  # noqa: BLE001 — 손상 PDF·pypdfium2 미설치 등
        pass
    try:
        meta = pdfio.metadata(str(pdf_path))
    except Exception:  # noqa: BLE001
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


def folder_base(meta: dict | PaperMeta, template: str | None = None) -> str:
    """서지 + 이름 규칙 → 폴더/파일 기준명. 규칙은 config [output].naming (기본 {year}_{title}_{author}).

    조각: {year} 연도 · {title} 제목 약칭(CamelCase) · {author} 1저자 성 · {venue} 학회(영숫자만).
    규칙에 {title}이 있는데 제목을 못 만들면 빈 문자열 반환 → 호출측에서 리네임을 건너뛴다.
    없는 조각(연도 미상 등)은 빠지고, 그 자리에 겹친 구분자는 하나로 줄인다.
    """
    from md4paper import config

    d = meta.model_dump() if isinstance(meta, PaperMeta) else dict(meta)
    tpl = template or config.resolve_naming_template()
    short = re.sub(r"[^A-Za-z0-9]", "", d.get("short_title") or "") or _camel_fallback(d.get("title") or "")
    if "{title}" in tpl and not short:
        return ""
    authors = d.get("authors") or []
    values = {
        "{year}": str(d["year"]) if d.get("year") else "",
        "{title}": short,
        "{author}": _last_name(authors[0]) if authors else "",
        "{venue}": re.sub(r"[^A-Za-z0-9]+", "", d.get("venue") or "")[:20],
    }
    out = tpl
    for key, val in values.items():
        out = out.replace(key, val or "\x00")  # 빈 조각은 센티널로 — 그 자리 구분자만 정리

    def _drop_empty(m: re.Match) -> str:
        if m.start() == 0 or m.end() == len(m.string):  # 문자열 가장자리 → 구분자째 제거
            return ""
        seps = m.group(0).replace("\x00", "")
        return seps[:1]  # 가운데 → 구분자 하나만 남김 (리터럴 '--' 등은 건드리지 않는다)

    out = re.sub(r"[_\-. ]*(?:\x00[_\-. ]*)+", _drop_empty, out)
    out = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", out)  # 파일명 금지 문자 제거 (규칙의 리터럴 부분 방어)
    return out.strip("_-. ")


# 이름 규칙 미리보기용 예시 서지 (UI·CLI에서 "예: 2017_AttentionIsAllYouNeed_Vaswani"로 표시)
_EXAMPLE_META = PaperMeta(
    title="Attention Is All You Need", authors=["Ashish Vaswani", "Noam Shazeer"],
    year=2017, venue="NeurIPS", short_title="AttentionIsAllYouNeed",
)


def naming_preview(template: str | None = None) -> str:
    """이름 규칙을 예시 논문에 적용한 결과 (규칙이 어떤 이름을 만드는지 즉시 보여주기)."""
    return folder_base(_EXAMPLE_META, template)


def apply_naming(workspace) -> dict:  # noqa: ANN001 — Path | str
    """작업 폴더의 모든 논문(숨김 포함)을 현재 이름 규칙으로 정리.

    논문 폴더·.md4·원본 PDF를 리네임(rename_workdir)하고, 저장 위치가 지정돼 있으면
    새 이름으로 다시 내보낸 뒤 옛 이름 사본을 청소한다. 서지가 없는 논문은 건너뛴다.
    반환: {"renamed": n, "unchanged": n, "no_meta": n}
    """
    from pathlib import Path

    from md4paper import library
    from md4paper.workdir import WorkDir, recent_workdirs, rename_workdir

    ws = Path(workspace)
    counts = {"renamed": 0, "unchanged": 0, "no_meta": 0}
    for r in recent_workdirs(ws, limit=100_000, include_hidden=True):
        wd = WorkDir(r["root"])
        meta = load(wd)
        base = folder_base(meta) if meta else ""
        if not base:
            counts["no_meta"] += 1
            continue
        old_stem = wd.root.stem
        new_wd = rename_workdir(wd, base, ws)
        if new_wd.root == wd.root:
            counts["unchanged"] += 1
            continue
        counts["renamed"] += 1
        if library.configured():  # 저장 위치 사본도 새 이름으로 (자동 저장 꺼져 있어도 — 명시적 정리 동작)
            try:
                library.export_paper(new_wd)
                library.remove_stem(old_stem)
            except OSError:
                pass  # 사본 정리 실패가 리네임 자체를 막지 않게
    return counts
