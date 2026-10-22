"""한국어 문체(korean_style) → 시스템 프롬프트 조각.

해라체 | 합니다체 | 해요체 | custom:"<직접 프롬프트>". 웹 UI 주요 설정.
"""

from __future__ import annotations

_STYLES = {
    "해라체": "문어체 해라체(~한다/~이다)로 번역한다. 논문 번역의 표준 문체다.",
    "합니다체": "경어체 합니다체(~합니다/~입니다)로 번역한다.",
    "해요체": "부드러운 해요체(~해요/~예요)로 번역한다.",
}


def names() -> tuple[str, ...]:
    """이름 있는 문체들 (custom 제외) — 옛 번역 기록에서 문체를 되찾을 때 후보로 쓴다."""
    return tuple(_STYLES)


def style_instruction(korean_style: str) -> str:
    """korean_style 값 → 문체 지시문."""
    ks = (korean_style or "해라체").strip()
    if ks.startswith("custom:"):
        return ks[len("custom:") :].strip().strip('"').strip()
    return _STYLES.get(ks, _STYLES["해라체"])
