"""translate 단위 테스트 — 청킹/보호, 검증, 문체, 시스템 프롬프트, 병기 후처리, 유닛화."""

from md4paper.ir import GlossaryEntry, GlossaryList
from md4paper.translate import chunker, engine, glossary, postprocess
from md4paper.translate.style import style_instruction
from md4paper.translate.validate import validate


def test_split_by_paragraphs_reassembles():
    text = "para one\nline two\n\npara two\n\npara three\n\npara four"
    parts = chunker.split_by_paragraphs(text, target_chars=15)
    assert len(parts) >= 2  # 여러 파트로 나뉨
    assert "\n".join(parts) == text  # 합치면 원문 그대로


def test_split_by_paragraphs_keeps_fenced_code():
    text = "intro\n\n```\n\n\ncode with blank lines\n\n\n```\n\ntail"
    parts = chunker.split_by_paragraphs(text, target_chars=5)
    assert "\n".join(parts) == text
    # 펜스 내부 빈 줄에서 안 쪼개짐 — 코드 블록이 한 파트 안에 온전히
    assert any("code with blank lines" in p and p.count("```") == 2 for p in parts)


def test_dedupe_first_use_keeps_only_first():
    gloss = GlossaryList(entries=[
        GlossaryEntry(term="Transformer", korean="트랜스포머", policy="병기-first-use"),
        GlossaryEntry(term="attention", korean="어텐션", policy="transliterate"),  # 병기 아님
    ])
    text = "트랜스포머(Transformer)는 좋다. 다른 곳에서도 트랜스포머(Transformer)를 쓴다. 어텐션(attention)."
    out = postprocess.dedupe_first_use(text, gloss)
    assert out.count("트랜스포머(Transformer)") == 1  # 첫 등장만 병기
    assert out.count("트랜스포머") == 2  # 나머지는 원어 없이
    assert "어텐션(attention)" in out  # 병기 정책 아닌 건 그대로


def test_glossary_dedupe_ignores_case():
    entries = [
        GlossaryEntry(term="Data-Prompt Co-Evolution", korean="데이터-프롬프트 공진화", policy="keep"),
        GlossaryEntry(term="test set", korean="테스트 세트", policy="병기-first-use"),
        GlossaryEntry(term="data-prompt  co-evolution", korean="", policy="translate"),  # 표기만 다른 같은 용어
        GlossaryEntry(term="TEST SET", korean="시험 세트", policy="translate"),
    ]
    kept = glossary.dedupe(entries)
    assert [e.term for e in kept] == ["Data-Prompt Co-Evolution", "test set"]  # 먼저 나온 표기 유지
    assert kept[1].korean == "테스트 세트" and kept[1].policy == "병기-first-use"  # 앞 행의 설정이 이긴다


def test_glossary_dedupe_fills_empty_korean():
    kept = glossary.dedupe([
        GlossaryEntry(term="Edge Case", korean="", policy="translate"),
        GlossaryEntry(term="edge case", korean="경계 사례", policy="병기-first-use"),
    ])
    assert len(kept) == 1
    assert kept[0].term == "Edge Case"  # 표기는 앞 행
    assert (kept[0].korean, kept[0].policy) == ("경계 사례", "병기-first-use")  # 비어 있던 번역어는 뒤 행에서 채움


def test_glossary_save_load_merges_case_variants(tmp_path):
    from md4paper.workdir import WorkDir

    wd = WorkDir(tmp_path / "p.md4")
    glossary.save(GlossaryList(entries=[
        GlossaryEntry(term="LLM-as-judge", korean="LLM 평가자", policy="병기-first-use"),
        GlossaryEntry(term="llm-as-judge", korean="엘엘엠 평가자", policy="translate"),
    ]), wd)
    loaded = glossary.load(wd)
    assert [(e.term, e.korean) for e in loaded.entries] == [("LLM-as-judge", "LLM 평가자")]


def test_dedupe_first_use_protects_fenced_code():
    gloss = GlossaryList(entries=[
        GlossaryEntry(term="x", korean="엑스", policy="병기-first-use"),
    ])
    text = "엑스(x)는 변수다.\n\n```\n엑스(x) = 1\n```\n\n또 엑스(x)."
    out = postprocess.dedupe_first_use(text, gloss)
    assert "```\n엑스(x) = 1\n```" in out  # 펜스 코드 안은 그대로 (dedup 대상 아님)
    # 프로즈: 첫 등장 유지 + 코드 1개 = "엑스(x)" 2회, 두 번째 프로즈는 원어 제거
    assert out.count("엑스(x)") == 2
    assert out.endswith("또 엑스.")


def test_protect_restore_roundtrip():
    text = "See `code[1]` and $$E=mc^2$$ and ![img](p.png) and [link](http://x.com)."
    protected, store = chunker.protect(text)
    assert "http://x.com" not in protected  # URL이 링크 안에 보호됨
    assert "$$E=mc^2$$" not in protected
    assert "⟦MD4_" in protected
    assert chunker.restore(protected, store) == text


def test_protect_fenced_code():
    text = "before\n```\narr[1] = $x$\n```\nafter"
    protected, store = chunker.protect(text)
    assert "arr[1]" not in protected
    assert chunker.restore(protected, store) == text


def test_inline_math_and_code_not_protected():
    text = "The value $x$ and `y` inline."
    protected, _ = chunker.protect(text)
    assert "$x$" in protected  # 인라인 수식은 인라인 유지
    assert "`y`" in protected


def test_protect_headings_keeps_titles():
    text = "# Introduction\n\nsome body text\n\n## Method\n\nmore"
    protected, store = chunker.protect(text, protect_headings=True)
    # 헤더의 # 구조는 남고 제목은 센티넬로
    assert "# ⟦MD4_" in protected
    assert "Introduction" not in protected
    assert "Method" not in protected
    # 본문은 그대로 (번역 대상)
    assert "some body text" in protected
    # 복원하면 원문
    assert chunker.restore(protected, store) == text


def test_split_chunks_reassembles():
    md = "# A\n\nbody a\n\n## B\n\nbody b\n\n## C\n\nbody c\n"
    chunks = chunker.split_chunks(md, min_chars=1)
    assert "\n".join(chunks) + "\n" == md  # 무손실 복원
    assert len(chunks) >= 3


def test_split_chunks_merges_small():
    md = "# A\n\nx\n\n## B\n\ny\n\n## C\n\nz\n"
    big = chunker.split_chunks(md, min_chars=10_000)
    assert len(big) == 1  # 전부 병합


def test_validate_passes_identical_structure():
    src = "# Head\n\nSome text with $x$ and `y` and | a | b |."
    # 번역: 텍스트만 바꾸고 구조 유지
    tr = "# 제목\n\n$x$ 와 `y` 그리고 | a | b | 가 있는 문장."
    assert validate(src, tr) == []


def test_validate_catches_heading_loss():
    src = "# Head\n\ntext"
    tr = "제목\n\ntext"  # 헤더 마크 사라짐
    problems = validate(src, tr)
    assert any("헤더" in p for p in problems)


def test_validate_catches_placeholder_leftover():
    src = "text"
    tr = "번역 ⟦MD4_0⟧ 남음"
    assert any("플레이스홀더" in p for p in validate(src, tr))


def test_validate_catches_math_count():
    src = "$a$ and $b$"
    tr = "$a$ 만"  # 짝 맞던 수식 스팬 2개 → 1개 (유실)
    assert any("수식" in p for p in validate(src, tr))


# --- 구조가 멀쩡한 번역을 잡던 회귀 (실측 2건) ---------------------------------
def test_validate_allows_currency_dollar_dropped_in_korean():
    """통화 기호는 수식 구분자가 아니다.

    실측: CoAuthor 4.2.3~4.3.2 절이 "We paid them $2.50" → "2.50달러를 지급했다"로
    `$`가 1→0이 되어 구조 파손 판정을 받았다. 재시도 메시지가 "수식 구분자($) 수"라고
    알려 주자 모델이 `$2.50$`로 고쳐 1→2가 되어 두 번째도 실패, 그 절이 영어로 남았다.
    """
    src = "We paid them $2.50 for each writing session."
    assert validate(src, "각 글쓰기 세션마다 2.50달러를 지급했다.") == []


def test_validate_allows_inline_code_added_by_translator():
    """원문에 없던 짝 맞는 인라인 코드를 붙이는 것은 구조 파손이 아니다.

    실측: GPT Instructor 3.5절이 "the polr function in R" → "R의 `polr` 함수"로
    백틱이 0→2가 되어 첫 시도가 반려됐다(두 번째 시도는 통과).
    """
    src = "we ran an ordinal logistic regression using the polr function in R."
    assert validate(src, "R의 `polr` 함수를 사용하여 순서형 로지스틱 회귀를 실시했다.") == []


def test_validate_still_catches_lost_and_unbalanced_delimiters():
    """느슨해진 것은 '추가'뿐 — 유실과 짝 없는 구분자는 그대로 잡는다."""
    assert any("코드" in p for p in validate("Use the `polr` function.", "polr 함수를 쓴다."))
    assert any("$" in p for p in validate("No math here.", "여기엔 $ 수식이 없다."))
    assert any("백틱" in p for p in validate("No code here.", "여기엔 ` 코드가 없다."))


def test_translate_chunk_reports_dropped_sentinel():
    """모델이 ⟦MD4_n⟧을 흘리면 그 사실 자체를 돌려준다.

    restore는 str.replace라 흘린 센티넬은 영영 복원되지 않는데, 잔존 검사는 *남은* 것만
    보므로 못 잡는다. 검증에는 "링크 수"로만 나타나 재시도 메시지가 모델을 헛짚게 된다.
    """
    src = "See [a](http://x) and [b](http://y)."
    prov = engine_fake_dropping_one_sentinel()
    out, missing = engine.translate_chunk(prov, "sys", src)
    assert len(missing) == 1
    assert any("링크" in p for p in validate(src, out))  # 겉으로는 링크 수로 보이고
    assert missing  # 진짜 이유는 센티넬 유실이다


def engine_fake_dropping_one_sentinel():
    from md4paper.llm.base import FakeProvider

    return FakeProvider(  # 두 번째 센티넬을 흘리는 모델
        complete_fn=lambda system, user: user.replace("⟦MD4_1⟧", "", 1),
        model="fake-model",
    )


def test_style_instruction():
    assert "한다" in style_instruction("해라체")
    assert "합니다" in style_instruction("합니다체")
    assert "해요" in style_instruction("해요체")
    # custom 프롬프트
    assert style_instruction('custom:"아주 격식있게"') == "아주 격식있게"


def test_build_system_prompt_injects_context():
    gloss = GlossaryList(entries=[GlossaryEntry(term="attention", korean="어텐션", policy="transliterate")])
    sp = engine.build_system_prompt("해라체", gloss, "My Title", "This is the abstract.")
    assert "My Title" in sp  # 문서 컨텍스트 주입
    assert "This is the abstract." in sp
    assert "attention → 어텐션" in sp  # 용어집 주입
    assert "한다" in sp  # 문체
    assert "⟦MD4_n⟧" in sp  # 플레이스홀더 보존 지시


def test_validate_does_not_pair_two_currency_sigils_as_math():
    """한 줄의 통화 기호 둘을 '수식 스팬'으로 묶지 않는다.

    실측 코퍼스에서 인라인 수식 매칭 10개 중 4개가 이런 가짜였다("US$100 and C$50",
    "$1.25/MTok output) and Claude 3.5 Sonnet ($"). 가짜로 묶이면 그 기호를 자연스럽게 옮긴
    번역이 '수식 스팬 유실'로 반려된다 — 고치려던 바로 그 오탐이 되돌아온다.
    """
    assert validate("We paid US$100 and C$50.", "미화 100달러와 캐나다화 50달러를 지급했다.") == []
    assert validate("Cost ($) | Output ($)", "비용 ($) | 출력 ($)") == []
    # 진짜 수식은 형태를 가리지 않고 그대로 지킨다
    for src, broken in (("$\\alpha=0.5$ holds", "알파는 0.5다"),
                        ("- $\\theta_j$ = threshold", "- 임계값"),
                        ("complexity $O(n \\log n)$", "복잡도는 n log n")):
        assert any("수식" in p for p in validate(src, broken)), src
