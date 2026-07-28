"""translate 엔드투엔드 — FakeProvider로 실제 API 없이 전체 흐름 검증."""

from pathlib import Path

import pytest

from md4paper import pipeline
from md4paper.ir import GlossaryEntry, GlossaryList
from md4paper.llm import FakeProvider
from md4paper.translate.apply import run_translate
from md4paper.workdir import WorkDir

CORPUS = Path(__file__).parent / "corpus"

FAKE_GLOSSARY = GlossaryList(entries=[
    GlossaryEntry(term="attention", korean="어텐션", policy="transliterate"),
    GlossaryEntry(term="encoder", korean="인코더", policy="translate"),
])


def _body(user: str) -> str:
    """유저 메시지에서 '번역할 본문'만 추출 (실제 번역가처럼 앞 문맥 블록은 무시)."""
    lines = user.split("\n")
    for i, ln in enumerate(lines):
        if ln.startswith("=== 번역할 본문"):
            return "\n".join(lines[i + 1:])
    return user


def _identity_translate(system: str, user: str) -> str:
    """구조 보존 가짜 번역 — 본문을 그대로 반환(플레이스홀더/구조 유지)."""
    return _body(user)


def _fake(complete_fn=_identity_translate):
    return FakeProvider(
        complete_fn=complete_fn,
        parse_fn=lambda s, u, schema: FAKE_GLOSSARY,
        model="gpt-5.6-luna",
    )


@pytest.fixture
def converted(tmp_path):
    wd = WorkDir(tmp_path / "paper.md4")
    pipeline.convert(CORPUS / "sample_cite.md", wd)
    return wd


def test_translate_produces_ko_md(converted):
    summary = run_translate(converted, _fake(), "해라체")
    assert converted.ko_md.exists()
    assert summary["chunks"] >= 1
    assert summary["passthrough"] == 0
    # 컨텍스트·용어집 아티팩트 생성
    assert converted.context_md.exists()
    assert converted.glossary_yaml.exists()
    assert "Abstract" in converted.context_md.read_text(encoding="utf-8")


def test_structure_preserved(converted):
    run_translate(converted, _fake(), "해라체")
    en = converted.en_md.read_text(encoding="utf-8")
    ko = converted.ko_md.read_text(encoding="utf-8")
    # identity 번역이므로 헤더 수 동일
    assert en.count("\n# ") == ko.count("\n# ")
    assert en.count("\n## ") == ko.count("\n## ")


def test_cache_reuse(converted):
    fake1 = _fake()
    run_translate(converted, fake1, "해라체")
    # 두 번째 실행: 전부 캐시 히트 → 신규 번역 0
    fake2 = _fake()
    summary = run_translate(converted, fake2, "해라체")
    assert summary["cached"] == summary["chunks"]
    assert summary["ok"] == 0


def test_style_change_invalidates_cache(converted):
    run_translate(converted, _fake(), "해라체")
    # 문체 변경 → 시스템 프롬프트 변경 → 캐시 무효 → 재번역
    summary = run_translate(converted, _fake(), "합니다체")
    assert summary["cached"] == 0


def test_glossary_edit_respected(converted):
    # 용어집 먼저 생성
    pipeline.run_glossary(converted, _fake(), regenerate=True)
    # 사용자가 수정
    converted.glossary_yaml.write_text(
        "- term: attention\n  korean: 주의집중\n  policy: translate\n", encoding="utf-8"
    )
    # 번역 시 재생성하지 않고 수정본 사용 (parse 호출 안 됨을 간접 확인: 항목 수=1)
    summary = run_translate(converted, _fake(), "해라체")
    assert summary["glossary"] == 1


def test_progress_callback_reports_status(converted):
    # on_progress(done, total, status{sid: 상태}) — 진행바 + 섹션별 상태
    calls: list[tuple[int, int, dict]] = []
    run_translate(converted, _fake(), "해라체",
                  on_progress=lambda d, t, s: calls.append((d, t, dict(s))), workers=1)
    assert calls, "진행 콜백이 호출되지 않음"
    assert all(0 <= d <= t for d, t, _ in calls)
    assert [d for d, _, _ in calls] == sorted(d for d, _, _ in calls)  # 단조 비감소
    assert calls[-1][0] == calls[-1][1]  # 마지막엔 100%
    assert any(v == "done" for v in calls[-1][2].values())  # 섹션이 완료로 표시됨


def test_parallel_preserves_order(converted):
    # 병렬(workers=4)이어도 결과가 문서 순서로 조립되는지
    order = []

    def mark(system: str, user: str) -> str:
        body = _body(user)
        first = next((ln for ln in body.splitlines() if ln.strip()), "")
        order.append(first)
        return body

    fake = FakeProvider(complete_fn=mark, parse_fn=lambda s, u, sc: FAKE_GLOSSARY, model="fake")
    run_translate(converted, fake, "해라체", workers=4)
    ko = converted.ko_md.read_text(encoding="utf-8")
    # 헤더가 문서 순서대로 (identity라 원문 헤더 유지)
    assert ko.index("# Abstract") < ko.index("# 1 Introduction") < ko.index("# References")


def test_references_not_translated_by_default(converted):
    # sample_cite.md에는 References 섹션이 있음 → 기본적으로 영어 원문 유지
    from md4paper.review import manifest as manifest_io

    m = manifest_io.load(converted)
    assert m.translate_references is False  # 기본값

    # 번역기가 "[KO]" 접두를 붙이는 가짜로 어떤 청크가 번역됐는지 표시
    def mark(system: str, user: str) -> str:
        return "[KO]\n" + _body(user)

    fake = FakeProvider(complete_fn=mark, parse_fn=lambda s, u, sc: FAKE_GLOSSARY, model="fake")
    run_translate(converted, fake, "해라체")
    ko = converted.ko_md.read_text(encoding="utf-8")
    # 참고문헌 항목은 번역 마크 없이 원문 유지
    assert "Ashish Vaswani et al. Attention is all you need" in ko
    ref_idx = ko.index("Ashish Vaswani")
    refs_section = ko[ref_idx:]
    assert "[KO]" not in refs_section  # 참고문헌 영역엔 번역 마크 없음
    # 본문은 번역 마크가 있음
    assert "[KO]" in ko[:ref_idx]


def test_passthrough_on_structure_break(converted):
    # 항상 헤더를 지우는 가짜 번역 → 검증 실패 → 재시도도 실패 → 영어 통과
    def break_headings(system: str, user: str) -> str:
        return user.replace("# ", "").replace("#", "")

    summary = run_translate(converted, _fake(break_headings), "해라체")
    assert summary["passthrough"] >= 1
    ko = converted.ko_md.read_text(encoding="utf-8")
    assert "untranslated" in ko  # 통과 청크에 경고 주석

    # 실패 이유가 sid별로 채워진다 (UI에 "왜 실패했는지" 표시용)
    assert summary["failures"], "failures가 비어 있으면 안 됨"
    assert any("헤더" in reason for reason in summary["failures"].values())

    # 실패(passthrough)는 캐시에 저장하지 않는다 → 재실행 시 그 섹션만 다시 시도
    import json
    cached = json.loads(converted.cache_json.read_text(encoding="utf-8")).get("chunks", {})
    assert not any("untranslated" in v for v in cached.values())


def test_section_selection_limits_translation(converted):
    """번역 단계 Tree Select — 고른 섹션만 번역되고 나머지는 영어 원문 유지."""
    from md4paper.review import manifest as manifest_io
    from md4paper.ui.controller import UIController

    ctrl = UIController(converted)
    # Introduction 만 번역 대상으로
    ctrl.set_all_translate(False)
    intro = next(s for s in ctrl.translatable_sections() if "Introduction" in s.text)
    intro.translate = True
    manifest_io.save(ctrl.manifest, converted)

    def mark(system: str, user: str) -> str:
        return "[KO]\n" + _body(user)

    fake = FakeProvider(complete_fn=mark, parse_fn=lambda s, u, sc: FAKE_GLOSSARY, model="fake")
    run_translate(converted, fake, "해라체")
    ko = converted.ko_md.read_text(encoding="utf-8")

    # 선택한 섹션만 번역 마크가 붙는다
    assert ko.count("[KO]") >= 1
    method_idx = ko.find("2 Method")
    intro_idx = ko.find("1 Introduction")
    assert intro_idx >= 0 and method_idx > intro_idx
    assert "[KO]" not in ko[method_idx:]  # 선택 안 한 섹션은 원문


def test_section_tree_nesting(converted):
    from md4paper.ui.controller import UIController

    tree = UIController(converted).section_tree()
    assert tree, "트리가 비어 있음"
    # 최상위 노드는 h1, 하위는 children으로 중첩
    assert all("id" in n and "label" in n and "children" in n for n in tree)


def test_glossary_block_hides_korean_for_keep_and_labels_policies():
    from md4paper.ir import GlossaryEntry, GlossaryList
    from md4paper.translate.engine import _glossary_block

    gl = GlossaryList(entries=[
        GlossaryEntry(term="human-in-the-loop", korean="인간 개입", policy="keep"),
        GlossaryEntry(term="test set", korean="테스트 세트", policy="translate"),
        GlossaryEntry(term="prompt", korean="프롬프트", policy="병기-first-use"),
    ])
    block = _glossary_block(gl)
    assert "human-in-the-loop → 영어 원문 그대로" in block
    assert "인간 개입" not in block  # keep 용어의 한국어는 프롬프트에 노출하지 않음
    assert "test set → 테스트 세트 (이 번역어로)" in block
    assert "prompt → 프롬프트 (첫 등장 시" in block


def test_enforce_keep_reverts_translated_keep_terms():
    from md4paper.ir import GlossaryEntry, GlossaryList
    from md4paper.translate.postprocess import enforce_keep

    gloss = GlossaryList(entries=[
        GlossaryEntry(term="Data-Prompt Co-Evolution", korean="데이터-프롬프트 공진화", policy="keep"),
        GlossaryEntry(term="test set", korean="테스트 세트", policy="translate"),
    ])
    text = ("본 연구는 데이터-프롬프트 공진화 워크플로를 제안한다. "
            "데이터-프롬프트 공진화(Data-Prompt Co-Evolution)는 유용하며 테스트 세트도 쓴다.\n")
    out = enforce_keep(text, gloss)
    assert "데이터-프롬프트 공진화" not in out                 # keep 용어 모두 영어로 복원
    assert out.count("Data-Prompt Co-Evolution") == 2         # 단독+병기 둘 다
    assert "테스트 세트" in out                                # translate 용어는 그대로
