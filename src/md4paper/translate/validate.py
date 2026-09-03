"""번역 결과의 구조 검증 — 결정론적 (load-bearing).

원문(영어)과 번역문(한국어, 플레이스홀더 복원 후)의 구조 불변량을 비교한다.
불일치가 있으면 위반 항목 목록을 반환하고, 호출부가 재시도/폴백한다.

수식·인라인 코드는 **문자 수가 아니라 스팬**으로 본다. 문자 수로 재면 구조가 멀쩡한
번역이 걸린다 — 실제로 두 논문의 섹션이 그래서 영어로 남았다.
  - "We paid them $2.50" → "2.50달러를 지급했다" : `$` 1→0. 통화 기호는 수식 구분자가 아닌데
    개수로만 재서 위반이 됐고, 재시도 메시지가 "수식 구분자($) 수"라고 알려 주는 바람에
    모델이 `$2.50$`로 고쳐 1→2가 되어 두 번째도 실패했다(그래서 라벨도 함께 고친다).
  - "the polr function in R" → "R의 `polr` 함수" : 백틱 0→2. 원문보다 나은 표기를 붙였는데
    구조 파손으로 판정됐다.
구조가 실제로 깨지는 것은 **스팬을 잃을 때**(짝이 맞던 `$…$`/`` `…` ``가 사라져 수식·코드가
평문이 됨)와 **짝 없는 구분자가 새로 생길 때**(뒤 텍스트를 통째로 삼킴)다. 그 둘만 잡는다.
"""

from __future__ import annotations

import re

_ATX = re.compile(r"^(#{1,6})\s+")
_FENCE = re.compile(r"^\s*(```|~~~)")
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)|!?\[\[[^\]]*\]\]")
_LINK = re.compile(r"(?<!!)\[[^\]]*\]\([^)]*\)")
_SENTINEL = re.compile(r"⟦MD4_\d+⟧")

# 짝이 맞는 스팬만 — 홀로 선 `$`(통화)나 홀로 선 백틱은 스팬이 아니다.
_FENCED = re.compile(r"```.*?```|~~~.*?~~~", re.DOTALL)
_DISPLAY_MATH = re.compile(r"\$\$.+?\$\$", re.DOTALL)
_INLINE_MATH = re.compile(r"\$(?![\s$])[^$\n]*?(?<![\s$])\$")
_INLINE_CODE = re.compile(r"`[^`\n]+`")
# 한 줄에 통화 기호가 둘이면 그 사이가 통째로 '수식 스팬'으로 잡힌다
# ("US$100 and C$50", "$1.25/MTok output) and Claude 3.5 Sonnet ($"). 실측 코퍼스에서 인라인
# 수식 매칭 10개 중 4개가 이런 가짜였다. 진짜 인라인 수식 안에는 **맨 영어 낱말**이 없다 —
# 변수는 한 글자이고($x_i$, $O(n \log n)$), 두 글자 이상이면 LaTeX 명령이라 앞에 \가 붙는다
# ($\alpha$, $\log$). 그걸로 가른다. 표 파이프가 든 것도 수식이 아니다.
_BARE_WORD = re.compile(r"^[A-Za-z]{2,}$")


def _inline_math(text: str) -> list[tuple[int, int]]:
    """진짜 인라인 수식 스팬의 (시작, 끝). 산문이 낀 가짜 페어링은 뺀다."""
    return [m.span() for m in _INLINE_MATH.finditer(text)
            if "|" not in m.group(0)
            and not any(_BARE_WORD.match(tok) for tok in m.group(0)[1:-1].split())]


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


def _math_spans(text: str) -> int:
    """짝이 맞는 수식 스팬 수 ($$…$$ + $…$)."""
    body, n_display = _DISPLAY_MATH.subn(" ", text)
    return n_display + len(_inline_math(body))


def _code_spans(text: str) -> int:
    """짝이 맞는 인라인 코드 스팬 수 (펜스 코드는 제외 — 센티넬로 이미 보호된다)."""
    return len(_INLINE_CODE.findall(_FENCED.sub(" ", text)))


def _stray_math(text: str) -> int:
    """스팬을 이루지 못하고 남은 `$` — 통화 기호이거나, 짝을 잃은 구분자."""
    body = _DISPLAY_MATH.sub(" ", text)
    kept, last = [], 0
    for start, end in _inline_math(body):  # 진짜 스팬만 걷어내고 센다
        kept.append(body[last:start])
        last = end
    kept.append(body[last:])
    return "".join(kept).count("$")


def _stray_code(text: str) -> int:
    """스팬을 이루지 못하고 남은 백틱."""
    body = _FENCED.sub(" ", text)
    return _INLINE_CODE.sub(" ", body).count("`")


def validate(source: str, translated: str) -> list[str]:
    """복원된 번역문이 원문의 구조를 보존했는지. 위반 항목 목록(비면 통과)."""
    problems: list[str] = []

    if _heading_levels(source) != _heading_levels(translated):
        problems.append("헤더 레벨/개수 불일치")

    # 개수가 그대로여야 하는 것들 — 한국어로 옮긴다고 늘거나 줄 이유가 없다.
    exact = {
        "이미지 수": lambda t: len(_IMAGE.findall(t)),
        "링크 수": lambda t: len(_LINK.findall(t)),
        "표 파이프(|) 수": lambda t: t.count("|"),
    }
    for name, fn in exact.items():
        if fn(source) != fn(translated):
            problems.append(name)

    # 수식·인라인 코드 — 유실과 '짝 없는 구분자 추가'만 위반. 새로 붙인 짝 맞는 표기는 통과.
    if _math_spans(translated) < _math_spans(source):
        problems.append("수식 스팬 유실($…$)")
    if _stray_math(translated) > _stray_math(source):
        problems.append("짝 없는 $ 추가")
    if _code_spans(translated) < _code_spans(source):
        problems.append("인라인 코드 스팬 유실(`…`)")
    if _stray_code(translated) > _stray_code(source):
        problems.append("짝 없는 백틱 추가")

    if _SENTINEL.search(translated):
        problems.append("플레이스홀더 미복원(⟦MD4_n⟧ 잔존)")

    return problems
