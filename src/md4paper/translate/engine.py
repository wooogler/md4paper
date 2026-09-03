"""번역 엔진 — 고정 시스템 프롬프트(문체+용어집+문서 컨텍스트) + 청크별 번역 + 재시도 사다리."""

from __future__ import annotations

from md4paper.ir import GlossaryList
from md4paper.llm.base import Provider
from md4paper.translate import chunker
from md4paper.translate.style import style_instruction
from md4paper.translate.validate import validate

_POLICY_NOTE = {
    "translate": "의미 번역",
    "transliterate": "음역",
    "keep": "원어 유지",
    "병기-first-use": "첫 등장 시 원어 병기",
}


def _glossary_block(gloss: GlossaryList) -> str:
    if not gloss.entries:
        return "(없음)"
    lines = []
    for e in gloss.entries:
        if e.policy == "keep":  # 원어 유지 — 한국어를 보여주지 않는다(보여주면 LLM이 그걸로 번역함)
            lines.append(f"- {e.term} → 영어 원문 그대로 (번역·음역 금지)")
        elif e.policy == "transliterate":
            lines.append(f"- {e.term} → {e.korean} (음역, 소리대로)")
        elif e.policy == "병기-first-use":
            lines.append(f"- {e.term} → {e.korean} (첫 등장 시 '한국어(원문)' 병기)")
        else:  # translate
            lines.append(f"- {e.term} → {e.korean} (이 번역어로)")
    return "\n".join(lines)


def build_system_prompt(
    korean_style: str, gloss: GlossaryList, title: str, abstract: str, outline: str = ""
) -> str:
    """전 섹션 공통 고정 프롬프트 (프로바이더 캐싱 대상). outline=문서 섹션 제목 트리."""
    outline_block = (
        f"\n\n[문서 개요 — 섹션 구조. 지금 번역하는 부분이 논문 어디에 속하는지 파악용]\n{outline}"
        if outline else ""
    )
    return f"""당신은 학술 논문을 한국어로 번역하는 전문 번역가다.

[문체] {style_instruction(korean_style)}

[구조 보존 — 반드시 지킬 것]
- 마크다운 구조를 원문과 동일하게: 헤더 #의 개수와 레벨, 목록, 표의 | 구조를 그대로 유지한다.
- ⟦MD4_n⟧ 형태의 토큰은 절대 번역·수정·삭제하지 말고 위치 그대로 둔다(코드·수식·링크·이미지의 자리표시자).
- 인라인 수식 $...$, 인라인 코드 `...` 의 내용은 번역하지 않고 그대로 둔다.
- 인용 마커([1], [저자 연도] 등)와 앵커(#ref-N)는 그대로 유지한다.
- 유저 메시지에 '=== 앞 문맥' 블록이 있으면 참고만 하고 절대 다시 출력하지 마라. '=== 번역할 본문' 아래만 번역한다.
- 번역문만 출력한다. 설명이나 주석을 덧붙이지 않는다.

[용어 일관성] 아래 용어집을 반드시 따른다(대소문자 무시 — 제목·문장 첫머리라 표기가 달라도 같은 용어). 각 항목의 괄호 지시대로:
- '영어 원문 그대로'인 용어는 번역·음역하지 말고 영어 그대로 둔다(예: Data-Prompt Co-Evolution → Data-Prompt Co-Evolution).
- '이 번역어로'는 그 한국어로 옮긴다. '음역'은 소리 나는 대로 한글로 옮긴다.
- '첫 등장 시 병기'는 나올 때 "한국어(원문)"로 쓴다(중복 병기는 후처리가 정리하니 등장할 때마다 병기해도 된다).
{_glossary_block(gloss)}

[문서 컨텍스트 — 주제와 용어 일관성 유지에 활용]
제목: {title}
초록: {abstract}{outline_block}"""


def translate_chunk(
    provider: Provider, system_prompt: str, source: str, *, max_tokens: int = 8192,
    protect_headings: bool = False, context_tail: str = "",
) -> tuple[str, list[str]]:
    """한 청크 번역: 보호 → 호출 → 복원. context_tail=직전 섹션 원문 끝(참고용, 번역 안 함).

    반환: (복원된 번역문, **모델이 흘린 센티넬 키 목록**). restore는 str.replace라 모델이
    ⟦MD4_n⟧을 빠뜨리면 그 링크·이미지·수식은 영영 복원되지 않는데, 잔존 검사는 *남은* 센티넬만
    보므로 사라진 것은 못 잡는다. 그때 검증에는 "링크 수" 같은 엉뚱한 이름으로만 나타나
    재시도 메시지가 모델을 헛짚게 한다 — 그래서 여기서 직접 세어 돌려준다.
    """
    protected, store = chunker.protect(source, protect_headings=protect_headings)
    if context_tail.strip():
        user = (
            "=== 앞 문맥 (직전 섹션 끝 · 참고용, 다시 출력하지 말 것) ===\n"
            + context_tail.strip()
            + "\n\n=== 번역할 본문 (이 아래만 번역) ===\n"
            + protected
        )
    else:
        user = protected
    out = provider.complete(system_prompt, user, max_tokens=max_tokens)
    missing = [k for k in store if k not in out]
    return chunker.restore(out, store), missing


def translate_with_retry(
    provider: Provider, system_prompt: str, source: str, *, max_retries: int = 1,
    protect_headings: bool = False, context_tail: str = "",
) -> tuple[str, str, list[str]]:
    """번역 + 구조 검증 재시도 사다리.

    반환: (텍스트, 상태[ok|retried|passthrough], 위반항목). passthrough면 마지막 시도의 위반 목록,
    성공이면 빈 목록.
    """
    sys_prompt = system_prompt
    problems: list[str] = []
    for attempt in range(max_retries + 1):
        translated, missing = translate_chunk(
            provider, sys_prompt, source, protect_headings=protect_headings, context_tail=context_tail
        )
        problems = validate(source, translated)
        if missing:  # 진짜 원인을 앞에 세운다 — 재시도 메시지가 이걸 먼저 읽어야 한다
            problems.insert(0, f"플레이스홀더 유실(⟦MD4_n⟧ {len(missing)}개를 빠뜨림)")
        if not problems:
            return translated, ("ok" if attempt == 0 else "retried"), []
        sys_prompt = (
            system_prompt
            + "\n\n[재시도] 직전 번역이 다음 구조를 어겼다: "
            + ", ".join(problems)
            + ". 이번엔 마크다운 구조를 정확히 보존하라."
        )
    # 두 번 실패 → 영어 원문 통과 (구조는 확실히 보존). 위반 목록도 함께 돌려준다.
    # 위반 항목을 주석에 적어 둔다 — 이유를 문서 자체가 들고 있어야 나중에 왜 실패했는지 알 수 있다
    # (status.json에도 남기지만, ko.md만 손에 든 사람도 바로 보이도록).
    why = f"구조 검증 실패: {', '.join(problems)}" if problems else "구조 검증 실패"
    return f"<!-- md4paper: untranslated ({why}) -->\n" + source, "passthrough", problems
