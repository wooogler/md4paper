"""청킹 + 플레이스홀더 보호.

헤더 경계로 분할하고 작은 섹션은 병합한다(펜스/표/수식 내부는 헤더가 없어 자연히 안 쪼개짐).
번역 전, 변형되면 안 되는 스팬(펜스 코드·디스플레이 수식·이미지·마크다운 링크·raw URL)을
⟦MD4_n⟧ 센티넬로 치환하고, 번역 후 복원한다. 인라인 수식·인라인 코드는 인라인으로 두고 검증한다.
"""

from __future__ import annotations

import re

_ATX = re.compile(r"^(#{1,6})\s+\S")
_FENCE = re.compile(r"^\s*(```|~~~)")

# 보호 대상 (순서 중요: 펜스·디스플레이수식 먼저, 이미지→링크→URL)
_PROTECT_PATTERNS = [
    re.compile(r"```.*?```", re.DOTALL),
    re.compile(r"~~~.*?~~~", re.DOTALL),
    re.compile(r"\$\$.+?\$\$", re.DOTALL),
    re.compile(r"<figure>.*?</figure>", re.DOTALL),  # HTML 플레이버 그림
    re.compile(r"!\[[^\]]*\]\([^)]*\)"),  # 이미지
    re.compile(r"!?\[\[[^\]]*\]\]"),  # Obsidian 위키링크 임베드
    re.compile(r"\[[^\]]*\]\([^)]*\)"),  # 마크다운 링크 (인용 링크 포함)
    re.compile(r"https?://[^\s)]+"),  # raw URL
    # autoref 상호참조는 영어 유지 (Figure 3, Table 2, Section 4.1, Eq. 5 …)
    re.compile(
        r"\b(?:Section|Sec\.|Figure|Fig\.|Table|Tab\.|Appendix|App\.|Equation|Eq\.|Algorithm|Alg\.)"
        r"\s*\d+(?:\.\d+)*",
    ),
]

_SENTINEL_RE = re.compile(r"⟦MD4_\d+⟧")


_HEADING_TEXT_RE = re.compile(r"^(#{1,6}\s+)(.+?)(\s*)$")


def protect(text: str, protect_headings: bool = False) -> tuple[str, dict[str, str]]:
    """보호 스팬을 센티넬로 치환. (치환된 텍스트, 복원맵) 반환.

    protect_headings=True면 헤더의 제목 텍스트도 보호(번역 안 함 — 섹션 제목 영어 유지).
    """
    store: dict[str, str] = {}
    counter = 0

    def make(m: re.Match) -> str:
        nonlocal counter
        key = f"⟦MD4_{counter}⟧"
        store[key] = m.group(0)
        counter += 1
        return key

    if protect_headings:
        # 헤더의 '#' 구조는 남기고 제목 텍스트만 센티넬로 (펜스 밖에서만)
        in_fence = False
        new_lines: list[str] = []
        for line in text.splitlines():
            if _FENCE.match(line):
                in_fence = not in_fence
                new_lines.append(line)
                continue
            hm = _HEADING_TEXT_RE.match(line) if not in_fence else None
            if hm:
                key = f"⟦MD4_{counter}⟧"
                store[key] = hm.group(2)
                counter += 1
                new_lines.append(f"{hm.group(1)}{key}")
            else:
                new_lines.append(line)
        text = "\n".join(new_lines)

    for pat in _PROTECT_PATTERNS:
        text = pat.sub(make, text)
    return text, store


def restore(text: str, store: dict[str, str]) -> str:
    for key, val in store.items():
        text = text.replace(key, val)
    return text


def _heading_lines(lines: list[str]) -> list[int]:
    out: list[int] = []
    in_fence = False
    for i, line in enumerate(lines):
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence and _ATX.match(line):
            out.append(i)
    return out


def split_by_paragraphs(text: str, target_chars: int = 6000) -> list[str]:
    """큰 섹션을 빈 줄(문단) 경계로 파트 분할. 펜스 코드 내부는 안 쪼갠다.

    각 파트가 target_chars에 이르면 끊는다. 합치면('\\n'.join) 원문이 그대로 복원된다.
    작아서 안 나뉘면 [text] 하나를 그대로 반환.
    """
    lines = text.split("\n")
    blocks: list[str] = []
    cur: list[str] = []
    in_fence = False
    for line in lines:
        if _FENCE.match(line):
            in_fence = not in_fence
        cur.append(line)
        if line.strip() == "" and not in_fence:  # 문단 경계 (펜스 밖 빈 줄)
            blocks.append("\n".join(cur))
            cur = []
    if cur:
        blocks.append("\n".join(cur))

    parts: list[str] = []
    buf = ""
    for b in blocks:
        buf = b if not buf else buf + "\n" + b
        if len(buf) >= target_chars:
            parts.append(buf)
            buf = ""
    if buf:
        if parts:
            parts[-1] = parts[-1] + "\n" + buf  # 자투리는 마지막 파트에 흡수
        else:
            parts.append(buf)
    return parts or [text]


def split_chunks(md_text: str, min_chars: int = 1200) -> list[str]:
    """헤더 경계로 분할하고 작은 조각을 병합. 합치면 원문이 그대로 복원된다."""
    lines = md_text.splitlines()
    if not lines:
        return []
    heads = _heading_lines(lines)
    bounds = sorted({0, *heads, len(lines)})
    raw_chunks = ["\n".join(lines[a:b]) for a, b in zip(bounds, bounds[1:])]

    merged: list[str] = []
    buf = ""
    for ch in raw_chunks:
        buf = ch if not buf else buf + "\n" + ch
        if len(buf) >= min_chars:
            merged.append(buf)
            buf = ""
    if buf:
        if merged:
            merged[-1] = merged[-1] + "\n" + buf
        else:
            merged.append(buf)
    return merged
