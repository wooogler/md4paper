"""본문 구조(run-in 소제목) 판정 — LLM 하이브리드.

front_matter와 같은 '라벨만, 생성하지 않음' 패턴: 추출기가 애매하게 남긴 run-in 후보
(예: '· Systematize Iteration: …', 'RQ4: …', '4.2.2 The Data Panel: …')가 진짜 소제목인지,
아니면 정의형 불릿 목록·캡션·평범한 문장인지를 LLM이 인덱스로 판정하고, 코드가 그대로 조립한다.

번호형/ATX 헤더는 결정적 규칙(numbering)으로 두고, 애매한 무번호 run-in만 판정 대상으로 삼는다.
키가 없거나 판정이 검증을 통과 못 하면 호출부가 규칙(runin 레벨링)으로 폴백한다.
"""

from __future__ import annotations

from md4paper.ir import SectionLayout
from md4paper.llm.base import Provider

_SYSTEM = """You classify RUN-IN heading candidates from the body of an academic paper. A run-in
candidate is a line that MIGHT be a subsection heading written inline at the start of a paragraph or
list item — but it might instead be:
- a bulleted or definition-list lead-in (e.g. "Systematize Iteration: The constant cycle..."),
- a figure or table caption,
- or an ordinary sentence.

You receive the paper's headings in document order. Already-confirmed headings appear as
"[i] L{n} {text}" where n is their level. Candidates you must judge appear as
"[i] ?? {text}  ->  {the text that follows}".

For EACH "??" candidate, return a decision referenced by its given index:
- is_heading: true ONLY if it is a genuine subsection heading (a short title that introduces the
  following text as its own subsection). false if it is a list/definition lead-in, a caption, or an
  ordinary sentence.
- level: when is_heading, the heading level (2-6) — one level deeper than its parent (the nearest
  preceding confirmed heading). SIBLING candidates under the same parent MUST share the same level.
  Use 0 when not a heading.

Judge only the "??" candidates, by their given index. Never invent indices. When unsure, prefer
is_heading=false (leave it as body) — a missed subsection is safer than turning a list item or
sentence into a heading."""


def _fmt(items: list[dict]) -> str:
    lines: list[str] = []
    for it in items:
        if it.get("level") is None:  # 판정 대상 후보
            ctx = " ".join((it.get("context") or "").split())[:160]
            lines.append(f"[{it['index']}] ?? {it['text']}  ->  {ctx}")
        else:
            lines.append(f"[{it['index']}] L{it['level']} {it['text']}")
    return "\n".join(lines)


def classify(provider: Provider, items: list[dict]) -> dict[int, tuple[bool, int]] | None:
    """run-in 후보 판정. 반환: {후보 인덱스: (is_heading, level)}.

    - 판정 대상 후보가 없으면 {} (LLM 호출 안 함).
    - LLM 호출/검증 실패 시 None → 호출부가 전부 규칙 폴백.
    - 개별 후보의 판정이 이상하면(레벨 범위 밖 등) 그 후보만 결과에서 빼 규칙 폴백을 유도한다.
    """
    cand_idx = {it["index"] for it in items if it.get("level") is None}
    if not cand_idx:
        return {}
    try:
        layout = provider.parse(_SYSTEM, _fmt(items), SectionLayout, max_tokens=1024)
    except Exception:  # noqa: BLE001 — 실패 시 폴백
        return None
    out: dict[int, tuple[bool, int]] = {}
    for d in layout.decisions:
        if d.index not in cand_idx:
            continue
        if d.is_heading:
            if not (isinstance(d.level, int) and 2 <= d.level <= 6):
                continue  # 레벨 이상 → 이 후보는 규칙 폴백
            out[d.index] = (True, d.level)
        else:
            out[d.index] = (False, 0)
    return out
