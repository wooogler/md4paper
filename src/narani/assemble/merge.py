"""직접 고친 en.md를 살린 채 다시 조립하기 — 3-way merge (줄 → 낱말 두 단계).

en.md는 `raw.md + sections.yaml`로 처음부터 다시 만드는 파생물인데, 사용자가 편집기에서 고친 내용은
en.md에만 있다. 그래서 헤더 레벨 하나만 바꿔도 재조립이 고친 내용을 통째로 덮어썼다. 셋을 견주면 살릴 수 있다.

  base   — 바꾸기 **전** 구조로 조립했을 문서 (사용자가 고치기 시작한 바탕)
  mine   — 사용자가 고친 en.md
  theirs — 바꾼 **뒤** 구조로 새로 조립한 문서

base에서 mine으로 간 변경과 base에서 theirs로 간 변경이 서로 다른 자리면 둘 다 반영한다. 레벨 바꾸기는 헤더 줄만,
고치기는 대개 본문 문장만 건드리므로 거의 다 여기서 끝난다(실측: NIRVANA, 5종 레벨 변경 모두 충돌 0).

같은 줄을 양쪽이 고쳤으면 그 구간을 **낱말 단위**로 다시 견준다 — 인용 표기를 바꾸면 theirs가 문단 줄마다
`[1]`을 고치는데, 같은 문단의 다른 문장을 사용자가 고쳤어도 낱말이 안 겹치면 둘 다 산다.
그래도 겹치면:
  · 한 줄짜리 헤더를 사용자도 고쳤으면, 새 헤더 모양(`###`, 기울임 …)에 사용자가 고친 제목 글자를 넣는다.
  · 구조가 그 구간을 통째로 지웠으면('통째 삭제'·'위에 합침') 지운다 — 사용자가 방금 고른 것이 그것이다(dropped).
  · 나머지는 사용자 편집을 남긴다(conflicts) — 사용자가 쓴 글은 조용히 지우지 않는다.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

_TOKEN = re.compile(r"\s+|\S+")
_HEAD = re.compile(r"^#{1,6}\s+")


@dataclass
class Merged:
    text: str
    conflicts: int = 0  # 같은 자리를 양쪽이 다르게 고쳐 사용자 편집을 남긴 곳
    dropped: int = 0    # 구조가 통째로 지운 구간이라 그 안의 사용자 편집도 함께 빠진 곳


def _hunks(base: list[str], other: list[str], side: int) -> list[tuple[int, int, list[str], int]]:
    sm = difflib.SequenceMatcher(None, base, other, autojunk=False)
    return [(i1, i2, other[j1:j2], side) for tag, i1, i2, j1, j2 in sm.get_opcodes() if tag != "equal"]


def _overlap(a: tuple, b: tuple, touch: bool = False) -> bool:
    """base 구간 [a1,a2)·[b1,b2)가 겹치나. 끼워 넣기(빈 구간)는 같은 자리이거나 상대 구간 '안'일 때만 겹친다.

    touch=True(낱말 단계)면 맞닿기만 해도 겹친 것으로 본다 — 줄 단계에서 이미 같은 줄을 둘 다 고친 구간이라,
    구조가 지운 문장 끝에 사용자가 붙인 낱말만 덩그러니 남는 일이 없게."""
    (a1, a2), (b1, b2) = a[:2], b[:2]
    if touch:
        return a1 <= b2 and b1 <= a2
    if a1 == a2 and b1 == b2:
        return a1 == b1
    if a1 == a2:
        return b1 < a1 < b2
    if b1 == b2:
        return a1 < b1 < a2
    return a1 < b2 and b1 < a2


def _apply(base: list, g1: int, g2: int, hunks: list) -> list:
    out, p = [], g1
    for i1, i2, rep, _ in hunks:
        out += base[p:i1] + rep
        p = i2
    return out + base[p:g2]


def _merge(base: list, mine: list, theirs: list, clash, touch: bool = False) -> list | None:  # noqa: ANN001
    """겹치지 않는 변경은 둘 다 반영하고, 겹친 구간은 clash(base, mine, theirs)에 맡긴다(None이면 실패)."""
    hs = sorted(_hunks(base, mine, 0) + _hunks(base, theirs, 1), key=lambda h: (h[0], h[1], h[3]))
    out: list = []
    pos = k = 0
    while k < len(hs):
        grp = [hs[k]]
        k += 1
        while k < len(hs) and any(_overlap(hs[k], g, touch) for g in grp):
            grp.append(hs[k])
            k += 1
        g1, g2 = min(g[0] for g in grp), max(g[1] for g in grp)
        out += base[pos:g1]
        m, t = [g for g in grp if g[3] == 0], [g for g in grp if g[3] == 1]
        if not m or not t:
            out += _apply(base, g1, g2, grp)
        else:
            mr, tr = _apply(base, g1, g2, m), _apply(base, g1, g2, t)
            res = mr if mr == tr else clash(base[g1:g2], mr, tr)
            if res is None:
                return None
            out += res
        pos = g2
    return out + base[pos:]


def _tokens(lines: list[str]) -> list[str]:
    return _TOKEN.findall("\n".join(lines))


def merge3(base: str, mine: str, theirs: str) -> Merged:
    """사용자 편집(mine)을 새 조립본(theirs)에 옮겨 싣는다. base는 둘의 공통 조상."""
    res = Merged("")

    def clash(b: list[str], m: list[str], t: list[str]) -> list[str]:
        words = _merge(_tokens(b), _tokens(m), _tokens(t), lambda *_: None, touch=True)
        if words is not None:
            s = "".join(words)
            return s.split("\n") if s else []
        if len(b) == len(m) == len(t) == 1:
            core = _HEAD.sub("", b[0]).strip()
            if core and core in t[0]:  # 헤더 모양은 새 구조대로, 제목 글자는 사용자가 고친 대로
                return [t[0].replace(core, _HEAD.sub("", m[0]).strip(), 1)]
        if not any(x.strip() for x in t):
            res.dropped += 1
            return t
        res.conflicts += 1
        return m

    lines = _merge(base.splitlines(), mine.splitlines(), theirs.splitlines(), clash) or []
    res.text = "\n".join(lines) + ("\n" if lines and (theirs.endswith("\n") or mine.endswith("\n")) else "")
    return res


def line_map(old: list[str], new: list[str]) -> list[int]:
    """old의 줄 번호 → new의 줄 번호. 바뀐 구간 안의 줄은 그 구간의 같은 순번 줄(넘치면 마지막 줄)로."""
    out = [0] * len(old)
    sm = difflib.SequenceMatcher(None, old, new, autojunk=False)
    for _tag, i1, i2, j1, j2 in sm.get_opcodes():
        for i in range(i1, i2):
            out[i] = j1 + min(i - i1, max(j2 - j1 - 1, 0))
    return out
