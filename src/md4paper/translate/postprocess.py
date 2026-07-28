"""번역 후처리 — 병기(첫 등장 시 원어 병기) 전역 1회 정리.

섹션을 병렬·독립 번역하므로 각 섹션이 병기 용어를 매번 "한국어(English)"로 병기한다.
조립된 ko.md에서 policy=병기-first-use 용어별로 문서 순서 첫 등장만 남기고 이후 병기는 뗀다(결정론적).
"""

from __future__ import annotations

import re

from md4paper.ir import GlossaryList
from md4paper.translate import chunker


def dedupe_first_use(text: str, gloss: GlossaryList) -> str:
    """policy=병기-first-use 용어의 "한국어(English)" 병기를 문서 첫 등장만 남기고 정리."""
    biki = [
        e for e in gloss.entries
        if e.policy == "병기-first-use" and e.korean.strip() and e.term.strip()
    ]
    if not biki:
        return text

    # 코드/수식/링크 등 보호 스팬 안의 괄호는 건드리지 않는다
    protected, store = chunker.protect(text)
    for e in biki:
        kor, term = e.korean.strip(), e.term.strip()
        # "한국어(English)" — 반각/전각 괄호·공백 허용, 원어는 대소문자 무시
        pat = re.compile(rf"{re.escape(kor)}\s*[(（]\s*{re.escape(term)}\s*[)）]", re.IGNORECASE)
        seen = {"n": 0}

        def repl(m: re.Match, kor=kor, seen=seen) -> str:
            seen["n"] += 1
            return m.group(0) if seen["n"] == 1 else kor  # 첫 등장만 병기 유지

        protected = pat.sub(repl, protected)
    return chunker.restore(protected, store)


def enforce_keep(text: str, gloss: GlossaryList) -> str:
    """policy=keep 용어가 (LLM 실수로) 한국어로 번역됐으면 영어 원문으로 되돌린다(결정론적 보장).

    용어집의 korean(번역어) 표기를 term(영어)로 치환한다 — keep 용어는 애초에 번역하면 안 되므로.
    '한국어(원문)' 병기 형태도 통째로 영어 원문으로. 코드/수식/링크 보호 스팬은 건드리지 않는다.
    """
    keep = [
        e for e in gloss.entries
        if e.policy == "keep" and e.korean.strip() and e.term.strip()
    ]
    if not keep:
        return text
    protected, store = chunker.protect(text)
    for e in keep:
        kor, term = e.korean.strip(), e.term.strip()
        protected = re.sub(  # '한국어(English)' 병기 → 영어만
            rf"{re.escape(kor)}\s*[(（]\s*{re.escape(term)}\s*[)）]", term, protected, flags=re.IGNORECASE)
        protected = protected.replace(kor, term)  # 단독 한국어 → 영어 원문
    return chunker.restore(protected, store)
