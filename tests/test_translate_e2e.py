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


def test_switching_model_retranslates_and_records_model(converted):
    """재번역 때 모델을 바꾸면 캐시를 쓰지 않고 다시 번역하고, status.json에 그 모델이 남는다."""
    from md4paper.ui.controller import UIController

    calls = {"n": 0}

    def count(system: str, user: str) -> str:
        calls["n"] += 1
        return _body(user)

    def prov(model: str) -> FakeProvider:
        return FakeProvider(complete_fn=count, parse_fn=lambda s, u, sc: FAKE_GLOSSARY, model=model)

    pipeline.run_translate(converted, prov("m-a"))
    first = calls["n"]
    assert first >= 1
    assert pipeline.last_translate_model(converted) == ("fake", "m-a")
    assert UIController(converted).translate_model() == "fake/m-a"  # UI 드롭다운 기본값

    pipeline.run_translate(converted, prov("m-a"))
    assert calls["n"] == first  # 같은 모델 → 전부 캐시

    pipeline.run_translate(converted, prov("m-b"))
    assert calls["n"] == 2 * first  # 다른 모델 → 전부 새로 번역
    st = converted.load_status()["translate"]
    assert (st["provider"], st["model"]) == ("fake", "m-b")


def test_translate_model_options_lists_keyed_providers(converted, monkeypatch):
    from md4paper import config
    from md4paper.ui.controller import UIController

    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    ctrl = UIController(converted)
    opts = ctrl.translate_model_options()
    assert all(k.startswith("anthropic/") for k in opts if k != ctrl.translate_model())
    assert f"anthropic/{config.MODEL_TIERS['anthropic'][-1]}" in opts
    assert ctrl.translate_model() in opts  # 기본값(키 없는 전역 기본이어도)은 항상 고를 수 있다


def test_stale_sections_track_source_changes(converted):
    """원문이 바뀐 섹션만 '낡음'으로 — 번역 직후엔 비고, 한 섹션 본문을 고치면 그 섹션(만)이 잡힌다."""
    import json

    from md4paper.review import manifest as manifest_io
    from md4paper.translate.apply import stale_section_ids

    m = manifest_io.load(converted)
    assert stale_section_ids(converted, m) == set()  # 번역 전 — 낡을 번역이 없다
    run_translate(converted, _fake(), "해라체")
    assert stale_section_ids(converted, m) == set()  # 방금 번역했으니 전부 캐시에 있다

    smap = sorted(json.loads(converted.sections_map.read_text(encoding="utf-8"))["sections"],
                  key=lambda e: e["out_line"])
    lines = converted.en_md.read_text(encoding="utf-8").splitlines()
    # 번역 대상인 본문 섹션 하나(참고문헌은 기본으로 번역하지 않아 '낡음'도 없다). 줄 수를 바꾸지 않고
    # 본문 한 줄만 고친다 — 다른 섹션의 라인 범위가 그대로여야 그 섹션들이 낡았다고 잡히지 않는다.
    flags = {sec.id: sec.translate for sec in m.sections}
    target = body = None
    for idx, e in enumerate(smap):  # 번역 대상이고 본문 줄이 하나라도 있는 첫 섹션
        if not flags.get(e["id"]) or "reference" in e["text"].lower():
            continue
        end = smap[idx + 1]["out_line"] if idx + 1 < len(smap) else len(lines)
        body = next((i for i in range(e["out_line"] + 1, end) if lines[i].strip()), None)
        if body is not None:
            target = e
            break
    assert target is not None
    lines[body] += " (edited after translation)"
    converted.en_md.write_text("\n".join(lines) + "\n", encoding="utf-8")

    stale = stale_section_ids(converted, m)
    assert target["id"] in stale
    assert len(stale) < len(smap)  # 다른 섹션까지 전부 낡았다고 하지는 않는다

    run_translate(converted, _fake(), "해라체")  # 다시 번역하면 그 유닛만 새로 돌고 낡음은 사라진다
    assert stale_section_ids(converted, m) == set()


def test_unticked_sections_keep_existing_translation(converted):
    """일부만 다시 번역하려고 나머지 체크를 풀어도, 이미 번역해 둔 섹션은 영어로 돌아가지 않는다."""
    from md4paper.review import manifest as manifest_io
    from md4paper.translate.apply import unticked_without_cache
    from md4paper.ui.controller import UIController

    def mark(system: str, user: str) -> str:  # 헤더가 아닌 본문 줄마다 표식 — 구조는 그대로
        return "\n".join(ln + " [KO]" if ln.strip() and not ln.startswith("#") else ln
                         for ln in _body(user).split("\n"))

    fake = FakeProvider(complete_fn=mark, parse_fn=lambda s, u, sc: FAKE_GLOSSARY, model="fake")
    run_translate(converted, fake, "해라체")
    ko_all = converted.ko_md.read_text(encoding="utf-8")
    n_all = ko_all.count("[KO]")
    assert n_all >= 2

    ctrl = UIController(converted)
    ctrl.set_all_translate(False)
    intro = next(s for s in ctrl.translatable_sections() if "Introduction" in s.text)
    intro.translate = True
    manifest_io.save(ctrl.manifest, converted)
    m = manifest_io.load(converted)
    assert unticked_without_cache(converted, m) == set()  # 보관된 번역이 있으니 잃을 섹션이 없다

    summary = run_translate(converted, fake, "해라체")
    ko_again = converted.ko_md.read_text(encoding="utf-8")
    assert ko_again.count("[KO]") == n_all  # 체크를 푼 섹션의 번역이 그대로 남았다
    assert summary["kept"] >= 1
    assert summary["ok"] == 0  # 전부 캐시(선택 섹션)와 보관분(나머지)으로 — 새 호출 없음

    # 캐시가 통째로 사라진 상태라면 → 풀린 섹션은 영어로 바뀔 것이고, 그 섹션들이 경고 대상에 잡힌다
    converted.cache_json.unlink()
    losing = unticked_without_cache(converted, m)
    assert losing and intro.id not in losing


# --- 컬렉션 용어집 · 유닛 단위 용어집 캐시 키(v3) -----------------------------------

def _filler(tag: str, n: int = 30) -> str:
    return " ".join(f"This {tag} paragraph sentence number {i} says plain things." for i in range(n))


@pytest.fixture
def long_paper(tmp_path, monkeypatch):
    """섹션마다 유닛 하나(병합 기준 1500자를 넘게) — 용어 하나가 바뀌면 그 섹션만 낡는지 보려고.

    Recurrent Part: recurrence·attention, Encoder Part: encoder, Plain Part: 용어 없음.
    컬렉션 용어집 폴더가 테스트마다 비도록 CONFIG_DIR을 tmp로(projects.json은 conftest가 비운다).
    """
    from md4paper import config, projects, workdir

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / "cfg")
    md = ("# A Long Study\n\n## Recurrent Part\n\nWe revisit recurrence and attention here. " + _filler("first")
          + "\n\n## Encoder Part\n\nThe encoder maps inputs. " + _filler("second")
          + "\n\n## Plain Part\n\n" + _filler("third") + "\n")
    src = tmp_path / "ws" / "long.md"
    src.parent.mkdir(parents=True)
    src.write_text(md, encoding="utf-8")
    wd = WorkDir(tmp_path / "ws" / "long.md4")
    pipeline.convert(src, wd)
    pid = projects.create("Coll")["id"]
    workdir.set_projects(wd.root, [pid])
    return wd, pid


def _sid(wd, text: str) -> str:
    import json

    return next(e["id"] for e in json.loads(wd.sections_map.read_text(encoding="utf-8"))["sections"]
                if e["text"] == text)


def _counting(model: str = "gpt-5.6-luna"):
    calls = {"n": 0}

    def count(system: str, user: str) -> str:
        calls["n"] += 1
        return _body(user)

    return calls, FakeProvider(complete_fn=count, parse_fn=lambda s, u, sc: FAKE_GLOSSARY, model=model)


def _entry(term: str, korean: str, policy: str = "translate") -> GlossaryEntry:
    return GlossaryEntry(term=term, korean=korean, policy=policy)


def test_collection_term_stales_only_units_that_use_it(long_paper):
    from md4paper.review import manifest as manifest_io
    from md4paper.translate import glossary
    from md4paper.translate.apply import stale_section_ids

    wd, pid = long_paper
    calls, fake = _counting()
    pipeline.run_translate(wd, fake)
    m = manifest_io.load(wd)
    assert stale_section_ids(wd, m) == set()
    first = calls["n"]
    assert first == 3

    # 본문에 안 나오는 용어 → 어느 유닛의 해시도 안 바뀐다
    glossary.save_collection(pid, GlossaryList(entries=[_entry("Transformer-XL", "트랜스포머-XL")]))
    assert stale_section_ids(wd, m) == set()

    # 'recurrence'(Recurrent Part에만 나옴) — 그 유닛만 낡는다. 병합된 유닛이라 제목 섹션도 함께.
    glossary.save_collection(pid, GlossaryList(entries=[_entry("Recurrence", "순환")]))
    stale = stale_section_ids(wd, m)
    assert _sid(wd, "Recurrent Part") in stale
    assert _sid(wd, "Encoder Part") not in stale and _sid(wd, "Plain Part") not in stale

    summary = pipeline.run_translate(wd, fake)
    assert calls["n"] == first + 1  # 그 유닛만 LLM을 다시 부른다
    assert summary["cached"] == 2 and summary["glossary"] == 3  # effective = 논문 2 + 컬렉션 1
    assert stale_section_ids(wd, m) == set()

    # 논문 용어집과 같은 번역어로 덮어쓰면 프롬프트 줄이 같아 아무것도 낡지 않는다
    glossary.save_collection(pid, GlossaryList(entries=[_entry("Recurrence", "순환"),
                                                        _entry("ENCODER", "인코더")]))
    assert stale_section_ids(wd, m) == set()
    # 번역어를 바꾸면 encoder가 나오는 유닛만
    glossary.save_collection(pid, GlossaryList(entries=[_entry("Recurrence", "순환"),
                                                        _entry("ENCODER", "부호기")]))
    assert stale_section_ids(wd, m) == {_sid(wd, "Encoder Part")}


def _plain_chunks(wd) -> dict[str, str]:
    """지금 v3 캐시의 항목을 옛(v2·v1) 모양 — 평문 `hash_text(원문)` 해시 — 으로."""
    import json

    from md4paper.review import manifest as manifest_io
    from md4paper.translate import apply, glossary
    from md4paper.workdir import hash_text

    m = manifest_io.load(wd)
    stored = json.loads(wd.cache_json.read_text(encoding="utf-8"))
    eff = glossary.effective(wd)[0]
    plain: dict[str, str] = {}
    for u in apply.build_units(wd, m, wd.en_md.read_text(encoding="utf-8").splitlines()):
        for text in [u["source"], *(t for _s, t in (u.get("sections") or []))]:
            hit = stored["chunks"].get(apply.unit_hash(text, eff))
            if hit is not None:
                plain[hash_text(text)] = hit
    return plain


def _plant_v2_cache(wd) -> None:
    """지금 v3 캐시를 옛 v2 모양으로 — 논문 용어집으로 만든 v2 키 + 평문 해시 항목."""
    import json

    from md4paper.review import manifest as manifest_io
    from md4paper.translate import apply, glossary

    m = manifest_io.load(wd)
    _v3, v2 = apply._cache_keys(m.korean_style, glossary.load(wd), "gpt-5.6-luna", m)
    wd.cache_json.write_text(json.dumps({"key": v2, "chunks": _plain_chunks(wd)}, ensure_ascii=False),
                             encoding="utf-8")


def _plant_v1_cache(wd, model: str = "gpt-5.6-luna") -> None:
    """지금 v3 캐시를 더 옛 v1 모양으로 — v2 이전 코드처럼 키 = 시스템 프롬프트 전체 + 꼬리의 해시.

    apply의 키 함수를 거치지 않고 옛 코드(`main`의 run_translate)와 같은 식으로 직접 만든다 —
    apply가 v1 키를 다시 만드는 쪽이 옛 코드와 어긋나면 이 테스트들이 잡는다.
    """
    import json

    from md4paper.review import manifest as manifest_io
    from md4paper.translate import apply, context, engine, glossary
    from md4paper.workdir import hash_text

    m = manifest_io.load(wd)
    title, abstract = context.extract_context(wd, m)
    outline = apply._build_outline(json.loads(wd.sections_map.read_text(encoding="utf-8"))["sections"])
    prompt = engine.build_system_prompt(m.korean_style, glossary.load(wd), title, abstract, outline)
    tail = f"{model}|headers={m.translate_headers}|refs={m.translate_references}"
    wd.cache_json.write_text(
        json.dumps({"key": hash_text(f"{prompt}|{tail}"), "chunks": _plain_chunks(wd)}, ensure_ascii=False),
        encoding="utf-8")


def test_v2_cache_is_read_and_upgraded_when_paper_glossary_unchanged(long_paper):
    import json

    from md4paper.review import manifest as manifest_io
    from md4paper.translate import apply, glossary

    wd, _pid = long_paper
    calls, fake = _counting()
    pipeline.run_translate(wd, fake)
    _plant_v2_cache(wd)
    m = manifest_io.load(wd)
    assert apply.stale_section_ids(wd, m) == set()  # 옛 키가 논문 용어집과 맞으니 전부 보관됨

    before = calls["n"]
    summary = pipeline.run_translate(wd, fake)
    assert calls["n"] == before and summary["cached"] == summary["chunks"] == 3
    stored = json.loads(wd.cache_json.read_text(encoding="utf-8"))
    v3, _v2 = apply._cache_keys(m.korean_style, glossary.load(wd), "gpt-5.6-luna", m)
    assert stored["key"] == v3 and stored["scheme"] == 3
    eff = glossary.effective(wd)[0]
    units = apply.build_units(wd, m, wd.en_md.read_text(encoding="utf-8").splitlines())
    current = set().union(*(apply.unit_hashes(u, eff) for u in units))
    assert set(stored["chunks"]) <= current  # 평문 해시로 남은 옛 항목 없음 — 전부 새 해시로 다시 적혔다
    assert apply.stale_section_ids(wd, m) == set()


def test_v2_cache_rejects_only_units_whose_terms_the_collection_overrides(long_paper):
    from md4paper.review import manifest as manifest_io
    from md4paper.translate import apply, glossary

    wd, pid = long_paper
    calls, fake = _counting()
    pipeline.run_translate(wd, fake)
    _plant_v2_cache(wd)
    glossary.save_collection(pid, GlossaryList(entries=[_entry("encoder", "부호기")]))
    m = manifest_io.load(wd)
    assert apply.stale_section_ids(wd, m) == {_sid(wd, "Encoder Part")}

    before = calls["n"]
    summary = pipeline.run_translate(wd, fake)
    assert calls["n"] == before + 1 and summary["cached"] == 2
    assert apply.stale_section_ids(wd, m) == set()


def test_v2_cache_with_edited_paper_glossary_is_not_reused(long_paper):
    """옛 키가 지금 논문 용어집과 안 맞으면(그 뒤 논문 용어집을 고침) 예전처럼 전부 다시 번역 — 낡음은 빈 집합.

    `glossary.save`는 저장 전에 캐시를 v3로 옮기므로, 여기서는 그 길을 거치지 않은 편집(파일을 직접
    고침)을 `glossary.dump`로 흉내 낸다.
    """
    from md4paper.review import manifest as manifest_io
    from md4paper.translate import apply, glossary

    wd, _pid = long_paper
    calls, fake = _counting()
    pipeline.run_translate(wd, fake)
    _plant_v2_cache(wd)
    glossary.dump(GlossaryList(entries=[*glossary.load(wd).entries, _entry("plain", "평범")]),
                  wd.glossary_yaml)
    assert apply.stale_section_ids(wd, manifest_io.load(wd)) == set()
    before = calls["n"]
    pipeline.run_translate(wd, fake)
    assert calls["n"] == before + 3


def test_retranslate_units_counts_full_retranslation_when_cache_is_unusable(long_paper):
    """캐시를 통째로 못 쓰면 stale_units는 빈 목록('원문 바뀜' 아님)이지만 retranslate_units는 전부·full."""
    from md4paper.review import manifest as manifest_io
    from md4paper.translate import apply, glossary

    wd, _pid = long_paper
    m = manifest_io.load(wd)
    assert apply.retranslate_units(wd, m) == ([], False)  # (d) 번역 없음 — 재번역이 아니다
    calls, fake = _counting()
    pipeline.run_translate(wd, fake)
    assert apply.retranslate_units(wd, m) == ([], False)  # (a) 번역 직후
    every = [u for u in apply.build_units(wd, m, wd.en_md.read_text(encoding="utf-8").splitlines())
             if u["translatable"] and u["sids"]]
    assert len(every) == 3

    # (b) 옛 v2 캐시 + 그 뒤 논문 용어집 변경(save를 거치지 않아 이행 없음) → 키 불일치
    #     (status.json에 지난 모델 기록이 있다)
    _plant_v2_cache(wd)
    glossary.dump(GlossaryList(entries=[*glossary.load(wd).entries, _entry("plain", "평범")]),
                  wd.glossary_yaml)
    assert apply.stale_units(wd, m) == []  # '원문 바뀜'은 그대로 — 원문이 바뀐 게 아니다
    units, full = apply.retranslate_units(wd, m)
    assert full and [u["sids"] for u in units] == [u["sids"] for u in every]
    before = calls["n"]
    pipeline.run_translate(wd, fake)
    assert calls["n"] == before + len(units)  # 실제로도 전부 다시 돈다
    assert apply.retranslate_units(wd, m) == ([], False)

    # (c) cache.json이 없으면 전부
    wd.cache_json.unlink()
    units, full = apply.retranslate_units(wd, m)
    assert full and len(units) == 3
    assert apply.stale_units(wd, m) == []

    # (d) ko.md가 없으면 '번역 없음' — 캐시가 없어도 재번역으로 세지 않는다
    wd.ko_md.unlink()
    assert apply.retranslate_units(wd, m) == ([], False)


def test_v1_cache_is_read_and_upgraded_when_paper_glossary_unchanged(long_paper):
    """시스템 프롬프트 해시 키(v1) 캐시 — 논문 용어집이 그대로면 전부 적중하고 저장 뒤 v3가 된다."""
    import json

    from md4paper.review import manifest as manifest_io
    from md4paper.translate import apply

    wd, _pid = long_paper
    calls, fake = _counting()
    pipeline.run_translate(wd, fake)
    _plant_v1_cache(wd)
    m = manifest_io.load(wd)
    assert apply.stale_section_ids(wd, m) == set()
    assert apply.retranslate_units(wd, m) == ([], False)

    before = calls["n"]
    summary = pipeline.run_translate(wd, fake)
    assert calls["n"] == before and summary["cached"] == summary["chunks"] == 3
    stored = json.loads(wd.cache_json.read_text(encoding="utf-8"))
    assert stored["scheme"] == 3
    assert apply.stale_section_ids(wd, m) == set()
    assert apply.retranslate_units(wd, m) == ([], False)


def test_v1_cache_rejects_only_units_whose_terms_the_collection_overrides(long_paper):
    from md4paper.review import manifest as manifest_io
    from md4paper.translate import apply, glossary

    wd, pid = long_paper
    calls, fake = _counting()
    pipeline.run_translate(wd, fake)
    _plant_v1_cache(wd)
    glossary.save_collection(pid, GlossaryList(entries=[_entry("encoder", "부호기")]))
    m = manifest_io.load(wd)
    assert apply.stale_section_ids(wd, m) == {_sid(wd, "Encoder Part")}
    units, full = apply.retranslate_units(wd, m)
    assert not full and [u["sids"][-1] for u in units] == [_sid(wd, "Encoder Part")]

    before = calls["n"]
    summary = pipeline.run_translate(wd, fake)
    assert calls["n"] == before + 1 and summary["cached"] == 2
    assert apply.stale_section_ids(wd, m) == set()


def test_stored_v1_cache_is_checked_against_the_recorded_model(long_paper):
    """모델 기록이 있으면 v1 키도 그 모델로 맞춰 본다 — 맞으면 옛 모드, 다른 모델의 캐시면 못 씀(full)."""
    from md4paper.review import manifest as manifest_io
    from md4paper.translate import apply

    wd, _pid = long_paper
    _calls, fake = _counting()
    pipeline.run_translate(wd, fake)
    assert wd.load_status()["translate"]["model"] == "gpt-5.6-luna"
    m = manifest_io.load(wd)

    _plant_v1_cache(wd)
    cache = apply._stored_cache(wd, m, None)
    assert cache is not None and cache.legacy is not None
    assert apply.retranslate_units(wd, m) == ([], False)

    _plant_v1_cache(wd, model="gpt-6-luna")  # 기록(5.6-luna)과 다른 모델로 만든 옛 캐시
    assert apply._stored_cache(wd, m, None) is None
    units, full = apply.retranslate_units(wd, m)
    assert full and len(units) == 3


@pytest.mark.parametrize("plant", [_plant_v1_cache, _plant_v2_cache])
def test_saving_paper_glossary_migrates_old_cache_first(long_paper, plant):
    """논문 용어집을 고쳐 저장하면 먼저 캐시가 v3로 옮겨져, 고친 용어가 나오는 유닛만 다시 번역된다."""
    import json

    from md4paper.review import manifest as manifest_io
    from md4paper.translate import apply, glossary

    wd, _pid = long_paper
    calls, fake = _counting()
    pipeline.run_translate(wd, fake)
    plant(wd)
    m = manifest_io.load(wd)
    # 'inputs'는 Encoder Part에만 나온다
    glossary.save(GlossaryList(entries=[*glossary.load(wd).entries, _entry("inputs", "입력")]), wd)
    stored = json.loads(wd.cache_json.read_text(encoding="utf-8"))
    assert stored["scheme"] == 3
    assert stored["key"] == apply._cache_keys(m.korean_style, glossary.load(wd), "gpt-5.6-luna", m)[0]
    units, full = apply.retranslate_units(wd, m)
    assert not full and [u["sids"][-1] for u in units] == [_sid(wd, "Encoder Part")]

    before = calls["n"]
    summary = pipeline.run_translate(wd, fake)
    assert calls["n"] == before + 1 and summary["cached"] == 2
    assert apply.migrate_cache(wd) is False  # 이미 v3 — 할 일 없음


def test_migrate_cache_without_model_record_uses_default_model(long_paper):
    """모델 기록도 input_hash도 없는 옛 번역은 뷰어 기본값과 같은 규칙(전역 기본 모델·manifest 문체)으로."""
    import json

    from md4paper import config
    from md4paper.review import manifest as manifest_io
    from md4paper.translate import apply, glossary

    wd, _pid = long_paper
    _calls, fake = _counting()
    pipeline.run_translate(wd, fake)
    model = config.resolve_model(config.resolve_provider())
    _plant_v1_cache(wd, model=model)
    status = wd.load_status()
    status["translate"] = {k: v for k, v in status["translate"].items()
                           if k not in ("model", "provider", "input_hash")}
    wd.save_status(status)
    m = manifest_io.load(wd)
    assert pipeline.recover_translate_model(wd) is None
    assert apply.migrate_cache(wd) is True
    stored = json.loads(wd.cache_json.read_text(encoding="utf-8"))
    assert stored["key"] == apply._cache_keys(m.korean_style, glossary.load(wd), model, m)[0]
    assert apply.retranslate_units(wd, m) == ([], False)

    wd.cache_json.unlink()
    assert apply.migrate_cache(wd) is False  # 캐시 없음


def _forget_model_record(wd) -> None:
    """모델 기록 전의 번역처럼 — status.json translate에서 provider·model만 지우고 input_hash는 남긴다."""
    status = wd.load_status()
    status["translate"] = {k: v for k, v in status["translate"].items() if k not in ("model", "provider")}
    wd.save_status(status)


def test_model_is_recovered_from_input_hash_without_record(long_paper):
    """모델 기록이 없어도 input_hash(`문체:모델`)로 되찾아 옛 키를 맞춰 본다 — 판정·이행·기본 모델이 같은 모델로."""
    import json

    from md4paper.review import manifest as manifest_io
    from md4paper.translate import apply, glossary
    from md4paper.ui import translate_job
    from md4paper.ui.controller import UIController

    wd, pid = long_paper
    _calls, fake = _counting()
    pipeline.run_translate(wd, fake)
    _forget_model_record(wd)
    m = manifest_io.load(wd)
    assert pipeline.last_translate_model(wd) is None  # 명시 기록은 그대로 '없음'
    assert pipeline.recover_translate_model(wd, m) == ("openai", "gpt-5.6-luna")
    assert pipeline.recover_translate(wd) == ("openai", "gpt-5.6-luna", m.korean_style)
    assert translate_job.default_model(wd) == "openai/gpt-5.6-luna"
    assert UIController(wd).translate_model() == "openai/gpt-5.6-luna"

    _plant_v1_cache(wd)  # 5.6-luna로 만든 v1 — 되찾은 모델과 맞는다
    cache = apply._stored_cache(wd, m, None)
    assert cache is not None and cache.legacy is not None
    assert apply.retranslate_units(wd, m) == ([], False)

    _plant_v1_cache(wd, model="gpt-6-luna")  # 되찾은 모델과 다른 모델의 캐시 → 못 씀
    assert apply._stored_cache(wd, m, None) is None
    units, full = apply.retranslate_units(wd, m)
    assert full and len(units) == 3
    assert apply.migrate_cache(wd) is False

    _plant_v1_cache(wd)
    assert apply.migrate_cache(wd) is True
    stored = json.loads(wd.cache_json.read_text(encoding="utf-8"))
    assert stored["key"] == apply._cache_keys(m.korean_style, glossary.load(wd), "gpt-5.6-luna", m)[0]
    glossary.save_collection(pid, GlossaryList(entries=[_entry("recurrence", "순환")]))
    assert [r["model"] for r in glossary.impact(pid, wd.root.parent)] == ["gpt-5.6-luna"]


def test_recover_translate_model_misses_unknown_combinations(long_paper):
    wd, _pid = long_paper
    assert pipeline.recover_translate_model(wd) is None  # 번역 기록 없음
    status = wd.load_status()
    status["translate"] = {"input_hash": "0000000000000000", "done": True, "style": "해라체"}
    wd.save_status(status)
    assert pipeline.recover_translate_model(wd) is None  # 목록 밖 모델·custom 문체 — 못 찾는다


def test_non_primary_member_is_left_out_of_impact(long_paper):
    """둘째 자리로만 속한 컬렉션의 용어집은 적용되지 않으니, 그 컬렉션의 영향 미리보기에도 안 든다."""
    from md4paper import projects, workdir
    from md4paper.translate import glossary

    wd, pid = long_paper
    ws = wd.root.parent
    _calls, fake = _counting()
    pipeline.run_translate(wd, fake)
    first = projects.create("Primary")["id"]
    workdir.set_projects(wd.root, [first, pid])
    glossary.save_collection(pid, GlossaryList(entries=[_entry("recurrence", "순환")]))
    assert glossary.members(pid, ws) == [] and glossary.impact(pid, ws) == []
    glossary.save_collection(first, GlossaryList(entries=[_entry("recurrence", "순환")]))
    assert [r["root"] for r in glossary.impact(first, ws)] == [wd.root]


def test_relevant_lines_match_at_word_start_and_are_sorted():
    from md4paper.translate.apply import relevant_lines, unit_hash

    edge = GlossaryList(entries=[_entry("edge", "엣지")])
    assert relevant_lines(edge, "Prior knowledge matters.") == ""  # knowledge 안의 edge는 아니다
    assert relevant_lines(edge, "An edge-case.") != ""
    ai = GlossaryList(entries=[_entry("AI", "인공지능")])
    assert relevant_lines(ai, "As she said.") == "" and relevant_lines(ai, "(AI) tools") != ""
    model = GlossaryList(entries=[_entry("model", "모델")])
    assert relevant_lines(model, "Two language models.") != ""  # 뒤 경계는 안 본다 — 복수형도 잡힘

    text = "The encoder and the decoder of the model."
    a = GlossaryList(entries=[_entry("encoder", "인코더"), _entry("decoder", "디코더"), _entry("model", "모델")])
    b = GlossaryList(entries=list(reversed(a.entries)))
    assert relevant_lines(a, text) == relevant_lines(b, text)
    assert unit_hash(text, a) == unit_hash(text, b)


def test_unit_hash_is_plain_without_relevant_terms():
    from md4paper.translate.apply import relevant_lines, unit_hash
    from md4paper.workdir import hash_text

    gl = GlossaryList(entries=[_entry("Living Test Set", "살아 있는 테스트 세트"), _entry("encoder", "인코더")])
    plain = "Nothing relevant in this text."
    assert unit_hash(plain, gl) == hash_text(plain)
    assert unit_hash(plain, GlossaryList(entries=[])) == hash_text(plain)
    hit = "We grow a living\ntest   set over time."  # 대소문자·줄바꿈·공백이 달라도 나온 것으로
    assert relevant_lines(gl, hit) == "- living test set → 살아 있는 테스트 세트 (이 번역어로)"
    assert unit_hash(hit, gl) != hash_text(hit)
    # 원어 표기(대소문자)만 다른 같은 항목은 같은 해시 — 프롬프트가 대소문자를 무시하라고 지시한다
    same = GlossaryList(entries=[_entry("LIVING TEST SET", "살아 있는 테스트 세트")])
    assert unit_hash(hit, same) == unit_hash(hit, GlossaryList(entries=gl.entries[:1]))


def test_impact_reports_stale_papers_and_rough_cost(long_paper):
    from md4paper.translate import glossary

    wd, pid = long_paper
    ws = wd.root.parent
    _calls, fake = _counting()
    pipeline.run_translate(wd, fake)
    assert glossary.impact(pid, ws) == []  # 낡은 게 없으면 빈 목록

    glossary.save_collection(pid, GlossaryList(entries=[_entry("recurrence", "순환")]))
    rows = glossary.impact(pid, ws)
    assert len(rows) == 1
    row = rows[0]
    assert row["root"] == wd.root and row["model"] == "gpt-5.6-luna"  # 지난 번역 모델
    assert row["stale"][-1] == _sid(wd, "Recurrent Part") and row["units"] == 1
    assert 1500 < row["chars"] < 2500
    inp = int(row["chars"] / 3.5)
    assert row["est_cost_usd"] == pytest.approx(inp / 1e6 * 0.2 + int(inp * 1.3) / 1e6 * 1.2)
    assert row["title"]

    other = glossary.impact(pid, ws, model_for=lambda _wd: "claude-sonnet-5")
    assert other[0]["model"] == "claude-sonnet-5" and other[0]["est_cost_usd"] > row["est_cost_usd"]
    assert glossary.impact("p-없는-컬렉션", ws) == []


def test_impact_marks_full_retranslation_when_cache_is_gone(long_paper):
    from md4paper.translate import glossary

    wd, pid = long_paper
    ws = wd.root.parent
    _calls, fake = _counting()
    pipeline.run_translate(wd, fake)
    glossary.save_collection(pid, GlossaryList(entries=[_entry("recurrence", "순환")]))
    assert glossary.impact(pid, ws)[0]["full"] is False  # 낡은 유닛 하나 — 캐시는 쓴다

    wd.cache_json.unlink()
    rows = glossary.impact(pid, ws)
    assert len(rows) == 1
    row = rows[0]
    assert row["full"] is True and row["units"] == 3
    assert {_sid(wd, t) for t in ("Recurrent Part", "Encoder Part", "Plain Part")} <= set(row["stale"])
    assert row["chars"] > 3 * 1500  # 유닛 전체 원문


def test_v1_legacy_key_is_golden(tmp_path, monkeypatch):
    """고정 입력의 옛 키(v1·v2)를 리터럴로 박는다.

    engine.build_system_prompt 문구를 바꾸면 깨진다 — v1 캐시를 가진 사용자가 전부 재번역되므로 바꾸지
    말 것(바꾸려면 옛 프롬프트 문자열을 별도로 보존해 키를 만들 것). v2 키도 같은 이유로
    engine._glossary_block(glossary_line) 형식을 고정한다. `_plant_v1_cache`는 지금 코드로 키를 만들어
    이 변화를 못 잡으므로 이 테스트가 따로 있다.
    """
    import json

    from md4paper.ir import Manifest
    from md4paper.translate import apply, context

    wd = WorkDir(tmp_path / "g.md4")
    wd.out.mkdir(parents=True)
    wd.sections_map.write_text(json.dumps({"sections": [
        {"id": "s1", "text": "Abstract", "level": 2, "out_line": 2},
        {"id": "s2", "text": "1 Introduction", "level": 2, "out_line": 6},
        {"id": "s3", "text": "1.1 Background", "level": 3, "out_line": 9}]}), encoding="utf-8")
    monkeypatch.setattr(context, "title_abstract",
                        lambda wd, m: ("A Golden Paper", "We study golden keys for caches."))
    m = Manifest(title="A Golden Paper", korean_style="해라체", translate_headers=True,
                 translate_references=False)
    gl = GlossaryList(entries=[_entry("attention", "어텐션", "transliterate"), _entry("encoder", "인코더"),
                               _entry("Transformer", "", "keep")])
    v2 = apply._cache_keys("해라체", gl, "gpt-6-luna", m)[1]
    assert v2 == "3370b794e7f0afaa"
    assert apply._legacy_keys(wd, m, "해라체", gl, "gpt-6-luna") - {v2} == {"7f6f1e33874abf43"}


def test_stored_cache_without_any_record_checks_key_with_default_model(long_paper):
    """모델 기록도 input_hash도 없으면 전역 기본 모델·manifest 문체로 옛 키를 맞춰 본다 — 키를 안 보고
    '쓸 수 있음'이라 하면, 용어집을 save 밖에서 고쳤을 때 표시(몇 유닛)와 실행(전부)이 어긋난다."""
    from md4paper import config
    from md4paper.review import manifest as manifest_io
    from md4paper.translate import apply, glossary

    wd, _pid = long_paper
    _calls, fake = _counting()
    pipeline.run_translate(wd, fake)
    _plant_v1_cache(wd, model=config.resolve_model(config.resolve_provider()))
    status = wd.load_status()
    status.pop("translate")
    wd.save_status(status)
    m = manifest_io.load(wd)
    cache = apply._stored_cache(wd, m, None)
    assert cache is not None and cache.legacy is not None
    assert apply.retranslate_units(wd, m) == ([], False)

    # 논문 용어집을 save 밖에서(직접 편집) 고치면 옛 키가 안 맞는다 → 못 씀, 전부 다시
    glossary.dump(GlossaryList(entries=[*glossary.load(wd).entries, _entry("inputs", "입력")]),
                  wd.glossary_yaml)
    assert apply._stored_cache(wd, m, None) is None
    units, full = apply.retranslate_units(wd, m)
    assert full and len(units) == 3


def test_migrate_cache_write_failure_keeps_original(long_paper, monkeypatch, caplog):
    """이행 쓰기가 실패해도 원본 cache.json은 그대로, 임시 파일은 안 남고, 용어집 저장은 계속된다."""
    import logging

    from md4paper.translate import apply, glossary

    wd, _pid = long_paper
    _calls, fake = _counting()
    pipeline.run_translate(wd, fake)
    _plant_v1_cache(wd)
    before = wd.cache_json.read_bytes()

    def boom(src, dst):  # noqa: ANN001
        raise OSError("디스크 가득")

    monkeypatch.setattr(apply.os, "replace", boom)
    with pytest.raises(OSError):
        apply.migrate_cache(wd)
    assert wd.cache_json.read_bytes() == before
    assert not wd.cache_json.with_name(wd.cache_json.name + ".tmp").exists()

    new = GlossaryList(entries=[*glossary.load(wd).entries, _entry("inputs", "입력")])
    with caplog.at_level(logging.WARNING, logger="md4paper.translate.glossary"):
        glossary.save(new, wd)
    assert "캐시 이행 실패" in caplog.text
    assert [e.term for e in glossary.load(wd).entries][-1] == "inputs"  # 저장은 됐다
    assert wd.cache_json.read_bytes() == before
