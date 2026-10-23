"""번역 대상 언어 — 언어 코드(BCP 47)·이름·문체 프리셋.

사용자마다 모국어 하나로 읽는다: 전역 설정 `[translate].target_lang`(기본 ko)이 새 논문의 번역 언어이고,
논문은 번역을 한 언어로만 둔다(`WorkDir.lang`). 한국어는 예전 파일·캐시 이름과 프롬프트를 그대로 쓰고,
다른 언어만 이름에 언어 코드가 붙는다(`paper.ja.md`, `cache.ja.json`, …).

프리셋이 있는 언어(ko·ja·zh-Hans·es)는 문체를 고를 수 있다. 목록 밖 언어도 코드만 맞으면 번역한다 —
문체는 학술 문어체 하나와 `custom:"…"`(직접 쓴 지시문).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

DEFAULT = "ko"
# 파일 이름에 들어가므로 좁게 받는다: 주 언어 2~3자 + 하위 태그(문자·숫자 2~8자)
_CODE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")


@dataclass(frozen=True)
class Lang:
    code: str
    native: str        # 그 언어로 쓴 이름 (화면 표시)
    english: str       # 프롬프트에 쓰는 영어 이름
    styles: dict[str, str] = field(default_factory=dict)  # 문체 이름 → 지시문 (첫 항목이 기본)

    @property
    def default_style(self) -> str:
        return next(iter(self.styles))


# 한국어 지시문은 번역 캐시 키(옛 v1 = 시스템 프롬프트 해시)에 들어가므로 글자 하나 바꾸면 안 된다.
_PRESETS = {
    "ko": Lang("ko", "한국어", "Korean", {
        "해라체": "문어체 해라체(~한다/~이다)로 번역한다. 논문 번역의 표준 문체다.",
        "합니다체": "경어체 합니다체(~합니다/~입니다)로 번역한다.",
        "해요체": "부드러운 해요체(~해요/~예요)로 번역한다.",
    }),
    "ja": Lang("ja", "日本語", "Japanese", {
        "である調": "Write in the plain written style (である調: ～である／～だ), the standard register of "
                   "Japanese academic papers.",
        "です・ます調": "Write in the polite style (です・ます調: ～です／～ます).",
    }),
    "zh-Hans": Lang("zh-Hans", "简体中文", "Simplified Chinese", {
        "书面语": "Write formal written Chinese (书面语) as used in Chinese academic papers, in Simplified "
                 "characters with full-width Chinese punctuation.",
        "通俗": "Write plain, easy-to-read Chinese in Simplified characters while keeping technical terms exact.",
    }),
    "es": Lang("es", "Español", "Spanish", {
        "académico": "Write formal academic Spanish: impersonal register (se + verbo), neutral international "
                     "vocabulary.",
        "divulgativo": "Write clear, accessible Spanish for an educated general reader while keeping technical "
                       "terms exact.",
    }),
}

# 프리셋 없이 이름만 아는 언어 — 문체는 학술 문어체 하나
_NAMES = {
    "zh-Hant": ("繁體中文", "Traditional Chinese"), "fr": ("Français", "French"), "de": ("Deutsch", "German"),
    "pt": ("Português", "Portuguese"), "it": ("Italiano", "Italian"), "ru": ("Русский", "Russian"),
    "vi": ("Tiếng Việt", "Vietnamese"), "id": ("Bahasa Indonesia", "Indonesian"), "th": ("ไทย", "Thai"),
    "tr": ("Türkçe", "Turkish"), "ar": ("العربية", "Arabic"), "hi": ("हिन्दी", "Hindi"),
    "pl": ("Polski", "Polish"), "nl": ("Nederlands", "Dutch"), "uk": ("Українська", "Ukrainian"),
}
_ACADEMIC = "academic"


def normalize(code: str) -> str:
    """언어 코드를 표준 표기로 — `JA`→`ja`, `zh-hans`→`zh-Hans`, `pt-br`→`pt-BR`. 형식이 틀리면 ValueError."""
    raw = (code or "").strip().replace("_", "-")
    if not _CODE.match(raw):
        raise ValueError(f"언어 코드 형식이 아닙니다: {code!r} (예: ko, ja, zh-Hans, es)")
    head, *rest = raw.split("-")
    parts = [head.lower()]
    for p in rest:
        if len(p) == 4 and p.isalpha():
            parts.append(p.title())   # 문자 체계 (Hans·Hant)
        elif len(p) == 2 and p.isalpha():
            parts.append(p.upper())   # 지역 (BR·TW)
        else:
            parts.append(p.lower())
    return "-".join(parts)


def is_valid(code: object) -> bool:
    return isinstance(code, str) and bool(_CODE.match(code))


def get(code: str) -> Lang:
    """언어 정보. 프리셋이 없으면 이름만(모르면 코드 그대로) + 학술 문어체 하나."""
    code = normalize(code)
    if code in _PRESETS:
        return _PRESETS[code]
    native, english = _NAMES.get(code, (code, f"the language with BCP 47 code '{code}'"))
    return Lang(code, native, english, {
        _ACADEMIC: f"Write in the formal written register of academic papers in {english}.",
    })


def presets() -> tuple[str, ...]:
    """문체 프리셋이 있는 언어 코드들 (설정 화면 목록)."""
    return tuple(_PRESETS)


def style_names(code: str) -> tuple[str, ...]:
    return tuple(get(code).styles)


def style_instruction(style: str, code: str = DEFAULT) -> str:
    """문체 값 → 지시문. `custom:"…"`이면 그 글, 그 언어에 없는 이름이면 그 언어의 기본 문체."""
    s = (style or "").strip()
    if s.startswith("custom:"):
        return s[len("custom:"):].strip().strip('"').strip()
    lang = get(code)
    return lang.styles.get(s, lang.styles[lang.default_style])


def fit_style(code: str, style: str, default: str | None = None) -> str:
    """이 언어로 번역할 때 쓸 문체 값 — **다른 언어의 프리셋 이름일 때만** 바꾼다(default, 없으면 언어 기본).

    번역 캐시 키에 문체 값이 글자 그대로 들어가므로, 이 언어의 프리셋·`custom:`·모르는 값은 그대로 둔다
    (한국어 논문의 캐시가 다시 계산되면 안 된다). 언어를 바꿔 번역할 때 manifest에 남은 옛 언어의 문체
    ("해라체"로 일본어 번역 등)만 고친다.
    """
    lang = get(code)
    s = (style or "").strip()
    if not s:
        return default or lang.default_style
    if s.startswith("custom:") or s in lang.styles:
        return style
    if any(s in other.styles for c, other in _PRESETS.items() if c != lang.code):
        ok = default and (default in lang.styles or default.startswith("custom:"))
        return default if ok else lang.default_style
    return style


# --- 화면 표시 ------------------------------------------------------------------

# 문체 선택 칸의 설명 — 한국어는 예전 화면 그대로, 다른 언어는 이름 옆에 짧은 풀이
_STYLE_HINT = {
    "해라체": "~한다 · 논문 표준", "합니다체": "~합니다 · 경어", "해요체": "~해요 · 부드럽게",
    "である調": "～である · 논문 표준", "です・ます調": "～です · 경어",
    "书面语": "학술 문어체 · 논문 표준", "通俗": "쉬운 문장",
    "académico": "학술 문어체 · 논문 표준", "divulgativo": "쉬운 문장",
    _ACADEMIC: "학술 문어체",
}


def style_options(code: str) -> dict[str, str]:
    """문체 선택 칸 {값: 표시} — 그 언어의 프리셋들."""
    return {s: f"{s} ({_STYLE_HINT[s]})" if s in _STYLE_HINT else s for s in style_names(code)}


def language_options(current: str | None = None) -> dict[str, str]:
    """번역 언어 선택 칸 {코드: '日本語 · ja'} — 프리셋 언어, 이름만 아는 언어, 그리고 지금 값(목록 밖이어도)."""
    codes = [*_PRESETS, *_NAMES]
    if current and current not in codes:
        codes.append(current)
    return {c: f"{get(c).native} · {c}" for c in codes}


def short(code: str) -> str:
    """단추에 쓰는 짧은 이름 — `KO`·`JA`·`ZH`·`ES` (주 언어 대문자)."""
    return normalize(code).split("-")[0].upper()
