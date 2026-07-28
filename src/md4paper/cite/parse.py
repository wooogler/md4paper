"""참고문헌 구조화 추출 — References 텍스트 → RefEntry 목록.

번호형 참고문헌은 항목 경계로 잘라 여러 청크를 동시에 LLM 호출한다(가장 느린 단계라 벽시계 단축).
각 항목의 필드가 원문(raw)에 실제로 있는지 검증해 환각을 거른다.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor

from md4paper.ir import ReferenceList, RefEntry
from md4paper.llm.base import Provider

_SYSTEM = """You parse the reference/bibliography section of an academic paper.
Extract every entry from the given References text and return it as JSON. Rules:
- label: only the number cited in the body (no brackets, e.g. "12"). If entries are unnumbered, number them 1, 2, ... in order.
- authors: list of author names, exactly as written in the source.
- year: publication year (integer). Omit if absent.
- title: the paper/work title.
- short_name: a short name to refer to this work in in-text citations. Decide in this order:
  1) If it has a widely-recognized alias in the field, use it (e.g. "Transformer", "BERT", "ResNet", "Adam").
  2) Otherwise make a short title from the main title: drop the subtitle (anything after a colon or question mark),
     keep only the core noun phrase (tidy articles/prepositions, 2-5 words).
     e.g. "Red-teaming for generative AI: Silver bullet or security theater?" -> "Red-teaming for Generative AI".
     e.g. "Attention Is All You Need" -> "Attention Is All You Need" (keep as-is if already short, or the alias "Transformer").
  Leave empty only when there is no title to work from. Use only words that actually appear in the title; do not invent new words.
- venue: conference/journal name.
- doi, arxiv_id: if present.
- raw: copy that entry's source text **verbatim** (used for validation). Never fabricate it.
Leave a field empty when the information is absent. Do not invent anything not in the source."""


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


# 항목 시작 줄: "[12] ..." / "12. ..." / "12) ..." — 앞의 마크다운 불릿·앵커·볼드는 무시
#   (첫 파싱은 "[1] ...", 재파싱은 "<a id=ref-1></a>**[1]** ..." 형태로 들어온다)
_ENTRY_RE = re.compile(
    r'^\s*(?:[-*+]\s+)?(?:<a[^>]*>\s*</a>\s*)?(?:\*\*)?\s*(?:\[(\d{1,3})\]|(\d{1,3})[.)])'
)


def _split_entries(body_text: str) -> list[str] | None:
    """번호형 참고문헌을 항목 단위로 분리. 번호 순서가 신뢰되지 않으면 None(단일 호출)."""
    lines = body_text.split("\n")
    starts: list[tuple[int, int]] = []
    for i, ln in enumerate(lines):
        m = _ENTRY_RE.match(ln)
        if m:
            starts.append((i, int(m.group(1) or m.group(2))))
    if len(starts) < 4:  # 항목이 적으면 분할 이득이 없다
        return None
    nums = [n for _, n in starts]
    if nums[0] > 2:  # 참고문헌은 보통 1(±1)에서 시작
        return None
    increases = sum(1 for a, b in zip(nums, nums[1:]) if b > a)
    if increases < len(nums) * 0.8:  # 대체로 증가해야 진짜 번호 목록 (제목 속 [1] 오탐 차단)
        return None
    idxs = [i for i, _ in starts]
    entries = ["\n".join(lines[si:ei]).strip()
               for si, ei in zip(idxs, idxs[1:] + [len(lines)])]
    return [e for e in entries if e]


def _batch(entries: list[str], batch_size: int) -> list[str]:
    """항목들을 batch_size개씩 묶어 청크 문자열 목록으로."""
    return ["\n\n".join(entries[i:i + batch_size]) for i in range(0, len(entries), batch_size)]


def _auto_max_tokens(text: str) -> int:
    """출력 토큰 상한 자동 산정: 각 항목이 raw를 복사하므로 출력 ≈ 입력×2.6."""
    est = int(len(text) / 4 * 2.6) + 2000
    return max(8192, min(est, 60000))


def validate_entry(entry: RefEntry, body_norm: str) -> bool:
    """항목이 원문에 근거하는지 검증 (환각 방지).

    raw가 References 본문의 실제 조각이고, 1저자 성/연도가 raw에 등장하면 통과.
    """
    raw_norm = _norm(entry.raw)
    if len(raw_norm) < 10 or raw_norm not in body_norm:
        return False
    if entry.year and str(entry.year) not in raw_norm:
        return False
    if entry.authors:
        surname = entry.authors[0].split()[-1] if entry.authors[0].split() else entry.authors[0]
        if surname and _norm(surname) not in raw_norm:
            return False
    return True


def _parse_one(provider: Provider, text: str, max_tokens: int | None) -> list[RefEntry]:
    mt = max_tokens if max_tokens is not None else _auto_max_tokens(text)
    return provider.parse(_SYSTEM, text, ReferenceList, max_tokens=mt).references


def parse_references(
    body_text: str,
    provider: Provider,
    *,
    max_tokens: int | None = None,
    batch_size: int = 12,
    max_workers: int = 8,
) -> tuple[list[RefEntry], list[RefEntry]]:
    """References 본문 → (검증 통과 항목, 기각 항목).

    번호형 목록은 항목 경계로 batch_size개씩 잘라 동시에 호출한다(LLM 출력 생성이 순차라
    한 번에 다 뽑으면 느림 → 분할 병렬로 벽시계 단축). 나머지는 단일 호출로 폴백.
    기각 항목은 감사/로그용으로 함께 반환한다(파이프라인은 통과 항목만 사용).
    """
    entries = _split_entries(body_text)
    if not entries or len(entries) <= batch_size:
        refs = _parse_one(provider, body_text, max_tokens)  # 분할 이득 없음/불가 → 단일 호출
    else:
        chunks = _batch(entries, batch_size)
        with ThreadPoolExecutor(max_workers=min(max_workers, len(chunks))) as ex:
            refs = [r for sub in ex.map(lambda c: _parse_one(provider, c, max_tokens), chunks)
                    for r in sub]

    body_norm = _norm(body_text)
    accepted: list[RefEntry] = []
    rejected: list[RefEntry] = []
    seen: set[str] = set()
    for entry in refs:
        if entry.label in seen:  # 청크 경계 중복 방지
            continue
        seen.add(entry.label)
        (accepted if validate_entry(entry, body_norm) else rejected).append(entry)
    accepted.sort(key=lambda r: int(r.label) if r.label.isdigit() else 10**9)
    return accepted, rejected
