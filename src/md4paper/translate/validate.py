"""번역 결과의 구조 검증 — 결정론적 (load-bearing).

원문(영어)과 번역문(한국어, 플레이스홀더 복원 후)의 구조 불변량을 비교한다.
불일치가 있으면 위반 항목 목록을 반환하고, 호출부가 재시도/폴백한다.
"""

from __future__ import annotations

import re

_ATX = re.compile(r"^(#{1,6})\s+")
_FENCE = re.compile(r"^\s*(```|~~~)")
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)|!?\[\[[^\]]*\]\]")
_LINK = re.compile(r"(?<!!)\[[^\]]*\]\([^)]*\)")
_SENTINEL = re.compile(r"⟦MD4_\d+⟧")


def _heading_levels(text: str) -> list[int]:
    levels = []
    in_fence = False
    for line in text.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = _ATX.match(line)
        if m:
            levels.append(len(m.group(1)))
    return levels


def validate(source: str, translated: str) -> list[str]:
    """복원된 번역문이 원문의 구조를 보존했는지. 위반 항목 목록(비면 통과)."""
    problems: list[str] = []

    if _heading_levels(source) != _heading_levels(translated):
        problems.append("헤더 레벨/개수 불일치")

    checks = {
        "이미지 수": lambda t: len(_IMAGE.findall(t)),
        "링크 수": lambda t: len(_LINK.findall(t)),
        "인라인 코드(backtick) 수": lambda t: t.count("`"),
        "수식 구분자($) 수": lambda t: t.count("$"),
        "표 파이프(|) 수": lambda t: t.count("|"),
    }
    for name, fn in checks.items():
        if fn(source) != fn(translated):
            problems.append(name)

    if _SENTINEL.search(translated):
        problems.append("플레이스홀더 미복원(⟦MD4_n⟧ 잔존)")

    return problems
