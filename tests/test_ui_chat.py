"""뷰어 RAG 챗봇 — 청크·검색·답변 조립·기록 + /chat 라우트."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("nicegui", reason="웹 UI 의존성(ui extra) 미설치")

from md4paper.llm.base import FakeProvider  # noqa: E402
from md4paper.ui import chat  # noqa: E402
from md4paper.workdir import WorkDir  # noqa: E402

EN = """# Bespoke Bots

We study how instructors customize chatbots.

## 2 Method

We interviewed 18 instructors at three universities.

Interviews lasted 45 minutes each.

## 3 Results

Instructors wanted control over the tone of generated answers.
"""

KO = """# 맞춤형 봇

우리는 교수자가 챗봇을 어떻게 맞춤화하는지 연구한다.

## 2 방법

세 개 대학의 교수자 18명을 인터뷰했다.

인터뷰는 각각 45분씩 진행했다.

## 3 결과

교수자들은 생성된 답변의 어투를 통제하고 싶어 했다.
"""


def _wd(tmp_path, en: str = EN, ko: str | None = KO) -> WorkDir:
    wd = WorkDir(tmp_path / "p.md4")
    wd.ensure()
    wd.en_md.write_text(en, encoding="utf-8")
    if ko is not None:
        wd.ko_md.write_text(ko, encoding="utf-8")
    return wd


def _item(aid: str, row: int, quote: str, note: str = "", side: str = "en",
          color: str = "yellow") -> dict:
    """annotations.json 항목 하나 (v2 — 앵커 목록 구조)."""
    return {"id": aid, "color": color, "note": note,
            "anchors": [{"side": side, "row": row, "start": 0, "end": len(quote),
                         "quote": quote, "prefix": "", "suffix": ""}]}


# --- 청크 ---------------------------------------------------------------


def test_build_chunks_matches_align_rows():
    from md4paper.ui.app import align_rows

    rows = align_rows(EN, KO)
    chunks = chat.build_chunks(EN, KO)
    assert rows is not None
    assert len(chunks) == len(rows)
    assert [c.row for c in chunks] == list(range(len(rows)))
    for c, (enb, kob, _h) in zip(chunks, rows):
        assert c.en == enb and c.ko == kob
    # 헤더는 그 섹션의 뒤따르는 문단 행에도 이어진다 (가장 가까운 앞선 헤더)
    method = [c for c in chunks if "interviewed 18" in c.en]
    assert method and method[0].heading == "2 Method"
    assert "18명" in (method[0].ko or "")


def test_build_chunks_falls_back_without_translation():
    from md4paper.ui.app import _split_sections

    chunks = chat.build_chunks(EN, None)
    assert len(chunks) == len(_split_sections(EN))
    assert all(c.ko is None for c in chunks)


def test_build_chunks_row_count_unchanged_by_served_markdown():
    """이미지 경로 치환은 줄 경계를 바꾸지 않는다 — 행 인덱스가 뷰어와 같아야 한다."""
    from md4paper.ui.app import served_markdown

    en = EN + "\n![그림 1](images/fig1.png)\n\n다음 문단.\n"
    ko = KO + "\n![그림 1](images/fig1.png)\n\n다음 문단.\n"
    raw = chat.build_chunks(en, ko)
    served = chat.build_chunks(served_markdown(en, "tok123"), served_markdown(ko, "tok123"))
    assert len(raw) == len(served)
    assert [c.heading for c in raw] == [c.heading for c in served]


def test_heading_only_rows_are_not_searchable():
    chunks = chat.build_chunks("# T\n\n## Empty\n\n## Body\n\nreal text\n", None)
    by_head = {c.heading: c for c in chunks}
    assert by_head["Empty"].searchable is False
    assert by_head["Body"].searchable is True


# --- 독자 메모 붙이기 ---------------------------------------------------


def test_attach_notes_by_row():
    chunks = chat.attach_notes(chat.build_chunks(EN, KO),
                               [_item("a1", 3, "We interviewed 18 instructors", "표본이 작다")])
    by_row = {c.row: c for c in chunks}
    assert [n["id"] for n in by_row[3].notes] == ["a1"]
    assert by_row[3].notes[0]["note"] == "표본이 작다"
    assert all(not c.notes for c in chunks if c.row != 3)


def test_attach_notes_recovers_shifted_row_by_quote():
    """재조립으로 행이 밀렸으면 인용문으로 다시 찾는다 (사용자가 쓴 글을 놓치지 않게)."""
    chunks = chat.attach_notes(chat.build_chunks(EN, KO),
                               [_item("a2", 99, "Interviews lasted 45 minutes each.", "짧다")])
    hit = [c for c in chunks if c.notes]
    assert [c.row for c in hit] == [4]
    assert hit[0].notes[0]["row"] == 4      # 되찾은 행 번호로 고쳐 붙인다


def test_attach_notes_drops_unfindable_and_broken_items():
    chunks = chat.attach_notes(chat.build_chunks(EN, KO), [
        _item("a3", 99, "이 논문에 없는 문장입니다"),
        {"id": "a4", "anchors": []},        # 앵커가 없으면 위치가 없다
        "망가진 항목",
    ])
    assert all(not c.notes for c in chunks)


def test_attach_notes_makes_heading_row_searchable():
    """헤더만 있는 행도 메모가 붙으면 검색 대상이 된다 — 메모가 곧 내용이다."""
    chunks = chat.attach_notes(chat.build_chunks(EN, KO),
                               [_item("a5", 5, "## 3 Results", "여기부터 다시 읽기")])
    by_row = {c.row: c for c in chunks}
    assert by_row[5].searchable is True
    assert chat.doc_text(by_row[5]).count("여기부터") == 1
    assert "여기부터" not in chat.doc_text(by_row[5], include_notes=False)


def test_attach_notes_marks_both_sides():
    item = _item("a6", 1, "We study how instructors customize chatbots.", "핵심")
    item["anchors"].append({"side": "ko", "row": 1, "start": 0, "end": 5,
                            "quote": "우리는 교수자가 챗봇을 어떻게 맞춤화하는지 연구한다.",
                            "prefix": "", "suffix": ""})
    chunks = chat.attach_notes(chat.build_chunks(EN, KO), [item])
    note = next(c for c in chunks if c.notes).notes[0]
    assert note["side"] == "both" and note["quote"].startswith("We study")


def test_chunks_of_reads_annotations_file(tmp_path):
    from md4paper.ui import annotations

    wd = _wd(tmp_path)
    annotations.save(wd, [_item("a7", 4, "Interviews lasted 45 minutes each.", "확인 필요")])
    assert [c.row for c in chat.chunks_of(wd) if c.notes] == [4]
    assert not any(c.notes for c in chat.chunks_of(wd, include_notes=False))


# --- 메모가 걸린 검색 ---------------------------------------------------


def test_search_finds_row_by_note_text_only():
    """질문이 메모에만 있는 말로 이뤄져도 그 행이 1위여야 한다."""
    chunks = chat.attach_notes(chat.build_chunks(EN, KO),
                               [_item("a8", 6, "Instructors wanted control", "어투 통제는 후속 연구 거리")])
    rows = chat.retrieve(chunks, "후속 연구 거리라고 적어 둔 데가 어디야?")
    assert rows[0] == 6
    assert chat.retrieve(chunks, "후속 연구 거리라고 적어 둔 데가 어디야?",
                         include_notes=False)[0] != 6


def test_note_keyword_question_puts_noted_rows_first():
    chunks = chat.attach_notes(chat.build_chunks(EN, KO),
                               [_item("a9", 6, "Instructors wanted control", "여기 중요")])
    rows = chat.retrieve(chunks, "내가 메모한 내용 정리해줘")
    assert rows[0] == 6
    assert chat.wants_notes("하이라이트 정리") and not chat.wants_notes("참가자 수는?")


# --- 토크나이저 ---------------------------------------------------------


def test_tokenize_english_words_and_korean_bigrams():
    toks = chat.tokenize("**Interviewed** 18 instructors of the study")
    assert "the" not in toks and "of" not in toks  # 흔한 기능어는 검색 신호가 아니다
    assert "interview" in toks and "18" in toks and "instructor" in toks
    assert not any("*" in t for t in toks)
    ko = chat.tokenize("설문조사")
    assert ko == ["설문", "문조", "조사"]
    assert chat.tokenize("가") == ["가"]


def test_tokenize_drops_markdown_and_latex_symbols():
    toks = chat.tokenize(r"$\alpha$ [link](http://x.y) `code`")
    assert "alpha" not in toks  # \alpha 는 LaTeX 명령이라 제거
    assert "code" in toks and "link" in toks


# --- BM25 ---------------------------------------------------------------


def test_bm25_ranks_relevant_chunk_first():
    chunks = chat.build_chunks(EN, KO)
    idx = chat.BM25Index(chunks)
    (row, score), *_ = idx.search(chat.tokenize("how many instructors were interviewed?"))
    assert score > 0
    assert "interviewed 18" in next(c for c in chunks if c.row == row).en


def test_bm25_matches_korean_question_against_translation():
    chunks = chat.build_chunks(EN, KO)
    hits = chat.BM25Index(chunks).search(chat.tokenize("인터뷰는 몇 분 진행했나?"))
    top = next(c for c in chunks if c.row == hits[0][0])
    assert "45" in top.en


def test_retrieve_falls_back_to_document_start():
    chunks = chat.build_chunks(EN, KO)
    rows = chat.retrieve(chunks, "zzzz")  # 겹치는 어휘가 전혀 없는 질문
    assert rows and len(rows) <= 3


# --- 인용 해석 ----------------------------------------------------------


def test_resolve_citations_numbers_in_order_and_drops_unknown():
    text = "첫째 [[r5]] 둘째 [[ r2 ]] 다시 [[r5]] 그리고 [[r99]]."
    clean, rows = chat.resolve_citations(text, [2, 5, 7])
    assert rows == [5, 2]
    assert "[[r99]]" not in clean
    assert clean.count("[[r5]]") == 2


def test_render_html_makes_chips_and_escapes_html():
    html = chat.render_html("주장 [[r5]] <script>alert(1)</script>", [5])
    assert 'data-row="5"' in html and 'data-n="1"' in html and 'data-kind="row"' in html
    assert "<script>" not in html and "&lt;script&gt;" in html


def test_resolve_citations_handles_rows_and_notes_with_shared_numbers():
    text = "문단 근거 [[r2]] 그리고 메모 [[a9x]] 다시 [[r2]] 없는 메모 [[aXXX]]."
    clean, refs = chat.resolve_citations(text, [2, 5], ["a9x"])
    assert refs == [2, "a9x"]           # 등장 순서로 1,2번을 공유한다
    assert "[[aXXX]]" not in clean and clean.count("[[r2]]") == 2
    html = chat.render_html(clean, refs, {"a9x": {"id": "a9x", "row": 4}})
    assert 'data-kind="row" data-row="2" data-n="1"' in html
    assert 'chat-cite-note' in html and 'data-id="a9x"' in html
    assert 'data-row="4" data-n="2"' in html


def test_note_tag_always_starts_with_a_and_is_attribute_safe():
    assert chat.note_tag("am1k2x") == "am1k2x"
    assert chat.note_tag('b"><img>') == "abimg"   # 마커·속성을 깨는 문자는 버린다


def test_build_user_prompt_includes_reader_notes(tmp_path):
    wd = _wd(tmp_path)
    chunks = chat.attach_notes(chat.build_chunks(EN, KO),
                               [_item("a1", 3, "We interviewed 18 instructors", "표본이 작다")])
    prompt = chat.build_user_prompt(wd, chunks, [3], "몇 명?")
    assert "독자 메모 [a1] (원문에 표시, 노랑)" in prompt
    assert '"We interviewed 18 instructors" — 표본이 작다' in prompt
    assert "독자 메모" not in chat.build_user_prompt(wd, chunks, [3], "몇 명?",
                                                  include_notes=False)


def test_build_user_prompt_marks_highlight_without_note(tmp_path):
    wd = _wd(tmp_path)
    chunks = chat.attach_notes(chat.build_chunks(EN, KO),
                               [_item("a2", 4, "Interviews lasted 45 minutes each.")])
    assert "(메모 없이 하이라이트만)" in chat.build_user_prompt(wd, chunks, [4], "질문")


def test_build_system_mentions_notes_only_when_needed():
    assert "독자 메모" not in chat.build_system(False)
    assert "[[a" in chat.build_system(True)


# --- 답변 ---------------------------------------------------------------


def _fake(answer_text: str, *, parse_ok: bool = True) -> FakeProvider:
    def parse_fn(_s, _u, schema):
        if not parse_ok:
            raise RuntimeError("확장 실패")
        return schema(keywords_en=["interview", "instructors"], keywords_ko=["인터뷰"])

    return FakeProvider(complete_fn=lambda _s, _u: answer_text, parse_fn=parse_fn,
                        model="gpt-5.6-luna")


def test_answer_builds_turn_with_only_allowed_citations(tmp_path):
    wd = _wd(tmp_path)
    rows = chat.retrieve(chat.build_chunks(EN, KO), "몇 명을 인터뷰했나?")
    used = rows[0]
    prov = _fake(f"18명을 인터뷰했다 [[r{used}]]. 그리고 [[r99]] 없는 근거.")
    turn = chat.answer(wd, prov, "몇 명을 인터뷰했나?")
    assert [c["row"] for c in turn["citations"]] == [used]
    assert turn["citations"][0]["n"] == 1
    assert turn["citations"][0]["en"]  # 인용 칩 아래에 펼칠 원문
    assert f'data-row="{used}"' in turn["answer_html"]
    assert "[[r99]]" not in turn["answer_md"] and "r99" not in turn["answer_html"]
    assert used in turn["retrieved"] and turn["model"] == "gpt-5.6-luna"
    assert turn["error"] is None and turn["cost_usd"] >= 0


def test_answer_escapes_html_in_model_output(tmp_path):
    wd = _wd(tmp_path)
    turn = chat.answer(wd, _fake("<script>x</script> 답"), "질문")
    assert "<script>" not in turn["answer_html"]


def test_answer_survives_query_expansion_failure(tmp_path):
    wd = _wd(tmp_path)
    turn = chat.answer(wd, _fake("답 [[r0]]", parse_ok=False), "인터뷰 몇 명?")
    assert turn["answer_md"].startswith("답")
    assert turn["retrieved"]  # 원문 토큰만으로도 검색이 된다


def test_answer_without_translation(tmp_path):
    wd = _wd(tmp_path, ko=None)
    turn = chat.answer(wd, _fake("영어만 있는 논문 [[r1]]"), "method?")
    assert turn["citations"] == [] or turn["citations"][0]["ko"] is None


def test_answer_prompt_includes_paragraph_markers(tmp_path):
    wd = _wd(tmp_path)
    seen = {}

    def complete_fn(system, user):
        seen["system"], seen["user"] = system, user
        return "확인"

    prov = FakeProvider(complete_fn=complete_fn,
                        parse_fn=lambda _s, _u, sc: sc(), model="gpt-5.6-luna")
    chat.answer(wd, prov, "인터뷰 몇 명?")
    assert "[[r" in seen["system"]  # 인용 표기 규칙
    assert "[r" in seen["user"] and "EN:" in seen["user"] and "KO:" in seen["user"]
    assert "## 질문" in seen["user"]


def test_answer_cites_reader_note(tmp_path):
    from md4paper.ui import annotations

    wd = _wd(tmp_path)
    annotations.save(wd, [_item("anote1", 3, "We interviewed 18 instructors", "표본이 작다",
                                color="green")])
    seen = {}

    def complete_fn(system, user):
        seen["system"], seen["user"] = system, user
        return "본문은 18명이라 하고 [[r3]], 메모에는 표본이 작다고 적혀 있다 [[anote1]]."

    prov = FakeProvider(complete_fn=complete_fn, parse_fn=lambda _s, _u, sc: sc(),
                        model="gpt-5.6-luna")
    turn = chat.answer(wd, prov, "내가 메모한 내용 정리해줘")
    assert "독자 메모 [anote1]" in seen["user"] and "[[a" in seen["system"]
    kinds = [(c["kind"], c["n"]) for c in turn["citations"]]
    assert ("row", 1) in kinds and ("note", 2) in kinds
    note = next(c for c in turn["citations"] if c["kind"] == "note")
    assert note["id"] == "anote1" and note["row"] == 3 and note["color"] == "green"
    assert note["quote"].startswith("We interviewed") and note["note"] == "표본이 작다"
    assert note["heading"] == "2 Method" and note["en"]
    assert 'data-kind="note"' in turn["answer_html"]
    assert 'data-id="anote1"' in turn["answer_html"]


def test_answer_without_notes_drops_note_citations(tmp_path):
    from md4paper.ui import annotations

    wd = _wd(tmp_path)
    annotations.save(wd, [_item("anote2", 3, "We interviewed 18 instructors", "표본이 작다")])
    prov = _fake("메모 근거 [[anote2]] 만 쓴 답.")
    turn = chat.answer(wd, prov, "질문", include_notes=False)
    assert turn["citations"] == []                    # 허용되지 않은 근거는 지운다
    assert "anote2" not in turn["answer_md"]


# --- 기록 ---------------------------------------------------------------


def _turn(**over) -> dict:
    base = {"id": "t1", "ts": 1.0, "question": "질문", "answer_md": "답",
            "answer_html": "<p>답</p>", "citations": [{"n": 1, "row": 3, "heading": "H",
                                                       "en": "en", "ko": "ko"}],
            "retrieved": [3, 4], "model": "m", "cost_usd": 0.001, "error": None}
    base.update(over)
    return base


def test_history_roundtrip_and_clear(tmp_path):
    wd = _wd(tmp_path)
    chat.append(wd, _turn())
    chat.append(wd, _turn(id="t2", question="둘째"))
    assert json.loads(wd.chat_json.read_text(encoding="utf-8"))["version"] == chat.VERSION
    turns = chat.load(wd)
    assert [t["question"] for t in turns] == ["질문", "둘째"]
    assert turns[0]["citations"][0]["row"] == 3
    chat.clear(wd)
    assert not wd.chat_json.exists()  # 빈 껍데기를 남기지 않는다
    assert chat.load(wd) == []


def test_history_drops_broken_turns_and_caps_length(tmp_path):
    wd = _wd(tmp_path)
    chat.append(wd, _turn(question="   "))  # 질문이 없으면 저장하지 않는다
    assert chat.load(wd) == []
    for i in range(chat.MAX_TURNS + 5):
        chat.append(wd, _turn(id=f"t{i}", question=f"q{i}"))
    turns = chat.load(wd)
    assert len(turns) == chat.MAX_TURNS
    assert turns[0]["question"] == "q5"  # 오래된 것부터 버린다


def test_load_ignores_corrupt_file(tmp_path):
    wd = _wd(tmp_path)
    wd.chat_json.write_text("{망가진", encoding="utf-8")
    assert chat.load(wd) == []


# --- 라우트 -------------------------------------------------------------


@pytest.fixture()
def client(tmp_path):
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from md4paper.ui import chat_panel

    wd = _wd(tmp_path)
    app = fastapi.FastAPI()
    state = {"provider": lambda: _fake("검색 결과에 따르면 18명이다 [[r0]]")}
    chat_panel.register_routes(app, lambda t: wd if t == "tok" else None,
                               build_provider=lambda: state["provider"]())
    return TestClient(app), wd, state


def test_route_get_post_delete_flow(client):
    c, wd, _state = client
    got = c.get("/chat/tok").json()
    assert got["turns"] == [] and got["ready"] is True

    posted = c.post("/chat/tok", json={"question": "몇 명을 인터뷰했나?"})
    assert posted.status_code == 200
    turn = posted.json()
    assert turn["question"] == "몇 명을 인터뷰했나?" and turn["retrieved"]
    assert chat.load(wd)  # 기록에 남는다

    assert len(c.get("/chat/tok").json()["turns"]) == 1
    assert c.delete("/chat/tok").json() == {"ok": True}
    assert c.get("/chat/tok").json()["turns"] == []


def test_route_reports_note_count_and_passes_include_notes(client):
    from md4paper.ui import annotations

    c, wd, state = client
    assert c.get("/chat/tok").json()["notes"] == 0
    annotations.save(wd, [_item("anote3", 3, "We interviewed 18 instructors", "표본이 작다"),
                          _item("anote4", 4, "Interviews lasted 45 minutes each.")])
    assert c.get("/chat/tok").json()["notes"] == 2

    seen = {}

    def spy():
        def complete_fn(_s, user):
            seen["user"] = user
            return "답"
        return FakeProvider(complete_fn=complete_fn, parse_fn=lambda _s, _u, sc: sc())

    state["provider"] = spy
    c.post("/chat/tok", json={"question": "메모 정리"})
    assert "독자 메모 [anote3]" in seen["user"]              # 기본은 포함
    c.post("/chat/tok", json={"question": "메모 정리", "include_notes": False})
    assert "독자 메모" not in seen["user"]


def test_route_rejects_empty_question(client):
    c, _wd, _state = client
    r = c.post("/chat/tok", json={"question": "   "})
    assert r.status_code == 400 and r.json()["error"]


def test_route_reports_missing_key(client):
    c, _wd, state = client

    def no_key():
        raise RuntimeError("openai API 키가 없습니다.")

    state["provider"] = no_key
    got = c.get("/chat/tok").json()
    assert got["ready"] is False and "키가 없습니다" in got["reason"]
    r = c.post("/chat/tok", json={"question": "질문"})
    assert r.status_code == 400 and "키가 없습니다" in r.json()["error"]


def test_route_reports_llm_failure(client):
    c, _wd, state = client

    def boom():
        return FakeProvider(complete_fn=lambda _s, _u: (_ for _ in ()).throw(
            RuntimeError("429 rate limited\n두 번째 줄")), parse_fn=lambda _s, _u, sc: sc())

    state["provider"] = boom
    r = c.post("/chat/tok", json={"question": "질문"})
    assert r.status_code == 502 and r.json()["error"] == "429 rate limited"


def test_route_unknown_token_is_404(client):
    c, _wd, _state = client
    assert c.get("/chat/nope").status_code == 404
    assert c.post("/chat/nope", json={"question": "q"}).status_code == 404
    assert c.delete("/chat/nope").status_code == 404


def test_init_js_carries_token_and_reason():
    from md4paper.ui import chat_panel

    js = chat_panel.init_js("tok123", False, "openai API 키가 없습니다.")
    assert "tok123" in js and "__mdChatReady" in js and "키가 없습니다" in js
    assert "<" not in js  # </script> 조기 종료 방지


# --- 모델 고르기 --------------------------------------------------------


def test_chat_choice_falls_back_to_global_default():
    """[chat]을 안 정했으면 전역 기본을 그대로 따른다."""
    from md4paper import config

    assert config.resolve_chat_choice() == (config.DEFAULT_PROVIDER,
                                            config.DEFAULT_MODELS[config.DEFAULT_PROVIDER])
    config.set_default("default_provider", "anthropic")
    assert config.resolve_chat_choice() == ("anthropic", config.DEFAULT_MODELS["anthropic"])


def test_chat_choice_overrides_global_and_clears():
    """챗봇만 다른 모델로 — 전역 기본(번역 등)은 건드리지 않는다."""
    from md4paper import config

    config.set_default("default_provider", "openai")
    config.set_chat_choice("anthropic", "claude-opus-5")
    assert config.resolve_chat_choice() == ("anthropic", "claude-opus-5")
    assert config.resolve_provider() == "openai"          # 전역은 그대로

    config.set_chat_choice("gemini")                      # 모델 생략 → 그 제공사 기본
    assert config.resolve_chat_choice() == ("gemini", config.DEFAULT_MODELS["gemini"])

    config.set_chat_choice(None)                          # 해제 → 다시 전역
    assert config.resolve_chat_choice() == ("openai", config.DEFAULT_MODELS["openai"])


def test_chat_choice_rejects_unknown_provider():
    from md4paper import config

    with pytest.raises(ValueError):
        config.set_chat_choice("mistral", "whatever")


def test_model_options_marks_providers_without_key():
    """키 없는 제공사도 목록에는 남긴다 — 왜 못 고르는지 보여주려고."""
    from md4paper import config
    from md4paper.ui import chat_panel

    config.set_key("openai", "sk-test")
    opts = chat_panel.model_options()
    assert {o["model"] for o in opts} >= set(config.MODEL_TIERS["anthropic"])
    by_prov = {o["provider"]: o["has_key"] for o in opts}
    assert by_prov["openai"] is True and by_prov["anthropic"] is False


def test_route_put_model_switches_and_reports(client):
    c, _wd, _state = client
    from md4paper import config

    config.set_key("anthropic", "sk-ant-test")
    got = c.put("/chat/tok/model", json={"provider": "anthropic", "model": "claude-sonnet-5"})
    assert got.status_code == 200 and got.json()["model"] == "claude-sonnet-5"
    assert config.resolve_chat_choice() == ("anthropic", "claude-sonnet-5")
    assert c.get("/chat/tok").json()["picked"] == "claude-sonnet-5"


def test_route_put_model_rejects_unknown_provider(client):
    c, _wd, _state = client
    got = c.put("/chat/tok/model", json={"provider": "mistral", "model": "x"})
    assert got.status_code == 400


def test_route_put_model_unknown_token_is_404(client):
    c, _wd, _state = client
    assert c.put("/chat/nope/model", json={"provider": "openai"}).status_code == 404


# --- '읽던 자리로' 오버레이 (문자열 계약) -------------------------------
# 이 기능은 순수 클라이언트라 파이썬 로직이 없다. 그래서 검증 표면은 (1) 마크업이 서랍 밖에
# 있다는 구조, (2) 파이썬 상수와 CSS/JS 문자열이 한 값을 가리킨다는 단일 출처, (3) 순서가
# 곧 동작인 몇 줄(무장은 스크롤 전에, 유예는 자동 해제 앞에)이다.


def _js_block(src: str, head: str) -> str:
    """`function foo(`부터 다음 함수 선언까지 — 한 함수 안의 줄 순서를 보려고 자른다."""
    i = src.index(head)
    return src[i:src.index("\n  function ", i + 1)]


def test_jump_back_markup_sits_outside_the_drawer():
    """서랍 안에 두면 서랍을 닫는 순간 함께 사라진다 — 접고 본문만 읽는 사람에게도 복귀는 필요하다."""
    from md4paper.ui import chat_panel

    html = chat_panel.HTML
    assert html.index('class="mc-foot"') < html.index('id="md-jump-back"') < html.index("<script>")
    # 서랍 시작 → 알약 시작 사이의 div가 균형 잡혀 있다 = 서랍이 먼저 닫힌다(중첩이 아니다)
    seg = html[html.index('<div id="md-chat-panel"'):html.index('<div id="md-jump-back"')]
    assert seg.count("<div") == seg.count("</div>")


def test_chat_panel_body_html_divs_balance():
    """마크업을 잘못 닫으면 서랍이 알약을 삼킨다 — 브라우저는 조용히 고쳐서 티가 안 난다."""
    from md4paper.ui import chat_panel

    body = chat_panel.HTML.split("<script>")[0]
    assert body.count("<div") == body.count("</div>")


def test_jump_back_constants_are_single_sourced():
    """파이썬 상수와 문자열 안 리터럴이 갈라지면 CSS가 붙지 않거나 전역 이름이 어긋난다."""
    from md4paper.ui import chat_panel

    css, html = chat_panel.CSS, chat_panel.HTML
    assert f'id="{chat_panel.JUMP_BACK_ID}"' in html
    assert f"#{chat_panel.JUMP_BACK_ID}" in css
    assert f"z-index: {chat_panel.JUMP_BACK_Z}" in css
    assert html.count(f"window.{chat_panel.JUMP_BACK_API} =") == 1
    assert f"JB_EDGE = {chat_panel.JUMP_EDGE_PX}" in html


def test_jump_back_z_index_between_find_bar_and_drawers():
    """알약은 본문 층(app.py의 핸들·스티키는 1~5) 위, 서랍·찾기 바 아래여야 한다.

    서랍보다 위로 올리면 서랍 옆에 붙은 알약이 서랍을 뚫고 나오고, 찾기 바보다 위로 올리면
    Esc의 주인이 찾기 바일 때(아래 Esc 게이트) 알약이 그 위를 덮어 순서가 어긋난다.
    """
    import re

    from md4paper.ui import chat_panel, find_bar

    panel_rule = chat_panel.CSS.split("#md-chat-panel {", 1)[1].split("}", 1)[0]
    panel_z = int(re.search(r"z-index:\s*(\d+)", panel_rule).group(1))
    find_z = max(int(v) for v in re.findall(r"z-index:\s*(\d+)", find_bar.CSS))
    assert 100 < chat_panel.JUMP_BACK_Z < min(panel_z, find_z)


def test_jump_back_has_dark_mode_override():
    """안 덮으면 흰 알약이 다크 본문 위에서 눈을 때린다."""
    from md4paper.ui import chat_panel

    dark = chat_panel.CSS.split("@media (prefers-color-scheme: dark)")[1]
    assert "#md-jump-back" in dark
    assert "background: #262626" in dark
    assert "--mjb-ring" in dark


def test_jump_back_marks_before_every_programmatic_scroll():
    """무장이 스크롤 뒤로 가면 애니메이션 중간값을 집어 앵커가 목적지 쪽으로 끌려간다."""
    from md4paper.ui import chat_panel

    for head in ("function gotoRow(", "function gotoNote("):
        piece = _js_block(chat_panel.HTML, head)
        assert piece.index("jbMark(") < piece.index("scrollIntoView(")


def test_jump_back_container_lookup_is_gated():
    """단독 `.sbs-grid`는 번역 탭 결과 그리드까지 잡는다 — [data-row]로 뷰어 것만 가려낸다."""
    from md4paper.ui import chat_panel

    html = chat_panel.HTML
    assert "querySelector('.sbs-grid')" not in html
    assert "'.sbs-grid, .vpane'" in html
    assert "matches('[data-row]')" in html


def test_jump_back_buttons_are_real_and_labelled():
    """진짜 button 두 개라 Tab·Enter가 그냥 되고, 숨을 때 탭 순서에서 알아서 빠진다."""
    from md4paper.ui import chat_panel

    html = chat_panel.HTML
    seg = html[html.index('<div id="md-jump-back"'):html.index("<script>")]
    assert seg.count('type="button"') == 2
    assert 'class="mjb-go"' in seg and 'class="mjb-x"' in seg
    assert seg.count("aria-label=") == 2
    assert seg.count('aria-hidden="true"') == 1
    assert 'role="status"' in seg


def test_jump_back_thresholds_have_hysteresis():
    """해제 임계가 무장 임계보다 커지면 알약이 뜨자마자 스스로 사라진다."""
    import re

    from md4paper.ui import chat_panel

    html = chat_panel.HTML
    arm = float(re.search(r"JB_ARM = ([\d.]+)", html).group(1))
    home = float(re.search(r"JB_HOME = ([\d.]+)", html).group(1))
    arm_min = int(re.search(r"JB_ARM_MIN = (\d+)", html).group(1))
    home_min = int(re.search(r"JB_HOME_MIN = (\d+)", html).group(1))
    assert arm > home and arm_min > home_min


def test_jump_back_has_settle_grace_before_auto_dismiss():
    """유예가 빠지면 무장 직후엔 아직 핀 자리라 첫 무거운 틱이 곧바로 해제한다 — 나가는 점프
    도중에 알약이 사라진다."""
    import re

    from md4paper.ui import chat_panel

    html = chat_panel.HTML
    grace = int(re.search(r"JB_GRACE = (\d+)", html).group(1))
    assert grace >= 500                      # smooth scrollIntoView(대개 300~500ms)보다 넉넉히
    assert "Date.now() - jbJumpAt > JB_GRACE" in html


def test_jump_back_flash_is_not_the_citation_flash():
    """"여기가 근거다"(파랑)와 "여기로 돌아왔다"(무채색)는 다른 말이다 — 회귀로 기존 둘도 본다."""
    from md4paper.ui import chat_panel

    css, html = chat_panel.CSS, chat_panel.HTML
    assert ".mjb-flash" in css and "@keyframes mdJumpFlash" in css
    assert "'mjb-flash'" in html
    assert ".chat-cite-flash" in css
    assert "'chat-cite-flash'" in html and "'md-anno-flash'" in html


def test_jump_back_wheel_handler_can_prevent_default():
    """passive 리스너는 preventDefault를 못 한다 — 알약 위에서 휠이 먹통이 된다."""
    from md4paper.ui import chat_panel

    assert "'wheel'" in chat_panel.HTML
    assert "{passive: false}" in chat_panel.HTML


def test_jump_pin_is_not_persisted():
    """핀은 메모리에만 산다 — 리로드하면 뷰어는 맨 위에서 다시 시작하므로 지난 생애의 핀은
    도움보다 혼란이다(서랍 폭·'메모 포함'과 성격이 다르다)."""
    import re

    from md4paper.ui import chat_panel

    keys = set(re.findall(r"localStorage\.(?:get|set)Item\('([^']+)'", chat_panel.HTML))
    assert keys == {"md4chat.notes", "md4chat.w"}


def test_escape_still_closes_drawer_without_pin():
    """Esc는 핀이 없으면 오늘과 똑같이 서랍을 닫고, 다른 오버레이가 열려 있으면 비켜선다."""
    from md4paper.ui import chat_panel

    html = chat_panel.HTML
    seg = html[html.index("document.addEventListener('keydown'"):]
    assert "#md-img-zoom.open" in seg and "#md-anno-pop.open" in seg and "#md4-find.on" in seg
    assert "jbDismiss()" in seg
    assert "panel.classList.remove('open')" in seg
    # 회귀 — 서랍의 나머지 배선을 건드리지 않았다
    assert "window.__mdChatTogglePanel" in html
    assert "a.chat-cite" in html and ".mc-list" in html
