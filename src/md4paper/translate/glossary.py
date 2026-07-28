"""용어집 — 번역 전에 자동 생성하고, 사용자가 번역어를 검토·수정한 뒤 번역에 적용한다.

glossary.yaml은 편집 가능한 아티팩트다: 지금은 $EDITOR, 이후 웹 UI가 "자동 생성 → 표시 →
번역어 수정 → 번역"의 검토 지점으로 삼는다. 번역 단계는 이 파일을 소비한다.
"""

from __future__ import annotations

import io
import re

from ruamel.yaml import YAML

from md4paper.ir import GlossaryEntry, GlossaryList
from md4paper.llm.base import Provider
from md4paper.workdir import WorkDir

_SYSTEM = """You build a glossary for translating an academic paper into Korean.
From the title and the excerpt below (abstract + introduction in full, plus section titles), pick this
paper's key technical terms and propose a Korean rendering for each. Each entry:
- term: the English term (verbatim)
- korean: its Korean translation (or transliteration)
- policy: translate | transliterate | keep | 병기-first-use  (병기 = show the English alongside on first use)
Only terms that must be translated consistently in this paper's context, at most 25. Exclude common everyday words.
Terms are case-insensitive — never list one twice in different capitalizations (e.g. both "Living Test Set" and
"living test set"); pick one form. The `korean` field must be written in Korean."""

_HEADER = (
    "# 용어집 — 번역 전에 검토/수정하세요 (웹 UI에서도 편집 가능).\n"
    "# policy: translate(의미번역) | transliterate(음역) | keep(원어유지) | 병기-first-use(첫등장시 병기)\n"
)


def _yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.width = 4096
    return y


def term_key(term: str) -> str:
    """중복 판정 키 — 대소문자와 앞뒤/중복 공백 차이를 흡수한다.

    "Data-Prompt Co-Evolution"과 "data-prompt co-evolution"은 논문 안에서(제목·문장 첫머리 등)
    표기만 다른 같은 용어다. 둘을 따로 두면 번역어·policy가 서로 어긋난 채 프롬프트에 들어간다.
    """
    return re.sub(r"\s+", " ", term or "").strip().casefold()


def dedupe(entries: list[GlossaryEntry]) -> list[GlossaryEntry]:
    """대소문자만 다른 항목을 하나로 합친다 — 먼저 나온 표기·설정을 남기고,

    앞선 항목의 번역어가 비어 있으면 뒤 항목의 번역어·policy로 채운다."""
    at: dict[str, int] = {}
    kept: list[GlossaryEntry] = []
    for e in entries:
        key = term_key(e.term)
        if not key:
            continue
        if key not in at:
            at[key] = len(kept)
            kept.append(e.model_copy())
        elif not kept[at[key]].korean.strip() and e.korean.strip():
            kept[at[key]].korean, kept[at[key]].policy = e.korean, e.policy
    return kept


def to_yaml(gloss: GlossaryList) -> str:
    data = [{"term": e.term, "korean": e.korean, "policy": e.policy} for e in dedupe(gloss.entries)]
    stream = io.StringIO()
    _yaml().dump(data, stream)
    return _HEADER + stream.getvalue()


def save(gloss: GlossaryList, wd: WorkDir) -> None:
    wd.translate.mkdir(parents=True, exist_ok=True)
    wd.glossary_yaml.write_text(to_yaml(gloss), encoding="utf-8")


def load(wd: WorkDir) -> GlossaryList:
    data = _yaml().load(wd.glossary_yaml.read_text(encoding="utf-8")) or []
    entries = [
        GlossaryEntry(term=str(d["term"]), korean=str(d.get("korean", "")), policy=d.get("policy", "translate"))
        for d in data
        if d.get("term")
    ]
    return GlossaryList(entries=dedupe(entries))  # 예전에 저장된 대소문자 중복도 열 때 합쳐진다


_SYSTEM_ADD = """You pick technical terms to ADD to a glossary for translating an academic paper into Korean.
From the body excerpt below, choose key technical terms, but EXCLUDE any already in the 'existing terms' list —
only new ones. Each entry:
- term: the English term
- korean: its Korean translation (or transliteration)
- policy: translate | transliterate | keep | 병기-first-use
Only newly added terms, at most 20. Exclude common everyday words. Terms are case-insensitive — a term whose
capitalization differs from an existing one is NOT new. The `korean` field must be in Korean."""


def generate(provider: Provider, title: str, source: str, *, max_tokens: int = 4096) -> GlossaryList:
    """LLM으로 용어집 초안 생성. source=초록·서론·섹션 제목·첫 문단 발췌."""
    user = f"Title: {title}\n\nExcerpt:\n{source}"
    result = provider.parse(_SYSTEM, user, GlossaryList, max_tokens=max_tokens)
    return GlossaryList(entries=dedupe(result.entries))


def extend(
    provider: Provider, title: str, text: str, existing_terms: list[str], *, max_tokens: int = 4096
) -> GlossaryList:
    """본문 발췌에서 '기존 용어에 없는' 새 용어만 뽑는다 (중복은 결정론적으로 한 번 더 거른다)."""
    have = ", ".join(existing_terms) or "(none)"
    user = f"Title: {title}\n\nExisting terms: {have}\n\nBody excerpt:\n{text}"
    result = provider.parse(_SYSTEM_ADD, user, GlossaryList, max_tokens=max_tokens)
    have = {term_key(t) for t in existing_terms}
    fresh = [e for e in result.entries if term_key(e.term) not in have]
    return GlossaryList(entries=dedupe(fresh))  # 응답 안에서 대소문자만 다른 중복도 제거


def ensure(
    provider: Provider, wd: WorkDir, title: str, source: str, *, regenerate: bool = False
) -> GlossaryList:
    """glossary.yaml이 있으면 그걸(사용자 수정 반영), 없으면 생성해 저장."""
    if wd.glossary_yaml.exists() and not regenerate:
        return load(wd)
    gloss = generate(provider, title, source)
    save(gloss, wd)
    return gloss
