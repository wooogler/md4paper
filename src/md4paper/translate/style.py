"""문체(manifest `korean_style` — 이름은 옛것이고 값은 논문 번역 언어의 문체) → 시스템 프롬프트 조각.

한국어: 해라체 | 합니다체 | 해요체. 다른 언어의 프리셋은 `md4paper.langs`. 모든 언어에서 custom:"<직접 프롬프트>".
"""

from __future__ import annotations

from md4paper import langs


def names(lang: str = langs.DEFAULT) -> tuple[str, ...]:
    """이름 있는 문체들 (custom 제외) — 옛 번역 기록에서 문체를 되찾을 때 후보로 쓴다."""
    return langs.style_names(lang)


def style_instruction(korean_style: str, lang: str = langs.DEFAULT) -> str:
    """문체 값 → 지시문."""
    return langs.style_instruction(korean_style or langs.get(lang).default_style, lang)
