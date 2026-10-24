"""에이전트 검색 — 정렬 행 청크·메모 붙이기·BM25 (chat.py에서 옮긴 것) + 논문별 캐시."""

from __future__ import annotations

import os

import pytest

pytest.importorskip("nicegui", reason="웹 UI 의존성(ui extra) 미설치")
pytest.importorskip("pydantic_ai", reason="agent 의존성 미설치")

from agent_support import EN, KO, item, make_wd, write_notes  # noqa: E402

from narani.ui.agent import retrieval  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_cache():
    retrieval.clear_cache()
    yield
    retrieval.clear_cache()


def test_build_chunks_matches_align_rows_and_marks_heading_rows():
    from narani.ui.app import align_rows

    rows = align_rows(EN, KO)
    chunks = retrieval.build_chunks(EN, KO)
    assert rows is not None and len(chunks) == len(rows)
    assert [c.row for c in chunks] == list(range(len(rows)))
    method = next(c for c in chunks if "interviewed 18" in c.en)
    assert method.heading == "2 Method" and "18명" in (method.ko or "")
    heads = [(c.head_level, c.heading) for c in chunks if c.head_level]
    assert heads == [(1, "Bespoke Bots"), (2, "2 Method"), (2, "3 Results")]


def test_build_chunks_falls_back_without_translation():
    from narani.ui.app import _split_sections

    chunks = retrieval.build_chunks(EN, None)
    assert len(chunks) == len(_split_sections(EN)) and all(c.ko is None for c in chunks)


def test_attach_notes_by_row_and_recovers_shifted_row():
    chunks = retrieval.attach_notes(retrieval.build_chunks(EN, KO), [
        item("a1", 3, "We interviewed 18 instructors", "표본이 작다"),
        item("a2", 99, "Interviews lasted 45 minutes each.", "짧다"),
        item("a3", 99, "이 논문에 없는 문장입니다"),
        {"id": "a4", "anchors": []},
        "망가진 항목",
    ])
    by_row = {c.row: c for c in chunks}
    assert [n["id"] for n in by_row[3].notes] == ["a1"]
    assert [n["id"] for n in by_row[4].notes] == ["a2"] and by_row[4].notes[0]["row"] == 4
    assert sum(len(c.notes) for c in chunks) == 2


def test_attach_notes_makes_heading_row_searchable():
    chunks = retrieval.attach_notes(retrieval.build_chunks(EN, KO),
                                    [item("a5", 5, "## 3 Results", "여기부터 다시 읽기")])
    by_row = {c.row: c for c in chunks}
    assert by_row[5].searchable is True
    assert "여기부터" in retrieval.doc_text(by_row[5])
    assert "여기부터" not in retrieval.doc_text(by_row[5], include_notes=False)


def test_bm25_ranks_relevant_rows_english_and_korean():
    chunks = retrieval.build_chunks(EN, KO)
    idx = retrieval.BM25Index(chunks)
    (row, score), *_ = idx.search(retrieval.tokenize("how many instructors were interviewed?"))
    assert score > 0 and "interviewed 18" in chunks[row].en
    hits = idx.search(retrieval.tokenize("인터뷰는 몇 분 진행했나?"))
    assert "45" in chunks[hits[0][0]].en


def test_retrieve_note_text_and_note_questions_and_fallback():
    chunks = retrieval.attach_notes(retrieval.build_chunks(EN, KO),
                                    [item("a8", 6, "Instructors wanted control", "후속 연구 거리")])
    assert retrieval.retrieve(chunks, "후속 연구 거리라고 적어 둔 데")[0] == 6
    assert retrieval.retrieve(chunks, "내가 메모한 내용 정리해줘")[0] == 6
    rows = retrieval.retrieve(chunks, "zzzz")
    assert rows and len(rows) <= 3


def test_retrieve_uses_given_index():
    chunks = retrieval.build_chunks(EN, KO)
    idx = retrieval.BM25Index(chunks)
    assert retrieval.retrieve(chunks, "interviewed", index=idx) == \
        retrieval.retrieve(chunks, "interviewed")


def test_tokenize_english_words_and_korean_bigrams():
    toks = retrieval.tokenize("**Interviewed** 18 instructors of the study")
    assert "the" not in toks and "interview" in toks and "instructor" in toks
    assert retrieval.tokenize("설문조사") == ["설문", "문조", "조사"]


# --- 캐시 ---------------------------------------------------------------


def _bump(path):
    """mtime을 확실히 바꾼다 (같은 틱 안의 두 번 쓰기에도 키가 달라지게)."""
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))


def test_paper_cache_hits_until_files_change(tmp_path):
    wd = make_wd(tmp_path)
    first = retrieval.paper(wd)
    assert retrieval.paper(wd) is first          # 그대로면 다시 만들지 않는다

    write_notes(wd, [item("a1", 3, "We interviewed 18 instructors", "메모")])
    _bump(wd.annotations_json)
    second = retrieval.paper(wd)
    assert second is not first                   # 메모가 바뀌면 무효화
    assert any(c.notes for c in second.chunks)

    wd.en_md.write_text(EN + "\nNew closing paragraph.\n", encoding="utf-8")
    _bump(wd.en_md)
    third = retrieval.paper(wd)
    assert third is not second
    # 같은 논문의 낡은 판은 버린다 — 캐시에는 이 논문이 하나만 남는다
    root = str(wd.root.resolve())
    assert sum(1 for k in retrieval._cache if k[0] == root) == 1


def test_chunks_of_reads_annotations_and_can_skip_notes(tmp_path):
    wd = make_wd(tmp_path)
    write_notes(wd, [item("a7", 3, "We interviewed 18 instructors", "메모")])
    assert any(c.notes for c in retrieval.chunks_of(wd))
    assert not any(c.notes for c in retrieval.chunks_of(wd, include_notes=False))


def test_cache_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(retrieval, "CACHE_PAPERS", 2)
    wds = [make_wd(tmp_path, f"p{i}") for i in range(3)]
    for wd in wds:
        retrieval.paper(wd)
    assert len(retrieval._cache) == 2
