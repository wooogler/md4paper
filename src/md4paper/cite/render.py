"""참고문헌 목록 렌더 — 앵커 + DOI/arXiv 하이퍼링크 + 단축명.

본문 인용이 #ref-N 앵커로 점프하고, 각 항목의 제목이 DOI/arXiv URL로 연결돼 바로 논문에 접근한다.
"""

from __future__ import annotations

from md4paper.ir import RefEntry


def render_entry(entry: RefEntry, reference_links: bool = True) -> str:
    """참고문헌 항목 하나를 마크다운 한 줄로."""
    anchor = f'<a id="ref-{entry.label}"></a>'
    url = entry.url() if reference_links else None

    authors = ", ".join(entry.authors) if entry.authors else ""
    if url and entry.title:
        title_part = f"[{entry.title}]({url})"
    else:
        title_part = entry.title

    segs = [s for s in (authors, title_part) if s]
    tail = " ".join(s for s in (entry.venue, str(entry.year) if entry.year else "") if s)
    if tail:
        segs.append(tail)
    body = ". ".join(segs)

    line = f"{anchor}**[{entry.label}]** {body}".rstrip()
    if entry.short_name:
        line += f" *({entry.short_name})*"
    # 제목에 링크를 못 걸었는데 URL이 있으면 뒤에 노출
    if url and not (url and entry.title):
        line += f" — [{url}]({url})"
    return line


def render_reference_list(refs: list[RefEntry], reference_links: bool = True) -> list[str]:
    """참고문헌 목록 → 마크다운 줄들 (항목 사이 빈 줄)."""
    out: list[str] = []
    for entry in refs:
        out.append(render_entry(entry, reference_links))
        out.append("")
    if out and out[-1] == "":
        out.pop()
    return out
