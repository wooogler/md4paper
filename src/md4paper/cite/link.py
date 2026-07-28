"""본문 인용 마커 탐지·치환 — 결정론적 (LLM 없음).

두 인용 스타일을 모두 지원하고, 본문에서 더 많이 매칭되는 쪽을 자동 채택한다:
- 번호형: [1] / [1,2] / [1-4] — 모든 인덱스가 참고문헌 범위 [1, N] 안일 때만 치환.
- 저자-연도형: (Wei et al., 2021) / Wei et al. (2021) — 첫 저자 성 + 연도로 참고문헌 매칭
  (ACL·arXiv 계열 다수). 동일 저자·연도 중복은 접미사(2022a/2022b)로 순번 구분.
코드 펜스·인라인 코드·수식은 마스킹해 건드리지 않는다.
스타일 표시 조합(parts): keep / number / authoryear / short.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from md4paper.ir import RefEntry

# 보호 구간: 인라인 코드, 디스플레이/인라인 수식 (한 줄 내)
_PROTECT_RE = re.compile(r"(`[^`]*`|\$\$[^$]*\$\$|\$[^$\n]*\$)")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
# [1] / [1, 2] / [1-4] / [1–4]  (대괄호 안 숫자와 구분자만)
_CITE_RE = re.compile(r"\[(\d{1,3}(?:\s*[,–—-]\s*\d{1,3})*)\]")


@dataclass
class LinkStats:
    rewritten: int = 0
    skipped_out_of_range: int = 0
    matches: list[dict] = field(default_factory=list)


def _expand(inner: str) -> list[int] | None:
    """'1, 3-5' → [1,3,4,5]. 이상한 범위면 None."""
    out: list[int] = []
    for part in re.split(r"\s*,\s*", inner.strip()):
        m = re.fullmatch(r"(\d+)\s*[–—-]\s*(\d+)", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if not (a <= b and b - a < 100):
                return None
            out.extend(range(a, b + 1))
        elif part.isdigit():
            out.append(int(part))
        else:
            return None
    return out


def _display(entry: RefEntry, parts: list[str]) -> str:
    """선택된 요소들을 조합한 인용 표시 텍스트 (예: number+short → '1, Transformer')."""
    out: list[str] = []
    if "number" in parts:
        out.append(entry.label)
    if "authoryear" in parts:
        out.append(entry.author_year())
    if "short" in parts:
        # short_name 없으면 author-year로 폴백 (단, 이미 authoryear를 넣었으면 중복 방지)
        alt = entry.short_name or ("" if "authoryear" in parts else entry.author_year())
        if alt:
            out.append(alt)
    return ", ".join(dict.fromkeys(out)) or entry.label


def _replacement(indices: list[int], refs_by_label: dict[str, RefEntry], parts: list[str]) -> str:
    tokens = [f"[{_display(refs_by_label[str(i)], parts)}](#ref-{i})" for i in indices]
    # 요소가 번호뿐이면 콤마, 아니면 세미콜론으로 항목 구분
    sep = ", " if parts == ["number"] else "; "
    return "[" + sep.join(tokens) + "]"


def _rewrite_text(text: str, refs_by_label: dict[str, RefEntry], parts: list[str], stats: LinkStats) -> str:
    max_label = max((int(k) for k in refs_by_label), default=0)

    def repl(m: re.Match) -> str:
        end = m.end()
        # 마크다운 링크 [1](url) 는 건드리지 않음
        if end < len(text) and text[end] == "(":
            return m.group(0)
        # 이미지/각주 각주(^) 앞머리 제외
        start = m.start()
        if start > 0 and text[start - 1] in "!^":
            return m.group(0)
        indices = _expand(m.group(1))
        if not indices or any(i < 1 or i > max_label for i in indices):
            stats.skipped_out_of_range += 1
            return m.group(0)
        if any(str(i) not in refs_by_label for i in indices):
            return m.group(0)
        stats.rewritten += 1
        stats.matches.append({"original": m.group(0), "indices": indices})
        return _replacement(indices, refs_by_label, parts)

    return _CITE_RE.sub(repl, text)


def rewrite_line(line: str, refs_by_label: dict[str, RefEntry], parts: list[str], stats: LinkStats) -> str:
    """한 줄에서 보호 구간을 제외하고 인용 마커를 치환."""
    segments = _PROTECT_RE.split(line)
    for i in range(0, len(segments), 2):  # 짝수 인덱스 = 비보호 텍스트
        segments[i] = _rewrite_text(segments[i], refs_by_label, parts, stats)
    return "".join(segments)


def _rewrite_lines_numbered(
    lines: list[str], refs: list[RefEntry], parts: list[str], skip_range: range | None = None
) -> tuple[list[str], LinkStats]:
    """번호형 [n] 마커를 치환. skip_range(참고문헌)는 제외."""
    refs_by_label = {r.label: r for r in refs}
    stats = LinkStats()
    out: list[str] = []
    in_fence = False
    skip = set(skip_range) if skip_range else set()
    for idx, line in enumerate(lines):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            out.append(line)
            continue
        if in_fence or idx in skip:
            out.append(line)
            continue
        out.append(rewrite_line(line, refs_by_label, parts, stats))
    return out, stats


# --- 저자-연도형 (Author, Year) ------------------------------------------
_CAP = re.compile(r"[A-Z][A-Za-zÀ-ÿ'’.\-]*")
_YEAR_SUFFIX = re.compile(r"\b((?:18|19|20)\d{2})([a-z])?\b")
# 연도를 품은 괄호 인용:  (Wei et al., 2021)  /  (A et al., 2021; B, 2022b)
_PAREN_CITE = re.compile(r"\(([^()]{0,400}?(?:18|19|20)\d{2}[a-z]?[^()]{0,200}?)\)")
# 서술형 인용:  Wei et al. (2021)  /  Brown and Mann (2020)  /  Casanueva (2022)
_NARRATIVE_CITE = re.compile(
    r"\b[A-Z][A-Za-zÀ-ÿ'’\-]+(?:\s+et\s+al\.?|\s+and\s+[A-Z][A-Za-zÀ-ÿ'’\-]+)?\s*"
    r"\((?:18|19|20)\d{2}[a-z]?\)"
)


def _surname(name: str) -> str:
    """저자 이름에서 성(마지막 대문자 토큰) 소문자."""
    toks = _CAP.findall(name)
    return toks[-1].rstrip(".").lower() if toks else ""


def _authoryear_index(refs: list[RefEntry]) -> dict:
    """(첫 저자 성, 연도) → [참고문헌 라벨 순]. 동일 키 여러 개면 접미사로 순번 구분."""
    idx: dict[tuple[str, int], list[RefEntry]] = {}
    for r in refs:
        if r.authors and r.year:
            idx.setdefault((_surname(r.authors[0]), r.year), []).append(r)
    for bucket in idx.values():
        bucket.sort(key=lambda r: int(r.label) if r.label.isdigit() else 10**9)
    return idx


def _match_authoryear(segment: str, idx: dict) -> RefEntry | None:
    """세그먼트에서 (첫 저자 성, 연도, 접미사)를 뽑아 참고문헌 매칭. 없으면 None."""
    ym = _YEAR_SUFFIX.search(segment)
    if not ym:
        return None
    names = _CAP.findall(segment[: ym.start()])  # 연도 앞의 첫 대문자 토큰 = 첫 저자 성
    if not names:
        return None
    bucket = idx.get((names[0].rstrip(".").lower(), int(ym.group(1))))
    if not bucket:
        return None
    if ym.group(2):  # 2022a→0, 2022b→1 …
        i = ord(ym.group(2)) - ord("a")
        return bucket[i] if 0 <= i < len(bucket) else bucket[0]
    return bucket[0]


def _rewrite_authoryear_text(text: str, idx: dict, stats: LinkStats) -> str:
    def paren_repl(m: re.Match) -> str:
        parts_out, linked = [], False
        for seg in re.split(r"\s*;\s*", m.group(1)):
            r = _match_authoryear(seg, idx)
            if r and f"](#ref-{r.label})" not in seg:
                parts_out.append(f"[{seg.strip()}](#ref-{r.label})")
                linked = True
            else:
                parts_out.append(seg.strip())
        if linked:
            stats.rewritten += 1
        return "(" + "; ".join(parts_out) + ")"

    def narr_repl(m: re.Match) -> str:
        r = _match_authoryear(m.group(0), idx)
        if r:
            stats.rewritten += 1
            return f"[{m.group(0)}](#ref-{r.label})"
        return m.group(0)

    return _NARRATIVE_CITE.sub(narr_repl, _PAREN_CITE.sub(paren_repl, text))


def _rewrite_lines_authoryear(
    lines: list[str], refs: list[RefEntry], skip_range: range | None = None
) -> tuple[list[str], LinkStats]:
    """저자-연도형 인용을 참고문헌 앵커로 링크. skip_range(참고문헌)는 제외."""
    idx = _authoryear_index(refs)
    stats = LinkStats()
    if not idx:
        return list(lines), stats
    out: list[str] = []
    in_fence = False
    skip = set(skip_range) if skip_range else set()
    for i, line in enumerate(lines):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            out.append(line)
            continue
        if in_fence or i in skip:
            out.append(line)
            continue
        segments = _PROTECT_RE.split(line)  # 코드·수식 보호 구간 제외
        for j in range(0, len(segments), 2):
            segments[j] = _rewrite_authoryear_text(segments[j], idx, stats)
        out.append("".join(segments))
    return out, stats


def rewrite_lines(
    lines: list[str], refs: list[RefEntry], parts: list[str], skip_range: range | None = None
) -> tuple[list[str], LinkStats]:
    """줄 목록에서 인용을 치환. 번호형·저자연도형을 모두 시도해 더 많이 링크된 쪽을 채택."""
    if not parts:
        parts = ["number"]
    num_out, num_stats = _rewrite_lines_numbered(lines, refs, parts, skip_range)
    ay_out, ay_stats = _rewrite_lines_authoryear(lines, refs, skip_range)
    if ay_stats.rewritten > num_stats.rewritten:  # 스타일 자동 판별
        return ay_out, ay_stats
    return num_out, num_stats
