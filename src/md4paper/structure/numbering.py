"""번호 체계 기반 헤더 레벨 재도출 — 이 프로젝트의 핵심 IP.

marker의 헤더 레벨은 글자 크기 k-means로 추정된 것이라 논문에서 자주 틀린다.
여기서는 헤더 텍스트의 번호(1 / 1.1 / II / A.1 / 무번호 키워드)를 파싱해 레벨을 다시 계산한다.

모두 순수 함수 — 부작용 없음, 철저히 단위 테스트됨.
"""

from __future__ import annotations

import re

from md4paper.ir import Detected, Scheme

# --- 정규식 (모두 앵커드) --------------------------------------------------

# 1  /  1.  /  1.1  /  2.3.1  /  3)  — 점으로 구분된 아라비아 숫자
_DOTTED_ARABIC = re.compile(r"^(\d+(?:\.\d+)*)\.?\)?\s+(.*)$")
# I. / II) / IV.  — 로마 숫자 + 구분자
_ROMAN = re.compile(r"^([IVXLCDM]+)[.)]\s+(.*)$")
# A. / B) / A.1 / C.2.3  — 대문자 뒤에 반드시 구분자(.)나 .숫자 (관사 'A Survey' 오탐 방지)
_LETTER = re.compile(r"^([A-Z])(?:\.(\d+(?:\.\d+)*)|[.)])\s+(.*)$")

# 무번호이지만 레벨 1로 다뤄야 하는 관용적 절 이름 (소문자 비교)
_UNNUMBERED_KEYWORDS = {
    "abstract",
    "references",
    "reference",
    "bibliography",
    "acknowledgment",
    "acknowledgments",
    "acknowledgement",
    "acknowledgements",
    "appendix",
    "appendices",
    "index",
    "keywords",
    "author contributions",
    "conflict of interest",
    "conflicts of interest",
    "funding",
    "data availability",
    "supplementary material",
    "supplementary materials",
    "ethics statement",
}

_ROMAN_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}


def _is_valid_roman(s: str) -> bool:
    """엄격한 로마 숫자 검증 (예: 'IIII', 'VX'는 거부)."""
    if not s or any(ch not in _ROMAN_VALUES for ch in s):
        return False
    return bool(re.fullmatch(r"M{0,3}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})", s))


def classify(text: str) -> Detected:
    """헤더 텍스트 한 줄을 번호 체계로 분류한다.

    번호가 있으면 번호 우선("1 Introduction"은 키워드가 아니라 dotted-arabic).
    번호가 없을 때만 무번호 키워드를 확인한다.
    """
    t = text.strip()

    # 1) 점 구분 아라비아 숫자 (가장 흔함: arXiv/ML 논문)
    m = _DOTTED_ARABIC.match(t)
    if m:
        number = m.group(1)
        depth = number.count(".") + 1
        return Detected(scheme=Scheme.DOTTED_ARABIC, number=number, depth=depth, title=m.group(2).strip())

    # 2) 로마 숫자 (검증 통과 시에만)
    m = _ROMAN.match(t)
    if m and _is_valid_roman(m.group(1)):
        return Detected(scheme=Scheme.ROMAN, number=m.group(1), depth=1, title=m.group(2).strip())

    # 3) 대문자 (부록 A. / A.1). 유효 로마 단일 문자(I,V,X,L,C,D,M)는 위에서 이미 로마로 잡힘.
    m = _LETTER.match(t)
    if m:
        letter, subnums = m.group(1), m.group(2)  # subnums 예: "1" 또는 "1.2" (없으면 None)
        if subnums:
            depth = 2 + subnums.count(".")  # A.1 → 2, A.1.2 → 3
            number = f"{letter}.{subnums}"
        else:
            depth = 1  # "A." / "A)"
            number = letter
        return Detected(scheme=Scheme.LETTER, number=number, depth=depth, title=m.group(3).strip())

    # 4) 무번호 관용 절
    cleaned = re.sub(r"[:\-–—]+\s*$", "", t).strip().lower()
    if cleaned in _UNNUMBERED_KEYWORDS or cleaned.split()[:1] == ["appendix"]:
        return Detected(scheme=Scheme.UNNUMBERED, number="", depth=1, title=t)

    # 5) 감지 실패
    return Detected(scheme=Scheme.NONE, number="", depth=1, title=t)


def elect_mode(detecteds: list[Detected]) -> str:
    """문서 전체의 지배적 체계를 다수결로 선출한다.

    반환: "arabic" (점-아라비아 위주) | "roman-mixed" (IEEE식 로마+문자+숫자 중첩)
    """
    has_roman = any(d.scheme is Scheme.ROMAN for d in detecteds)
    has_letter = any(d.scheme is Scheme.LETTER for d in detecteds)
    has_arabic = any(d.scheme is Scheme.DOTTED_ARABIC for d in detecteds)

    # 로마 최상위 섹션이 있고 그 아래 문자/숫자가 함께 쓰이면 IEEE식 중첩
    if has_roman and (has_letter or has_arabic):
        return "roman-mixed"
    return "arabic"


def assign_level(d: Detected, mode: str) -> tuple[int, bool]:
    """감지 결과를 헤더 레벨(1-6)로 변환. 반환: (level, needs_review)."""
    if d.scheme is Scheme.UNNUMBERED:
        return 1, False

    if d.scheme is Scheme.NONE:
        # 번호 없음 — 호출부가 marker_level로 폴백하도록 needs_review 표시
        return 1, True

    if mode == "roman-mixed":
        # IEEE식: 로마(외) → 문자(중) → 아라비아(내)
        if d.scheme is Scheme.ROMAN:
            return 1, False
        if d.scheme is Scheme.LETTER:
            return min(2 + (d.depth - 1), 6), False
        if d.scheme is Scheme.DOTTED_ARABIC:
            return min(3 + (d.depth - 1), 6), False

    # arabic 모드: 레벨 = 점 깊이 (사용자가 원한 "1→#, 1.1→##, 1.1.1→###")
    return min(max(d.depth, 1), 6), False


def relevel(texts: list[str]) -> list[tuple[Detected, int, bool]]:
    """헤더 텍스트 목록 → 각 (Detected, level, needs_review).

    구조 단계의 주 진입점. 문서 전체를 보고 모드를 선출한 뒤 레벨을 배정한다.
    """
    detecteds = [classify(t) for t in texts]
    mode = elect_mode(detecteds)
    out: list[tuple[Detected, int, bool]] = []
    for d in detecteds:
        level, needs_review = assign_level(d, mode)
        out.append((d, level, needs_review))
    return out
